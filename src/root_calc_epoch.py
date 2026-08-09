"""
Epoch-aligned time-weighted root APY.

Reads the exact history of RootAlphaDividendsPerSubnet plateaux inside the window
by walking backward through LastEpochBlock, one storage-read per epoch per subnet.
Every plateau's value and duration is known exactly (no dependence on assumed
tempo/period arithmetic — the chain records the actual firing block for every
epoch), so the time-weighted average is bit-perfect: no boundary jitter, no
discretization error from uniform sampling.

Algorithm per subnet N in window [start_block, end_block]:

    L₀   = LastEpochBlock[N] at end_block            # most recent epoch ≤ end_block
    L₁   = LastEpochBlock[N] at (L₀ - 1)             # previous epoch
    L₂   = LastEpochBlock[N] at (L₁ - 1)             # ...
    ...
    Lₖ   = ... (first Lᵢ ≤ start_block, i.e. pre-window epoch that covers start_block)

For each plateau Lᵢ, its RAD value covers [Lᵢ, Lᵢ₋₁). Clip to window:
    plateau_start = max(Lᵢ, start_block)
    plateau_end   = min(Lᵢ₋₁ or end_block, end_block)

Read RAD[N, hotkey], SubnetTAO[N], SubnetAlphaIn[N] at max(Lᵢ, start_block).
Convert to annual TAO rate at plateau: (RAD/RAO) · price · (YEAR_BLOCKS / tempo).
Time-weight by plateau_duration / window_blocks. Sum plateaux → subnet's window-avg
annual rate. Sum subnets → total annual TAO. Divide by root_stake → APY %.

Cost per subnet: (k+1) reads for schedule walk + 3·(k+1) reads for value/price
per plateau + 1 read of tempo ≈ 4k + 5 reads. For 1h window with tempo=360
(typical k=1): ~9 reads/subnet · ~120 active subnets ≈ 1080 RPC calls, all
parallelizable across subnets via asyncio.gather.
"""
import asyncio
from typing import Tuple, List, Dict, Optional

from constants import BLOCK_SECONDS, INTERVAL_SECONDS, RAO_PER_TAO, ROOT_MIN_STAKE_TAO
from bittensor import AsyncSubtensor
from helpers import query_subtensor, unwrap_scalar


YEAR_BLOCKS = INTERVAL_SECONDS["year"] / BLOCK_SECONDS  # 2_628_000


async def _read_last_epoch_block(subtensor: AsyncSubtensor, netuid: int, at_block: int) -> Optional[int]:
    """Return LastEpochBlock[netuid] as seen at `at_block`, or None on failure."""
    if at_block < 0:
        return None
    try:
        block_hash = await subtensor.substrate.get_block_hash(at_block)
        res = await subtensor.substrate.query(
            module="SubtensorModule", storage_function="LastEpochBlock",
            params=[netuid], block_hash=block_hash,
        )
        v = unwrap_scalar(getattr(res, "value", 0)) or 0
        return int(v)
    except Exception:
        return None


async def _collect_epoch_blocks(
    subtensor: AsyncSubtensor, netuid: int, start_block: int, end_block: int
) -> List[int]:
    """
    Walk LastEpochBlock backward from end_block down until we cross start_block.

    Returns epoch blocks in ascending order. Always includes the "pre-window"
    epoch (the last epoch that fired at or before start_block), whose plateau
    covers the beginning of the window.
    """
    epochs: List[int] = []
    L = await _read_last_epoch_block(subtensor, netuid, end_block)
    if L is None or L == 0:
        return []

    epochs.append(L)
    # Walk back until we've captured the plateau that covers start_block.
    max_iter = 20000  # sanity cap; 30d @ 12s tempo max would be ~216k blocks / min_tempo
    for _ in range(max_iter):
        if epochs[-1] <= start_block:
            break
        L_prev = await _read_last_epoch_block(subtensor, netuid, epochs[-1] - 1)
        if L_prev is None or L_prev == 0 or L_prev >= epochs[-1]:
            break
        epochs.append(L_prev)

    epochs.reverse()
    return epochs


async def _subnet_window_contribution(
    subtensor: AsyncSubtensor,
    hotkey: str,
    netuid: int,
    start_block: int,
    end_block: int,
    end_block_hash: str,
) -> Optional[Dict]:
    """
    Time-weighted annual TAO contribution of one subnet for a hotkey in the window.

    Returns a per-subnet dict, or None if the subnet contributes nothing / can't be read.
    """
    try:
        tempo_res = await subtensor.substrate.query(
            module="SubtensorModule", storage_function="Tempo",
            params=[netuid], block_hash=end_block_hash,
        )
        tempo = int(unwrap_scalar(getattr(tempo_res, "value", 0)) or 0)
    except Exception:
        return None
    if tempo == 0:
        return None

    epoch_blocks = await _collect_epoch_blocks(subtensor, netuid, start_block, end_block)
    if not epoch_blocks:
        return None

    window_blocks = max(end_block - start_block, 1)

    async def read_plateau(i: int, L: int):
        # Plateau [max(L, start_block), min(next epoch or end_block, end_block))
        p_start = max(L, start_block)
        p_end = epoch_blocks[i + 1] if i + 1 < len(epoch_blocks) else end_block
        p_end = min(p_end, end_block)
        if p_end <= p_start:
            return None

        read_at = max(L, start_block)
        try:
            block_hash = await subtensor.substrate.get_block_hash(read_at)
            rad_res, tao_res, alpha_res = await asyncio.gather(
                subtensor.substrate.query(
                    module="SubtensorModule", storage_function="RootAlphaDividendsPerSubnet",
                    params=[netuid, hotkey], block_hash=block_hash,
                ),
                subtensor.substrate.query(
                    module="SubtensorModule", storage_function="SubnetTAO",
                    params=[netuid], block_hash=block_hash,
                ),
                subtensor.substrate.query(
                    module="SubtensorModule", storage_function="SubnetAlphaIn",
                    params=[netuid], block_hash=block_hash,
                ),
            )
            rad_alpha_rao = int(unwrap_scalar(getattr(rad_res, "value", 0)) or 0)
            tao_in_rao = int(unwrap_scalar(getattr(tao_res, "value", 0)) or 0)
            alpha_in_rao = int(unwrap_scalar(getattr(alpha_res, "value", 0)) or 0)
        except Exception:
            return None

        if alpha_in_rao <= 0 or tao_in_rao <= 0:
            return None
        if rad_alpha_rao == 0:
            return None

        price = tao_in_rao / alpha_in_rao
        per_epoch_tao = (rad_alpha_rao / RAO_PER_TAO) * price
        # TAO actually earned during the portion of this plateau that lies inside the window.
        # per-block rate = per_epoch_tao / tempo; multiplied by plateau_blocks_in_window
        # gives the earned-TAO fraction attributable to this plateau.
        plateau_blocks = p_end - p_start
        plateau_tao = per_epoch_tao * (plateau_blocks / tempo)
        return plateau_tao

    contributions = await asyncio.gather(
        *[read_plateau(i, L) for i, L in enumerate(epoch_blocks)]
    )
    window_tao = sum(c for c in contributions if c is not None)
    if window_tao == 0:
        return None

    return {
        "netuid": netuid,
        "tempo": tempo,
        "epoch_blocks": epoch_blocks,
        "num_plateaux": sum(1 for c in contributions if c is not None),
        "window_tao": window_tao,
    }


async def calculate_hotkey_root_apy_epoch_aligned(
    subtensor: AsyncSubtensor,
    hotkey: str,
    interval: str,
    end_block: int,
    no_filters: bool = False,
    verbose: bool = False,
) -> Tuple[float, float, List[Dict]]:
    """
    Time-weighted root APY over the [end_block - window, end_block] window.

    Same output shape as calculate_hotkey_root_apy_snapshot:
        (apy_percent, total_annual_tao, per_subnet_contributions)
    """
    interval_seconds = INTERVAL_SECONDS[interval]
    interval_blocks = int(interval_seconds / BLOCK_SECONDS)
    start_block = max(end_block - interval_blocks, 0)

    root_stake_raw = await query_subtensor(subtensor, "TotalHotkeyAlpha", end_block, [hotkey, 0])
    root_stake_tao = (float(root_stake_raw) if root_stake_raw else 0.0) / RAO_PER_TAO
    if root_stake_tao <= 0:
        return 0.0, 0.0, []
    if (not no_filters) and root_stake_tao < ROOT_MIN_STAKE_TAO:
        return 0.0, 0.0, []

    total_networks_raw = await query_subtensor(subtensor, "TotalNetworks", end_block, [])
    total_networks = int(total_networks_raw) if total_networks_raw else 0
    netuids = list(range(1, total_networks + 1))

    end_block_hash = await subtensor.substrate.get_block_hash(end_block)

    results = await asyncio.gather(*[
        _subnet_window_contribution(subtensor, hotkey, nu, start_block, end_block, end_block_hash)
        for nu in netuids
    ])
    contributions = [r for r in results if r is not None]

    print(
        f"  DEBUG: window [{start_block}, {end_block}] ({interval}, {end_block - start_block} blocks), "
        f"netuids probed={len(netuids)}, with contribution={len(contributions)}"
    )

    # Total TAO earned across the window, then compound-annualize as in v1/v2/v3:
    #   period_yield = window_tao / root_stake_tao
    #   apy = (1 + period_yield) ^ (year / window) - 1
    total_window_tao = sum(c["window_tao"] for c in contributions)
    period_yield = total_window_tao / root_stake_tao
    window_seconds = (end_block - start_block) * BLOCK_SECONDS
    compounding_periods = INTERVAL_SECONDS["year"] / max(window_seconds, 1)
    apy = 100.0 * ((1.0 + period_yield) ** compounding_periods - 1.0)
    # Report the projected annual TAO implied by APY, so `divs` in the CLI table still
    # answers "TAO/year at this rate".
    total_annual_tao = root_stake_tao * (apy / 100.0)

    if verbose:
        contributions.sort(key=lambda c: -c["window_tao"])
        print(f"  root_stake: {root_stake_tao:.3f} TAO")
        print(f"  window TAO earned: {total_window_tao:.6f}")
        print(f"  period yield: {period_yield * 100:.6f}%  (compounded over {compounding_periods:.1f} periods)")
        print(f"  implied annual TAO: {total_annual_tao:.4f}")
        print("  top 10 subnets by TAO earned in window:")
        for c in contributions[:10]:
            print(
                f"    SN{c['netuid']:>3}  tempo={c['tempo']:>4}  "
                f"plateaux={c['num_plateaux']:>2}  "
                f"epoch_blocks={c['epoch_blocks'][:3]}{'...' if len(c['epoch_blocks']) > 3 else ''}  "
                f"= {c['window_tao']:>10.6f} TAO in window"
            )

    return apy, total_annual_tao, contributions
