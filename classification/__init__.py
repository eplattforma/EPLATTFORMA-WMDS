"""
Operational Intelligence Classification Module

This module provides automated item classification for warehouse operations.
It computes attributes like fragility, spill risk, pressure sensitivity, etc.
based on rules, category defaults, and SKU overrides.
"""

from .engine import reclassify_items
from .resolver import resolve_attribute
from .rules import (
    compute_unit_type,
    compute_fragility,
    compute_spill_risk,
    compute_pressure_sensitivity,
    compute_stackability,
    compute_temperature_sensitivity,
    compute_shape_type,
    compute_pick_difficulty,
    compute_shelf_height,
    compute_box_fit_rule,
    compute_zone
)

__all__ = [
    'reclassify_items',
    'resolve_attribute',
    'compute_unit_type',
    'compute_fragility',
    'compute_spill_risk',
    'compute_pressure_sensitivity',
    'compute_stackability',
    'compute_temperature_sensitivity',
    'compute_shape_type',
    'compute_pick_difficulty',
    'compute_shelf_height',
    'compute_box_fit_rule',
    'compute_zone'
]
