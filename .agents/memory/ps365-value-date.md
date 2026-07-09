---
name: PS365 value date vs utc0
description: Why DW reporting must use invoice_date_local, not invoice_date_utc0
---
# PS365 value date vs utc0

**Rule:** All sales reporting (pbi_fact_sales, KPI dashboard) must date invoices by
`invoice_date_local` (PS365 "value date"), falling back to `invoice_date_utc0` only
when local is missing.

**Why:** PS365's own reports and its `list_loyalty_invoices_header` from/to filter
operate on the value date. `invoice_date_utc0` can differ from it by days or even
*months* (observed: local 2023-01-11 vs utc0 2023-05-11). Using utc0 made Jan 2023
net sales read €120k instead of the correct €113.2k — initially misdiagnosed as
"missing credit notes".

**How to apply:** Sync preloads and view date columns use
`COALESCE(invoice_date_local, invoice_date_utc0)`. A HEADER_ADJ arm in
pbi_fact_sales (item_code NULL) reconciles per-invoice line-total gaps to header
net (`total_sub - total_discount`) so SUM(line_total_excl) always ties to headers.
A handful of voided invoices never appear in the API and keep NULL local dates.

**Also:** `list_loyalty_invoices_header` rejects page_size > 100 (response_code 306).
