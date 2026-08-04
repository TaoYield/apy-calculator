"""
Tests for calculate_hotkey_root_apy_from_baskets (runtime spec 441, "Root Reborn").

Spec 441 retired the RootClaimable rate, so the basket deposit series is now the source of root
yield. These pin the metric's scale and its guards against publishing a wrong number.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from root_calc import calculate_hotkey_root_apy_from_baskets, MIN_BASKET_SPAN_BLOCKS

# Root stake comfortably above the 4000 TAO floor.
ROOT_STAKE_RAO = 173_547_671_079_432  # ~173,547 TAO
ROOT_STAKE_TAO = ROOT_STAKE_RAO / 1e9


def obs(block, deposited_rao, stake_rao=ROOT_STAKE_RAO):
    return {"block": block, "deposited_rao": deposited_rao, "stake_rao": stake_rao}


def test_matches_observed_mainnet_scale():
    """
    Measured on mainnet at spec 441: deposits grew 8.621903 -> 15.797257 TAO over 2000 blocks
    (~6.67h) against ~173,547 TAO of root stake, i.e. ~5.6% APY. Pins units end to end so a
    rao/TAO mistake cannot slip through.
    """
    observations = [
        obs(8_767_941, 8_621_903_000),
        obs(8_769_941, 15_797_257_000),
    ]

    apy, total_divs_tao, period_yield, skipped = calculate_hotkey_root_apy_from_baskets(observations)

    assert skipped == 0
    assert abs(total_divs_tao - 7.175354) < 1e-5
    assert abs(period_yield - (7.175354 / ROOT_STAKE_TAO)) < 1e-9
    # ~5.6%; the old force-sold regime capped near 6.6%.
    assert 5.3 < apy < 5.9


def test_flat_deposits_give_zero_yield():
    """Income only: if no dividends were credited, yield is zero regardless of holdings value."""
    observations = [obs(100, 5_000_000_000), obs(1000, 5_000_000_000), obs(2000, 5_000_000_000)]

    apy, total_divs_tao, period_yield, skipped = calculate_hotkey_root_apy_from_baskets(observations)

    assert skipped == 0
    assert total_divs_tao == 0.0
    assert period_yield == 0.0
    assert apy == 0.0


def test_single_observation_is_baseline_only():
    """One sample is a baseline, not a measurable interval — must not imply 0% earned."""
    apy, total_divs_tao, period_yield, skipped = calculate_hotkey_root_apy_from_baskets(
        [obs(100, 5_000_000_000)]
    )

    assert (apy, total_divs_tao, period_yield, skipped) == (0.0, 0.0, 0.0, 0)


def test_decrease_is_skipped_and_rebaselined():
    """
    Deposits are monotonic; a decrease means the pair is not comparable. It must be skipped and
    re-baselined so the following epoch's yield is still credited, rather than clamped to zero.
    """
    observations = [
        obs(1000, 10_000_000_000),
        obs(2000, 4_000_000_000),  # anomaly
        obs(3000, 5_000_000_000),  # +1 TAO from the re-baselined 4
    ]

    _, total_divs_tao, _, skipped = calculate_hotkey_root_apy_from_baskets(observations)

    assert skipped == 1
    assert abs(total_divs_tao - 1.0) < 1e-9


def test_short_span_is_not_annualized():
    """
    Two samples one block apart would compound ~2.6M times and publish an absurd APY. Must
    report no data instead, with skipped forced non-zero so callers see the window as unusable.
    """
    observations = [obs(8_000_000, 1_000_000_000), obs(8_000_001, 2_000_000_000)]

    apy, _, _, skipped = calculate_hotkey_root_apy_from_baskets(observations)

    assert apy == 0.0
    assert skipped >= 1


def test_annualizes_over_observed_span():
    """
    APY must be annualized over the span actually measured, so the same per-block rate yields the
    same APY whether the window holds few or many samples. Otherwise a 30d window with only hours
    of basket coverage during the transition would be understated by the coverage ratio.
    """
    def build(samples):
        return [obs(8_000_000 + i * 1000, i * 1_000_000_000) for i in range(samples)]

    short_apy, _, _, _ = calculate_hotkey_root_apy_from_baskets(build(3))
    long_apy, _, _, _ = calculate_hotkey_root_apy_from_baskets(build(30))

    assert abs(short_apy - long_apy) < short_apy * 0.02


def test_min_stake_filter_and_no_filters_bypass():
    """Below 4000 TAO the sample is noise and filtered, unless no_filters is set."""
    tiny_stake_rao = 3999 * 10**9
    observations = [
        obs(1000, 1_000_000_000, tiny_stake_rao),
        obs(2000, 2_000_000_000, tiny_stake_rao),
    ]

    _, _, _, skipped = calculate_hotkey_root_apy_from_baskets(observations)
    assert skipped == 1

    _, total_divs_tao, _, skipped_no_filters = calculate_hotkey_root_apy_from_baskets(
        observations, no_filters=True
    )
    assert skipped_no_filters == 0
    assert abs(total_divs_tao - 1.0) < 1e-9


def test_zero_stake_is_skipped():
    """Without stake there is nothing to normalize per unit, so the pair is unusable."""
    observations = [obs(1000, 1_000_000_000, 0), obs(2000, 2_000_000_000, 0)]

    apy, _, _, skipped = calculate_hotkey_root_apy_from_baskets(observations)

    assert skipped == 2 or apy == 0.0
    assert apy == 0.0


def test_min_span_constant_is_sane():
    """Guard the constant itself: it must be long enough to reject adjacent-block noise."""
    assert MIN_BASKET_SPAN_BLOCKS >= 10
