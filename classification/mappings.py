"""
Category and attribute mappings for Operational Intelligence classification.

These mappings define heuristics based on category codes and keywords.
Update this file to adjust classification behavior without changing rule logic.
"""

LIQUID_CATEGORIES = {
    'ALD', 'ALE', 'ALW', 'ALB',  # Alcohol categories
    'BEV', 'JUI', 'SOF', 'WAT', 'ENE', 'SOD',  # Beverages
    'CLN', 'DET', 'FAB',  # Cleaning products
    'OIL', 'VIN', 'SAU',  # Oils, vinegars, sauces
    'MIL', 'CRE',  # Dairy liquids
}

GLASS_BOTTLE_CATEGORIES = {
    'ALD', 'ALE', 'ALW', 'ALB',  # Spirits, wine, beer in glass
    'OLV', 'VIN',  # Olive oil, vinegar in glass
}

FRAGILE_CATEGORIES = {
    'CHO': 'YES',      # Chocolate - melts and breaks
    'BIS': 'SEMI',     # Biscuits - can crush
    'SNA': 'YES',      # Snacks/chips - very crushable
    'EGG': 'YES',      # Eggs - extremely fragile
    'CER': 'SEMI',     # Cereals - boxes can crush
    'ALD': 'YES',      # Spirits in glass
    'ALE': 'YES',      # Wine in glass
    'ALB': 'SEMI',     # Beer (some glass, some cans)
    'GLA': 'YES',      # Glass products
    'CRI': 'YES',      # Crisps
    'POR': 'YES',      # Porcelain/ceramics
}

HEAT_SENSITIVE_CATEGORIES = {
    'CHO',  # Chocolate
    'ICE',  # Ice cream
    'FRO',  # Frozen items
    'CAN',  # Candles
    'WAX',  # Wax products
}

HIGH_PRESSURE_SENSITIVITY_CATEGORIES = {
    'SNA',  # Snacks/chips
    'CRI',  # Crisps
    'BRE',  # Bread
}

MEDIUM_PRESSURE_SENSITIVITY_CATEGORIES = {
    'CER',  # Cereals
    'BIS',  # Biscuits
    'ALD', 'ALE',  # Glass bottles
    'EGG',  # Eggs
}

ROUND_SHAPE_CATEGORIES = {
    'ALD', 'ALE', 'ALB', 'ALW',  # Alcohol bottles
    'BEV', 'JUI', 'SOF', 'WAT', 'ENE', 'SOD',  # Beverage bottles
    'OIL', 'VIN',  # Oil/vinegar bottles
    'CLN', 'DET',  # Cleaning sprays/bottles
    'CAN',  # Cans
}

FLAT_SHAPE_CATEGORIES = {
    'MAG', 'BOO',  # Magazines, books
    'PAP',  # Paper products
    'ENV',  # Envelopes
}

LIQUID_KEYWORDS = [
    'ml', 'lt', 'ltr', 'litre', 'liter', 'bottle', 'spray', 'liquid',
    'juice', 'water', 'oil', 'vinegar', 'sauce', 'syrup', 'drink',
    'beverage', 'wine', 'beer', 'spirit', 'vodka', 'whisky', 'gin',
    'shampoo', 'conditioner', 'detergent', 'cleaner', 'bleach'
]

FRAGILE_KEYWORDS = [
    'glass', 'fragile', 'delicate', 'crystal', 'porcelain', 'ceramic',
    'chocolate', 'egg', 'chip', 'crisp', 'wafer'
]

HEAT_SENSITIVE_KEYWORDS = [
    'chocolate', 'candy', 'candle', 'wax', 'ice cream', 'frozen'
]

UNIT_TYPE_MAP = {
    'VPACK': 'virtual_pack',
    'PAC': 'pack',
    'BOX': 'box',
    'CASE': 'case',
    'ITEM': 'item',
    'EA': 'item',
    'PC': 'item',
    'PCS': 'item',
}

ZONE_CATEGORY_MAP = {
    'CHO': 'SENSITIVE',
    'SNA': 'SNACKS',
    'CRI': 'SNACKS',
    'FRO': 'SENSITIVE',
    'ICE': 'SENSITIVE',
}

BOX_FIT_PRIORITY = {
    'COOLER_BAG': 1,
    'BOTTOM': 2,
    'MIDDLE': 3,
    'TOP': 4,
}
