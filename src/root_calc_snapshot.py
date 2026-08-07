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
from helpers import query_subtensor


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

    # We deliberately don't use `subtensor.get_all_subnets_info(block=block)` here — it
    # decodes fields (e.g. `burn`) that come back as tuples on post-441 mainnet and blows up
    # inside bittensor's Balance.from_rao. Same class of bug we already worked around in the
    # basket path. Query the primitives directly instead.
    #
    # 1) Enumerate active netuids from NetworksAdded (SubtensorModule).
    # 2) For each, read Tempo(netuid) — one chain hit per subnet, cheap in parallel.
    netuids_map = await subtensor.substrate.query_map(
        module="SubtensorModule", storage_function="NetworksAdded",
        block_hash=await subtensor.substrate.get_block_hash(block),
    )
    netuids: List[int] = []
    async for netuid, added in netuids_map:
        nu = getattr(netuid, "value", netuid)
        try:
            nu = int(nu)
        except (TypeError, ValueError):
            continue
        if nu == 0:
            continue  # root itself isn't a source of root dividends
        netuids.append(nu)

    async def one_subnet(netuid: int):
        tempo_raw = await query_subtensor(subtensor, "Tempo", block, [netuid])
        if not tempo_raw:
            return None
        tempo = int(tempo_raw)
        if tempo == 0:
            return None

        # Last epoch's alpha dividend for this (netuid, hotkey). Written atomically at each
        # subnet epoch fire in coinbase.rs; cleared and rewritten at the next fire. Between
        # fires it holds a stable "last known payout" value.
        per_epoch_alpha_rao = await query_subtensor(
            subtensor, "RootAlphaDividendsPerSubnet", block, [netuid, hotkey]
        )
        per_epoch_alpha_rao = int(per_epoch_alpha_rao) if per_epoch_alpha_rao else 0
        if per_epoch_alpha_rao == 0:
            return None

        # Price of subnet alpha in TAO (mid, from the swap pool at this block).
        try:
            price_balance = await subtensor.get_subnet_price(netuid=netuid, block=block)
            price_tao_per_alpha = float(price_balance.tao) if price_balance is not None else 0.0
        except Exception:
            price_tao_per_alpha = 0.0
        if price_tao_per_alpha <= 0:
            return None

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
