"""Cockpit Ticket 1 — read-only access to vw_customer_offer_opportunity.

The view is created by ``migrations.cockpit_schema``. Joins
``ps_items_dw`` (DwItem) for human-readable names.
"""
from sqlalchemy import text

from app import db


def get_offer_opportunities(customer_code: str, limit: int = 10) -> list[dict]:
    """Top SKUs the customer buys regularly with no active offer where peers do.

    Returns [] (does not raise) if the view is missing — keeps the cockpit
    page renderable on backends where the view didn't ship (e.g. SQLite).
    """
    try:
        rows = db.session.execute(text("""
            SELECT
                o.sku,
                COALESCE(i.item_name, o.sku) AS item_name,
                o.revenue_90d,
                o.gp_90d,
                o.gm_pct_avg,
                o.peer_offered_count,
                o.last_bought
            FROM vw_customer_offer_opportunity o
            LEFT JOIN ps_items_dw i ON i.item_code_365 = o.sku
            WHERE o.customer_code_365 = :code
            ORDER BY o.peer_offered_count DESC, o.revenue_90d DESC
            LIMIT :lim
        """), {"code": customer_code, "lim": limit}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []
