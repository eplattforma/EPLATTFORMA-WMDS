import logging
from collections import defaultdict
from datetime import date, timedelta
from math import isfinite
from sqlalchemy import text

from app import db
from dw_analytics_models import (
    DwRecoBasket,
    DwCategoryPenetration,
    DwShareOfWallet,
    DwChurnRisk,
)
from models import DwInvoiceHeader, DwInvoiceLine, DwItem

logger = logging.getLogger(__name__)


def run_market_basket(min_support=0.01, min_confidence=0.2, max_rules=1000):
    """
    Build market basket rules:
    - from_item_code -> to_item_code
    - store results in DwRecoBasket
    """
    logger.info("Starting market basket analysis...")

    rows = db.session.execute(text("""
        SELECT invoice_no_365, item_code_365
        FROM dw_invoice_line
    """)).fetchall()

    baskets = defaultdict(set)
    for inv_no, item_code in rows:
        baskets[inv_no].add(item_code)

    total_baskets = len(baskets)
    if total_baskets == 0:
        logger.info("No baskets found.")
        return

    item_count = defaultdict(int)
    pair_count = defaultdict(int)

    for items in baskets.values():
        items = list(items)
        for i in range(len(items)):
            item_count[items[i]] += 1
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a = items[i]
                b = items[j]
                if a == b:
                    continue
                key = tuple(sorted((a, b)))
                pair_count[key] += 1

    rules = []
    for (a, b), cnt_ab in pair_count.items():
        support_ab = cnt_ab / total_baskets
        if support_ab < min_support:
            continue

        support_a = item_count[a] / total_baskets
        support_b = item_count[b] / total_baskets

        conf_a_b = cnt_ab / item_count[a]
        conf_b_a = cnt_ab / item_count[b]

        lift_a_b = conf_a_b / support_b if support_b > 0 else None
        lift_b_a = conf_b_a / support_a if support_a > 0 else None

        if conf_a_b >= min_confidence:
            rules.append({
                "from_item": a,
                "to_item": b,
                "support": support_ab,
                "confidence": conf_a_b,
                "lift": lift_a_b,
            })
        if conf_b_a >= min_confidence:
            rules.append({
                "from_item": b,
                "to_item": a,
                "support": support_ab,
                "confidence": conf_b_a,
                "lift": lift_b_a,
            })

    rules.sort(
        key=lambda r: (
            -(r["lift"] if r["lift"] is not None and isfinite(r["lift"]) else r["confidence"])
        )
    )

    DwRecoBasket.query.delete()
    db.session.commit()

    for r in rules[:max_rules]:
        rec = DwRecoBasket(
            from_item_code=r["from_item"],
            to_item_code=r["to_item"],
            support=r["support"],
            confidence=r["confidence"],
            lift=r["lift"],
        )
        db.session.add(rec)

    db.session.commit()
    logger.info(f"✅ Saved {min(len(rules), max_rules)} market basket rules.")


def run_category_penetration():
    """
    Build customer x category matrix and store in DwCategoryPenetration.
    """
    logger.info("Starting category penetration analysis...")

    rows = db.session.execute(text("""
        SELECT
            h.customer_code_365,
            i.category_code_365,
            SUM(l.line_total_incl) AS total_spend
        FROM dw_invoice_line l
        JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
        JOIN dw_item i ON i.item_code_365 = l.item_code_365
        WHERE i.category_code_365 IS NOT NULL
        GROUP BY h.customer_code_365, i.category_code_365
    """)).fetchall()

    all_cats_rows = db.session.execute(text("""
        SELECT DISTINCT category_code_365
        FROM dw_item
        WHERE category_code_365 IS NOT NULL
    """)).fetchall()
    all_categories = [r[0] for r in all_cats_rows]

    cust_cat_spend = defaultdict(dict)
    for cust, cat, spend in rows:
        cust_cat_spend[cust][cat] = float(spend or 0)

    DwCategoryPenetration.query.delete()
    db.session.commit()

    for cust, cat_map in cust_cat_spend.items():
        for cat in all_categories:
            spend = cat_map.get(cat, 0.0)
            has_category = 1 if spend > 0 else 0
            rec = DwCategoryPenetration(
                customer_code_365=cust,
                category_code=cat,
                total_spend=spend,
                has_category=has_category,
            )
            db.session.add(rec)

    db.session.commit()
    logger.info("✅ Category penetration table refreshed.")


def run_share_of_wallet():
    """
    Compute per-customer:
    - actual total spend
    - global average spend
    - opportunity_gap
    Store in DwShareOfWallet.
    """
    logger.info("Starting share of wallet analysis...")

    rows = db.session.execute(text("""
        SELECT h.customer_code_365, SUM(l.line_total_incl) AS total_spend
        FROM dw_invoice_line l
        JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
        GROUP BY h.customer_code_365
    """)).fetchall()

    if not rows:
        logger.info("No spend data.")
        return

    spends = [float(s or 0) for _, s in rows]
    avg_spend = sum(spends) / len(spends)

    DwShareOfWallet.query.delete()
    db.session.commit()

    for cust, spend in rows:
        spend_val = float(spend or 0)
        gap = max(avg_spend - spend_val, 0.0)
        rec = DwShareOfWallet(
            customer_code_365=cust,
            actual_spend=spend_val,
            avg_spend=avg_spend,
            opportunity_gap=gap,
        )
        db.session.add(rec)

    db.session.commit()
    logger.info("✅ Share of wallet table refreshed.")


def run_churn_analysis(days_window=90, drop_threshold=0.5):
    """
    Compare two consecutive periods of length days_window:
    - previous period vs recent period
    - if recent / previous < drop_threshold => churn_flag = 1
    """
    logger.info("Starting churn risk analysis...")

    today = date.today()
    recent_start = today - timedelta(days=days_window)
    prev_start = today - timedelta(days=2 * days_window)

    rows = db.session.execute(text("""
        SELECT
            h.customer_code_365,
            i.category_code_365,
            SUM(CASE
                    WHEN h.invoice_date_utc0 >= :recent_start THEN l.line_total_incl
                    ELSE 0
                END) AS recent_spend,
            SUM(CASE
                    WHEN h.invoice_date_utc0 >= :prev_start
                     AND h.invoice_date_utc0 < :recent_start
                    THEN l.line_total_incl
                    ELSE 0
                END) AS prev_spend
        FROM dw_invoice_line l
        JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
        JOIN dw_item i ON i.item_code_365 = l.item_code_365
        WHERE h.invoice_date_utc0 >= :prev_start
        GROUP BY h.customer_code_365, i.category_code_365
    """), {
        "recent_start": recent_start,
        "prev_start": prev_start,
    }).fetchall()

    DwChurnRisk.query.delete()
    db.session.commit()

    for cust, cat, recent_spend, prev_spend in rows:
        recent_val = float(recent_spend or 0)
        prev_val = float(prev_spend or 0)

        if prev_val <= 0:
            continue

        ratio = recent_val / prev_val
        drop_pct = 1 - ratio
        churn_flag = 1 if ratio < drop_threshold else 0

        rec = DwChurnRisk(
            customer_code_365=cust,
            category_code=cat,
            recent_spend=recent_val,
            prev_spend=prev_val,
            spend_ratio=ratio,
            drop_pct=drop_pct,
            churn_flag=churn_flag,
        )
        db.session.add(rec)

    db.session.commit()
    logger.info("✅ Churn risk table refreshed.")


def run_all_analytics():
    """Run all analytics jobs"""
    logger.info("=" * 80)
    logger.info("Starting all analytics jobs...")
    logger.info("=" * 80)
    try:
        run_market_basket()
        run_category_penetration()
        run_share_of_wallet()
        run_churn_analysis()
        logger.info("=" * 80)
        logger.info("✅ All analytics jobs completed successfully!")
        logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error running analytics jobs: {str(e)}", exc_info=True)
        raise
