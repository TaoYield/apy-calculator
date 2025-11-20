"""
Test for calculate_hotkey_subnet_apy function using real data from calc_args_subnet_20251117_103000.json.

This test uses actual data captured from a production run to ensure the calculation
logic produces consistent results.
"""
import json
from pathlib import Path
import pytest

from src.subnet_calc import calculate_hotkey_subnet_apy


@pytest.mark.unit
def test_calculate_hotkey_subnet_apy_tao5():
    """Test calculate_hotkey_subnet_apy with real data from calc_args_subnet_20251117_103000.json"""
    # Load test data from JSON file
    json_file = Path(__file__).parent / "data" / "calc_args_subnet_20251117_103000.json"
    
    if not json_file.exists():
        pytest.skip(f"Test data file not found: {json_file}")
    
    with open(json_file, "r") as f:
        test_data = json.load(f)
    
    # Extract inputs and ensure correct types
    events = test_data["events"]
    fetched_data = test_data["fetched_data"]  # This is the 'results' parameter
    baseline_root_claimable_alpha_per_tao = float(test_data["baseline_root_claimable_alpha_per_tao"])
    actual_interval_seconds = float(test_data["actual_interval_seconds"])
    no_filters = bool(test_data["no_filters"])
    
    # Convert fetched_data to ensure proper types
    # fetched_data can contain dicts or -1 for failed queries
    results = []
    for item in fetched_data:
        if item == -1:
            results.append(-1)
        elif isinstance(item, dict):
            # Ensure all numeric values are floats
            converted = {}
            for k, v in item.items():
                if isinstance(v, (int, float)):
                    converted[k] = float(v)
                else:
                    converted[k] = v
            results.append(converted)
        else:
            results.append(item)
    
    # Extract expected results
    expected_results = test_data["results"]
    expected_apy_percent = expected_results["apy_percent"]
    expected_alpha_only_divs_sum_alpha = expected_results["alpha_only_divs_sum_alpha"]
    expected_period_yield = expected_results["period_yield"]
    expected_skipped = expected_results["skipped"]
    expected_total_divs_sum_alpha = expected_results["total_divs_sum_alpha"]
    
    # Run calculation
    apy_percent, alpha_only_divs_sum_alpha, period_yield, skipped, total_divs_sum_alpha = calculate_hotkey_subnet_apy(
        events=events,
        results=results,
        baseline_root_claimable_alpha_per_tao=baseline_root_claimable_alpha_per_tao,
        actual_interval_seconds=actual_interval_seconds,
        no_filters=no_filters,
    )
    
    # Assert results match expected values
    # Use epsilon for floating point comparison (0.000001 precision)
    assert abs(apy_percent - expected_apy_percent) < 0.000001, (
        f"APY percent mismatch: expected {expected_apy_percent}, got {apy_percent}"
    )
    
    assert abs(alpha_only_divs_sum_alpha - expected_alpha_only_divs_sum_alpha) < 0.000001, (
        f"Alpha-only divs sum alpha mismatch: expected {expected_alpha_only_divs_sum_alpha}, got {alpha_only_divs_sum_alpha}"
    )
    
    assert abs(period_yield - expected_period_yield) < 0.000001, (
        f"Period yield mismatch: expected {expected_period_yield}, got {period_yield}"
    )
    
    assert skipped == expected_skipped, (
        f"Skipped count mismatch: expected {expected_skipped}, got {skipped}"
    )
    
    assert abs(total_divs_sum_alpha - expected_total_divs_sum_alpha) < 0.000001, (
        f"Total divs sum alpha mismatch: expected {expected_total_divs_sum_alpha}, got {total_divs_sum_alpha}"
    )
