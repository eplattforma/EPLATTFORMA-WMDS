"""
Magento → PS365 customer mapping sync (REST API pull).

Pulls all customers from the Magento REST API and upserts them into
``magento_customer_map``. Replaces the manual CSV export step — the same
table and ``vw_customer_magento`` view are fed, just from a live source.

The PS365 code lives in the customer's ``custom_attributes`` under the
attribute code ``powersoft_code`` (verified against the live store).
Rows with a blank powersoft_code are skipped.
"""
import json
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

PS365_ATTRIBUTE_CODE = 'powersoft_code'
PAGE_SIZE = 200
MAX_PAGES = 200  # hard stop: 40k customers

UPSERT_SQL = text("""
    INSERT INTO magento_customer_map
      (magento_customer_id, customer_code_365,
       magento_group_id, magento_group_name,
       source_filename, imported_at)
    VALUES (:mid, :code, :gid, :gname, 'magento_api', now())
    ON CONFLICT (magento_customer_id) DO UPDATE SET
      customer_code_365  = EXCLUDED.customer_code_365,
      magento_group_id   = EXCLUDED.magento_group_id,
      magento_group_name = EXCLUDED.magento_group_name,
      source_filename    = EXCLUDED.source_filename,
      imported_at        = now()
""")


def _fetch_group_names():
    """Group id → label (best effort; sync proceeds without names on failure)."""
    from integrations.magento_rest_oauth import magento_rest_get
    try:
        status, body = magento_rest_get(
            '/rest/V1/customerGroups/search',
            params={'searchCriteria[pageSize]': 100,
                    'searchCriteria[currentPage]': 1})
        if status == 200:
            items = json.loads(body).get('items', [])
            return {g.get('id'): g.get('code') for g in items if g.get('id') is not None}
        logger.warning(f"Magento customerGroups fetch HTTP {status}")
    except Exception as e:
        logger.warning(f"Magento customerGroups fetch failed: {e}")
    return {}


def sync_magento_customer_map():
    """Pull all Magento customers and upsert the PS365 mapping.

    Returns a summary dict:
      total_pulled, written, skipped_blank_code, mismatch_count,
      mismatched_codes (first 50)
    Raises on hard API failure (non-200 first page) so callers/job
    tracking see a FAILED run instead of a silent empty sync.
    """
    from app import db
    from integrations.magento_rest_oauth import magento_rest_get

    group_names = _fetch_group_names()

    total_pulled = 0
    written = 0
    skipped_blank = 0
    seen_codes = set()

    page = 1
    while page <= MAX_PAGES:
        status, body = magento_rest_get(
            '/rest/V1/customers/search',
            params={'searchCriteria[pageSize]': PAGE_SIZE,
                    'searchCriteria[currentPage]': page})
        if status != 200:
            raise RuntimeError(
                f"Magento customers/search HTTP {status} on page {page}: {body[:200]}")
        items = json.loads(body).get('items', [])
        if not items:
            break

        for c in items:
            total_pulled += 1
            mid = c.get('id')
            if mid is None:
                continue
            code = ''
            for attr in c.get('custom_attributes', []) or []:
                if attr.get('attribute_code') == PS365_ATTRIBUTE_CODE:
                    code = str(attr.get('value') or '').strip()
                    break
            if not code:
                skipped_blank += 1
                continue
            gid = c.get('group_id')
            db.session.execute(UPSERT_SQL, {
                'mid': int(mid),
                'code': code,
                'gid': int(gid) if gid is not None else None,
                'gname': group_names.get(gid),
            })
            seen_codes.add(code)
            written += 1

        if len(items) < PAGE_SIZE:
            break
        page += 1

    mismatched = []
    if seen_codes:
        existing = {
            row[0] for row in db.session.execute(
                text("SELECT customer_code_365 FROM ps_customers "
                     "WHERE customer_code_365 = ANY(:codes)"),
                {'codes': list(seen_codes)},
            )
        }
        mismatched = sorted(seen_codes - existing)

    # Commit last: a "failed" sync must mean nothing was persisted.
    db.session.commit()

    summary = {
        'total_pulled': total_pulled,
        'written': written,
        'skipped_blank_code': skipped_blank,
        'mismatch_count': len(mismatched),
        'mismatched_codes': mismatched[:50],
    }
    logger.info(
        f"Magento customer map sync: pulled {total_pulled}, wrote {written}, "
        f"skipped {skipped_blank} blank codes, {len(mismatched)} PS365 codes "
        f"not in ps_customers"
    )
    return summary
