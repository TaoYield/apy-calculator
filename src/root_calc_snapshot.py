"""
Snapshot-based root APY.

Answers a different question than root_calc.py's sliding-window methods:

  Q: "At the current per-epoch payout rate on each subnet, what would this
     hotkey earn in a year?"
  A: sum over subnets of (last_epoch_dividend * epochs_per_year) / root_stake

The sliding-window basket path (`calculate_hotkey_root_apy_from_baskets`)
answers a very literal question — "if the next year plays out exactly like
the last N minutes, how much per year?" — and inherits the round-robin
flush jitter as a result. Basket credits enter a validator's queue when
each subnet fires an epoch, then a per-block round-robin drain flushes the
queue as one lumped `BasketDeposited` event; whether a given large flush
lands inside or outside a 72-minute window is essentially random, moving
the reported 1h APY 1-2pp block-to-block for the same underlying rate.

The snapshot approach bypasses that entirely. It reads
`RootAlphaDividendsPerSubnet[netuid, hotkey]` — a storage subtensor writes
atomically the moment each subnet's epoch fires (see
`~/subtensor/pallets/subtensor/src/coinbase/run_coinbase.rs:900+`,
`mutate` on this storage), untouched by the round-robin flush machinery.
That value is the hotkey's last-epoch dividend on that subnet, denominated
in the subnet's alpha. Annualize by tempo, price into TAO, sum, divide by
root stake — you get an APY that only changes when something real changes
(subnet emission, tempo, hotkey weights, stake share).

Tradeoff to know: this is a *rate projection*, not a *realised yield*. A
subnet that just cleared its epoch counter has value 0 until it next
fires; on the very first call after enact you'll undercount for the first
tempo. In steady state (any moment >tempo after enact) every active
subnet has a fresh non-zero entry and the sum stabilises.
"""
import asyncio
import math
from typing import Tuple, List

from constants import BLOCK_SECONDS, INTERVAL_SECONDS, RAO_PER_TAO, ROOT_MIN_STAKE_TAO
from bittensor import AsyncSubtensor
from helpers import query_subtensor, unwrap_scalar


YEAR_BLOCKS = INTERVAL_SECONDS["year"] / BLOCK_SECONDS  # 2_628_000


async def calculate_hotkey_root_apy_snapshot(
    subtensor: AsyncSubtensor,
    hotkey: str,
    block: int,
    no_filters: bool = False,
    verbose: bool = False,
) -> Tuple[float, float, List[dict]]:
    """
    Compute snapshot APY for a hotkey at a given block.

    Returns:
        (apy_percent, total_annual_tao, per_subnet_contributions)
        per_subnet_contributions: list of {netuid, per_epoch_alpha, price, tempo, annual_tao}
    """
    root_stake_rao = await query_subtensor(subtensor, "TotalHotkeyAlpha", block, [hotkey, 0])
    root_stake_tao = (float(root_stake_rao) if root_stake_rao else 0.0) / RAO_PER_TAO
    if root_stake_tao <= 0:
        return 0.0, 0.0, []
    if (not no_filters) and root_stake_tao < ROOT_MIN_STAKE_TAO:
        return 0.0, 0.0, []

    # We deliberately don't use `subtensor.get_all_subnets_info(block=block)` — it decodes
    # subnet burn as tuple on post-441 chains and blows up inside bittensor's Balance.from_rao.
    # Enumerate netuids from TotalNetworks (single u16) + probe each with Tempo instead.
    total_networks_raw = await query_subtensor(subtensor, "TotalNetworks", block, [])
    total_networks = int(total_networks_raw) if total_networks_raw else 0
    if verbose or total_networks == 0:
        print(f"  DEBUG: TotalNetworks = {total_networks}")
    netuids: List[int] = [nu for nu in range(1, total_networks + 1)]

    block_hash = await subtensor.substrate.get_block_hash(block)

    # Diagnostic: iterate the whole DoubleMap globally to see whether it has ANY entries at
    # this block. If empty → storage is cleared / stale at this block for everyone (not just
    # our hotkey). If populated → key encoding issue on the per-subnet path below.
    try:
        map_iter = await subtensor.substrate.query_map(
            module="SubtensorModule", storage_function="RootAlphaDividendsPerSubnet",
            block_hash=block_hash,
        )
        sample_entries = []
        count = 0
        our_matches = 0
        async for k, v in map_iter:
            count += 1
            key_str = str(getattr(k, "value", k))
            val = unwrap_scalar(getattr(v, "value", v))
            if hotkey in key_str:
                our_matches += 1
                if len(sample_entries) < 3:
                    sample_entries.append((key_str, val))
            if count >= 500:
                break
        print(f"  DEBUG: RootAlphaDividendsPerSubnet global iter — first {count} entries, matches for tao5: {our_matches}")
        for k, v in sample_entries:
            print(f"    key={k}  value={v}")
    except Exception as e:
        print(f"  DEBUG: global iter failed: {type(e).__name__}: {e}")

    async def one_subnet(netuid: int):
        # Tempo — StorageMap<NetUid, u16>. Direct query is safest across bittensor versions.
        try:
            tempo_res = await subtensor.substrate.query(
                module="SubtensorModule", storage_function="Tempo",
                params=[netuid], block_hash=block_hash,
            )
            tempo = int(unwrap_scalar(getattr(tempo_res, "value", 0)) or 0)
        except Exception as e:
            if netuid <= 3:
                print(f"  DEBUG SN{netuid}: Tempo query failed: {type(e).__name__}: {e}")
            return None
        if tempo == 0:
            return None

        # Last epoch's alpha dividend, StorageDoubleMap<NetUid, AccountId, AlphaBalance>.
        # Cleared at start of the subnet's coinbase block, repopulated during — so between
        # epoch fires this holds the last known per-hotkey payout for that subnet.
        try:
            div_res = await subtensor.substrate.query(
                module="SubtensorModule", storage_function="RootAlphaDividendsPerSubnet",
                params=[netuid, hotkey], block_hash=block_hash,
            )
            raw = unwrap_scalar(getattr(div_res, "value", 0)) or 0
            per_epoch_alpha_rao = int(raw)
        except Exception as e:
            if netuid <= 3:
                print(f"  DEBUG SN{netuid}: RootAlphaDividendsPerSubnet query failed: {type(e).__name__}: {e}")
            return None
        if verbose and netuid <= 5:
            print(f"  DEBUG SN{netuid}: tempo={tempo}, per_epoch_alpha_rao={per_epoch_alpha_rao}")
        if per_epoch_alpha_rao == 0:
            return None

        # Price = SubnetTAO / SubnetAlphaIn (pool reserves, both in rao). Direct storage
        # read, doesn't depend on runtime `Swap.AlphaSqrtPrice` which is missing on spec 443
        # and causes `get_subnet_price` to fail silently.
        try:
            tao_res = await subtensor.substrate.query(
                module="SubtensorModule", storage_function="SubnetTAO",
                params=[netuid], block_hash=block_hash,
            )
            alpha_res = await subtensor.substrate.query(
                module="SubtensorModule", storage_function="SubnetAlphaIn",
                params=[netuid], block_hash=block_hash,
            )
            tao_in_rao = int(unwrap_scalar(getattr(tao_res, "value", 0)) or 0)
            alpha_in_rao = int(unwrap_scalar(getattr(alpha_res, "value", 0)) or 0)
        except Exception as e:
            if verbose and netuid <= 5:
                print(f"  DEBUG SN{netuid}: pool reserves query failed: {type(e).__name__}: {e}")
            return None
        if alpha_in_rao <= 0 or tao_in_rao <= 0:
            return None
        # Both values are in their raw units (rao for TAO, rao for alpha). Ratio gives
        # TAO-per-alpha as a plain float — matches subnet_markers.Price / 1e9 in btguru.
        price_tao_per_alpha = tao_in_rao / alpha_in_rao

        per_epoch_tao = (per_epoch_alpha_rao / RAO_PER_TAO) * price_tao_per_alpha
        epochs_per_year = YEAR_BLOCKS / tempo
        annual_tao = per_epoch_tao * epochs_per_year

        return {
            "netuid": netuid,
            "tempo": tempo,
            "per_epoch_alpha": per_epoch_alpha_rao / RAO_PER_TAO,
            "price_tao_per_alpha": price_tao_per_alpha,
            "per_epoch_tao": per_epoch_tao,
            "epochs_per_year": epochs_per_year,
            "annual_tao": annual_tao,
        }

    # Kick off all per-subnet reads concurrently.
    results = await asyncio.gather(*[one_subnet(nu) for nu in netuids])
    contributions = [r for r in results if r is not None]

    # Always-visible sanity signal: if these are empty, the storage query returned nothing
    # or the (netuid, hotkey) key order is wrong. Verbose adds the top-N table below.
    zero_dividend = sum(1 for r in results if r is None)
    print(
        f"  DEBUG: netuids probed={len(netuids)}, "
        f"with dividend contribution={len(contributions)}, "
        f"skipped (0 or no price)={zero_dividend}"
    )

    total_annual_tao = sum(c["annual_tao"] for c in contributions)
    apy = 100.0 * total_annual_tao / root_stake_tao

    if verbose:
        contributions.sort(key=lambda c: -c["annual_tao"])
        print(f"  root_stake: {root_stake_tao:.3f} TAO")
        print(f"  active subnets contributing: {len(contributions)}")
        print(f"  total annual TAO: {total_annual_tao:.4f}")
        print("  top 10 by annual TAO:")
        for c in contributions[:10]:
            print(
                f"    SN{c['netuid']:>3}  "
                f"per_epoch={c['per_epoch_alpha']:>10.4f}α  "
                f"× price={c['price_tao_per_alpha']:>8.6f}  "
                f"× epochs/yr={c['epochs_per_year']:>7.0f}  "
                f"= {c['annual_tao']:>7.4f} TAO/yr"
            )

    return apy, total_annual_tao, contributions
