"""Cockpit Ticket 1 — pure data-access layer for customer spend targets.

No view rendering, no business orchestration. All writes use a single
transaction and log to ``customer_spend_target_history`` (with
``previous_*`` snapshot of the prior values) AND emit an audit event to
``cockpit_audit_log`` (brief §6.7) before committing.

Schema follows cockpit-brief §6.1 column names exactly:
``target_weekly_ambition``, ``target_monthly``, ``target_quarterly``,
``target_annual``; history uses ``event``/``created_at`` with
``previous_*`` columns. The main table has no ``proposed_*`` numeric
columns — pending-proposal values live as the latest ``event='proposed'``
row in the history table.

Cadence behaviour (cockpit-brief §10.5): when the inline-edit endpoint
receives ``annual`` only, missing cadences are derived from it
(annual/12, annual/4, annual/52) **only if those fields were empty on
the existing row**; explicit existing values are preserved.
"""
from __future__ import annotations

import json
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
    """Return {weekly_ambition, monthly, quarterly, annual} with auto-fill
    from annual for fields the payload itself didn't supply. Used for
    *fresh* targets (propose, bulk-set on customers without an existing
    row). For inline-edit see ``_resolve_cadences_for_inline_edit``.
    """
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


def _resolve_cadences_for_inline_edit(payload: dict, existing: dict | None) -> dict:
    """Brief §10.5 inline-edit semantics.

    For each cadence:
      * payload provided an explicit value → use it (manager override)
      * else existing row has a non-null value → preserve it
      * else annual provided → derive from annual
      * else → None
    """
    payload_w = _to_dec(payload.get("weekly_ambition"))
    payload_m = _to_dec(payload.get("monthly"))
    payload_q = _to_dec(payload.get("quarterly"))
    payload_y = _to_dec(payload.get("annual"))

    existing = existing or {}
    e_w = _to_dec(existing.get("target_weekly_ambition"))
    e_m = _to_dec(existing.get("target_monthly"))
    e_q = _to_dec(existing.get("target_quarterly"))
    e_y = _to_dec(existing.get("target_annual"))

    annual = payload_y if payload_y is not None else e_y

    def _resolve(payload_v, existing_v, divisor):
        if payload_v is not None:
            return payload_v
        if existing_v is not None:
            return existing_v
        if annual is not None:
            return (annual / Decimal(divisor)).quantize(Decimal("0.01"))
        return None

    return {
        "weekly_ambition": _resolve(payload_w, e_w, 52),
        "monthly":         _resolve(payload_m, e_m, 12),
        "quarterly":       _resolve(payload_q, e_q, 4),
        "annual":          annual,
    }


def _customer_exists(customer_code: str) -> bool:
    row = db.session.execute(
        text("SELECT 1 FROM ps_customers WHERE customer_code_365 = :c"),
        {"c": customer_code},
    ).first()
    return row is not None


def _get_existing_row(customer_code: str) -> dict | None:
    row = db.session.execute(text("""
        SELECT customer_code_365, target_weekly_ambition, target_monthly,
               target_quarterly, target_annual, status,
               proposed_by, proposed_at, proposed_notes,
               approved_by, approved_at,
               last_modified_by, last_modified_at
        FROM customer_spend_target
        WHERE customer_code_365 = :c
    """), {"c": customer_code}).mappings().first()
    return dict(row) if row else None


def _resolve_display_name(username: str | None) -> str | None:
    """Brief Section 14: 'All user-visible names use display_name, never
    raw usernames.' Falls back to the username itself when no users-row
    exists (e.g. system actors)."""
    if not username:
        return None
    try:
        row = db.session.execute(
            text("SELECT display_name FROM users WHERE username = :u"),
            {"u": username},
        ).first()
    except Exception:
        return username
    if row and row[0]:
        return row[0]
    return username


def _emit_audit(event_name: str, actor: str, customer_code: str | None,
                payload: dict | None):
    """Write to ``cockpit_audit_log`` (brief §6.7).

    Centralised so future migration to the operational batch's audit-event
    API only touches this function (see ASSUMPTION-038).
    """
    db.session.execute(text("""
        INSERT INTO cockpit_audit_log
            (event_name, actor_username, customer_code_365, payload_json, created_at)
        VALUES (:ev, :a, :c, :p, :now)
    """), {
        "ev": event_name, "a": actor, "c": customer_code,
        "p": json.dumps(payload, default=str) if payload is not None else None,
        "now": _utc_now(),
    })


# ─── reads ──────────────────────────────────────────────────────────────

def get_target(customer_code: str) -> dict:
    """Return the active target plus, if applicable, the pending-proposal
    values (which live as the latest ``event='proposed'`` row in the
    history table — brief §6.1 has no proposed_* numeric columns)."""
    row = _get_existing_row(customer_code)

    out = {"customer_code": customer_code, "active": None, "pending_proposal": None}
    if not row:
        return out

    has_active = any(row.get(k) is not None for k in
                     ("target_weekly_ambition", "target_monthly",
                      "target_quarterly", "target_annual")) \
                 and row.get("status") in ("active", "proposed")

    if has_active:
        out["active"] = {
            "weekly_ambition": row["target_weekly_ambition"],
            "monthly":         row["target_monthly"],
            "quarterly":       row["target_quarterly"],
            "annual":          row["target_annual"],
            "approved_by":     row["approved_by"],
            "approved_by_display_name": _resolve_display_name(row["approved_by"]),
            "approved_at":     row["approved_at"],
            "last_modified_by": row["last_modified_by"],
            "last_modified_by_display_name":
                _resolve_display_name(row["last_modified_by"]),
            "last_modified_at": row["last_modified_at"],
        }
    if row.get("status") == "proposed" and row.get("proposed_at") is not None:
        prop = db.session.execute(text("""
            SELECT target_weekly_ambition, target_monthly,
                   target_quarterly, target_annual, notes
            FROM customer_spend_target_history
            WHERE customer_code_365 = :c AND event = 'proposed'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """), {"c": customer_code}).mappings().first()
        if prop:
            out["pending_proposal"] = {
                "weekly_ambition": prop["target_weekly_ambition"],
                "monthly":         prop["target_monthly"],
                "quarterly":       prop["target_quarterly"],
                "annual":          prop["target_annual"],
                "proposed_by":     row["proposed_by"],
                "proposed_by_display_name":
                    _resolve_display_name(row["proposed_by"]),
                "proposed_at":     row["proposed_at"],
                "proposed_notes":  row["proposed_notes"],
            }
    return out


def get_target_history(customer_code: str, limit: int = 50) -> list[dict]:
    rows = db.session.execute(text("""
        SELECT h.id, h.event, h.actor_username, h.created_at,
               h.target_weekly_ambition, h.target_monthly,
               h.target_quarterly, h.target_annual,
               h.previous_weekly_ambition, h.previous_monthly,
               h.previous_quarterly, h.previous_annual,
               h.notes,
               COALESCE(u.display_name, h.actor_username) AS actor_display_name
        FROM customer_spend_target_history h
        LEFT JOIN users u ON u.username = h.actor_username
        WHERE h.customer_code_365 = :c
        ORDER BY h.created_at DESC, h.id DESC
        LIMIT :lim
    """), {"c": customer_code, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


# ─── writes ─────────────────────────────────────────────────────────────

def _insert_history(customer_code: str, event: str, actor: str,
                    cadences: dict | None, previous: dict | None = None,
                    notes: str | None = None):
    cad = cadences or {}
    prev = previous or {}
    db.session.execute(text("""
        INSERT INTO customer_spend_target_history
            (customer_code_365, event, actor_username, created_at,
             target_weekly_ambition, target_monthly, target_quarterly, target_annual,
             previous_weekly_ambition, previous_monthly,
             previous_quarterly, previous_annual,
             notes)
        VALUES (:c, :ev, :a, :now,
                :w, :m, :q, :y,
                :pw, :pm, :pq, :py,
                :n)
    """), {
        "c": customer_code, "ev": event, "a": actor, "now": _utc_now(),
        "w": cad.get("weekly_ambition"), "m": cad.get("monthly"),
        "q": cad.get("quarterly"), "y": cad.get("annual"),
        "pw": prev.get("target_weekly_ambition"),
        "pm": prev.get("target_monthly"),
        "pq": prev.get("target_quarterly"),
        "py": prev.get("target_annual"),
        "n": notes,
    })


def propose_target(customer_code: str, payload: dict, actor: str) -> dict:
    """AM proposes a target. Active values (if any) are preserved; the
    proposed numbers live in the history row (brief §6.1 schema has no
    proposed_* numeric columns on the main table)."""
    if not _customer_exists(customer_code):
        raise ValueError(f"Customer not found: {customer_code}")

    cad = _normalize_cadences(payload)
    notes = (payload.get("notes") or None)
    now = _utc_now()
    existing = _get_existing_row(customer_code)

    if existing:
        db.session.execute(text("""
            UPDATE customer_spend_target SET
                status = 'proposed',
                proposed_by = :a,
                proposed_at = :now,
                proposed_notes = :n,
                last_modified_by = :a,
                last_modified_at = :now
            WHERE customer_code_365 = :c
        """), {"c": customer_code, "a": actor, "now": now, "n": notes})
    else:
        db.session.execute(text("""
            INSERT INTO customer_spend_target
                (customer_code_365, status,
                 proposed_by, proposed_at, proposed_notes,
                 last_modified_by, last_modified_at)
            VALUES (:c, 'proposed', :a, :now, :n, :a, :now)
        """), {"c": customer_code, "a": actor, "now": now, "n": notes})

    _insert_history(customer_code, "proposed", actor, cad,
                    previous=existing, notes=notes)
    _emit_audit("customer.target.proposed", actor, customer_code,
                {"cadences": {k: str(v) if v is not None else None
                              for k, v in cad.items()},
                 "notes": notes})
    db.session.commit()
    return get_target(customer_code)


def set_target_directly(customer_code: str, payload: dict, actor: str) -> dict:
    """Manager sets target without an AM proposal. Status -> 'active'.

    Brief §10.5: when the payload supplies only ``annual``, derived
    cadences fill **only empty** existing fields — explicit non-null
    cadence values on the existing row are preserved.
    """
    if not _customer_exists(customer_code):
        raise ValueError(f"Customer not found: {customer_code}")

    existing = _get_existing_row(customer_code)
    cad = _resolve_cadences_for_inline_edit(payload, existing)
    notes = (payload.get("notes") or None)
    now = _utc_now()

    is_modification = existing is not None and any(
        existing.get(k) is not None for k in
        ("target_weekly_ambition", "target_monthly",
         "target_quarterly", "target_annual")
    )

    if existing:
        db.session.execute(text("""
            UPDATE customer_spend_target SET
                target_weekly_ambition = :w,
                target_monthly         = :m,
                target_quarterly       = :q,
                target_annual          = :y,
                status                 = 'active',
                approved_by            = :a,
                approved_at            = :now,
                last_modified_by       = :a,
                last_modified_at       = :now,
                proposed_by = NULL, proposed_at = NULL, proposed_notes = NULL
            WHERE customer_code_365 = :c
        """), {"c": customer_code, "a": actor, "now": now,
               "w": cad["weekly_ambition"], "m": cad["monthly"],
               "q": cad["quarterly"], "y": cad["annual"]})
    else:
        db.session.execute(text("""
            INSERT INTO customer_spend_target
                (customer_code_365, target_weekly_ambition, target_monthly,
                 target_quarterly, target_annual,
                 status, approved_by, approved_at,
                 last_modified_by, last_modified_at)
            VALUES (:c, :w, :m, :q, :y, 'active', :a, :now, :a, :now)
        """), {"c": customer_code, "a": actor, "now": now,
               "w": cad["weekly_ambition"], "m": cad["monthly"],
               "q": cad["quarterly"], "y": cad["annual"]})

    event = "modified_by_manager" if is_modification else "created"
    _insert_history(customer_code, event, actor, cad,
                    previous=existing, notes=notes)
    _emit_audit("customer.target.set", actor, customer_code,
                {"cadences": {k: str(v) if v is not None else None
                              for k, v in cad.items()},
                 "notes": notes})
    db.session.commit()
    return get_target(customer_code)


def bulk_set_annual_targets(customer_codes: list[str], annual,
                            actor: str) -> dict:
    """Brief §10.5: 'set annual = X for selected customers'.

    Atomic: pre-validates every code; if **any** is unknown the whole
    operation is rejected (``ValueError``) before a single write — no
    partial commits.
    """
    annual_dec = _to_dec(annual)
    if annual_dec is None:
        raise ValueError("annual is required and must be numeric")
    if not customer_codes:
        raise ValueError("customer_codes must be a non-empty list")

    unknown = [c for c in customer_codes if not _customer_exists(c)]
    if unknown:
        raise ValueError(
            f"Unknown customer code(s): {', '.join(unknown)} "
            "— bulk operation rejected, no writes performed"
        )

    now = _utc_now()
    applied: list[str] = []

    try:
        for code in customer_codes:
            existing = _get_existing_row(code)
            cad = _resolve_cadences_for_inline_edit({"annual": annual_dec},
                                                    existing)
            is_modification = existing is not None and any(
                existing.get(k) is not None for k in
                ("target_weekly_ambition", "target_monthly",
                 "target_quarterly", "target_annual")
            )

            if existing:
                db.session.execute(text("""
                    UPDATE customer_spend_target SET
                        target_weekly_ambition = :w,
                        target_monthly         = :m,
                        target_quarterly       = :q,
                        target_annual          = :y,
                        status                 = 'active',
                        approved_by            = :a,
                        approved_at            = :now,
                        last_modified_by       = :a,
                        last_modified_at       = :now,
                        proposed_by = NULL, proposed_at = NULL,
                        proposed_notes = NULL
                    WHERE customer_code_365 = :c
                """), {"c": code, "a": actor, "now": now,
                       "w": cad["weekly_ambition"], "m": cad["monthly"],
                       "q": cad["quarterly"], "y": cad["annual"]})
            else:
                db.session.execute(text("""
                    INSERT INTO customer_spend_target
                        (customer_code_365, target_weekly_ambition,
                         target_monthly, target_quarterly, target_annual,
                         status, approved_by, approved_at,
                         last_modified_by, last_modified_at)
                    VALUES (:c, :w, :m, :q, :y, 'active', :a, :now, :a, :now)
                """), {"c": code, "a": actor, "now": now,
                       "w": cad["weekly_ambition"], "m": cad["monthly"],
                       "q": cad["quarterly"], "y": cad["annual"]})

            event = "modified_by_manager" if is_modification else "created"
            _insert_history(code, event, actor, cad,
                            previous=existing,
                            notes=f"bulk_set: annual={cad['annual']}")
            _emit_audit("customer.target.set", actor, code,
                        {"bulk": True, "annual": str(cad["annual"])})
            applied.append(code)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "applied": applied,
        "skipped": [],
        "annual": str(annual_dec),
        "actor": actor,
    }


def approve_proposal(customer_code: str, actor: str) -> dict:
    state = get_target(customer_code)
    pending = state.get("pending_proposal")
    if not pending:
        raise ValueError("No pending proposal to approve")

    cad = {
        "weekly_ambition": pending.get("weekly_ambition"),
        "monthly":         pending.get("monthly"),
        "quarterly":       pending.get("quarterly"),
        "annual":          pending.get("annual"),
    }
    existing = _get_existing_row(customer_code)
    now = _utc_now()
    db.session.execute(text("""
        UPDATE customer_spend_target SET
            target_weekly_ambition = :w,
            target_monthly         = :m,
            target_quarterly       = :q,
            target_annual          = :y,
            status = 'active',
            approved_by = :a, approved_at = :now,
            last_modified_by = :a, last_modified_at = :now,
            proposed_by = NULL, proposed_at = NULL, proposed_notes = NULL
        WHERE customer_code_365 = :c
    """), {"c": customer_code, "a": actor, "now": now,
           "w": cad["weekly_ambition"], "m": cad["monthly"],
           "q": cad["quarterly"], "y": cad["annual"]})

    # Brief §10.3: "Two history rows: customer.target.approved + an
    # active-state snapshot." The first row records the manager action;
    # the second is an append-only snapshot of the post-approval active
    # state so the audit trail can distinguish "manager approved X" from
    # "the active target is now X".
    _insert_history(customer_code, "approved", actor, cad,
                    previous=existing)
    _insert_history(customer_code, "active_snapshot", actor, cad,
                    previous=existing)
    _emit_audit("customer.target.approved", actor, customer_code,
                {"cadences": {k: str(v) if v is not None else None
                              for k, v in cad.items()}})
    db.session.commit()
    return get_target(customer_code)


def reject_proposal(customer_code: str, reason: str | None, actor: str) -> dict:
    state = get_target(customer_code)
    pending = state.get("pending_proposal")
    if not pending:
        raise ValueError("No pending proposal to reject")

    has_active = state.get("active") is not None
    new_status = "active" if has_active else "no_target"
    existing = _get_existing_row(customer_code)

    db.session.execute(text("""
        UPDATE customer_spend_target SET
            status = :st,
            proposed_by = NULL, proposed_at = NULL, proposed_notes = NULL,
            last_modified_by = :a, last_modified_at = :now
        WHERE customer_code_365 = :c
    """), {"c": customer_code, "a": actor, "st": new_status, "now": _utc_now()})

    _insert_history(customer_code, "rejected", actor, None,
                    previous=existing, notes=reason)
    _emit_audit("customer.target.rejected", actor, customer_code,
                {"reason": reason})
    db.session.commit()
    return get_target(customer_code)


def clear_target(customer_code: str, actor: str) -> dict:
    """Manager clears an active target — emits ``customer.target.cleared``
    (brief §6.7)."""
    existing = _get_existing_row(customer_code)
    if not existing:
        raise ValueError("No target to clear")

    db.session.execute(text("""
        UPDATE customer_spend_target SET
            target_weekly_ambition = NULL,
            target_monthly         = NULL,
            target_quarterly       = NULL,
            target_annual          = NULL,
            status                 = 'no_target',
            last_modified_by       = :a,
            last_modified_at       = :now
        WHERE customer_code_365 = :c
    """), {"c": customer_code, "a": actor, "now": _utc_now()})

    _insert_history(customer_code, "cleared", actor, None, previous=existing)
    _emit_audit("customer.target.cleared", actor, customer_code, None)
    db.session.commit()
    return get_target(customer_code)


# ─── achievement / list ─────────────────────────────────────────────────

def _period_bounds(period: str):
    today = datetime.now(timezone.utc).date()
    if period == "mtd":
        start = today.replace(day=1)
        days_in_period = (today - start).days + 1
        annualizer = 12
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
    # both PostgreSQL and SQLite.
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
        actual_row = None
    actual = float(actual_row["actual"] or 0) if actual_row else 0.0

    state = get_target(customer_code)
    active = state.get("active") or {}
    target_map = {
        "mtd":            active.get("monthly"),
        "qtd":            active.get("quarterly"),
        "ytd":            active.get("annual"),
        "weekly_average": active.get("weekly_ambition"),
    }
    target = target_map.get(period)
    target_f = float(target) if target is not None else None

    pct = (actual / target_f * 100.0) if target_f else None
    gap = (target_f - actual) if target_f is not None else None

    if period == "weekly_average":
        run_rate_projection = actual / 4.0
        period_total_target = target_f
    elif period == "ytd":
        run_rate_projection = (actual / days_in_period) * 365 if days_in_period else 0
        period_total_target = target_f
    else:
        if period == "mtd":
            from calendar import monthrange
            full = monthrange(end.year, end.month)[1]
        else:
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

    cutoff_90d = datetime.now(timezone.utc).date() - timedelta(days=90)
    params["cutoff_90d"] = cutoff_90d

    select_core = """
        SELECT
            c.customer_code_365 AS customer_code,
            COALESCE(c.company_name, '') AS customer_name,
            c.category_1_name AS classification,
            c.town AS district,
            c.agent_code_365 AS agent_code,
            c.agent_name AS agent_name,
            t.status,
            t.target_weekly_ambition AS weekly_ambition,
            t.target_monthly         AS monthly,
            t.target_quarterly       AS quarterly,
            t.target_annual          AS annual,
            t.approved_by, t.approved_at,
            t.last_modified_by, t.last_modified_at,
    """

    try:
        sql = text(f"""
            {select_core}
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
                COALESCE(t.target_annual, 0) DESC,
                c.customer_code_365
            LIMIT 500
        """)
        rows = db.session.execute(sql, params).mappings().all()
    except Exception:
        params.pop("cutoff_90d", None)
        sql = text(f"""
            {select_core}
                0 AS sales_90d
            FROM ps_customers c
            LEFT JOIN customer_spend_target t
                ON t.customer_code_365 = c.customer_code_365
            WHERE {' AND '.join(where)}
            ORDER BY
                COALESCE(c.category_1_name, 'Z'),
                COALESCE(t.target_annual, 0) DESC,
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
        run_rate_annual = sales_90d * 4
        d["run_rate_annual"] = round(run_rate_annual, 2)
        if annual and annual > 0:
            d["pct_of_annual_target"] = round((run_rate_annual / annual) * 100, 1)
            d["gap_to_annual"] = round(annual - run_rate_annual, 2)
        else:
            d["pct_of_annual_target"] = None
            d["gap_to_annual"] = None
        out.append(d)
    return out
