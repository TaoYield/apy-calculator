import asyncio
from typing import Tuple, List, Dict

import math

from constants import (
    BLOCK_SECONDS,
    INTERVAL_SECONDS,
    REQUIRED_BLOCKS_RATIO,
    BASKET_MIN_SPAN_BLOCKS,
    BASKET_MAX_SAMPLES,
    ROOT_MIN_STAKE_TAO,
    RAO_PER_TAO,
)
from bittensor import AsyncSubtensor
from apy import calculate_apy
from helpers import (
    get_root_claimable_entries,
    get_basket_deposited_tao,
    basket_supported,
    query_subtensor,
    unwrap_scalar,
)


def normalize_claimable_alpha(d: dict) -> Dict[int, float]:
    """Normalize to {netuid:int -> float α/TAO}."""
    out: Dict[int, float] = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        try:
            ki = int(k)
        except Exception:
            continue
        try:
            out[ki] = float(v)
        except Exception:
            continue
    return out


def calculate_hotkey_root_apy(
    events: List[Dict],
    baseline_claimable_alpha: Dict[int, float],
    root_claimable_dicts_raw: List[dict],
    stakes_raw: List[float],
    prices_tao_per_alpha: List[float],
    actual_interval_seconds: float,
    no_filters: bool = False,
) -> Tuple[float, float, float, int]:
    """
    Calculate APY for a hotkey from RootClaimable data.
    
    This function contains only calculation logic - all data must be provided as arguments.
    No async operations or external queries are performed.
    
    Args:
        events: List of event dicts with keys: block, netuid, period
        baseline_claimable_alpha: Baseline claimable rates {netuid: α/TAO}
        root_claimable_dicts_raw: List of claimable dicts (one per event), or -1 for failed queries
        stakes_raw: List of stake values in RAO (one per event), or -1.0 for failed queries
        prices_tao_per_alpha: List of prices in TAO/α (one per event), or -1.0 for failed queries
        actual_interval_seconds: Actual interval duration in seconds (for APY calculation)
        no_filters: If False, filter out stakes < 4000 TAO
    
    Returns:
        Tuple[float, float, int]: (apy_percent, total_dividends_tao, skipped_count)
    """
    RAO_PER_TAO = 10**9
    
    yield_product = 1.0
    total_divs_tao = 0.0
    skipped = 0

    prev_claimable_alpha_by_netuid: Dict[int, float] = dict(baseline_claimable_alpha)

    for idx, event in enumerate(events):
        netuid = event["netuid"]

        # Claimable rate (α/TAO)
        claimable_dict_raw = root_claimable_dicts_raw[idx]
        if claimable_dict_raw == -1:
            skipped += 1
            continue
        claimable_alpha = normalize_claimable_alpha(claimable_dict_raw)

        prev_alpha_per_tao = float(prev_claimable_alpha_by_netuid.get(netuid, 0.0))
        curr_alpha_per_tao = float(claimable_alpha.get(netuid, prev_alpha_per_tao))

        # Δα/TAO (clamp negatives to 0)
        delta_alpha_per_tao = curr_alpha_per_tao - prev_alpha_per_tao
        if delta_alpha_per_tao < 0:
            delta_alpha_per_tao = 0.0

        # Update baseline for next observation
        prev_claimable_alpha_by_netuid[netuid] = curr_alpha_per_tao

        # Stake (normalize to tao)
        stake_raw = stakes_raw[idx]
        if stake_raw <= 0:
            skipped += 1
            continue
        stake_rao = float(stake_raw)
        stake_tao = stake_rao / RAO_PER_TAO

        if (not no_filters) and (stake_tao < 4000):
            skipped += 1
            continue

        price_tao_per_alpha = float(prices_tao_per_alpha[idx])
        if price_tao_per_alpha <= 0:
            skipped += 1
            continue

        # Per-epoch values
        epoch_yield_ratio = delta_alpha_per_tao * price_tao_per_alpha  # dimensionless
        epoch_divs_tao    = (delta_alpha_per_tao * stake_tao) * price_tao_per_alpha

        total_divs_tao += epoch_divs_tao
        yield_product *= (1.0 + epoch_yield_ratio)

    # Period yield & APY
    period_yield = yield_product - 1.0
    compounding_periods = INTERVAL_SECONDS["year"] / actual_interval_seconds
    apy = calculate_apy(period_yield, compounding_periods)

    return apy, float(total_divs_tao), period_yield, skipped


def calculate_hotkey_root_apy_from_baskets(
    observations: List[Dict],
    no_filters: bool = False,
) -> Tuple[float, float, float, int]:
    """
    Calculate root APY from growth of a validator's basket deposits (runtime spec 441).

    Spec 441 ("Root Reborn") retired the RootClaimable rate that calculate_hotkey_root_apy
    reads, so on current blocks that function measures a permanently flat series and returns 0.
    Root dividends now accrue into an escrowed basket, and BasketDepositedTao accumulates the
    TAO value of each credited dividend priced when it accrued:

        epoch_yield_ratio = Δ deposited_tao / stake_tao

    This is income yield. It excludes mark-to-market moves on alpha the basket already holds,
    matching the pre-441 metric rather than total fund return. Unlike the claimable path it
    needs no price series (the chain has already valued each increment in TAO) and no per-netuid
    loop (deposits are tracked per validator, not per subnet).

    The first observation is only a baseline and contributes no yield, so callers should include
    one sample from before the window for the first in-window epoch to be counted.

    APY is annualized over the span actually measured rather than a nominal interval: during the
    transition to spec 441 a 30d window may hold only hours of basket samples, and annualizing
    those over a nominal 30d would understate APY by roughly the coverage ratio.

    Args:
        observations: sorted list of {"block": int, "deposited_rao": int, "stake_rao": float}
        no_filters: If False, filter out stakes < 4000 TAO

    Returns:
        Tuple[float, float, float, int]: (apy_percent, total_dividends_tao, period_yield, skipped)
    """
    if len(observations) < 2:
        # A single sample is only a baseline; there is no interval to measure across.
        return 0.0, 0.0, 0.0, 0

    yield_product = 1.0
    total_divs_tao = 0.0
    skipped = 0
    contributing_blocks = 0

    prev = observations[0]
    for curr in observations[1:]:
        # Deposits are cumulative and monotonic (redemptions are tracked separately in
        # BasketRedeemedTao). A decrease means the pair is not comparable, so skip it and
        # re-baseline rather than clamping a negative delta to a silent zero, which would
        # swallow the following epoch's yield.
        if curr["deposited_rao"] < prev["deposited_rao"]:
            skipped += 1
            prev = curr
            continue

        stake_rao = float(curr["stake_rao"])
        stake_tao = stake_rao / RAO_PER_TAO
        # A non-finite stake would pass both comparisons below (nan <= 0 and nan < floor are
        # both False) and poison the yield with nan, so reject it explicitly.
        if not math.isfinite(stake_rao) or stake_rao <= 0 or ((not no_filters) and stake_tao < ROOT_MIN_STAKE_TAO):
            skipped += 1
            prev = curr
            continue

        delta_tao = float(curr["deposited_rao"] - prev["deposited_rao"]) / RAO_PER_TAO
        epoch_yield_ratio = delta_tao / stake_tao

        total_divs_tao += delta_tao
        yield_product *= (1.0 + epoch_yield_ratio)
        if curr["block"] > prev["block"]:
            contributing_blocks += curr["block"] - prev["block"]
        prev = curr

    period_yield = yield_product - 1.0

    # Annualizing a very short span is numerically explosive: two samples one block apart would
    # compound the pair ~2.6M times. Report no data instead (skipped is forced non-zero so
    # callers relying on coverage see the window as unusable).
    if contributing_blocks < BASKET_MIN_SPAN_BLOCKS:
        return 0.0, float(total_divs_tao), period_yield, max(skipped, 1)

    observed_seconds = contributing_blocks * BLOCK_SECONDS
    compounding_periods = INTERVAL_SECONDS["year"] / observed_seconds

    # (1 + y) ** compounding_periods overflows float range for even a modest y over a short span
    # (at the ~50 block floor the exponent is ~52,560, which overflows around y = 1.4%). That
    # raises OverflowError or yields a meaningless ~1e229%, so treat it as unmeasurable instead.
    if period_yield > -1.0 and math.log1p(period_yield) * compounding_periods > 700:
        return 0.0, float(total_divs_tao), period_yield, max(skipped, 1)

    apy = calculate_apy(period_yield, compounding_periods)

    return apy, float(total_divs_tao), period_yield, skipped


async def retrieve_and_calculate_hotkey_root_apy(
    subtensor: AsyncSubtensor,
    hotkey: str,
    interval: str,
    block: int,
    progress,
    batch_size: int = 100,
    no_filters: bool = False,
) -> Tuple[float, float]:
    """
    Calculate APY for a hotkey from RootClaimable.

    RootClaimable is a cumulative claimable *rate* in ALPHA per staked TAO (α/TAO).
    For each (block, netuid) event:
        Δα/TAO = max(0, curr_rate - prev_rate)
        price  = get_subnet_price(netuid, block=event_block)  # tao/α (mid-price, no slippage)
        epoch_yield_ratio = Δα/TAO * price                 # dimensionless
        epoch_divs_tao    = (Δα/TAO * stake_tao) * price   # tao

    Returns:
        (apy_percent, total_dividends_tao)
    """

    # ------------------------ utils ------------------------
    def log(msg: str):
        try:
            progress.console.print(msg)
        except Exception:
            print(msg)

    # ------------------------ interval & events ------------------------
    interval_seconds = INTERVAL_SECONDS[interval]
    actual_interval_blocks = int(interval_seconds / BLOCK_SECONDS)
    actual_interval_seconds = actual_interval_blocks * BLOCK_SECONDS
    start_block = block - actual_interval_blocks

    # ------------------------ Baskets (spec 441) take precedence ------------------------
    # RootClaimable is frozen from spec 441 onwards, so prefer the basket series whenever the
    # runtime exposes it. Capability is read from metadata rather than inferred from a failed
    # query, so a transient RPC error cannot silently downgrade us to the claimable path (which
    # on a post-441 chain can only report 0%).
    # Check capability BEFORE the claimable prep: `get_all_subnets_info` is only needed for the
    # legacy per-subnet epoch-boundary events, and on a post-441 mainnet it currently fails
    # inside bittensor (`Swap.AlphaSqrtPrice` storage was renamed; Balance decoded from a tuple).
    # Doing the check first also avoids ~128 subnet decodes we don't use when basket is live.
    baseline_block = max(start_block - 1, 0)
    basket_live = await basket_supported(subtensor)

    if basket_live:
        # Deposits are cumulative and per-validator, so income between two samples is captured
        # in full by their delta. That means a fixed cadence loses nothing versus sampling every
        # epoch boundary, and avoids tens of thousands of historical state reads on a 30d window.
        step = max(1, math.ceil(actual_interval_blocks / BASKET_MAX_SAMPLES))
        sample_blocks = list(range(start_block, block + 1, step))
        if not sample_blocks or sample_blocks[-1] != block:
            sample_blocks.append(block)
        sample_blocks = [baseline_block] + [b for b in sample_blocks if b > baseline_block]

        basketTask = progress.add_task(
            f"[cyan]Fetching basket deposits for {hotkey}",
            total=len(sample_blocks)
        )

        # Sentinel rather than None on failure: a dropped sample must be counted, because
        # computing coverage over survivors alone would report 100% for a window where nearly
        # every fetch failed, and publish a confident APY from whatever happened to succeed.
        async def get_basket_sample(at_block: int):
            try:
                deposited = await get_basket_deposited_tao(subtensor, hotkey, at_block)
                stake = await query_subtensor(subtensor, "TotalHotkeyAlpha", at_block, [hotkey, 0])
                if deposited is None or stake is None:
                    return -1
                return {"block": at_block, "deposited_rao": deposited, "stake_rao": float(stake)}
            except Exception:
                return -1
            finally:
                progress.update(basketTask, advance=1)

        observations: List[Dict] = []
        failed = 0
        for i in range(0, len(sample_blocks), batch_size):
            batch = sample_blocks[i : i + batch_size]
            batch_results = await asyncio.gather(
                *[get_basket_sample(b) for b in batch], return_exceptions=True
            )
            for r in batch_results:
                if isinstance(r, dict):
                    observations.append(r)
                else:
                    failed += 1

        observations.sort(key=lambda o: o["block"])

        apy, total_dividends_tao, period_yield, skipped = calculate_hotkey_root_apy_from_baskets(
            observations=observations,
            no_filters=no_filters,
        )

        # Coverage is measured against the samples we ASKED for, counting both fetch failures
        # and pairs the calculation rejected.
        expected_pairs = max(len(sample_blocks) - 1, 0)
        good_pairs = expected_pairs - failed - skipped
        if expected_pairs == 0 or good_pairs <= 0:
            log("[yellow]No usable basket samples in window — APY unavailable.[/yellow]")
        elif good_pairs < REQUIRED_BLOCKS_RATIO * expected_pairs:
            log(
                f"[yellow]Coverage {good_pairs}/{expected_pairs} "
                f"({good_pairs/expected_pairs*100:.2f}%) < required "
                f"{REQUIRED_BLOCKS_RATIO*100:.2f}% "
                f"({failed} fetch failures, {skipped} skipped) — APY may be inaccurate.[/yellow]"
            )

        # The measured span can be shorter than the nominal interval while history backfills, so
        # label the period figures with what was actually observed rather than the interval name.
        measured_blocks = (observations[-1]["block"] - observations[0]["block"]) if len(observations) > 1 else 0
        log(f"Measured span: {measured_blocks} blocks ({measured_blocks * BLOCK_SECONDS / 3600:.2f}h), "
            f"{len(observations)} samples")
        log(f"Basket yield over measured span: {period_yield * 100:.6f}%")
        log(f"Basket dividends over measured span: {total_dividends_tao:.12f} tao")
        log(f"APY: {apy:.6f}%")

        return apy, float(total_dividends_tao)

    # ------------------------ Claimable-path prep (pre-441 only) ------------------------
    # Legacy per-subnet epoch boundaries — only built when basket storage is absent, since on
    # post-441 chains this can be tens of thousands of state reads that we do not use.
    subnets = await subtensor.get_all_subnets_info(block=block)

    events: List[Dict] = []
    for subnet in subnets:
        netuid = subnet.netuid
        tempo = subnet.tempo
        period = tempo + 1
        last_epoch_block = block - subnet.blocks_since_epoch
        epoch = last_epoch_block
        while epoch >= start_block:
            events.append({"block": epoch, "netuid": netuid, "period": period})
            epoch -= period

    events.sort(key=lambda x: (x["block"], x["netuid"]))

    # ------------------------ RootClaimable (α/TAO), with baseline ------------------------
    rootClaimableTask = progress.add_task(
        f"[cyan]Fetching root claimable entries for {hotkey}",
        total=len(events) + 1
    )

    async def get_root_claimable_with_progress(at_block: int) -> dict:
        res = await get_root_claimable_entries(subtensor, hotkey, at_block)
        progress.update(rootClaimableTask, advance=1)
        return res if isinstance(res, dict) else -1

    raw_baseline = await get_root_claimable_with_progress(baseline_block)
    baseline_claimable_alpha = (
        normalize_claimable_alpha(raw_baseline) if raw_baseline != -1 else {}
    )

    root_claimable_tasks = [
        (lambda event=event: get_root_claimable_with_progress(event["block"]))
        for event in events
    ]
    root_claimable_dicts_raw: List[dict] = []
    for i in range(0, len(root_claimable_tasks), batch_size):
        batch = root_claimable_tasks[i : i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        root_claimable_dicts_raw.extend([r if not isinstance(r, Exception) else -1 for r in batch_results])

    # ------------------------ Stakes (unit inference) ------------------------
    stakeTask = progress.add_task(f"[cyan]Fetching stakes for {hotkey}", total=len(events))

    async def query_stake_with_progress(at_block: int, params: List) -> float:
        try:
            result = await subtensor.query_subtensor("TotalHotkeyAlpha", block=at_block, params=params)
            return float(unwrap_scalar(result.value))  # may be tao or rao; convert later
        except Exception:
            return -1.0
        finally:
            progress.update(stakeTask, advance=1)

    stake_tasks = [
        (lambda event=event: query_stake_with_progress(event["block"], [hotkey, 0]))
        for event in events
    ]

    stakes_raw: List[float] = []
    for i in range(0, len(stake_tasks), batch_size):
        batch = stake_tasks[i : i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        stakes_raw.extend([(-1.0 if isinstance(r, Exception) else float(r)) for r in batch_results])

    # ------------------------ α→tao mid-price via get_subnet_price ------------------------
    # Note: use price *at the event block* if supported; otherwise fallback to head.
    priceTask = progress.add_task(
        f"[cyan]Fetching α→tao prices",
        total=len(events)
    )
    
    async def get_price_with_progress(at_block: int, netuid: int) -> float:
        try:
            # Use the built-in get_subnet_price method which calls SwapRuntimeApi.current_alpha_price
            price_balance = await subtensor.get_subnet_price(netuid=netuid, block=at_block)
            if price_balance is None:
                return -1.0
            # Convert Balance to TAO (price is already in TAO/α)
            price_tao = float(price_balance.tao)
            if price_tao <= 0:
                return -1.0
            return price_tao
        except Exception:
            return -1.0
        finally:
            progress.update(priceTask, advance=1)

    price_tasks = [
        (lambda event=event: get_price_with_progress(event["block"], event["netuid"]))
        for event in events
    ]

    prices_tao_per_alpha: List[float] = []
    for i in range(0, len(price_tasks), batch_size):
        batch = price_tasks[i : i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        prices_tao_per_alpha.extend([(-1.0 if isinstance(r, Exception) else float(r)) for r in batch_results])

    # ------------------------ Calculation ------------------------
    apy, total_dividends_tao, period_yield, skipped = calculate_hotkey_root_apy(
        events=events,
        baseline_claimable_alpha=baseline_claimable_alpha,
        root_claimable_dicts_raw=root_claimable_dicts_raw,
        stakes_raw=stakes_raw,
        prices_tao_per_alpha=prices_tao_per_alpha,
        actual_interval_seconds=actual_interval_seconds,
        no_filters=no_filters,
    )

    # Coverage note
    if len(events) - skipped < REQUIRED_BLOCKS_RATIO * len(events):
        log(
            f"[yellow]Coverage {len(events) - skipped}/{len(events)} "
            f"({(len(events) - skipped)/len(events)*100:.2f}%) < required "
            f"{REQUIRED_BLOCKS_RATIO*100:.2f}% — APY may be inaccurate.[/yellow]"
        )

    # Summary output
    log(f"Total {interval} yield: {period_yield * 100:.6f}%")
    log(f"Total {interval} dividends (tao):   {total_dividends_tao:.12f} tao")
    log(f"APY: {apy:.6f}%")

    return apy, float(total_dividends_tao)
