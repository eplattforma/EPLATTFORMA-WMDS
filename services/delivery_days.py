import re
from datetime import datetime
import json

VALID_DOW = set(range(1, 8))
VALID_WEEK = {1, 2}

def parse_delivery_days_strict(raw: str):
    # Extract all 2-digit tokens from messy strings (handles trailing commas, dots, spaces)
    tokens = re.findall(r"\d{2}", raw or "")
    if not tokens:
        return {"status": "EMPTY", "slots": [], "invalid": []}

    slots = []
    invalid = []

    for t in tokens:
        try:
            dow = int(t[0])
            wk = int(t[1])
            if dow not in VALID_DOW or wk not in VALID_WEEK:
                invalid.append(t)
            else:
                slots.append((dow, wk))
        except (IndexError, ValueError):
            invalid.append(t)

    # If ANY invalid exists => whole value is INVALID (per requirement)
    if invalid:
        return {"status": "INVALID", "slots": [], "invalid": sorted(set(invalid))}

    # Deduplicate
    slots = sorted(set(slots))
    return {"status": "OK", "slots": slots, "invalid": []}
