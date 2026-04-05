import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Dict
from constants import BLOCK_SECONDS, INTERVAL_SECONDS, REQUIRED_BLOCKS_RATIO
from bittensor import AsyncSubtensor
from apy import calculate_interval_blocks, calculate_apy
from filter import has_enough_stake
from helpers import (
    calc_inherited_on_subnet,
    get_children,
    get_divs_for_hotkey_on_subnet,   # returns alpha units (scaled via Balance; name kept)
    get_parents,
    get_stake_for_hotkey_on_subnet,   # netuid!=0 -> alpha stake; netuid==0 -> tao stake (root)
    get_tao_weight,
)


def calculate_hotkey_subnet_apy(
    events: List[Dict],
    results: List[dict],
    actual_interval_seconds: float,
    no_filters: bool = False,
) -> Tuple[float, float, float, int]:
    """
    Calculate APY for a hotkey on a subnet from fetched data.

    Since subtensor v3.3.0 (spec 361), AlphaDividendsPerSubnet contains only
    pure subnet alpha dividends. Root-originated dividends are now tracked
    separately in RootAlphaDividendsPerSubnet on-chain, so no deduction is
    needed here.

    Args:
        events: List of event dicts with keys: block, netuid, tempo
        results: List of fetched data dicts (one per event), or -1 for failed queries
        actual_interval_seconds: Actual interval duration in seconds (for APY calculation)
        no_filters: If False, apply stake filters to guard noisy events

    Returns:
        Tuple[float, float, float, int]: (apy_percent, divs_sum_alpha, period_yield, skipped)
    """
    divs_sum_alpha = 0.0
    yield_product = 1.0
    skipped = 0

    for event_index, _ in enumerate(events):
        data = results[event_index]
        if data == -1:
            skipped += 1
            continue

        subnet_alpha_stake = data["subnet_alpha_stake"]
        root_stake_tao     = data["root_stake_tao"]
        inh_root_stake     = data["inh_root_stake"]
        inh_subnet_stake   = data["inh_subnet_stake"]
        tao_weight_param   = data["tao_weight_param"]
        alpha_div_raw      = data["alpha_div_raw"]

        if alpha_div_raw == 0:
            continue

        # Apply filter (guards noisy/minuscule stake epochs)
        if not no_filters and not has_enough_stake(
            root_stake_tao, subnet_alpha_stake, inh_root_stake, inh_subnet_stake, tao_weight_param
        ):
            skipped += 1
            continue

        # AlphaDividendsPerSubnet now contains only pure subnet alpha dividends
        # (root dividends moved to RootAlphaDividendsPerSubnet in subtensor v3.3.0-361)
        denom = subnet_alpha_stake
        if denom <= 0:
            skipped += 1
            continue

        epoch_yield = alpha_div_raw / denom

        divs_sum_alpha  += alpha_div_raw
        yield_product   *= (1.0 + epoch_yield)

    period_yield = yield_product - 1.0
    compounding_periods = INTERVAL_SECONDS["year"] / actual_interval_seconds
    apy_percent = calculate_apy(period_yield, compounding_periods)

    return apy_percent, divs_sum_alpha, period_yield, skipped


async def retrieve_and_calculate_hotkey_subnet_apy(
    subtensor: AsyncSubtensor,
    netuid: int,
    hotkey: str,
    interval: str,
    block: int,
    progress,
    batch_size: int = 100,
    use_inherited_filer: bool = False,
    no_filters: bool = False,
) -> Tuple[float, float]:
    """
    Subnet APY for a hotkey.

    For each epoch boundary at block B:
      alpha_div_raw          = AlphaDividendsPerSubnet[netuid, hotkey]   (alpha)
      subnet_alpha_stake     = TotalHotkeyAlpha[hotkey, netuid]          (alpha)
      epoch_yield            = alpha_div_raw / subnet_alpha_stake         (dimensionless)

    Since subtensor v3.3.0 (spec 361), AlphaDividendsPerSubnet no longer
    includes root-originated dividends, so no root deduction is required.
    """

    if netuid == 0:
        raise Exception('For root network use calculate_hotkey_root_apy() instead')

    subnet = await subtensor.subnet(netuid, block)
    tempo = subnet.tempo
    last_epoch_block = subnet.last_step

    interval_blocks = calculate_interval_blocks(tempo, interval)

    # Calculate the actual interval using INTERVAL_SECONDS (like root_calc.py)
    interval_seconds = INTERVAL_SECONDS[interval]
    actual_interval_blocks = int(interval_seconds / BLOCK_SECONDS)
    actual_interval_seconds = actual_interval_blocks * BLOCK_SECONDS

    # FIX: Use exactly N epochs to ensure consistent event count
    # Calculate expected number of epochs for this interval (floor division)
    period = tempo + 1
    expected_epochs = actual_interval_blocks // period  # Floor division to get whole epochs
    if expected_epochs < 1:
        expected_epochs = 1  # At least 1 epoch

    # Use exactly expected_epochs epochs as the window
    # Start from last_epoch_block and go back (expected_epochs - 1) epochs
    # This ensures we always get exactly expected_epochs events
    start_block = last_epoch_block - (expected_epochs - 1) * period

    # Build list of epoch boundary blocks WITHIN the calculation window
    # Collect exactly expected_epochs events in reverse chronological order (newest first)
    # Then reverse to chronological (oldest first)
    events: List[Dict] = []
    epoch = last_epoch_block
    for _ in range(expected_epochs):
        events.append({"block": epoch, "netuid": netuid, "tempo": tempo})
        epoch -= period
        if epoch < start_block:
            break  # Safety check

    # Reverse to chronological order (oldest first)
    events.reverse()

    data_task = progress.add_task(f"[cyan]Fetching data for {hotkey}", total=len(events))

    async def query_data_with_progress(event_block: int, hotkey: str, netuid: int):
        """
        Fetch all data for one epoch boundary:
          - tao_weight (for filter)
          - subnet_alpha_stake (alpha), root_stake_tao (tao on root, for filter)
          - alpha_div_raw (alpha) from AlphaDividendsPerSubnet
          - inherited stakes (optional)
        """
        try:
            (
                tao_weight_param,
                subnet_alpha_stake,
                root_stake_tao,
                alpha_div_raw,
            ) = await asyncio.gather(
                get_tao_weight(subtensor, event_block),
                get_stake_for_hotkey_on_subnet(subtensor, hotkey, netuid, event_block),
                get_stake_for_hotkey_on_subnet(subtensor, hotkey, 0, event_block),
                get_divs_for_hotkey_on_subnet(subtensor, hotkey, netuid, event_block),
            )

            inh_root_stake = 0.0
            inh_subnet_stake = 0.0
            if use_inherited_filer:
                parents, children = await asyncio.gather(
                    get_parents(subtensor, hotkey, netuid, event_block),
                    get_children(subtensor, hotkey, netuid, event_block),
                )
                inh_root_stake, inh_subnet_stake = await asyncio.gather(
                    calc_inherited_on_subnet(subtensor, root_stake_tao, 0, parents, children, event_block),
                    calc_inherited_on_subnet(subtensor, subnet_alpha_stake, netuid, parents, children, event_block),
                )

            progress.update(data_task, advance=1)

            return {
                "block": event_block,
                "tao_weight_param": tao_weight_param,
                "alpha_div_raw": alpha_div_raw,
                "root_stake_tao": root_stake_tao,
                "subnet_alpha_stake": subnet_alpha_stake,
                "inh_root_stake": inh_root_stake,
                "inh_subnet_stake": inh_subnet_stake,
            }
        except Exception as e:
            progress.update(data_task, advance=1)
            return -1

    # Batch fetch
    data_tasks = [lambda event=event: query_data_with_progress(event["block"], hotkey, netuid) for event in events]

    results: List[dict] = []
    for i in range(0, len(data_tasks), batch_size):
        batch = data_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        results.extend([-1 if isinstance(r, Exception) else r for r in batch_results])

    # ------------------------ Calculation ------------------------
    apy_percent, divs_sum_alpha, period_yield, skipped = calculate_hotkey_subnet_apy(
        events=events,
        results=results,
        actual_interval_seconds=actual_interval_seconds,
        no_filters=no_filters,
    )

    if skipped > 0:
        progress.console.print(
            f"[yellow]Skipped {skipped} events due to query failures, filters, or invalid denominators.[/yellow]"
        )
        if len(events) - skipped < REQUIRED_BLOCKS_RATIO * len(events):
            progress.console.print(
                f"[yellow]Coverage is less than: {REQUIRED_BLOCKS_RATIO * 100:.6f}% and can lead to inaccurate results.[/yellow]"
            )

    progress.console.print(f"{interval} percentage yield: {period_yield * 100:.6f}%")
    progress.console.print(f"{interval} subnet divs [alpha]: {divs_sum_alpha:.6f}")
    progress.console.print(f"apy: {apy_percent:.6f}%")

    return apy_percent, divs_sum_alpha
