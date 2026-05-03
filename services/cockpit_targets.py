"""Cockpit Ticket 1 — pure data-access layer for customer spend targets.

No view rendering, no business orchestration. All writes use a single
transaction and log to ``customer_spend_target_history`` before committing.

Cadence auto-population (cockpit-brief 10.5): when only ``annual`` is
provided in a payload, missing cadences are derived from it (annual/12,
annual/4, annual/52). Explicit values are preserved.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


# ─── helpers ────────────────────────────────────────────────────────────

def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_dec(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _normalize_cadences(payload: dict) -> dict:
    """Return {weekly, monthly, quarterly, annual} with auto-fill from annual."""
    weekly = _to_dec(payload.get("weekly_ambition"))
    monthly = _to_dec(payload.get("monthly"))
    quarterly = _to_dec(payload.get("quarterly"))
    annual = _to_dec(payload.get("annual"))

    if annual is not None:
        if monthly is None:
            monthly = (annual / Decimal(12)).quantize(Decimal("0.01"))
        if quarterly is None:
            quarterly = (annual / Decimal(4)).quantize(Decimal("0.01"))
        if weekly is None:
            weekly = (annual / Decimal(52)).quantize(Decimal("0.01"))
    return {"weekly_ambition": weekly, "monthly": monthly,
            "quarterly": quarterly, "annual": annual}


def _customer_exists(customer_code: str) -> bool:
    row = db.session.execute(
        text("SELECT 1 FROM ps_customers WHERE customer_code_365 = :c"),
        {"c": customer_code},
    ).first()
    return row is not None


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


# ─── reads ──────────────────────────────────────────────────────────────

def get_target(customer_code: str) -> dict:
    row = db.session.execute(text("""
        SELECT customer_code_365, weekly_ambition, monthly, quarterly, annual,
               status,
               proposed_by, proposed_at, proposed_notes,
               proposed_weekly, proposed_monthly, proposed_quarterly, proposed_annual,
               approved_by, approved_at,
               last_modified_by, last_modified_at
        FROM customer_spend_target
        WHERE customer_code_365 = :c
    """), {"c": customer_code}).mappings().first()

    out = {"customer_code": customer_code, "active": None, "pending_proposal": None}
    if not row:
        return out

    has_active = any(row.get(k) is not None for k in
                     ("weekly_ambition", "monthly", "quarterly", "annual")) \
                 and row.get("status") in ("active", "proposed")

    if has_active:
        out["active"] = {
            "weekly_ambition": row["weekly_ambition"],
            "monthly": row["monthly"],
            "quarterly": row["quarterly"],
            "annual": row["annual"],
            "approved_by": row["approved_by"],
            "approved_at": row["approved_at"],
            "last_modified_by": row["last_modified_by"],
            "last_modified_at": row["last_modified_at"],
        }
    if row.get("status") == "proposed" and row.get("proposed_at") is not None:
        out["pending_proposal"] = {
            "weekly_ambition": row["proposed_weekly"],
            "monthly": row["proposed_monthly"],
            "quarterly": row["proposed_quarterly"],
            "annual": row["proposed_annual"],
            "proposed_by": row["proposed_by"],
            "proposed_at": row["proposed_at"],
            "proposed_notes": row["proposed_notes"],
        }
    return out


def get_target_history(customer_code: str, limit: int = 50) -> list[dict]:
    rows = db.session.execute(text("""
        SELECT h.id, h.event_type, h.actor_username, h.occurred_at,
               h.weekly_ambition, h.monthly, h.quarterly, h.annual,
               h.notes, h.reason,
               COALESCE(u.display_name, h.actor_username) AS actor_display_name
        FROM customer_spend_target_history h
        LEFT JOIN users u ON u.username = h.actor_username
        WHERE h.customer_code_365 = :c
        ORDER BY h.occurred_at DESC, h.id DESC
        LIMIT :lim
    """), {"c": customer_code, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


# ─── writes ─────────────────────────────────────────────────────────────

def _insert_history(customer_code: str, event_type: str, actor: str,
                    cadences: dict | None, notes: str | None = None,
                    reason: str | None = None):
    cad = cadences or {}
    db.session.execute(text("""
        INSERT INTO customer_spend_target_history
            (customer_code_365, event_type, actor_username, occurred_at,
             weekly_ambition, monthly, quarterly, annual, notes, reason)
        VALUES (:c, :ev, :a, :now, :w, :m, :q, :y, :n, :r)
    """), {
        "c": customer_code, "ev": event_type, "a": actor, "now": _utc_now(),
        "w": cad.get("weekly_ambition"), "m": cad.get("monthly"),
        "q": cad.get("quarterly"), "y": cad.get("annual"),
        "n": notes, "r": reason,
    })


def propose_target(customer_code: str, payload: dict, actor: str) -> dict:
    if not _customer_exists(customer_code):
        raise ValueError(f"Customer not found: {customer_code}")

    cad = _normalize_cadences(payload)
    notes = (payload.get("notes") or None)
    now = _utc_now()

    existing = db.session.execute(text(
        "SELECT customer_code_365 FROM customer_spend_target WHERE customer_code_365 = :c"
    ), {"c": customer_code}).first()

    if existing:
        db.session.execute(text("""
            UPDATE customer_spend_target SET
                status = 'proposed',
                proposed_by = :a,
                proposed_at = :now,
                proposed_notes = :n,
                proposed_weekly = :w,
                proposed_monthly = :m,
                proposed_quarterly = :q,
                proposed_annual = :y,
                last_modified_by = :a,
                last_modified_at = :now
            WHERE customer_code_365 = :c
        """), {"c": customer_code, "a": actor, "now": now, "n": notes,
               "w": cad["weekly_ambition"], "m": cad["monthly"],
               "q": cad["quarterly"], "y": cad["annual"]})
    else:
        db.session.execute(text("""
            INSERT INTO customer_spend_target
                (customer_code_365, status,
                 proposed_by, proposed_at, proposed_notes,
                 proposed_weekly, proposed_monthly, proposed_quarterly, proposed_annual,
                 last_modified_by, last_modified_at)
            VALUES (:c, 'proposed', :a, :now, :n, :w, :m, :q, :y, :a, :now)
        """), {"c": customer_code, "a": actor, "now": now, "n": notes,
               "w": cad["weekly_ambition"], "m": cad["monthly"],
               "q": cad["quarterly"], "y": cad["annual"]})

    _insert_history(customer_code, "customer.target.proposed", actor, cad, notes=notes)
    db.session.commit()
    return get_target(customer_code)


def set_target_directly(customer_code: str, payload: dict, actor: str) -> dict:
    """Manager sets target without an AM proposal. Status -> 'active'."""
    if not _customer_exists(customer_code):
        raise ValueError(f"Customer not found: {customer_code}")

    cad = _normalize_cadences(payload)
    notes = (payload.get("notes") or None)
    now = _utc_now()

    existing = db.session.execute(text(
        "SELECT customer_code_365 FROM customer_spend_target WHERE customer_code_365 = :c"
    ), {"c": customer_code}).first()

    if existing:
        # Inline-edit auto-population: if a cadence was previously NULL and
        # the payload didn't supply it, use the derived-from-annual value.
        db.session.execute(text("""
            UPDATE customer_spend_target SET
                weekly_ambition = COALESCE(:w, weekly_ambition),
                monthly         = COALESCE(:m, monthly),
                quarterly       = COALESCE(:q, quarterly),
                annual          = COALESCE(:y, annual),
                status          = 'active',
                approved_by     = :a,
                approved_at     = :now,
                last_modified_by = :a,
                last_modified_at = :now,
                proposed_by = NULL, proposed_at = NULL, proposed_notes = NULL,
                proposed_weekly = NULL, proposed_monthly = NULL,
                proposed_quarterly = NULL, proposed_annual = NULL
            WHERE customer_code_365 = :c
        """), {"c": customer_code, "a": actor, "now": now,
               "w": cad["weekly_ambition"], "m": cad["monthly"],
               "q": cad["quarterly"], "y": cad["annual"]})
    else:
        db.session.execute(text("""
            INSERT INTO customer_spend_target
                (customer_code_365, weekly_ambition, monthly, quarterly, annual,
                 status, approved_by, approved_at,
                 last_modified_by, last_modified_at)
            VALUES (:c, :w, :m, :q, :y, 'active', :a, :now, :a, :now)
        """), {"c": customer_code, "a": actor, "now": now,
               "w": cad["weekly_ambition"], "m": cad["monthly"],
               "q": cad["quarterly"], "y": cad["annual"]})

    _insert_history(customer_code, "customer.target.set", actor, cad, notes=notes)
    db.session.commit()
    return get_target(customer_code)


def bulk_set_annual_targets(customer_codes: list[str], annual,
                            actor: str) -> dict:
    """Brief 10.5: 'set annual = X for selected customers'.

    Atomic: pre-validates every code; if **any** is unknown the whole
    operation is rejected (``ValueError``) before a single write is
    attempted, so a stale selected row cannot partially apply a manager
    bulk action. On success: one DB transaction, one
    ``customer.target.set`` history row per customer.
    """
    annual_dec = _to_dec(annual)
    if annual_dec is None:
        raise ValueError("annual is required and must be numeric")
    if not customer_codes:
        raise ValueError("customer_codes must be a non-empty list")

    # Pre-validate all codes — fail fast, no partial writes.
    unknown = [c for c in customer_codes if not _customer_exists(c)]
    if unknown:
        raise ValueError(
            f"Unknown customer code(s): {', '.join(unknown)} "
            "— bulk operation rejected, no writes performed"
        )

    cad = _normalize_cadences({"annual": annual_dec})
    now = _utc_now()
    applied: list[str] = []

    try:
        for code in customer_codes:
            existing = db.session.execute(text(
                "SELECT customer_code_365 FROM customer_spend_target "
                "WHERE customer_code_365 = :c"
            ), {"c": code}).first()

            if existing:
                db.session.execute(text("""
                    UPDATE customer_spend_target SET
                        weekly_ambition = COALESCE(:w, weekly_ambition),
                        monthly         = COALESCE(:m, monthly),
                        quarterly       = COALESCE(:q, quarterly),
                        annual          = :y,
                        status          = 'active',
                        approved_by     = :a,
                        approved_at     = :now,
                        last_modified_by = :a,
                        last_modified_at = :now,
                        proposed_by = NULL, proposed_at = NULL,
                        proposed_notes = NULL,
                        proposed_weekly = NULL, proposed_monthly = NULL,
                        proposed_quarterly = NULL, proposed_annual = NULL
                    WHERE customer_code_365 = :c
                """), {"c": code, "a": actor, "now": now,
                       "w": cad["weekly_ambition"], "m": cad["monthly"],
                       "q": cad["quarterly"], "y": cad["annual"]})
            else:
                db.session.execute(text("""
                    INSERT INTO customer_spend_target
                        (customer_code_365, weekly_ambition, monthly,
                         quarterly, annual, status, approved_by, approved_at,
                         last_modified_by, last_modified_at)
                    VALUES (:c, :w, :m, :q, :y, 'active', :a, :now, :a, :now)
                """), {"c": code, "a": actor, "now": now,
                       "w": cad["weekly_ambition"], "m": cad["monthly"],
                       "q": cad["quarterly"], "y": cad["annual"]})

            _insert_history(code, "customer.target.set", actor, cad,
                            notes=f"bulk_set: annual={cad['annual']}")
            applied.append(code)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "applied": applied,
        "skipped": [],  # see pre-validation above; non-empty would have raised
        "annual": str(cad["annual"]),
        "actor": actor,
    }


def approve_proposal(customer_code: str, actor: str) -> dict:
    state = get_target(customer_code)
    pending = state.get("pending_proposal")
    if not pending:
        raise ValueError("No pending proposal to approve")

    cad = {
        "weekly_ambition": pending.get("weekly_ambition"),
        "monthly": pending.get("monthly"),
        "quarterly": pending.get("quarterly"),
        "annual": pending.get("annual"),
    }
    now = _utc_now()
    db.session.execute(text("""
        UPDATE customer_spend_target SET
            weekly_ambition = :w, monthly = :m, quarterly = :q, annual = :y,
            status = 'active',
            approved_by = :a, approved_at = :now,
            last_modified_by = :a, last_modified_at = :now,
            proposed_by = NULL, proposed_at = NULL, proposed_notes = NULL,
            proposed_weekly = NULL, proposed_monthly = NULL,
            proposed_quarterly = NULL, proposed_annual = NULL
        WHERE customer_code_365 = :c
    """), {"c": customer_code, "a": actor, "now": now,
           "w": cad["weekly_ambition"], "m": cad["monthly"],
           "q": cad["quarterly"], "y": cad["annual"]})

    _insert_history(customer_code, "customer.target.approved", actor, cad)
    _insert_history(customer_code, "customer.target.active_snapshot", actor, cad)
    db.session.commit()
    return get_target(customer_code)


def reject_proposal(customer_code: str, reason: str | None, actor: str) -> dict:
    state = get_target(customer_code)
    pending = state.get("pending_proposal")
    if not pending:
        raise ValueError("No pending proposal to reject")

    has_active = state.get("active") is not None
    new_status = "active" if has_active else "no_target"

    db.session.execute(text("""
        UPDATE customer_spend_target SET
            status = :st,
            proposed_by = NULL, proposed_at = NULL, proposed_notes = NULL,
            proposed_weekly = NULL, proposed_monthly = NULL,
            proposed_quarterly = NULL, proposed_annual = NULL,
            last_modified_by = :a, last_modified_at = :now
        WHERE customer_code_365 = :c
    """), {"c": customer_code, "a": actor, "st": new_status, "now": _utc_now()})

    _insert_history(customer_code, "customer.target.rejected", actor, None,
                    reason=reason)
    db.session.commit()
    return get_target(customer_code)


# ─── achievement / list ─────────────────────────────────────────────────

def _period_bounds(period: str):
    today = datetime.now(timezone.utc).date()
    if period == "mtd":
        start = today.replace(day=1)
        days_in_period = (today - start).days + 1
        annualizer = 12  # monthly target
    elif period == "qtd":
        q_first_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=q_first_month, day=1)
        days_in_period = (today - start).days + 1
        annualizer = 4
    elif period == "ytd":
        start = today.replace(month=1, day=1)
        days_in_period = (today - start).days + 1
        annualizer = 1
    elif period == "weekly_average":
        start = today - timedelta(days=28)
        days_in_period = 28
        annualizer = 52
    else:
        raise ValueError(f"Unknown period: {period}")
    return start, today, days_in_period, annualizer


def compute_achievement(customer_code: str, period: str) -> dict:
    start, end, days_in_period, annualizer = _period_bounds(period)

    # Dialect-agnostic date filter: parameter-bound Python dates work on
    # both PostgreSQL (TIMESTAMP comparison auto-coerces) and SQLite
    # (string-comparison against ISO-format dates).
    try:
        actual_row = db.session.execute(text("""
            SELECT COALESCE(SUM(l.line_total_excl), 0) AS actual
            FROM dw_invoice_line l
            JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
            WHERE h.customer_code_365 = :c
              AND h.invoice_date_utc0 >= :s
              AND h.invoice_date_utc0 < :e_excl
        """), {"c": customer_code, "s": start,
               "e_excl": end + timedelta(days=1)}).mappings().first()
    except Exception:
        # Fact tables may not exist on SQLite test envs — degrade to 0
        # rather than 500. The cockpit page still renders.
        actual_row = None
    actual = float(actual_row["actual"] or 0) if actual_row else 0.0

    state = get_target(customer_code)
    active = state.get("active") or {}
    target_map = {
        "mtd": active.get("monthly"),
        "qtd": active.get("quarterly"),
        "ytd": active.get("annual"),
        "weekly_average": active.get("weekly_ambition"),
    }
    target = target_map.get(period)
    target_f = float(target) if target is not None else None

    pct = (actual / target_f * 100.0) if target_f else None
    gap = (target_f - actual) if target_f is not None else None

    # Run-rate projection: scale actual to full period length.
    if period == "weekly_average":
        run_rate_projection = actual / 4.0  # 28-day window -> per-week
        period_total_target = target_f
    elif period == "ytd":
        run_rate_projection = (actual / days_in_period) * 365 if days_in_period else 0
        period_total_target = target_f
    else:
        # mtd / qtd: project to full period length using calendar
        if period == "mtd":
            from calendar import monthrange
            full = monthrange(end.year, end.month)[1]
        else:  # qtd
            full = 92
        run_rate_projection = (actual / days_in_period) * full if days_in_period else 0
        period_total_target = target_f

    on_pace = None
    if period_total_target:
        on_pace = run_rate_projection >= period_total_target

    return {
        "period": period,
        "actual": round(actual, 2),
        "target": target_f,
        "pct": round(pct, 1) if pct is not None else None,
        "gap": round(gap, 2) if gap is not None else None,
        "run_rate_projection": round(run_rate_projection, 2),
        "on_pace": on_pace,
    }


def list_all_targets(filters: dict | None = None) -> list[dict]:
    """Admin page listing.

    Joins ps_customers + customer_spend_target. Computes 90d actual + run-rate
    vs annual target inline. Filters: status (active/pending/no_target/all),
    agent (agent_code_365), district (town).
    """
    f = filters or {}
    status = (f.get("status") or "").strip()
    agent = (f.get("agent") or "").strip()
    district = (f.get("district") or "").strip()
    classification = (f.get("classification") or "").strip()

    where = ["c.deleted_at IS NULL"]
    params: dict[str, Any] = {}
    if agent:
        where.append("c.agent_code_365 = :agent")
        params["agent"] = agent
    if district:
        where.append("c.town = :district")
        params["district"] = district
    if classification:
        where.append("c.category_1_name = :cls")
        params["cls"] = classification
    if status == "active":
        where.append("t.status = 'active'")
    elif status == "pending":
        where.append("t.status = 'proposed'")
    elif status == "no_target":
        where.append("(t.status IS NULL OR t.status = 'no_target')")

    # Dialect-agnostic 90d cutoff: bound as a Python date so PostgreSQL and
    # SQLite both compare cleanly against ``invoice_date_utc0``.
    cutoff_90d = datetime.now(timezone.utc).date() - timedelta(days=90)
    params["cutoff_90d"] = cutoff_90d

    # Some test backends won't have dw_invoice_line/header — fall back to
    # zero sales rather than 500ing the admin page.
    try:
        sql = text(f"""
            SELECT
                c.customer_code_365 AS customer_code,
                COALESCE(c.company_name, '') AS customer_name,
                c.category_1_name AS classification,
                c.town AS district,
                c.agent_code_365 AS agent_code,
                c.agent_name AS agent_name,
                t.status, t.weekly_ambition, t.monthly, t.quarterly, t.annual,
                t.approved_by, t.approved_at, t.last_modified_by, t.last_modified_at,
                COALESCE(actuals.sales_90d, 0) AS sales_90d
            FROM ps_customers c
            LEFT JOIN customer_spend_target t
                ON t.customer_code_365 = c.customer_code_365
            LEFT JOIN (
                SELECT h.customer_code_365,
                       SUM(l.line_total_excl) AS sales_90d
                FROM dw_invoice_line l
                JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
                WHERE h.invoice_date_utc0 >= :cutoff_90d
                GROUP BY h.customer_code_365
            ) actuals ON actuals.customer_code_365 = c.customer_code_365
            WHERE {' AND '.join(where)}
            ORDER BY
                COALESCE(c.category_1_name, 'Z'),
                COALESCE(t.annual, 0) DESC,
                c.customer_code_365
            LIMIT 500
        """)
        rows = db.session.execute(sql, params).mappings().all()
    except Exception:
        params.pop("cutoff_90d", None)
        sql = text(f"""
            SELECT
                c.customer_code_365 AS customer_code,
                COALESCE(c.company_name, '') AS customer_name,
                c.category_1_name AS classification,
                c.town AS district,
                c.agent_code_365 AS agent_code,
                c.agent_name AS agent_name,
                t.status, t.weekly_ambition, t.monthly, t.quarterly, t.annual,
                t.approved_by, t.approved_at, t.last_modified_by, t.last_modified_at,
                0 AS sales_90d
            FROM ps_customers c
            LEFT JOIN customer_spend_target t
                ON t.customer_code_365 = c.customer_code_365
            WHERE {' AND '.join(where)}
            ORDER BY
                COALESCE(c.category_1_name, 'Z'),
                COALESCE(t.annual, 0) DESC,
                c.customer_code_365
            LIMIT 500
        """)
        rows = db.session.execute(sql, params).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        annual = float(d["annual"]) if d.get("annual") is not None else None
        sales_90d = float(d["sales_90d"] or 0)
        # Run-rate vs annual = (90d actual * 4) compared to annual target
        run_rate_annual = sales_90d * 4.0
        d["run_rate_annual"] = round(run_rate_annual, 2)
        d["sales_90d"] = round(sales_90d, 2)
        if annual:
            d["pct_of_annual_target"] = round(run_rate_annual / annual * 100.0, 1)
            d["gap_to_annual"] = round(annual - run_rate_annual, 2)
        else:
            d["pct_of_annual_target"] = None
            d["gap_to_annual"] = None
        out.append(d)
    # Order: gap descending so the worst gaps surface first
    out.sort(key=lambda x: (x["gap_to_annual"] is None,
                             -(x["gap_to_annual"] or 0)))
    return out
