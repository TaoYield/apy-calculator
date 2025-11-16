"""
Test cases for claimable_float function matching Go reference implementation.

Reference: validator_dividends_test.go
"""
import pytest
from src.helpers import claimable_float


@pytest.mark.unit
def test_claimable_float_basic_cases():
    """Test basic conversion from str representation"""
    test_cases = [
        ("4920112", 0.001146),
        ("5592406", 0.001302),
        ("5326458", 0.001240),
        ("6497779", 0.001513),
        ("5516964", 0.001285),
        ("4024324", 0.000937),
        ("6273345", 0.001461),
        ("5154553", 0.001200),
        ("5207123", 0.001212),
        ("5279477", 0.001229),
    ]
    
    for claimable_value, expected_float in test_cases:
        # Convert string to dict format expected by fixed_to_float
        bits_data = {"bits": int(claimable_value)}
        result = claimable_float(bits_data)
        # Use epsilon for floating point comparison (0.000001 precision, matching Go test)
        assert abs(result - expected_float) < 0.000001, (
            f"claimable_float({{'bits': {claimable_value}}}) should return approximately {expected_float}, got {result}"
        )


@pytest.mark.unit
def test_claimable_float_edge_cases():
    """Test edge cases from Go validator_dividends_test.go"""
    
    # Zero claimable
    result = claimable_float({"bits": 0})
    assert result == 0.0, "Zero claimable should return 0.0"
    
    # Large claimable value (from edge case test)
    result = claimable_float({"bits": 13387317})
    expected = 0.003117
    assert abs(result - expected) < 0.000001, (
        f"Large claimable value should convert correctly, expected {expected}, got {result}"
    )


@pytest.mark.unit
def test_claimable_float_dict_format():
    """Test dict format {'bits': value} used in actual code"""
    test_cases = [
        ({"bits": 4920112}, 0.001146),
        ({"bits": 5592406}, 0.001302),
        ({"bits": 0}, 0.0),
        ({"bits": 13387317}, 0.003117),
    ]
    
    for bits_data, expected_float in test_cases:
        result = claimable_float(bits_data)
        assert abs(result - expected_float) < 0.000001, (
            f"claimable_float({bits_data}) should return approximately {expected_float}, got {result}"
        )


@pytest.mark.unit
def test_claimable_float_integer_format():
    """Test integer format converted to dict"""
    test_cases = [
        (4920112, 0.001146),
        (5592406, 0.001302),
        (0, 0.0),
    ]
    
    for bits_value, expected_float in test_cases:
        # Convert integer to dict format expected by fixed_to_float
        bits_data = {"bits": bits_value}
        result = claimable_float(bits_data)
        assert abs(result - expected_float) < 0.000001, (
            f"claimable_float({{'bits': {bits_value}}}) should return approximately {expected_float}, got {result}"
        )


@pytest.mark.unit
def test_claimable_float_invalid_input():
    """Test invalid input handling - should raise errors"""
    # Test with empty dict (no 'bits' key) - should raise KeyError
    with pytest.raises(KeyError):
        claimable_float({})
    
    # Test with None - should raise TypeError
    with pytest.raises(TypeError):
        claimable_float(None)
