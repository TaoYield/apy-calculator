"""
Test for calculate_hotkey_root_apy function using real data from calc_args_20251116_112601.json.

This test uses actual data captured from a production run to ensure the calculation
logic produces consistent results.
"""
import json
from pathlib import Path
import pytest

from src.root_calc import calculate_hotkey_root_apy


@pytest.mark.unit
def test_calculate_hotkey_root_apy_tao5():
    """Test calculate_hotkey_root_apy with real data from calc_args_root_20251116_112601.json"""
    # Load test data from JSON file
    json_file = Path(__file__).parent / "data" / "calc_args_root_20251116_112601.json"
    
    if not json_file.exists():
        pytest.skip(f"Test data file not found: {json_file}")
    
    with open(json_file, "r") as f:
        test_data = json.load(f)
    
    # Extract inputs and ensure correct types
    events = test_data["events"]
    
    # Convert baseline_claimable_alpha string keys to int (JSON stores keys as strings)
    baseline_claimable_alpha = {
        int(k): float(v) for k, v in test_data["baseline_claimable_alpha"].items()
    }
    
    # root_claimable_dicts_raw may have string keys, convert them
    root_claimable_dicts_raw = []
    for item in test_data["root_claimable_dicts_raw"]:
        if item == -1:
            root_claimable_dicts_raw.append(-1)
        elif isinstance(item, dict):
            # Convert string keys to int
            converted = {int(k): float(v) for k, v in item.items()}
            root_claimable_dicts_raw.append(converted)
        else:
            root_claimable_dicts_raw.append(item)
    
    stakes_raw = [float(x) for x in test_data["stakes_raw"]]
    prices_tao_per_alpha = [float(x) for x in test_data["prices_tao_per_alpha"]]
    actual_interval_seconds = float(test_data["actual_interval_seconds"])
    no_filters = bool(test_data["no_filters"])
    
    # Extract expected results
    expected_results = test_data["results"]
    expected_apy = expected_results["apy"]
    expected_total_dividends_tao = expected_results["total_dividends_tao"]
    expected_period_yield = expected_results["period_yield"]
    expected_skipped = expected_results["skipped"]
    
    # Run calculation
    apy, total_dividends_tao, period_yield, skipped = calculate_hotkey_root_apy(
        events=events,
        baseline_claimable_alpha=baseline_claimable_alpha,
        root_claimable_dicts_raw=root_claimable_dicts_raw,
        stakes_raw=stakes_raw,
        prices_tao_per_alpha=prices_tao_per_alpha,
        actual_interval_seconds=actual_interval_seconds,
        no_filters=no_filters,
    )
    
    # Assert results match expected values
    # Use epsilon for floating point comparison (0.000001 precision)
    assert abs(apy - expected_apy) < 0.000001, (
        f"APY mismatch: expected {expected_apy}, got {apy}"
    )
    
    assert abs(total_dividends_tao - expected_total_dividends_tao) < 0.000001, (
        f"Total dividends TAO mismatch: expected {expected_total_dividends_tao}, got {total_dividends_tao}"
    )
    
    assert abs(period_yield - expected_period_yield) < 0.000001, (
        f"Period yield mismatch: expected {expected_period_yield}, got {period_yield}"
    )
    
    assert skipped == expected_skipped, (
        f"Skipped count mismatch: expected {expected_skipped}, got {skipped}"
    )
