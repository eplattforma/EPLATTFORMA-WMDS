---
name: Receipt test suite cross-file pollution
description: Why tests/test_receipt_controls.py fails when the whole tests/ dir is collected but passes alone
---

The rule: judge receipt-control test results by running `pytest tests/test_receipt_controls.py` alone. Running `pytest tests/ -k "receipt or payment"` collects/imports other test modules whose fixtures leak shared DB state (e.g. duplicate manual-receipt 409s), producing 8-14 flaky failures that vary between runs.

**Why:** other test modules create app/DB state at import or via differently-scoped fixtures; the receipt suite assumes a clean DB per class.

**How to apply:** before blaming a code change for receipt-test failures, re-run the file standalone; only investigate if it fails alone.
