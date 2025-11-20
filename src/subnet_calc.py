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
    get_root_claimable_entries,       # returns dict {netuid: alpha_per_tao} (cumulative rate)
)


def calculate_hotkey_subnet_apy(
    events: List[Dict],
    results: List[dict],
    baseline_root_claimable_alpha_per_tao: float,
    actual_interval_seconds: float,
    no_filters: bool = False,
) -> Tuple[float, float, float, int, float]:
    """
    Calculate APY for a hotkey on a subnet from fetched data.
    
    This function contains only calculation logic - all data must be provided as arguments.
    No async operations or external queries are performed.
    
    Args:
        events: List of event dicts with keys: block, netuid, tempo
        results: List of fetched data dicts (one per event), or -1 for failed queries
        baseline_root_claimable_alpha_per_tao: Baseline root claimable α/TAO for this netuid at start_block - 1
        actual_interval_seconds: Actual interval duration in seconds (for APY calculation)
        no_filters: If False, apply stake filters to guard noisy events
    
    Returns:
        Tuple[float, float, float, int, float]: (apy_percent, alpha_only_divs_sum_alpha, period_yield, skipped, total_divs_sum_alpha)
    """
    total_divs_sum_alpha = 0.0        # raw AlphaDividendsPerSubnet (alpha)
    alpha_only_divs_sum_alpha = 0.0   # after root deduction (alpha)
    yield_product = 1.0
    skipped = 0

    # Track previous root claimable alpha per tao for delta calculation
    prev_alpha_per_tao = float(baseline_root_claimable_alpha_per_tao)

    for event_index, _ in enumerate(events):
        data = results[event_index]
        if data == -1:
            # Query failed - we don't have claimable value, so we can't update prev_alpha_per_tao
            # This means the next epoch will calculate delta from the wrong baseline
            # This is unavoidable when queries fail, but we should still track it
            skipped += 1
            continue

        subnet_alpha_stake = data["subnet_alpha_stake"]
        root_stake_tao     = data["root_stake_tao"]
        inh_root_stake     = data["inh_root_stake"]
        inh_subnet_stake   = data["inh_subnet_stake"]
        tao_weight_param   = data["tao_weight_param"]
        alpha_div_raw      = data["alpha_div_raw"]
        root_claimable_alpha_per_tao = data["root_claimable_alpha_per_tao"]  # Current value at this block

        # Calculate delta: current - previous
        curr_alpha_per_tao = float(root_claimable_alpha_per_tao)
        d_alpha_per_tao = curr_alpha_per_tao - prev_alpha_per_tao
        if d_alpha_per_tao < 0:
            d_alpha_per_tao = 0.0  # clamp negative deltas

        # IMPORTANT: Update baseline for next observation BEFORE any skip conditions
        # This ensures that even if we skip this epoch for yield calculation,
        # the claimable progression is still tracked correctly for delta calculations
        prev_alpha_per_tao = curr_alpha_per_tao

        if alpha_div_raw == 0:
            # Skip yield calculation but claimable was already updated above - correct
            continue

        # Apply filter as before (guards noisy/minuscule stake epochs)
        if not no_filters and not has_enough_stake(
            root_stake_tao, subnet_alpha_stake, inh_root_stake, inh_subnet_stake, tao_weight_param
        ):
            # Skip yield calculation but claimable was already updated above - correct
            skipped += 1
            continue

        # Root-directed component in *alpha* for this epoch:
        #   root_alpha = dRC(α/tao) * root_stake_tao(tao)
        root_alpha_component = d_alpha_per_tao * root_stake_tao
        if root_alpha_component < 0:
            root_alpha_component = 0.0

        # Deduct root-directed alpha from the raw credited alpha
        alpha_only_div = alpha_div_raw - root_alpha_component
        if alpha_only_div < 0:
            alpha_only_div = 0.0

        # Denominator is *alpha* stake on the subnet
        denom = subnet_alpha_stake
        if denom <= 0:
            skipped += 1
            continue

        epoch_yield = alpha_only_div / denom

        alpha_only_divs_sum_alpha += alpha_only_div
        total_divs_sum_alpha      += alpha_div_raw
        yield_product             *= (1.0 + epoch_yield)

    period_yield = yield_product - 1.0
    compounding_periods = INTERVAL_SECONDS["year"] / actual_interval_seconds
    apy_percent = calculate_apy(period_yield, compounding_periods)

    return apy_percent, alpha_only_divs_sum_alpha, period_yield, skipped, total_divs_sum_alpha


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
    Subnet APY for a hotkey, with root-directed share deducted in *alpha* units.

    For each epoch boundary at block B:
      alpha_div_raw          = AlphaDividendsPerSubnet[netuid, hotkey]   (alpha)
      dRC_alpha_per_tao      = max(0, RC[hotkey][netuid]_B - RC[hotkey][netuid]_{B-1})
      root_stake_tao         = TotalHotkeyAlpha[hotkey, 0]               (tao on root)
      root_alpha_component   = dRC_alpha_per_tao * root_stake_tao        (alpha)
      alpha_only_div         = max(0, alpha_div_raw - root_alpha_component) (alpha)
      subnet_alpha_stake     = TotalHotkeyAlpha[hotkey, netuid]          (alpha)
      epoch_yield            = alpha_only_div / subnet_alpha_stake        (dimensionless)

    Notes:
      * No price conversion is needed; everything subtracted/compared is in alpha units.
      * We keep the existing has_enough_stake(...) filter to guard noisy events.
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
    # Then reverse to chronological (oldest first) for correct delta calculation
    events: List[Dict] = []
    epoch = last_epoch_block
    for _ in range(expected_epochs):
        events.append({"block": epoch, "netuid": netuid, "tempo": tempo})
        epoch -= period
        if epoch < start_block:
            break  # Safety check
    
    # Reverse to chronological order (oldest first) for correct delta calculation
    events.reverse()

    data_task = progress.add_task(f"[cyan]Fetching data for {hotkey}", total=len(events))

    async def query_data_with_progress(event_block: int, hotkey: str, netuid: int):
        """
        Fetch all data for one epoch boundary:
          - tao_weight (for filter)
          - subnet_alpha_stake (alpha), root_stake_tao (tao on root)
          - alpha_div_raw (alpha) from AlphaDividendsPerSubnet
          - RootClaimable at current block (alpha/tao) for this hotkey
          - inherited stakes (optional)
        """
        try:
            (
                tao_weight_param,
                subnet_alpha_stake,
                root_stake_tao,
                alpha_div_raw,
                rc_curr_map,
            ) = await asyncio.gather(
                get_tao_weight(subtensor, event_block),
                get_stake_for_hotkey_on_subnet(subtensor, hotkey, netuid, event_block),
                get_stake_for_hotkey_on_subnet(subtensor, hotkey, 0, event_block),
                get_divs_for_hotkey_on_subnet(subtensor, hotkey, netuid, event_block),
                get_root_claimable_entries(subtensor, hotkey, event_block),
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

            # Extract root claimable alpha per tao for this netuid at current block
            root_claimable_alpha_per_tao = float((rc_curr_map or {}).get(netuid, 0.0))

            progress.update(data_task, advance=1)

            return {
                "block": event_block,
                "tao_weight_param": tao_weight_param,
                "alpha_div_raw": alpha_div_raw,                      # alpha
                "root_stake_tao": root_stake_tao,                    # tao
                "subnet_alpha_stake": subnet_alpha_stake,            # alpha
                "inh_root_stake": inh_root_stake,
                "inh_subnet_stake": inh_subnet_stake,
                "root_claimable_alpha_per_tao": root_claimable_alpha_per_tao,  # alpha / tao at current block
            }
        except Exception as e:
            progress.update(data_task, advance=1)
            return -1

    # Fetch baseline root claimable at one epoch before the first (oldest) event in the interval
    # This ensures we can calculate the delta for the first event correctly
    if len(events) > 0:
        first_event_block = events[0]["block"]  # Oldest event (first in chronological order)
        baseline_block = max(first_event_block - period, 1)
    else:
        # Fallback: use start_block - period if no events
        baseline_block = max(start_block - period, 1)
    baseline_root_claimable_map = await get_root_claimable_entries(subtensor, hotkey, baseline_block)
    if baseline_root_claimable_map is None:
        baseline_root_claimable_map = {}
    # Extract value for this specific netuid
    baseline_root_claimable_alpha_per_tao = float(baseline_root_claimable_map.get(netuid, 0.0))

    # Batch fetch
    data_tasks = [lambda event=event: query_data_with_progress(event["block"], hotkey, netuid) for event in events]

    results: List[dict] = []
    for i in range(0, len(data_tasks), batch_size):
        batch = data_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        results.extend([-1 if isinstance(r, Exception) else r for r in batch_results])

    # ------------------------ Calculation ------------------------
    apy_percent, alpha_only_divs_sum_alpha, period_yield, skipped, total_divs_sum_alpha = calculate_hotkey_subnet_apy(
        events=events,
        results=results,
        baseline_root_claimable_alpha_per_tao=baseline_root_claimable_alpha_per_tao,
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

    progress.console.print(f"{interval} percentage yield (alpha-only base): {period_yield * 100:.6f}%")
    progress.console.print(f"{interval} total raw subnet divs [alpha]: {total_divs_sum_alpha:.6f}")
    progress.console.print(f"{interval} alpha-only dividends (after root deduction) [alpha]: {alpha_only_divs_sum_alpha:.6f}")
    progress.console.print(f"apy: {apy_percent:.6f}%")

    return apy_percent, alpha_only_divs_sum_alpha
