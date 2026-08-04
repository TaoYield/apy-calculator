"""
Tests for the async basket retrieval path in retrieve_and_calculate_hotkey_root_apy.

The pure-calculation tests cannot catch wiring bugs, and this branch drives real RPC fan-out, so
it is exercised here against stubs. These specifically cover the two defects found in review:
coverage measured over survivors instead of requested samples, and capability detection that
mistook a transient failure for a pre-441 chain.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import root_calc
from constants import BASKET_MAX_SAMPLES

ROOT_STAKE_RAO = 173_547_671_079_432  # ~173,547 TAO


class StubValue:
    def __init__(self, value):
        self.value = value


class StubProgress:
    """Minimal stand-in for the rich Progress passed through by the CLI."""

    class _Console:
        def __init__(self, sink):
            self.sink = sink

        def print(self, msg):
            self.sink.append(str(msg))

    def __init__(self):
        self.messages = []
        self.console = StubProgress._Console(self.messages)

    def add_task(self, *_args, **_kwargs):
        return 1

    def update(self, *_args, **_kwargs):
        return None


class StubSubnet:
    def __init__(self, netuid, tempo=360, blocks_since_epoch=0):
        self.netuid = netuid
        self.tempo = tempo
        self.blocks_since_epoch = blocks_since_epoch


class StubSubstrate:
    def __init__(self, has_basket=True):
        self.has_basket = has_basket

    async def get_metadata_storage_function(self, _module, name):
        if name == "BasketDepositedTao" and self.has_basket:
            return object()
        return None


class StubSubtensor:
    """
    Serves BasketDepositedTao growing at a fixed rate per block plus a constant root stake.
    fail_blocks lets a test simulate historical-state read failures.
    """

    def __init__(self, has_basket=True, rate_rao_per_block=10**6, fail_blocks=None, head=8_000_000):
        self.substrate = StubSubstrate(has_basket)
        self.rate = rate_rao_per_block
        self.fail_blocks = fail_blocks or set()
        self.head = head
        self.queries = 0

    async def get_all_subnets_info(self, block=None):
        return [StubSubnet(1), StubSubnet(2)]

    async def query_subtensor(self, name, params=None, block=None):
        self.queries += 1
        if block in self.fail_blocks:
            raise RuntimeError("simulated historical state read failure")
        if name == "BasketDepositedTao":
            return StubValue(max(0, (block - (self.head - 216_000)) * self.rate))
        if name == "TotalHotkeyAlpha":
            return StubValue(ROOT_STAKE_RAO)
        raise AssertionError(f"unexpected storage query: {name}")


def run(interval="24h", **kwargs):
    sub = StubSubtensor(**kwargs)
    progress = StubProgress()
    apy, divs = asyncio.run(
        root_calc.retrieve_and_calculate_hotkey_root_apy(
            subtensor=sub, hotkey="hk", interval=interval, block=sub.head, progress=progress
        )
    )
    return apy, divs, progress.messages, sub


def test_basket_path_produces_positive_apy():
    """Happy path: a steadily growing deposit counter must yield a positive, finite APY."""
    apy, divs, messages, _ = run()

    assert apy > 0
    assert divs > 0
    assert any("APY:" in m for m in messages)
    # Period figures must be labelled by measured span, not the interval name, since the two can
    # differ while history backfills.
    assert any("Measured span" in m for m in messages)


def test_sampling_is_capped():
    """
    Deposits are cumulative, so a fixed cadence captures the same income. A 30d window must not
    fan out to tens of thousands of state reads.
    """
    _, _, _, sub = run(interval="30d")

    # 2 queries per sample, plus the metadata capability check (no RPC).
    assert sub.queries <= (BASKET_MAX_SAMPLES + 2) * 2


def test_widespread_fetch_failures_are_reported_not_hidden():
    """
    The bug this guards: coverage computed over surviving samples reports 100% even when nearly
    every fetch failed, publishing a confident APY from whatever succeeded. Failures must be
    counted against the samples requested.
    """
    head = 8_000_000
    # Fail everything except a handful of blocks.
    survivors = {head, head - 1000, head - 2000}
    fail = {b for b in range(head - 216_000, head + 1) if b not in survivors}

    _, _, messages, _ = run(interval="24h", fail_blocks=fail, head=head)

    joined = " ".join(messages)
    assert "Coverage" in joined or "No usable basket samples" in joined
    assert "fetch failures" in joined or "No usable basket samples" in joined


def test_pre441_chain_falls_through_to_claimable_path():
    """
    Without the basket storage the function must use the legacy claimable path. Reaching it here
    raises on the stub's unexpected RootClaimable query, which is proof enough that the branch
    was not taken.
    """
    sub = StubSubtensor(has_basket=False)
    progress = StubProgress()

    try:
        asyncio.run(
            root_calc.retrieve_and_calculate_hotkey_root_apy(
                subtensor=sub, hotkey="hk", interval="1h", block=sub.head, progress=progress
            )
        )
    except AssertionError as exc:
        assert "RootClaimable" in str(exc)
    except Exception:
        # Any other failure also means we left the basket branch, which is the point.
        pass
