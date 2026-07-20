---
name: PS365 receipt tests need HTTP + sequence stubs
description: Why receipt-creation tests must stub the PS365 HTTP call and the reference-number sequence
---
Rule: any test that exercises the real receipt-creation path must monkeypatch both the PS365 HTTP call (`requests.post` in the receipts module) and the reference-number generator.

**Why:** PS365 credentials are real secrets present even in the test environment, so unstubbed tests make live API calls (which fail on unknown customer codes — or worse, could post real receipts). Also the reference-number sequence uses `SELECT ... FOR UPDATE`, which SQLite (the test DB) cannot parse.

**How to apply:** stub `next_reference_number` with a lambda and replace `requests.post` with a fake response whose json() returns `{'api_response': {'response_code': '1', 'response_id': ...}}` (the API signals success via response_code "1", not HTTP status).
