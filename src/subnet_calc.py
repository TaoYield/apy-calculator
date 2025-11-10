import asyncio
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


async def calculate_hotkey_subnet_apy(
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
    epoch = subnet.last_step
    progress.console.print(f"current block: {block}, last epoch block: {epoch}")

    interval_blocks = calculate_interval_blocks(tempo, interval)
    actual_interval_seconds = interval_blocks * BLOCK_SECONDS

    # Build list of epoch boundary blocks going backward in time
    events: List[Dict] = []
    period = tempo + 1
    while epoch >= block - interval_blocks:
        events.append({"block": epoch, "netuid": netuid, "tempo": tempo})
        epoch -= period

    data_task = progress.add_task(f"[cyan]Fetching data for {hotkey}", total=len(events))

    async def query_data_with_progress(event_block: int, hotkey: str, netuid: int):
        """
        Fetch all data for one epoch boundary:
          - tao_weight (for filter)
          - subnet_alpha_stake (alpha), root_stake_tao (tao on root)
          - alpha_div_raw (alpha) from AlphaDividendsPerSubnet
          - RootClaimable at block and block-1 (alpha/tao) for this hotkey
          - inherited stakes (optional)
        """
        prev_block = max(event_block - 1, 1)
        try:
            (
                tao_weight_param,
                subnet_alpha_stake,
                root_stake_tao,
                alpha_div_raw,
                rc_prev_map,
                rc_curr_map,
            ) = await asyncio.gather(
                get_tao_weight(subtensor, event_block),
                get_stake_for_hotkey_on_subnet(subtensor, hotkey, netuid, event_block),
                get_stake_for_hotkey_on_subnet(subtensor, hotkey, 0, event_block),
                get_divs_for_hotkey_on_subnet(subtensor, hotkey, netuid, event_block),
                get_root_claimable_entries(subtensor, hotkey, prev_block),
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

            # RootClaimable deltas are per-subnet; extract this netuid
            prev_alpha_per_tao = float((rc_prev_map or {}).get(netuid, 0.0))
            curr_alpha_per_tao = float((rc_curr_map or {}).get(netuid, prev_alpha_per_tao))
            d_alpha_per_tao = curr_alpha_per_tao - prev_alpha_per_tao
            if d_alpha_per_tao < 0:
                d_alpha_per_tao = 0.0  # clamp negative deltas

            progress.update(data_task, advance=1)

            return {
                "block": event_block,
                "tao_weight_param": tao_weight_param,
                "alpha_div_raw": alpha_div_raw,               # alpha
                "root_stake_tao": root_stake_tao,             # tao
                "subnet_alpha_stake": subnet_alpha_stake,     # alpha
                "inh_root_stake": inh_root_stake,
                "inh_subnet_stake": inh_subnet_stake,
                "d_alpha_per_tao": d_alpha_per_tao,           # alpha / tao
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

    total_divs_sum_alpha = 0.0        # raw AlphaDividendsPerSubnet (alpha)
    alpha_only_divs_sum_alpha = 0.0   # after root deduction (alpha)
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
        d_alpha_per_tao    = data["d_alpha_per_tao"]

        if alpha_div_raw == 0:
            continue

        # Apply filter as before (guards noisy/minuscule stake epochs)
        if not no_filters and not has_enough_stake(
            root_stake_tao, subnet_alpha_stake, inh_root_stake, inh_subnet_stake, tao_weight_param
        ):
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

    if skipped > 0:
        progress.console.print(
            f"[yellow]Skipped {skipped} events due to query failures, filters, or invalid denominators.[/yellow]"
        )
        if len(events) - skipped < REQUIRED_BLOCKS_RATIO * len(events):
            progress.console.print(
                f"[yellow]Coverage is less than: {REQUIRED_BLOCKS_RATIO * 100:.6f}% and can lead to inaccurate results.[/yellow]"
            )

    period_yield = yield_product - 1.0
    compounding_periods = INTERVAL_SECONDS["year"] / actual_interval_seconds

    progress.console.print(f"{interval} percentage yield (alpha-only base): {period_yield * 100:.6f}%")
    progress.console.print(f"{interval} total raw subnet divs [alpha]: {total_divs_sum_alpha:.6f}")
    progress.console.print(f"{interval} alpha-only dividends (after root deduction) [alpha]: {alpha_only_divs_sum_alpha:.6f}")

    apy_percent = calculate_apy(period_yield, compounding_periods)
    progress.console.print(f"apy: {apy_percent:.6f}%")

    return apy_percent, alpha_only_divs_sum_alpha
