"""Regression tests for item_tracking.parse_location_components.

Locks in the standard warehouse format ``NN-NN-LNN`` (e.g. ``30-07-C02``)
which previously mis-parsed: 'C02' matched the C-prefix corridor pattern
and overwrote the real corridor, and ``level`` was never set — so
``item_time_tracking.level`` stayed NULL for every new pick.
"""
from item_tracking import parse_location_components


def test_standard_format_with_bin():
    result = parse_location_components("30-07-C02")
    assert result["corridor"] == "30"
    assert result["shelf"] == "07"
    assert result["level"] == "C"
    assert result["bin_location"] == "02"


def test_standard_format_without_bin():
    result = parse_location_components("12-03-A")
    assert result["corridor"] == "12"
    assert result["shelf"] == "03"
    assert result["level"] == "A"
    assert result["bin_location"] is None


def test_legacy_prefixed_format_unchanged():
    result = parse_location_components("C01-S02-L03-B04")
    assert result["corridor"] == "C01"
    assert result["shelf"] == "S02"
    assert result["level"] == "L03"
    assert result["bin_location"] == "B04"


def test_non_matching_location():
    result = parse_location_components("COOLER")
    assert result == {
        "corridor": None,
        "shelf": None,
        "level": None,
        "bin_location": None,
    }


def test_empty_and_none():
    empty = {"corridor": None, "shelf": None, "level": None, "bin_location": None}
    assert parse_location_components("") == empty
    assert parse_location_components(None) == empty


def test_lowercase_input_normalised():
    result = parse_location_components("30-07-c02")
    assert result["level"] == "C"
    assert result["corridor"] == "30"
