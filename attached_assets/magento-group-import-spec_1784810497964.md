# Build Spec: Magento Customer Group → Replace Customer Category

**Goal:** Pull each customer's Magento ID and Magento customer group from Magento via the REST API, match to the PS365 customer code, and make the customer "category" shown across the app read the Magento group instead — without destroying source data or editing every screen blindly.

**Match key:** `customer_code_365` (the PS365 code, e.g. `77700101`).

**Worked example — 1 MINUTE KIOSK LTD (`77700101`, Magento ID 103):**

| | value now | value after |
|---|---|---|
| category shown | `3` ("CHECK", from Powersoft) | `4` (Magento group) |

**Coverage today:** 1,446 total customers; ~464 currently have a Magento ID (from the login table only — the API import should find more). Customers with no Magento account keep their old category via the fallback.

---

## Phase 1 — Storage (run once)

```sql
CREATE TABLE IF NOT EXISTS magento_customer_map (
  magento_customer_id integer PRIMARY KEY,
  customer_code_365   text NOT NULL,
  magento_group_id    integer,
  magento_group_name  text,
  source              text,
  imported_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mcm_code ON magento_customer_map (customer_code_365);
```

`magento_customer_id` is the primary key (not the PS365 code) so that a customer with more than one Magento account is not silently lost.

---

## Phase 2 — Pull from Magento REST API

**Secrets (Replit Secrets, never hardcoded):**
- `MAGENTO_BASE_URL` — the store URL
- `MAGENTO_TOKEN` — integration access token, created in Magento Admin → System → Integrations with Customer read access

**Discovery step (do NOT skip):**
Call `GET {MAGENTO_BASE_URL}/rest/V1/customers/103` with header `Authorization: Bearer {MAGENTO_TOKEN}`. Print the `custom_attributes` array and find which `attribute_code` holds the value `77700101`. Save it as constant `PS365_ATTR`. If the PS365 code is a standard field rather than a custom attribute, use that field instead — the point is to confirm where `77700101` lives before building the loop.

**Import loop:**

```
GET {MAGENTO_BASE_URL}/rest/V1/customers/search
    ?searchCriteria[pageSize]=200&searchCriteria[currentPage]=N
Header: Authorization: Bearer {MAGENTO_TOKEN}
```

Loop `N` from 1, incrementing until a page returns fewer than 200 items. For each customer:
- `magento_customer_id` = `item.id`
- `magento_group_id` = `item.group_id`
- `customer_code_365` = value of the `custom_attributes` entry where `attribute_code == PS365_ATTR`
- skip the row if that PS365 value is empty

**Upsert (idempotent, safe to re-run):**

```sql
INSERT INTO magento_customer_map
  (magento_customer_id, customer_code_365, magento_group_id, magento_group_name, source, imported_at)
VALUES ($1,$2,$3,$4,'magento_api', now())
ON CONFLICT (magento_customer_id) DO UPDATE SET
  customer_code_365 = EXCLUDED.customer_code_365,
  magento_group_id  = EXCLUDED.magento_group_id,
  source            = 'magento_api',
  imported_at       = now();
```

Schedule as a nightly job. After each run, log: total pulled, rows written, and count of PS365 codes not found in `ps_customers` (mismatches to review).

---

## Phase 3 — Expose the group as the category (single source of truth)

```sql
CREATE OR REPLACE VIEW vw_customer_magento AS
SELECT
  c.customer_code_365,
  c.company_name,
  m.magento_customer_id,
  m.magento_group_id,
  COALESCE(m.magento_group_id::text, c.category_code_1_365) AS customer_category
FROM ps_customers c
LEFT JOIN magento_customer_map m ON m.customer_code_365 = c.customer_code_365;
```

`customer_category` returns the Magento group when a mapping exists, otherwise falls back to the old category so no customer goes blank.

---

## Phase 4 — Repoint reads (deliberate, not blind)

Search the whole codebase for every read of `category_code_1_365` or `category_1_name` from `ps_customers` — displays, filters, exports, reports. For each hit, decide:
- **Display-only** → repoint to `customer_category` from `vw_customer_magento`.
- **Drives logic** (filtering / pricing / segmentation) → list it and pause for review. Do NOT silently swap, because changing category values to group values changes that logic's behaviour.

**Do not overwrite `ps_customers.category_code_1_365`.** It is synced from Powersoft365 and would be wiped on the next sync. The swap happens at the view/read layer only.

---

## Owner actions (not Replit)

1. Create the Magento integration token and add it to Replit Secrets.
2. Confirm the `PS365_ATTR` attribute code from the `/customers/103` discovery call.
