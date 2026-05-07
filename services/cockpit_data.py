"""Cockpit Ticket 2 — main data orchestrator for ``/cockpit/<customer_code>``.

This module assembles the full payload the cockpit page renders. It is
**pure additive**: existing routes (``routes_customer_analytics``,
``routes_customer_benchmark``, ``blueprints/peer_analytics``,
``routes_pricing_analytics``, ``services/crm_price_offers``) are NEVER
modified. Where SQL was inlined in a route handler, we replicate it
faithfully here (cockpit-brief §11.3 reuse-vs-replicate rule).

Caching (cockpit-brief §11.2):
* ``cachetools.TTLCache`` (5 min, 128 entries) keyed by
  ``(customer_code, period_days, compare, peer_group)``.
* Target reads and live-cart amount bypass the cache — users expect
  edits and live cart updates to reflect immediately.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from cachetools import TTLCache
from sqlalchemy import text

from app import db
from services.cockpit_offer_opportunity import get_offer_opportunities
from services.cockpit_targets import compute_achievement, get_target

logger = logging.getLogger(__name__)


def _safe(fn, default, label: str):
    """Run a panel-fetch and degrade to a safe default on any failure.
    Per ASSUMPTION-039 ff. — a single broken panel must not 500 the page."""
    try:
        return fn()
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning("cockpit panel %r failed: %s", label, exc)
        return default


# ─── caching ────────────────────────────────────────────────────────────

# ASSUMPTION-042: TTL = 5 min, max 128 entries.
_CACHE: TTLCache = TTLCache(maxsize=128, ttl=300)
RETURN_PREDICATE = "COALESCE(h.invoice_type,'') ILIKE '%RETURN%'"


def invalidate_cache(customer_code: str | None = None) -> None:
    """Drop cached entries (whole cache, or just one customer)."""
    if customer_code is None:
        _CACHE.clear()
        return
    for key in [k for k in list(_CACHE.keys()) if k[0] == customer_code]:
        _CACHE.pop(key, None)


# ─── period helpers ────────────────────────────────────────────────────

def _resolve_period(period_days: int) -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=period_days - 1), today


def _resolve_compare(start: date, end: date, compare: str) -> tuple[date, date] | None:
    compare = (compare or "").lower()
    if compare in ("none", ""):
        return None
    if compare == "py":
        try:
            return start.replace(year=start.year - 1), end.replace(year=end.year - 1)
        except ValueError:
            return start.replace(year=start.year - 1, day=28), \
                   end.replace(year=end.year - 1, day=28)
    # prev / prev_period
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=days - 1), prev_end


# ─── header ────────────────────────────────────────────────────────────

def _fetch_header(customer_code: str) -> dict:
    row = db.session.execute(text("""
        SELECT customer_code_365 AS customer_code,
               COALESCE(company_name, '') AS customer_name,
               category_1_name AS classification,
               town AS district,
               agent_code_365 AS agent_username,
               agent_name AS agent_name,
               reporting_group,
               customer_code_secondary AS magento_customer_id
        FROM ps_customers
        WHERE customer_code_365 = :c
        LIMIT 1
    """), {"c": customer_code}).mappings().first()
    out: dict = dict(row) if row else {"customer_code": customer_code, "customer_name": ""}

    # Resolve display name for the agent (Section 14: never raw usernames).
    agent_username = out.get("agent_username") or ""
    out["agent_display_name"] = out.get("agent_name") or agent_username

    # Last invoice age.
    last_inv = db.session.execute(text("""
        SELECT MAX(invoice_date_utc0)::date AS d
        FROM dw_invoice_header
        WHERE customer_code_365 = :c
    """), {"c": customer_code}).first()
    out["last_invoice_days"] = ((date.today() - last_inv[0]).days
                                if last_inv and last_inv[0] else None)

    # Last login (Magento).
    try:
        login_row = db.session.execute(text("""
            SELECT last_login_at
            FROM magento_customer_last_login_current
            WHERE customer_code_365 = :c
        """), {"c": customer_code}).first()
        if login_row and login_row[0]:
            out["last_login_days"] = (datetime.now(timezone.utc).date()
                                      - login_row[0].date()).days
        else:
            out["last_login_days"] = None
    except Exception:
        out["last_login_days"] = None

    out["slot"] = None  # Slots not yet modelled — ASSUMPTION-043.
    return out


# ─── live cart (always live, never cached) ─────────────────────────────

def fetch_live_cart(customer_code: str) -> dict:
    """Read the customer's current cart amount + age from the same source
    ``blueprints/abandoned_carts.py`` uses (``crm_abandoned_cart_state``,
    populated by the abandoned-cart sync job). We do **not** make an
    outbound Magento call here — the cockpit must stay snappy.
    """
    try:
        row = db.session.execute(text("""
            SELECT has_abandoned_cart, abandoned_cart_amount,
                   abandoned_cart_items, last_synced_at, magento_customer_id
            FROM crm_abandoned_cart_state
            WHERE customer_code_365 = :c
        """), {"c": customer_code}).mappings().first()
    except Exception:
        row = None
    if not row or not row["has_abandoned_cart"]:
        return {"amount": 0.0, "sku_count": 0, "is_addon": False,
                "age_minutes": None, "magento_customer_id": None,
                "magento_customer_url": None}
    last = row["last_synced_at"]
    age_minutes = None
    if last:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_minutes = int((datetime.now(timezone.utc) - last).total_seconds() / 60)
    mid = row.get("magento_customer_id")
    base = os.environ.get("MAGENTO_BASE_URL", "").rstrip("/")
    magento_url = f"{base}/admin/customer/index/edit/id/{mid}" if (base and mid) else None
    return {
        "amount": float(row["abandoned_cart_amount"] or 0),
        "sku_count": int(row["abandoned_cart_items"] or 0),
        "is_addon": False,
        "age_minutes": age_minutes,
        "magento_customer_id": mid,
        "magento_customer_url": magento_url,
    }


# ─── KPIs (sales, GP, engagement) ──────────────────────────────────────

def _fetch_sales_gp(customer_code: str, start: date, end: date) -> dict:
    row = db.session.execute(text(f"""
        SELECT
            COALESCE(SUM(CASE WHEN {RETURN_PREDICATE}
                              THEN -ABS(COALESCE(l.line_total_excl,0))
                              ELSE COALESCE(l.line_total_excl,0) END), 0) AS sales,
            COALESCE(SUM(CASE WHEN {RETURN_PREDICATE}
                              THEN -ABS(COALESCE(l.gross_profit,0))
                              ELSE COALESCE(l.gross_profit,0) END), 0) AS gp
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.customer_code_365 = :c
          AND h.invoice_date_utc0::date BETWEEN :s AND :e
    """), {"c": customer_code, "s": start, "e": end}).mappings().first()
    sales = float(row["sales"] or 0) if row else 0.0
    gp = float(row["gp"] or 0) if row else 0.0
    gm_pct = (gp / sales * 100.0) if sales else None
    return {"sales": sales, "gp": gp, "gm_pct": gm_pct}


def _delta(curr: float | None, prev: float | None) -> dict:
    if prev is None:
        return {"abs": None, "pct": None}
    abs_v = (curr or 0) - prev
    pct = (abs_v / prev * 100.0) if prev else None
    return {"abs": round(abs_v, 2),
            "pct": round(pct, 1) if pct is not None else None}


def _monthly_sparkline(customer_code: str, end: date, months: int = 12) -> list[float]:
    start = (end.replace(day=1) - timedelta(days=months * 31)).replace(day=1)
    rows = db.session.execute(text(f"""
        SELECT TO_CHAR(h.invoice_date_utc0, 'YYYY-MM') AS m,
               COALESCE(SUM(CASE WHEN {RETURN_PREDICATE}
                                 THEN -ABS(COALESCE(l.line_total_excl,0))
                                 ELSE COALESCE(l.line_total_excl,0) END), 0) AS s
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.customer_code_365 = :c
          AND h.invoice_date_utc0::date BETWEEN :s AND :e
        GROUP BY 1
        ORDER BY 1
    """), {"c": customer_code, "s": start, "e": end}).fetchall()
    series = {r[0]: float(r[1] or 0) for r in rows}
    out = []
    cursor = end.replace(day=1)
    history = []
    for _ in range(months):
        history.append(cursor.strftime("%Y-%m"))
        prev_first = (cursor - timedelta(days=1)).replace(day=1)
        cursor = prev_first
    for key in reversed(history):
        out.append(round(series.get(key, 0.0), 2))
    return out


# ASSUMPTION-039: engagement weights login=0.25 / invoice=0.30 /
# slot_usage=0.25 / offer_uptake=0.20 (cockpit-brief §11.4 verbatim).
def _compute_engagement_score(customer_code: str, header: dict) -> dict:
    """Composite 0–100 customer engagement score.

    Components (cockpit-brief §11.4, ASSUMPTION-039 for the weights):

        login_score        = clamp(100 - last_login_days × 3, 0, 100)
        invoice_score      = clamp(100 - last_invoice_days × 2, 0, 100)
        slot_usage_score   = (orders_in_last_6_slots / 6) × 100
        offer_uptake_score = (offers_with_sales_4w / active_offers) × 100
                             if active_offers > 0 else 50

        engagement_score = 0.25*login + 0.30*invoice
                         + 0.25*slot_usage + 0.20*offer_uptake
    """
    def _clamp(x): return max(0.0, min(100.0, x))

    lld = header.get("last_login_days")
    lid = header.get("last_invoice_days")
    login_score = _clamp(100 - lld * 3) if lld is not None else 0.0
    invoice_score = _clamp(100 - lid * 2) if lid is not None else 0.0

    # Slots: orders_in_last_6_slots / 6 — slot system not yet modelled
    # (ASSUMPTION-043). Use 6 most-recent invoice weeks as a proxy: how
    # many of the last 6 ISO weeks contain at least one invoice.
    six_weeks_ago = date.today() - timedelta(days=6 * 7)
    weeks = db.session.execute(text("""
        SELECT COUNT(DISTINCT date_trunc('week', invoice_date_utc0)::date)
        FROM dw_invoice_header
        WHERE customer_code_365 = :c
          AND invoice_date_utc0::date >= :s
    """), {"c": customer_code, "s": six_weeks_ago}).scalar() or 0
    slot_usage_score = _clamp((float(weeks) / 6.0) * 100)

    # Offer uptake: from crm_customer_offer_summary_current if present.
    try:
        ou = db.session.execute(text("""
            SELECT active_offer_skus,
                   offered_skus_bought_4w
            FROM crm_customer_offer_summary_current
            WHERE customer_code_365 = :c
        """), {"c": customer_code}).mappings().first()
    except Exception:
        ou = None
    if ou and (ou["active_offer_skus"] or 0) > 0:
        offer_uptake = (float(ou["offered_skus_bought_4w"] or 0)
                        / float(ou["active_offer_skus"]) * 100.0)
    else:
        offer_uptake = 50.0

    score = round(0.25 * login_score + 0.30 * invoice_score
                  + 0.25 * slot_usage_score + 0.20 * offer_uptake)
    return {
        "value": int(score),
        "components": {
            "login": round(login_score, 1),
            "invoice": round(invoice_score, 1),
            "slot_usage": round(slot_usage_score, 1),
            "offer_acceptance": round(offer_uptake, 1),
        },
    }


def _fetch_login_behaviour(customer_code: str, lookback_days: int = 90) -> dict | None:
    """Returns login pattern analysis for one customer.

    Reads from magento_customer_login_log (full history table).

    Returns dict with keys:
      heatmap                   — list of {day_of_week, time_bucket, count}
                                  where day_of_week is 0=Mon..6=Sun
                                  and time_bucket is "morning"/"afternoon"/"evening"
      peak_day_name             — Greek string like "Τρίτη" or None
      peak_hour_range           — string like "14:00 – 16:00" or None
      avg_session_minutes       — float (capped at 120) or None
      session_count_with_logout — int
      logins_last_30d           — int
      logins_prev_30d           — int
      trend_direction           — "up" / "down" / "stable" / None
      trend_pct                 — float or None
      last_login_at             — datetime or None
      total_logins_in_window    — int
      best_contact_window       — human-readable text (Greek)
      data_quality              — "good" / "limited" / "insufficient"

    Returns None when the customer has no login history in the window.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    cutoff_30d = now - timedelta(days=30)
    cutoff_60d = now - timedelta(days=60)

    # ── 1. Heatmap: logins bucketed by day-of-week × time-of-day ──────────
    #   Postgres DOW: 0=Sun..6=Sat → convert to 0=Mon..6=Sun via (DOW+6)%7
    #   Time buckets use Europe/Athens local time (customers are Greek B2B).
    #   00:00–05:59 Athens is treated as "evening" (late-night / previous day).
    heatmap_rows = db.session.execute(text("""
        SELECT
            ((EXTRACT(DOW FROM last_login_at AT TIME ZONE 'Europe/Athens'))::int + 6) % 7
                AS dow,
            CASE
                WHEN EXTRACT(HOUR FROM last_login_at AT TIME ZONE 'Europe/Athens') < 6
                    THEN 'evening'
                WHEN EXTRACT(HOUR FROM last_login_at AT TIME ZONE 'Europe/Athens') < 12
                    THEN 'morning'
                WHEN EXTRACT(HOUR FROM last_login_at AT TIME ZONE 'Europe/Athens') < 18
                    THEN 'afternoon'
                ELSE 'evening'
            END AS time_bucket,
            COUNT(*) AS cnt
        FROM magento_customer_login_log
        WHERE customer_code_365 = :code
          AND last_login_at >= :cutoff
        GROUP BY dow, time_bucket
    """), {"code": customer_code, "cutoff": cutoff}).mappings().all()

    total_logins_in_window = sum(int(r["cnt"]) for r in heatmap_rows)
    if total_logins_in_window == 0:
        return None

    heatmap = [
        {"day_of_week": int(r["dow"]), "time_bucket": r["time_bucket"],
         "count": int(r["cnt"])}
        for r in heatmap_rows
    ]

    data_quality = (
        "good"        if total_logins_in_window >= 10 else
        "limited"     if total_logins_in_window >= 3  else
        "insufficient"
    )

    # ── 2. Peak day ────────────────────────────────────────────────────────
    DAY_NAMES_EL = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη",
                    "Παρασκευή", "Σάββατο", "Κυριακή"]

    day_totals: dict[int, int] = {}
    for r in heatmap_rows:
        d = int(r["dow"])
        day_totals[d] = day_totals.get(d, 0) + int(r["cnt"])

    peak_day_num: int | None = None
    peak_day_name: str | None = None
    if day_totals:
        max_cnt = max(day_totals.values())
        tied = [d for d, c in day_totals.items() if c == max_cnt]
        if len(tied) > 1:
            rec = db.session.execute(text("""
                SELECT ((EXTRACT(DOW FROM last_login_at AT TIME ZONE 'Europe/Athens'))::int + 6) % 7
                           AS dow
                FROM magento_customer_login_log
                WHERE customer_code_365 = :code
                  AND last_login_at >= :cutoff
                  AND ((EXTRACT(DOW FROM last_login_at AT TIME ZONE 'Europe/Athens'))::int + 6) % 7
                      = ANY(:days)
                ORDER BY last_login_at DESC LIMIT 1
            """), {"code": customer_code, "cutoff": cutoff,
                   "days": tied}).mappings().first()
            peak_day_num = int(rec["dow"]) if rec else tied[0]
        else:
            peak_day_num = tied[0]

        if day_totals.get(peak_day_num, 0) >= 5:
            peak_day_name = DAY_NAMES_EL[peak_day_num]

    # ── 3. Peak 2-hour window ──────────────────────────────────────────────
    peak_hour_range: str | None = None
    if total_logins_in_window >= 5:
        hour_rows = db.session.execute(text("""
            SELECT EXTRACT(HOUR FROM last_login_at AT TIME ZONE 'Europe/Athens')::int AS hr,
                   COUNT(*) AS cnt
            FROM magento_customer_login_log
            WHERE customer_code_365 = :code AND last_login_at >= :cutoff
            GROUP BY hr
        """), {"code": customer_code, "cutoff": cutoff}).mappings().all()
        hour_map = {int(r["hr"]): int(r["cnt"]) for r in hour_rows}
        best_start, best_sum = None, 0
        for h in range(24):
            s = hour_map.get(h, 0) + hour_map.get((h + 1) % 24, 0)
            if s > best_sum:
                best_sum, best_start = s, h
        if best_start is not None and best_sum > 0:
            end_h = (best_start + 2) % 24
            peak_hour_range = f"{best_start:02d}:00 – {end_h:02d}:00"

    # ── 4. Session duration (only rows with both login + logout) ───────────
    session_rows = db.session.execute(text("""
        SELECT EXTRACT(EPOCH FROM (last_logout_at - last_login_at)) / 60.0 AS dur
        FROM magento_customer_login_log
        WHERE customer_code_365 = :code
          AND last_login_at >= :cutoff
          AND last_logout_at IS NOT NULL
          AND last_logout_at > last_login_at
          AND EXTRACT(EPOCH FROM (last_logout_at - last_login_at)) / 60.0 <= 120
    """), {"code": customer_code, "cutoff": cutoff}).mappings().all()
    valid_durs = [float(r["dur"]) for r in session_rows if r["dur"] is not None]
    avg_session_minutes = (
        round(sum(valid_durs) / len(valid_durs), 1)
        if len(valid_durs) >= 3 else None
    )
    session_count_with_logout = len(valid_durs)

    # ── 5. Trend: last 30d vs previous 30d ────────────────────────────────
    trend_row = db.session.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE last_login_at >= :c30) AS last_30d,
            COUNT(*) FILTER (WHERE last_login_at >= :c60 AND last_login_at < :c30) AS prev_30d
        FROM magento_customer_login_log
        WHERE customer_code_365 = :code AND last_login_at >= :c60
    """), {"code": customer_code, "c30": cutoff_30d, "c60": cutoff_60d}).mappings().first()

    logins_last_30d = int(trend_row["last_30d"]) if trend_row else 0
    logins_prev_30d = int(trend_row["prev_30d"]) if trend_row else 0
    trend_direction: str | None = None
    trend_pct: float | None = None
    if logins_prev_30d > 0:
        pct = (logins_last_30d - logins_prev_30d) / logins_prev_30d * 100
        trend_pct = round(pct, 1)
        trend_direction = "up" if pct > 20 else ("down" if pct < -20 else "stable")

    # ── 6. Last login timestamp ────────────────────────────────────────────
    last_row = db.session.execute(text("""
        SELECT MAX(last_login_at) AS last_login
        FROM magento_customer_login_log WHERE customer_code_365 = :code
    """), {"code": customer_code}).mappings().first()
    last_login_at = last_row["last_login"] if last_row else None

    # ── 7. Best contact window text (Greek-first) ──────────────────────────
    if data_quality == "good" and peak_day_name and peak_hour_range:
        start_h = int(peak_hour_range.split(":")[0])
        if start_h >= 18 or start_h < 6:
            earlier = "το απόγευμα"
        elif start_h >= 12:
            earlier = "το πρωί"
        else:
            earlier = "νωρίς το πρωί"
        best_contact_window = (
            f"Συνδέεται συνήθως {peak_day_name} {peak_hour_range}. "
            f"Στείλτε επικοινωνία {earlier} για μέγιστη αποδοχή."
        )
    else:
        best_contact_window = (
            "Ανεπαρκή δεδομένα σύνδεσης — επικοινωνήστε μέσω "
            "του προτιμώμενου καναλιού."
        )

    logger.info(
        "login_behaviour(%s): %d logins in %dd, peak_day=%s, quality=%s",
        customer_code, total_logins_in_window, lookback_days,
        peak_day_name, data_quality,
    )
    return {
        "heatmap": heatmap,
        "peak_day_name": peak_day_name,
        "peak_hour_range": peak_hour_range,
        "avg_session_minutes": avg_session_minutes,
        "session_count_with_logout": session_count_with_logout,
        "logins_last_30d": logins_last_30d,
        "logins_prev_30d": logins_prev_30d,
        "trend_direction": trend_direction,
        "trend_pct": trend_pct,
        "last_login_at": last_login_at,
        "total_logins_in_window": total_logins_in_window,
        "best_contact_window": best_contact_window,
        "data_quality": data_quality,
    }


def _fetch_open_orders(customer_code: str) -> dict:
    """Open / pending orders. Reads the canonical sources used by the rest
    of the codebase (`crm_customer_open_orders` rollup written by the
    PS365 pending-orders sync, with a fallback to summing the line-level
    `ps_pending_orders_header` table). Both fetches are wrapped in
    try/except + rollback so a missing/migrating table degrades the panel
    to zero rather than 500-ing the cockpit. See ASSUMPTION-039 ff."""
    # Preferred: pre-aggregated rollup populated by ps365_pending_orders sync.
    try:
        r = db.session.execute(text(
            "SELECT COALESCE(open_order_amount, 0), COALESCE(open_order_count, 0) "
            "FROM crm_customer_open_orders WHERE customer_code_365 = :c"
        ), {"c": customer_code}).first()
        if r is not None:
            return {"amount": float(r[0] or 0), "count": int(r[1] or 0)}
    except Exception:
        db.session.rollback()
    # Fallback: aggregate the header table directly (same schema as PSPendingOrderHeader).
    try:
        r = db.session.execute(text(
            "SELECT COALESCE(SUM(total_grand), 0), COUNT(*) "
            "FROM ps_pending_orders_header WHERE customer_code_365 = :c"
        ), {"c": customer_code}).first()
        return {"amount": float(r[0] or 0), "count": int(r[1] or 0)}
    except Exception:
        db.session.rollback()
        return {"amount": 0.0, "count": 0}


# ─── trend (12 months, with target overlay & peer avg) ─────────────────

def _fetch_trend_monthly(customer_code: str, peer_codes: list[str],
                         end: date, months: int = 12) -> list[dict]:
    start = (end.replace(day=1) - timedelta(days=months * 31)).replace(day=1)
    cust_rows = db.session.execute(text(f"""
        SELECT TO_CHAR(h.invoice_date_utc0, 'YYYY-MM') AS m,
               COALESCE(SUM(CASE WHEN {RETURN_PREDICATE}
                                 THEN -ABS(COALESCE(l.line_total_excl,0))
                                 ELSE COALESCE(l.line_total_excl,0) END), 0) AS sales,
               COALESCE(SUM(CASE WHEN {RETURN_PREDICATE}
                                 THEN -ABS(COALESCE(l.gross_profit,0))
                                 ELSE COALESCE(l.gross_profit,0) END), 0) AS gp
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.customer_code_365 = :c
          AND h.invoice_date_utc0::date BETWEEN :s AND :e
        GROUP BY 1 ORDER BY 1
    """), {"c": customer_code, "s": start, "e": end}).mappings().all()
    cust_map = {r["m"]: r for r in cust_rows}

    peer_avg_map: dict[str, float] = {}
    n = len(peer_codes)
    if n:
        rows = db.session.execute(text(f"""
            SELECT TO_CHAR(h.invoice_date_utc0, 'YYYY-MM') AS m,
                   COALESCE(SUM(CASE WHEN {RETURN_PREDICATE}
                                     THEN -ABS(COALESCE(l.line_total_excl,0))
                                     ELSE COALESCE(l.line_total_excl,0) END), 0) AS sales
            FROM dw_invoice_header h
            JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
            WHERE h.customer_code_365 = ANY(CAST(:codes AS text[]))
              AND h.invoice_date_utc0::date BETWEEN :s AND :e
            GROUP BY 1
        """), {"codes": peer_codes, "s": start, "e": end}).fetchall()
        for r in rows:
            peer_avg_map[r[0]] = float(r[1] or 0) / n

    target = get_target(customer_code).get("active") or {}
    monthly_target = float(target["monthly"]) if target.get("monthly") is not None else None

    out = []
    cursor = end.replace(day=1)
    months_list = []
    for _ in range(months):
        months_list.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    for key in reversed(months_list):
        c = cust_map.get(key)
        sales = float(c["sales"]) if c else 0.0
        gp = float(c["gp"]) if c else 0.0
        out.append({
            "month": key,
            "sales": round(sales, 2),
            "gp": round(gp, 2),
            "gm_pct": round(gp / sales * 100, 1) if sales else None,
            "peer_avg_sales": round(peer_avg_map.get(key, 0.0), 2),
            "target_monthly": monthly_target,
        })
    return out


# ─── PVM (cockpit-brief §11.5) ─────────────────────────────────────────

def _fetch_item_aggregates(customer_code: str, start: date, end: date) -> dict[str, dict]:
    rows = db.session.execute(text(f"""
        SELECT l.item_code_365 AS sku,
               SUM(CASE WHEN {RETURN_PREDICATE}
                        THEN -ABS(COALESCE(l.quantity,0))
                        ELSE COALESCE(l.quantity,0) END) AS qty,
               SUM(CASE WHEN {RETURN_PREDICATE}
                        THEN -ABS(COALESCE(l.line_total_excl,0))
                        ELSE COALESCE(l.line_total_excl,0) END) AS sales,
               SUM(CASE WHEN {RETURN_PREDICATE}
                        THEN -ABS(COALESCE(l.gross_profit,0))
                        ELSE COALESCE(l.gross_profit,0) END) AS gp
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.customer_code_365 = :c
          AND h.invoice_date_utc0::date BETWEEN :s AND :e
        GROUP BY l.item_code_365
    """), {"c": customer_code, "s": start, "e": end}).mappings().all()
    out = {}
    for r in rows:
        qty = float(r["qty"] or 0)
        sales = float(r["sales"] or 0)
        gp = float(r["gp"] or 0)
        out[r["sku"]] = {
            "qty": qty, "sales": sales, "gp": gp,
            "price": (sales / qty) if qty else 0.0,
        }
    return out


def _compute_pvm(customer_code: str, start: date, end: date,
                 prev_start: date | None, prev_end: date | None) -> dict:
    """Decompose sales delta into Price / Volume / Mix.

    Cross-term ``(p1-p0)*(q1-q0)`` for common SKUs is **assigned to mix**
    by convention (ASSUMPTION-041). Finance teams sometimes argue about
    this — pick once, document, move on.
    """
    if not prev_start or not prev_end:
        return {"price": 0, "volume": 0, "mix": 0,
                "total_sales_delta": 0, "total_gp_delta": 0,
                "sales_delta_check_pct": None}

    cur = _fetch_item_aggregates(customer_code, start, end)
    prev = _fetch_item_aggregates(customer_code, prev_start, prev_end)

    price_total = volume_total = mix_total = 0.0
    gp_price_total = gp_volume_total = gp_mix_total = 0.0
    for sku in set(cur) | set(prev):
        c = cur.get(sku); p = prev.get(sku)
        if c and p:
            # Sales-PVM (line_total_excl decomposition).
            price_total += (c["price"] - p["price"]) * p["qty"]
            volume_total += (c["qty"] - p["qty"]) * p["price"]
            # Cross-term → mix (ASSUMPTION-041).
            mix_total += (c["price"] - p["price"]) * (c["qty"] - p["qty"])
            # GP-PVM (gross_profit decomposition — same convention applied
            # to per-unit GP). Required by reviewer for parity with finance.
            p_gp_unit = (p["gp"] / p["qty"]) if p["qty"] else 0.0
            c_gp_unit = (c["gp"] / c["qty"]) if c["qty"] else 0.0
            gp_price_total += (c_gp_unit - p_gp_unit) * p["qty"]
            gp_volume_total += (c["qty"] - p["qty"]) * p_gp_unit
            gp_mix_total += (c_gp_unit - p_gp_unit) * (c["qty"] - p["qty"])
        elif c and not p:  # NEW
            mix_total += c["sales"]
            gp_mix_total += c["gp"]
        elif p and not c:  # LOST
            mix_total -= p["sales"]
            gp_mix_total -= p["gp"]

    cur_total = sum(v["sales"] for v in cur.values())
    prev_total = sum(v["sales"] for v in prev.values())
    sales_delta = cur_total - prev_total
    gp_delta = sum(v["gp"] for v in cur.values()) - sum(v["gp"] for v in prev.values())

    check_pct = None
    if abs(sales_delta) > 0.01:
        explained = price_total + volume_total + mix_total
        check_pct = abs((explained - sales_delta) / sales_delta) * 100
    gp_check_pct = None
    if abs(gp_delta) > 0.01:
        gp_explained = gp_price_total + gp_volume_total + gp_mix_total
        gp_check_pct = abs((gp_explained - gp_delta) / gp_delta) * 100

    return {
        "price": round(price_total, 2),
        "volume": round(volume_total, 2),
        "mix": round(mix_total, 2),
        "total_sales_delta": round(sales_delta, 2),
        "total_gp_delta": round(gp_delta, 2),
        "sales_delta_check_pct": round(check_pct, 3) if check_pct is not None else None,
        # GP-PVM (parallel decomposition on gross_profit). ASSUMPTION-041.
        "gp_price": round(gp_price_total, 2),
        "gp_volume": round(gp_volume_total, 2),
        "gp_mix": round(gp_mix_total, 2),
        "gp_delta_check_pct": round(gp_check_pct, 3) if gp_check_pct is not None else None,
    }


# ─── Top items by GP / revenue ─────────────────────────────────────────

def _fetch_top_items(customer_code: str, start: date, end: date,
                     order_by: str, limit: int = 15) -> list[dict]:
    col = "gp" if order_by == "gp" else "sales"
    rows = db.session.execute(text(f"""
        SELECT l.item_code_365 AS item_code,
               COALESCE(i.item_name, '') AS item_name,
               SUM(CASE WHEN {RETURN_PREDICATE}
                        THEN -ABS(COALESCE(l.quantity,0))
                        ELSE COALESCE(l.quantity,0) END) AS qty,
               SUM(CASE WHEN {RETURN_PREDICATE}
                        THEN -ABS(COALESCE(l.line_total_excl,0))
                        ELSE COALESCE(l.line_total_excl,0) END) AS sales,
               SUM(CASE WHEN {RETURN_PREDICATE}
                        THEN -ABS(COALESCE(l.gross_profit,0))
                        ELSE COALESCE(l.gross_profit,0) END) AS gp
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        LEFT JOIN ps_items_dw i ON i.item_code_365 = l.item_code_365
        WHERE h.customer_code_365 = :c
          AND h.invoice_date_utc0::date BETWEEN :s AND :e
        GROUP BY l.item_code_365, COALESCE(i.item_name,'')
        ORDER BY {col} DESC NULLS LAST
        LIMIT :lim
    """), {"c": customer_code, "s": start, "e": end, "lim": limit}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("qty", "sales", "gp"):
            d[k] = round(float(d[k] or 0), 2)
        d["gm_pct"] = round(d["gp"] / d["sales"] * 100, 1) if d["sales"] else None
        out.append(d)
    return out


# ─── Category mix vs peers (replicated from benchmark.category_mix) ────

def _fetch_category_mix_vs_peers(customer_code: str, peer_group: str,
                                 start: date, end: date,
                                 limit: int = 15) -> list[dict]:
    if not peer_group:
        return []
    rows = db.session.execute(text(f"""
    WITH base AS (
      SELECT h.customer_code_365,
             COALESCE(i.category_code_365,'(Uncategorized)') AS category_code,
             CASE WHEN {RETURN_PREDICATE}
                  THEN -ABS(COALESCE(l.line_total_excl,0))
                  ELSE COALESCE(l.line_total_excl,0) END AS sales_net
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      LEFT JOIN ps_items_dw i ON i.item_code_365 = l.item_code_365
      WHERE h.invoice_date_utc0::date BETWEEN :s AND :e
    ),
    peerset AS (
      SELECT customer_code_365 FROM ps_customers
      WHERE reporting_group = :grp AND customer_code_365 <> :c
    ),
    cust_cat AS (
      SELECT category_code, SUM(sales_net) AS sales
      FROM base WHERE customer_code_365 = :c GROUP BY category_code
    ),
    peer_cat AS (
      SELECT category_code, SUM(sales_net) AS sales
      FROM base WHERE customer_code_365 IN (SELECT customer_code_365 FROM peerset)
      GROUP BY category_code
    ),
    totals AS (
      SELECT (SELECT SUM(sales) FROM cust_cat) AS cust_total,
             (SELECT SUM(sales) FROM peer_cat) AS peer_total
    )
    SELECT COALESCE(cat.category_name, c.category_code, p.category_code) AS category,
           COALESCE(c.sales,0) / NULLIF((SELECT cust_total FROM totals),0) AS cust_share,
           COALESCE(p.sales,0) / NULLIF((SELECT peer_total FROM totals),0) AS peer_share,
           (COALESCE(c.sales,0) / NULLIF((SELECT cust_total FROM totals),0)) -
           (COALESCE(p.sales,0) / NULLIF((SELECT peer_total FROM totals),0)) AS gap
    FROM cust_cat c
    FULL OUTER JOIN peer_cat p ON p.category_code = c.category_code
    LEFT JOIN dw_item_categories cat
        ON cat.category_code_365 = COALESCE(c.category_code, p.category_code)
    ORDER BY ABS(
      (COALESCE(c.sales,0) / NULLIF((SELECT cust_total FROM totals),0)) -
      (COALESCE(p.sales,0) / NULLIF((SELECT peer_total FROM totals),0))
    ) DESC NULLS LAST
    LIMIT :lim
    """), {"c": customer_code, "grp": peer_group, "s": start, "e": end, "lim": limit}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["customer_pct"] = round(float(d.pop("cust_share") or 0) * 100, 1)
        d["peer_pct"] = round(float(d.pop("peer_share") or 0) * 100, 1)
        d["gap"] = round(float(d.get("gap") or 0) * 100, 1)
        out.append(d)
    return out


# ─── Active offers ─────────────────────────────────────────────────────

def _fetch_active_offers(customer_code: str, limit: int = 20) -> dict:
    """Active offers panel — line items + the canonical summary KPI block.

    The summary fields (utilisation %, share %, margin-risk count, …) come
    from ``services.crm_price_offers.load_offer_summary_map`` so we don't
    drift from the CRM dashboard / peer analytics view of the same data.
    The line items still come from ``crm_customer_offer_current`` because
    that table is the only source of per-SKU offer rows in the codebase.
    """
    lines: list[dict] = []
    try:
        rows = db.session.execute(text("""
            SELECT sku, item_code_365, product_name, brand_name,
                   rule_name, origin_price, offer_price,
                   discount_percent, gross_margin_percent,
                   sold_qty_4w, sold_value_4w, line_status, last_sold_at
            FROM crm_customer_offer_current
            WHERE customer_code_365 = :c AND is_active = true
            ORDER BY sold_value_4w DESC NULLS LAST, discount_percent DESC NULLS LAST
            LIMIT :lim
        """), {"c": customer_code, "lim": limit}).mappings().all()
        lines = [dict(r) for r in rows]
    except Exception:
        db.session.rollback()
    summary: dict = {}
    try:
        from services.crm_price_offers import (
            load_offer_summary_map, compute_offer_indicator,
        )
        m = load_offer_summary_map([customer_code]) or {}
        s = m.get(customer_code) or {}
        summary = {
            "indicator": compute_offer_indicator(s),
            "active_offer_skus": s.get("active_offer_skus") or 0,
            "offered_skus_bought_4w": s.get("offered_skus_bought_4w") or 0,
            "offered_skus_not_bought": s.get("offered_skus_not_bought") or 0,
            "offer_usage_pct": round(float(s.get("offer_usage_pct") or 0), 1),
            "offer_sales_4w": round(float(s.get("offer_sales_4w") or 0), 2),
            "offer_sales_share_pct": round(float(s.get("offer_sales_share_pct") or 0), 1),
            "avg_discount_percent": round(float(s.get("avg_discount_percent") or 0), 1),
            "margin_risk_skus": s.get("margin_risk_skus") or 0,
            "high_discount_unused_skus": s.get("high_discount_unused_skus") or 0,
        }
    except Exception:
        db.session.rollback()
        summary = {}
    return {"lines": lines, "summary": summary}


def _fetch_cross_sell(customer_code: str, peer_group: str,
                      start: date, end: date,
                      penetration_min: float = 0.30,
                      limit: int = 15) -> list[dict]:
    """Cross-sell suggestions: peer-popular SKUs in the **categories the
    customer already buys from** that the customer has not bought.

    This is intentionally a tighter cousin of the white-space panel — same
    peer-group convention, but filtered to categories the customer is
    already active in (so the suggestion is contextually relevant rather
    than a cold pitch). Falls back to an empty list when peer_group is
    missing or peers can't be resolved (ASSUMPTION-044, revised: now
    sourced from existing tables, not a deferred analytics-jobs feed).
    """
    if not peer_group:
        return []
    try:
        rows = db.session.execute(text(f"""
        WITH base AS (
          SELECT h.customer_code_365, l.item_code_365,
                 COALESCE(i.category_code_365,'') AS category_code,
                 CASE WHEN {RETURN_PREDICATE}
                      THEN -ABS(COALESCE(l.quantity,0))
                      ELSE COALESCE(l.quantity,0) END AS qty_net,
                 CASE WHEN {RETURN_PREDICATE}
                      THEN -ABS(COALESCE(l.line_total_excl,0))
                      ELSE COALESCE(l.line_total_excl,0) END AS sales_net
          FROM dw_invoice_header h
          JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
          LEFT JOIN ps_items_dw i ON i.item_code_365 = l.item_code_365
          WHERE h.invoice_date_utc0::date BETWEEN :s AND :e
        ),
        peerset AS (
          SELECT customer_code_365 FROM ps_customers
          WHERE reporting_group = :grp AND customer_code_365 <> :c
        ),
        peer_cnt AS (SELECT COUNT(*) AS n FROM peerset),
        cust_cats AS (
          SELECT DISTINCT category_code FROM base
          WHERE customer_code_365 = :c AND category_code <> ''
        ),
        cust_items AS (
          SELECT DISTINCT item_code_365 FROM base WHERE customer_code_365 = :c
        ),
        peer_item AS (
          SELECT b.item_code_365, b.category_code,
                 COUNT(DISTINCT b.customer_code_365) FILTER (WHERE b.qty_net > 0) AS buyers,
                 SUM(b.sales_net) AS peer_sales
          FROM base b JOIN peerset p ON p.customer_code_365 = b.customer_code_365
          WHERE b.category_code IN (SELECT category_code FROM cust_cats)
          GROUP BY b.item_code_365, b.category_code
        )
        SELECT pi.item_code_365 AS item_code,
               i.item_name,
               COALESCE(cat.category_name, pi.category_code) AS category,
               (pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) AS penetration,
               (pi.peer_sales::float / NULLIF((SELECT n FROM peer_cnt),0)) AS peer_avg_sales
        FROM peer_item pi
        LEFT JOIN ps_items_dw i ON i.item_code_365 = pi.item_code_365
        LEFT JOIN dw_item_categories cat ON cat.category_code_365 = pi.category_code
        WHERE pi.item_code_365 NOT IN (SELECT item_code_365 FROM cust_items)
          AND (pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) >= :pen
        ORDER BY (pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) DESC NULLS LAST,
                 pi.peer_sales DESC NULLS LAST
        LIMIT :lim
        """), {"c": customer_code, "grp": peer_group,
                "s": start, "e": end, "pen": penetration_min,
                "lim": limit}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        db.session.rollback()
        return []


# ─── White-space / Lapsed (replicated from benchmark) ──────────────────

def _fetch_white_space(customer_code: str, peer_group: str,
                       start: date, end: date,
                       penetration_min: float = 0.30,
                       limit: int = 20) -> list[dict]:
    if not peer_group:
        return []
    rows = db.session.execute(text(f"""
    WITH base AS (
      SELECT h.customer_code_365, l.item_code_365,
             CASE WHEN {RETURN_PREDICATE}
                  THEN -ABS(COALESCE(l.quantity,0))
                  ELSE COALESCE(l.quantity,0) END AS qty_net,
             CASE WHEN {RETURN_PREDICATE}
                  THEN -ABS(COALESCE(l.line_total_excl,0))
                  ELSE COALESCE(l.line_total_excl,0) END AS sales_net
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN :s AND :e
    ),
    peerset AS (
      SELECT customer_code_365 FROM ps_customers
      WHERE reporting_group = :grp AND customer_code_365 <> :c
    ),
    peer_cnt AS (SELECT COUNT(*) AS n FROM peerset),
    peer_item AS (
      SELECT b.item_code_365,
             COUNT(DISTINCT b.customer_code_365) FILTER (WHERE b.qty_net > 0) AS buyers,
             SUM(b.sales_net) AS peer_sales
      FROM base b JOIN peerset p ON p.customer_code_365 = b.customer_code_365
      GROUP BY b.item_code_365
    ),
    cust_item AS (
      SELECT item_code_365, SUM(qty_net) AS cust_qty
      FROM base WHERE customer_code_365 = :c GROUP BY item_code_365
    )
    SELECT pi.item_code_365 AS item_code,
           i.item_name,
           (pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) AS penetration,
           (pi.peer_sales::float / NULLIF((SELECT n FROM peer_cnt),0)) AS peer_avg_sales,
           ((pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) *
            (pi.peer_sales::float / NULLIF((SELECT n FROM peer_cnt),0))) AS opportunity_score
    FROM peer_item pi
    LEFT JOIN cust_item ci ON ci.item_code_365 = pi.item_code_365
    LEFT JOIN ps_items_dw i ON i.item_code_365 = pi.item_code_365
    WHERE COALESCE(ci.cust_qty,0) = 0
      AND (pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) >= :pen
    ORDER BY opportunity_score DESC NULLS LAST
    LIMIT :lim
    """), {"c": customer_code, "grp": peer_group, "s": start, "e": end,
           "pen": penetration_min, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def _fetch_lapsed_items(customer_code: str, peer_group: str, start: date,
                        end: date, prev_start: date, prev_end: date,
                        limit: int = 20) -> list[dict]:
    rows = db.session.execute(text(f"""
    WITH comp AS (
      SELECT l.item_code_365,
             SUM(CASE WHEN {RETURN_PREDICATE} THEN 0
                      ELSE GREATEST(COALESCE(l.quantity,0),0) END) AS comp_qty,
             SUM(CASE WHEN {RETURN_PREDICATE} THEN 0
                      ELSE GREATEST(COALESCE(l.line_total_excl,0),0) END) AS comp_sales
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.customer_code_365 = :c
        AND h.invoice_date_utc0::date BETWEEN :ps AND :pe
      GROUP BY l.item_code_365
      HAVING SUM(CASE WHEN {RETURN_PREDICATE} THEN 0
                      ELSE GREATEST(COALESCE(l.quantity,0),0) END) > 0
    ),
    curr AS (
      SELECT l.item_code_365,
             SUM(CASE WHEN {RETURN_PREDICATE} THEN 0
                      ELSE GREATEST(COALESCE(l.quantity,0),0) END) AS curr_qty
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.customer_code_365 = :c
        AND h.invoice_date_utc0::date BETWEEN :s AND :e
      GROUP BY l.item_code_365
    )
    SELECT c.item_code_365 AS item_code,
           i.item_name,
           c.comp_qty, c.comp_sales
    FROM comp c
    LEFT JOIN curr k ON k.item_code_365 = c.item_code_365
    LEFT JOIN ps_items_dw i ON i.item_code_365 = c.item_code_365
    WHERE COALESCE(k.curr_qty,0) = 0
    ORDER BY c.comp_sales DESC NULLS LAST
    LIMIT :lim
    """), {"c": customer_code, "s": start, "e": end,
           "ps": prev_start, "pe": prev_end, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


# ─── Churn risk by category (cockpit-brief §11.6) ──────────────────────

def _fetch_churn_risk_by_category(customer_code: str) -> list[dict]:
    """Compare last 90d vs the prior 90d revenue per category. Hide
    healthy rows (drop_pct >= -25). Severity thresholds per §11.6
    (ASSUMPTION-040)."""
    today = date.today()
    recent_start = today - timedelta(days=89)
    recent_end = today
    prev_end = recent_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=89)

    rows = db.session.execute(text(f"""
        SELECT COALESCE(cat.category_name, i.category_code_365, '(Uncategorized)') AS category,
               SUM(CASE WHEN h.invoice_date_utc0::date BETWEEN :ps AND :pe
                        THEN (CASE WHEN {RETURN_PREDICATE}
                                   THEN -ABS(COALESCE(l.line_total_excl,0))
                                   ELSE COALESCE(l.line_total_excl,0) END)
                        ELSE 0 END) AS prev_90d,
               SUM(CASE WHEN h.invoice_date_utc0::date BETWEEN :rs AND :re
                        THEN (CASE WHEN {RETURN_PREDICATE}
                                   THEN -ABS(COALESCE(l.line_total_excl,0))
                                   ELSE COALESCE(l.line_total_excl,0) END)
                        ELSE 0 END) AS recent_90d
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        LEFT JOIN ps_items_dw i ON i.item_code_365 = l.item_code_365
        LEFT JOIN dw_item_categories cat ON cat.category_code_365 = i.category_code_365
        WHERE h.customer_code_365 = :c
          AND h.invoice_date_utc0::date BETWEEN :ps AND :re
        GROUP BY 1
        HAVING SUM(CASE WHEN h.invoice_date_utc0::date BETWEEN :ps AND :pe
                        THEN (CASE WHEN {RETURN_PREDICATE}
                                   THEN -ABS(COALESCE(l.line_total_excl,0))
                                   ELSE COALESCE(l.line_total_excl,0) END)
                        ELSE 0 END) >= 100
    """), {"c": customer_code, "ps": prev_start, "pe": prev_end,
           "rs": recent_start, "re": recent_end}).mappings().all()
    out = []
    for r in rows:
        prev_v = float(r["prev_90d"] or 0)
        recent_v = float(r["recent_90d"] or 0)
        drop = ((recent_v - prev_v) / prev_v * 100.0) if prev_v else 0.0
        # ASSUMPTION-040.
        if drop < -50:
            severity = "negative"
        elif drop < -25:
            severity = "low"
        else:
            severity = "healthy"
        if severity == "healthy":
            continue
        out.append({
            "category": r["category"],
            "prev_90d": round(prev_v, 2),
            "recent_90d": round(recent_v, 2),
            "drop_pct": round(drop, 1),
            "severity": severity,
        })
    out.sort(key=lambda x: x["drop_pct"])  # worst first
    return out


# ─── Price index outliers (replicated, slimmed) ────────────────────────

def _fetch_price_index_outliers(customer_code: str, peer_group: str,
                                start: date, end: date,
                                limit: int = 15) -> list[dict]:
    if not peer_group:
        return []
    rows = db.session.execute(text(f"""
    WITH peerset AS (
      SELECT customer_code_365 FROM ps_customers
      WHERE reporting_group = :grp AND customer_code_365 <> :c
    ),
    base_gross AS (
      SELECT h.customer_code_365, l.item_code_365,
             SUM(CASE WHEN {RETURN_PREDICATE} THEN 0
                      ELSE GREATEST(COALESCE(l.quantity,0),0) END) AS qty_gross,
             SUM(CASE WHEN {RETURN_PREDICATE} THEN 0
                      ELSE GREATEST(COALESCE(l.line_total_excl,0),0) END) AS sales_gross
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN :s AND :e
      GROUP BY h.customer_code_365, l.item_code_365
    ),
    cust_item AS (
      SELECT item_code_365, SUM(qty_gross) AS qty, SUM(sales_gross) AS sales
      FROM base_gross WHERE customer_code_365 = :c GROUP BY item_code_365 HAVING SUM(qty_gross) > 0
    ),
    peer_item AS (
      SELECT item_code_365, SUM(qty_gross) AS qty, SUM(sales_gross) AS sales
      FROM base_gross WHERE customer_code_365 IN (SELECT customer_code_365 FROM peerset)
      GROUP BY item_code_365 HAVING SUM(qty_gross) > 0
    )
    SELECT c.item_code_365 AS item_code,
           i.item_name,
           (c.sales / NULLIF(c.qty,0)) AS cust_unit_price,
           (p.sales / NULLIF(p.qty,0)) AS peer_unit_price,
           (c.sales / NULLIF(c.qty,0)) / NULLIF((p.sales / NULLIF(p.qty,0)),0) AS price_index,
           c.qty AS cust_qty
    FROM cust_item c
    JOIN peer_item p ON p.item_code_365 = c.item_code_365
    LEFT JOIN ps_items_dw i ON i.item_code_365 = c.item_code_365
    ORDER BY ABS(((c.sales / NULLIF(c.qty,0)) /
                  NULLIF((p.sales / NULLIF(p.qty,0)),0)) - 1) DESC NULLS LAST
    LIMIT :lim
    """), {"c": customer_code, "grp": peer_group, "s": start, "e": end,
           "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


# ─── Activity timeline (cockpit-brief §11.7) ───────────────────────────

def _fetch_activity_timeline(customer_code: str, magento_customer_id: str | None,
                             max_events: int = 20) -> list[dict]:
    """Merge recent events from multiple sources into one chronological
    feed (last 14 days, max 20 events). Sources missing at runtime are
    silently omitted (ASSUMPTION-044)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    events: list[dict] = []

    # Login (single most-recent value per customer).
    try:
        row = db.session.execute(text("""
            SELECT last_login_at FROM magento_customer_last_login_current
            WHERE customer_code_365 = :c
        """), {"c": customer_code}).first()
        if row and row[0] and row[0] >= cutoff:
            events.append({"when": row[0].isoformat(), "type": "login",
                           "actor_username": None, "actor_display_name": None,
                           "summary": "Magento login"})
    except Exception:
        pass

    # Invoices (last 14 days).
    try:
        rows = db.session.execute(text("""
            SELECT h.invoice_no_365 AS no, h.invoice_date_utc0 AS d,
                   COALESCE(SUM(l.line_total_excl), 0) AS amt
            FROM dw_invoice_header h
            JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
            WHERE h.customer_code_365 = :c
              AND h.invoice_date_utc0 >= :s
            GROUP BY h.invoice_no_365, h.invoice_date_utc0
            ORDER BY h.invoice_date_utc0 DESC
            LIMIT :lim
        """), {"c": customer_code, "s": cutoff, "lim": max_events}).mappings().all()
        for r in rows:
            events.append({"when": r["d"].isoformat() if r["d"] else None,
                           "type": "invoice",
                           "actor_username": None, "actor_display_name": None,
                           "summary": f"Invoice {r['no']} · €{float(r['amt'] or 0):,.0f}"})
    except Exception:
        pass

    # SMS log.
    try:
        rows = db.session.execute(text("""
            SELECT created_at, created_by_username, message_text, provider_status
            FROM sms_log
            WHERE customer_code_365 = :c AND created_at >= :s
            ORDER BY created_at DESC LIMIT :lim
        """), {"c": customer_code, "s": cutoff, "lim": max_events}).mappings().all()
        for r in rows:
            who = _resolve_display_name(r["created_by_username"])
            msg = (r["message_text"] or "")[:80]
            events.append({"when": r["created_at"].isoformat() if r["created_at"] else None,
                           "type": "sms",
                           "actor_username": r["created_by_username"],
                           "actor_display_name": who,
                           "summary": f"SMS [{r['provider_status']}] {msg}"})
    except Exception:
        pass

    # Cart updates (use last_synced_at).
    try:
        row = db.session.execute(text("""
            SELECT last_synced_at, abandoned_cart_amount, has_abandoned_cart
            FROM crm_abandoned_cart_state WHERE customer_code_365 = :c
        """), {"c": customer_code}).first()
        if row and row[0] and row[0] >= cutoff and row[2]:
            events.append({"when": row[0].isoformat(), "type": "cart",
                           "actor_username": None, "actor_display_name": None,
                           "summary": f"Live cart €{float(row[1] or 0):,.2f}"})
    except Exception:
        pass

    # Target changes.
    try:
        rows = db.session.execute(text("""
            SELECT h.created_at, h.event, h.actor_username,
                   COALESCE(u.display_name, h.actor_username) AS dn,
                   h.target_annual
            FROM customer_spend_target_history h
            LEFT JOIN users u ON u.username = h.actor_username
            WHERE h.customer_code_365 = :c AND h.created_at >= :s
            ORDER BY h.created_at DESC LIMIT :lim
        """), {"c": customer_code, "s": cutoff, "lim": max_events}).mappings().all()
        for r in rows:
            events.append({"when": r["created_at"].isoformat() if r["created_at"] else None,
                           "type": "target_change",
                           "actor_username": r["actor_username"],
                           "actor_display_name": r["dn"],
                           "summary": f"Target {r['event']}"
                                      + (f" · annual €{r['target_annual']}"
                                         if r["target_annual"] is not None else "")})
    except Exception:
        pass

    events.sort(key=lambda e: e.get("when") or "", reverse=True)
    return events[:max_events]


def _resolve_display_name(username: str | None) -> str | None:
    if not username:
        return None
    try:
        row = db.session.execute(
            text("SELECT display_name FROM users WHERE username = :u"),
            {"u": username},
        ).first()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return username


# ─── Data freshness ────────────────────────────────────────────────────

def _fetch_data_freshness() -> dict:
    out = {"sales_last_synced_at": None,
           "offers_last_synced_at": None,
           "cart_last_synced_at": None}
    try:
        out["sales_last_synced_at"] = db.session.execute(
            text("SELECT MAX(invoice_date_utc0) FROM dw_invoice_header")
        ).scalar()
    except Exception:
        pass
    try:
        out["offers_last_synced_at"] = db.session.execute(
            text("SELECT MAX(snapshot_at) FROM crm_customer_offer_current")
        ).scalar()
    except Exception:
        pass
    try:
        out["cart_last_synced_at"] = db.session.execute(
            text("SELECT MAX(last_synced_at) FROM crm_abandoned_cart_state")
        ).scalar()
    except Exception:
        pass
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in out.items()}
    return out


# ─── peer group resolution ─────────────────────────────────────────────

def _resolve_peer_codes(customer_code: str, reporting_group: str | None,
                        peer_group: str | None) -> tuple[str, list[str]]:
    """Return (resolved_group_name, peer_customer_codes). Empty list when
    no peers can be found."""
    grp = (peer_group or "").strip()
    if grp in ("", "auto"):
        grp = (reporting_group or "").strip()
    if not grp:
        return "", []
    rows = db.session.execute(text("""
        SELECT customer_code_365 FROM ps_customers
        WHERE reporting_group = :g AND customer_code_365 <> :c
          AND deleted_at IS NULL
    """), {"g": grp, "c": customer_code}).fetchall()
    return grp, [r[0] for r in rows if r and r[0]]


# ─── Main entry ────────────────────────────────────────────────────────

def get_cockpit_data(customer_code: str, *, period_days: int = 90,
                     compare: str = "py",
                     peer_group: str | None = None) -> dict:
    """Assemble the full cockpit payload (cockpit-brief §11.1)."""
    key = (customer_code, period_days, compare, peer_group or "")
    cached = _CACHE.get(key)
    if cached is not None:
        # Refresh the never-cache slices on every request.
        live = dict(cached)
        live["target"] = _fresh_target_block(customer_code)
        lc = fetch_live_cart(customer_code)
        live["kpis"] = dict(live["kpis"])
        live["kpis"]["live_cart"] = lc
        if "header" in live:
            live["header"] = dict(live["header"])
            live["header"]["live_cart_amount"] = lc["amount"]
            live["header"]["live_cart_age_minutes"] = lc["age_minutes"]
        return live

    payload = _build_cockpit_payload(customer_code, period_days, compare, peer_group)
    _CACHE[key] = payload

    # Same overlay as the cache-hit path so the very first response after
    # a target change reflects the latest target without an extra request.
    fresh = dict(payload)
    fresh["target"] = _fresh_target_block(customer_code)
    return fresh


def _fresh_target_block(customer_code: str) -> dict:
    state = get_target(customer_code)
    return {
        "state": state,
        "achievement": {p: compute_achievement(customer_code, p)
                        for p in ("mtd", "qtd", "ytd", "weekly_average")},
    }


def _build_cockpit_payload(customer_code: str, period_days: int,
                           compare: str, peer_group: str | None) -> dict:
    start, end = _resolve_period(period_days)
    cmp_range = _resolve_compare(start, end, compare)
    prev_start = prev_end = None
    if cmp_range:
        prev_start, prev_end = cmp_range

    header = _fetch_header(customer_code)
    grp_resolved, peer_codes = _resolve_peer_codes(
        customer_code, header.get("reporting_group"), peer_group)

    cur = _safe(lambda: _fetch_sales_gp(customer_code, start, end),
                {"sales": 0.0, "gp": 0.0, "gm_pct": None}, "sales_gp")
    prev_block = (_safe(lambda: _fetch_sales_gp(customer_code, prev_start, prev_end),
                        {"sales": None, "gp": None, "gm_pct": None}, "sales_gp_prev")
                  if prev_start and prev_end else
                  {"sales": None, "gp": None, "gm_pct": None})

    sparkline = _safe(lambda: _monthly_sparkline(customer_code, end), [], "sparkline")

    sales_kpi = {
        "value": round(cur["sales"], 2),
        "delta_abs": _delta(cur["sales"], prev_block["sales"])["abs"],
        "delta_pct": _delta(cur["sales"], prev_block["sales"])["pct"],
        "sparkline": sparkline,
    }
    gp_kpi = {
        "value": round(cur["gp"], 2),
        "gm_pct": round(cur["gm_pct"], 1) if cur["gm_pct"] is not None else None,
        "delta_abs": _delta(cur["gp"], prev_block["gp"])["abs"],
        "delta_pct": _delta(cur["gp"], prev_block["gp"])["pct"],
        "gm_delta_pts": (round(cur["gm_pct"] - prev_block["gm_pct"], 1)
                          if cur["gm_pct"] is not None
                          and prev_block["gm_pct"] is not None else None),
        "sparkline": [round(v, 2) for v in sparkline],  # same series, used for GP visual cue
    }

    engagement = _safe(lambda: _compute_engagement_score(customer_code, header),
                       {"score": 0, "components": {}}, "engagement")
    live_cart = _safe(lambda: fetch_live_cart(customer_code),
                      {"amount": 0.0, "sku_count": 0, "age_minutes": None,
                       "is_addon": False, "magento_customer_id": None}, "live_cart")
    open_orders = _safe(lambda: _fetch_open_orders(customer_code),
                        {"amount": 0.0, "count": 0}, "open_orders")

    header["live_cart_amount"] = live_cart["amount"]
    header["live_cart_age_minutes"] = live_cart["age_minutes"]

    trend_monthly = _safe(lambda: _fetch_trend_monthly(customer_code, peer_codes, end, months=12),
                          [], "trend_monthly")
    pvm = _safe(lambda: _compute_pvm(customer_code, start, end, prev_start, prev_end),
                {"price": 0, "volume": 0, "mix": 0,
                 "sales_prev": 0, "sales_curr": 0, "sales_delta_check_pct": None},
                "pvm")

    top_gp = _safe(lambda: _fetch_top_items(customer_code, start, end, "gp", limit=15),
                   [], "top_items_gp")
    top_rev = _safe(lambda: _fetch_top_items(customer_code, start, end, "sales", limit=15),
                    [], "top_items_revenue")
    cat_mix = _safe(lambda: _fetch_category_mix_vs_peers(customer_code, grp_resolved, start, end),
                    [], "category_mix")

    active_offers = _safe(lambda: _fetch_active_offers(customer_code),
                          {"lines": [], "summary": {}}, "active_offers")
    offer_opps = _safe(lambda: get_offer_opportunities(customer_code, limit=10),
                       [], "offer_opportunities")

    white_space = _safe(lambda: _fetch_white_space(customer_code, grp_resolved, start, end),
                        [], "white_space")
    lapsed = (_safe(lambda: _fetch_lapsed_items(customer_code, grp_resolved, start, end,
                                                prev_start, prev_end),
                    [], "lapsed_items")
              if prev_start and prev_end else [])
    cross_sell = _safe(lambda: _fetch_cross_sell(customer_code, grp_resolved, start, end),
                       [], "cross_sell")

    churn = _safe(lambda: _fetch_churn_risk_by_category(customer_code), [], "churn_risk")
    price_outliers = _safe(lambda: _fetch_price_index_outliers(customer_code, grp_resolved,
                                                               start, end),
                           [], "price_outliers")
    timeline = _safe(lambda: _fetch_activity_timeline(customer_code,
                                                     header.get("magento_customer_id")),
                     [], "activity_timeline")
    freshness = _safe(lambda: _fetch_data_freshness(), {}, "data_freshness")
    login_behaviour = _safe(
        lambda: _fetch_login_behaviour(customer_code, lookback_days=90),
        None,
        "login_behaviour",
    )

    payload = {
        "header": header,
        "controls": {
            "period_days": period_days,
            "compare": compare,
            "peer_group": peer_group or "auto",
            "peer_group_resolved": grp_resolved,
            "peer_customers": len(peer_codes),
            "period_from": str(start), "period_to": str(end),
            "compare_from": str(prev_start) if prev_start else None,
            "compare_to": str(prev_end) if prev_end else None,
        },
        "target": _fresh_target_block(customer_code),
        "kpis": {
            "sales": sales_kpi,
            "gross_profit": gp_kpi,
            "engagement_score": engagement,
            "live_cart": live_cart,
            "open_orders": open_orders,
        },
        "trend": {"monthly": trend_monthly},
        "pvm": pvm,
        "top_items_by_gp": top_gp,
        "top_items_by_revenue": top_rev,
        "category_mix_vs_peers": cat_mix,
        "active_offers": active_offers,
        "offer_opportunities": offer_opps,
        "white_space": white_space,
        "lapsed_items": lapsed,
        "cross_sell": cross_sell,
        "churn_risk_by_category": churn,
        "price_index_outliers": price_outliers,
        "activity_timeline": timeline,
        "data_freshness": freshness,
        "recommended_actions": [],  # Ticket 3.
        "login_behaviour": login_behaviour,
    }
    return payload


# ── Fleet-level login analytics ──────────────────────────────────────────────

def get_login_insights_fleet(top_n: int = 500) -> dict:
    """Fleet-level login analytics for /cockpit/login-insights.

    Returns dict with:
      most_engaged       — top 20 by logins in last 30 days
      at_risk            — customers whose login freq dropped 30%+ vs prior 30d
      dow_heatmap        — aggregated {day_of_week, time_bucket, count} for last 90d
      dormant_with_sales — no login 14+d but invoiced in last 60d
      generated_at       — UTC datetime
    """
    now = datetime.now(timezone.utc)
    cutoff_30d  = now - timedelta(days=30)
    cutoff_60d  = now - timedelta(days=60)
    cutoff_90d  = now - timedelta(days=90)

    # Ensure index exists once per process lifetime (harmless if already present)
    try:
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_login_log_customer_time
              ON magento_customer_login_log (customer_code_365, last_login_at DESC)
        """))
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    # ── Most engaged: top 20 by logins in last 30 days ────────────────────
    engaged_rows = db.session.execute(text("""
        SELECT
            l.customer_code_365,
            COALESCE(c.company_name, l.customer_code_365) AS company_name,
            COUNT(*) AS logins_last_30d,
            MAX(l.last_login_at) AS last_login_at,
            COUNT(*) FILTER (
                WHERE l.last_login_at >= :c60 AND l.last_login_at < :c30
            ) AS logins_prev_30d
        FROM magento_customer_login_log l
        LEFT JOIN ps_customers c ON c.customer_code_365 = l.customer_code_365
        WHERE l.last_login_at >= :c30
        GROUP BY l.customer_code_365, c.company_name
        ORDER BY logins_last_30d DESC
        LIMIT 20
    """), {"c30": cutoff_30d, "c60": cutoff_60d}).mappings().all()

    most_engaged = []
    for r in engaged_rows:
        prev = int(r["logins_prev_30d"] or 0)
        last = int(r["logins_last_30d"] or 0)
        trend = "→"
        if prev > 0:
            pct = (last - prev) / prev * 100
            trend = "↑" if pct > 20 else ("↓" if pct < -20 else "→")
        most_engaged.append({
            "customer_code": r["customer_code_365"],
            "company_name": r["company_name"],
            "logins_last_30d": last,
            "logins_prev_30d": prev,
            "last_login_at": r["last_login_at"],
            "trend": trend,
        })

    # ── At-risk: login frequency dropped 30%+ vs prior 30-day window ──────
    at_risk_rows = db.session.execute(text("""
        WITH counts AS (
            SELECT
                l.customer_code_365,
                COUNT(*) FILTER (WHERE l.last_login_at >= :c30)                         AS last_30d,
                COUNT(*) FILTER (WHERE l.last_login_at >= :c60 AND l.last_login_at < :c30) AS prev_30d,
                MAX(l.last_login_at)                                                     AS last_login_at
            FROM magento_customer_login_log l
            WHERE l.last_login_at >= :c60
            GROUP BY l.customer_code_365
        )
        SELECT
            c2.customer_code_365,
            COALESCE(ps.company_name, c2.customer_code_365) AS company_name,
            c2.last_30d,
            c2.prev_30d,
            c2.last_login_at,
            ROUND(
                (c2.last_30d::numeric - c2.prev_30d) / NULLIF(c2.prev_30d, 0) * 100,
                1
            ) AS drop_pct
        FROM counts c2
        LEFT JOIN ps_customers ps ON ps.customer_code_365 = c2.customer_code_365
        WHERE c2.prev_30d > 5
          AND c2.last_30d < c2.prev_30d * 0.7
        ORDER BY (c2.prev_30d - c2.last_30d) DESC
        LIMIT 20
    """), {"c30": cutoff_30d, "c60": cutoff_60d}).mappings().all()

    at_risk = [dict(r) for r in at_risk_rows]

    # ── DOW heatmap: all customers, last 90 days ───────────────────────────
    dow_rows = db.session.execute(text("""
        SELECT
            ((EXTRACT(DOW FROM last_login_at AT TIME ZONE 'Europe/Athens'))::int + 6) % 7
                AS dow,
            CASE
                WHEN EXTRACT(HOUR FROM last_login_at AT TIME ZONE 'Europe/Athens') < 6  THEN 'evening'
                WHEN EXTRACT(HOUR FROM last_login_at AT TIME ZONE 'Europe/Athens') < 12 THEN 'morning'
                WHEN EXTRACT(HOUR FROM last_login_at AT TIME ZONE 'Europe/Athens') < 18 THEN 'afternoon'
                ELSE 'evening'
            END AS time_bucket,
            COUNT(*) AS cnt
        FROM magento_customer_login_log
        WHERE last_login_at >= :c90
        GROUP BY dow, time_bucket
    """), {"c90": cutoff_90d}).mappings().all()

    dow_heatmap = [
        {"day_of_week": int(r["dow"]), "time_bucket": r["time_bucket"],
         "count": int(r["cnt"])}
        for r in dow_rows
    ]

    # ── Dormant but recently bought ────────────────────────────────────────
    # "Dormant" = no login for 14+ days; "recently bought" = invoices in last 60d.
    # Uses the `invoices` table (customer_code_365, upload_date as VARCHAR 'YYYY-MM-DD').
    dormant_rows = db.session.execute(text("""
        SELECT
            l.customer_code_365,
            COALESCE(ps.company_name, l.customer_code_365) AS company_name,
            l.last_login_at,
            SUM(i.total_grand)  AS recent_invoice_value,
            MAX(i.upload_date)  AS last_invoice_date
        FROM magento_customer_last_login_current l
        JOIN ps_customers ps ON ps.customer_code_365 = l.customer_code_365
        JOIN invoices i ON i.customer_code_365 = l.customer_code_365
        WHERE (l.last_login_at IS NULL
               OR l.last_login_at < NOW() - INTERVAL '14 days')
          AND i.upload_date >= to_char(NOW() - INTERVAL '60 days', 'YYYY-MM-DD')
        GROUP BY l.customer_code_365, ps.company_name, l.last_login_at
        ORDER BY recent_invoice_value DESC
        LIMIT 30
    """)).mappings().all()

    dormant_with_sales = [dict(r) for r in dormant_rows]

    logger.info(
        "login_insights_fleet: %d engaged, %d at_risk, %d dormant_with_sales",
        len(most_engaged), len(at_risk), len(dormant_with_sales),
    )
    return {
        "most_engaged": most_engaged,
        "at_risk": at_risk,
        "dow_heatmap": dow_heatmap,
        "dormant_with_sales": dormant_with_sales,
        "generated_at": now,
    }
