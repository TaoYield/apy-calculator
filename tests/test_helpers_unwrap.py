"""
Regression coverage for `unwrap_scalar`.

Discovered live: on mainnet at runtime spec 443, async-substrate-interface returns scalar
storage values (u64, u128, I96F32) wrapped in a 1-element list. The basket root APY path called
`int(value)` and `float(value)` directly on the query result and blew up with TypeError, which
the unit-test suite never saw because every test stubbed the storage layer with plain numbers.
Pin the unwrap contract so both encodings stay supported.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from helpers import unwrap_scalar


def test_passes_plain_scalar_through():
    assert unwrap_scalar(42) == 42
    assert unwrap_scalar(0) == 0
    assert unwrap_scalar(1.5) == 1.5


def test_unwraps_single_element_list_and_tuple():
    # Observed live: TotalHotkeyAlpha -> [171545078055037], BasketDepositedTao -> [8621903000].
    assert unwrap_scalar([171545078055037]) == 171545078055037
    assert unwrap_scalar((8621903000,)) == 8621903000


def test_none_passes_through():
    assert unwrap_scalar(None) is None


def test_multi_element_container_is_not_unwrapped():
    # Composite decodings (BTreeMaps, tuple-of-values) must not be flattened.
    assert unwrap_scalar([1, 2]) == [1, 2]
    assert unwrap_scalar((1, 2, 3)) == (1, 2, 3)


def test_empty_container_passes_through():
    # Not a "scalar wrapped in a list"; leave the caller to decide.
    assert unwrap_scalar([]) == []
    assert unwrap_scalar(()) == ()
