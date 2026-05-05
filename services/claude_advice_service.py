"""Claude-backed advice service for the Account Manager Cockpit (Ticket 3).

Sibling to ``ai_feedback_service.py`` (OpenAI-backed, used by the legacy
Customer Benchmark page). Both services coexist; the cache table
``ai_feedback_cache`` is shared with a key prefix to prevent collision
(OpenAI entries have no prefix, Claude/cockpit entries are prefixed with
``cockpit_``).

Greek output with English trade terminology preserved verbatim, focused
on closing the gap-to-target. Spec: cockpit-brief §12.
"""
import os
import json
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

CACHE_TTL_HOURS = 12
CACHE_KEY_PREFIX = "cockpit_"
MAX_ROWS = 50

# Lazy client cache — keyed by API key so a config rotation builds a fresh client.
_client_cache: dict = {}


def _cfg(key: str, default: str = "") -> str:
    """Read from Flask app config (preferred — set in app.py at boot) and
    fall back to os.environ when called outside an app context."""
    try:
        v = current_app.config.get(key)
        if v:
            return v
    except RuntimeError:
        pass
    return os.environ.get(key, default)


def _get_client():
    api_key = _cfg("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    cached = _client_cache.get(api_key)
    if cached is not None:
        return cached
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        _client_cache[api_key] = client
        return client
    except Exception:
        logger.exception("Failed to initialise Anthropic client")
        return None


GREEK_SYSTEM_PROMPT = """\
You are an experienced wholesale sales advisor for an Italian fine-foods distributor
operating in Cyprus. The user is an account manager looking at one customer's
performance and asking for actionable recommendations.

PRIMARY OPTIMIZATION SIGNAL:
The snapshot includes the customer's gap-to-target. Your recommendations must
prioritize closing that gap. Each recommendation should explicitly link to gap
reduction or risk mitigation against the target.

LANGUAGE RULES (firm, not preferences):
- Respond in Greek.
- Keep these terms in English verbatim because they are standard industry usage:
  SKU, GM%, GP, target, gap, run-rate, PVM, peer group, ABC classification,
  HORECA, Slot, ADD-ON, RFM, Index, Cross-sell, Churn Risk, White Space.
- Use Greek for verbs, reasoning, customer-friendly phrasing, action labels,
  category names where natural.
- Currency stays as €. Numbers stay as digits.

CONTENT RULES:
- Be specific. Cite exact numbers from the snapshot.
- Each recommendation must reference snapshot data (no generic platitudes).
- Prefer concrete actions ("Ανάθεση offer Q2-CHEESE στο SKU-2890").
- Prefer actions the AM can execute today (assign offer, propose order, send SMS).
- Tie expected impact to the gap. "Κλείνει €X από το €Y gap" beats "increases sales".
- If data doesn't support a recommendation, omit it. Do not pad.

OUTPUT FORMAT:
Return valid JSON with these exact top-level keys (no wrapper):
{
  "summary": "Greek - 2-3 sentence executive summary tied to the gap",
  "peer_context": "Greek - peer group description and where customer sits",
  "key_findings": ["Greek bullets, 3-5 items, each citing numbers"],
  "risks": ["Greek risk statements, 2-4 items"],
  "opportunities": [
    {"title": "Greek", "why": "Greek with English terms",
     "expected_impact": "Greek + €amount + which gap it closes",
     "confidence": 0.0-1.0}
  ],
  "next_actions": [
    {"priority": "P0|P1|P2", "action": "Greek action statement",
     "script_hint": "Greek - what to say to the customer if relevant"}
  ]
}
"""


def _hash_payload(payload: dict) -> str:
    """Returns a hex digest sized so that ``CACHE_KEY_PREFIX + hash``
    fits inside the existing ``ai_feedback_cache.payload_hash`` VARCHAR(64)
    column shared with the OpenAI service."""
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    full = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return full[: 64 - len(CACHE_KEY_PREFIX)]


def _clip_payload(payload: dict) -> dict:
    p = dict(payload)
    for k in ("white_space", "lapsed_items", "top_items_by_gp",
              "top_items_by_revenue", "price_index_outliers", "trend",
              "category_mix_vs_peers", "active_offers", "offer_opportunities",
              "cross_sell", "churn_risk_by_category", "activity_timeline"):
        v = p.get(k)
        if isinstance(v, list):
            p[k] = v[:MAX_ROWS]
        elif isinstance(v, dict):
            # active_offers is wrapped {summary, lines}; clip the lines list.
            if isinstance(v.get("lines"), list):
                v = dict(v)
                v["lines"] = v["lines"][:MAX_ROWS]
                p[k] = v
    return p


def _cache_get(payload_hash: str):
    q = text("""
      SELECT response_json FROM ai_feedback_cache
      WHERE payload_hash = :h AND expires_at > NOW()
      LIMIT 1
    """)
    row = db.session.execute(q, {"h": CACHE_KEY_PREFIX + payload_hash}).fetchone()
    return row[0] if row else None


def _cache_set(payload_hash: str, response_json: dict):
    q = text("""
      INSERT INTO ai_feedback_cache(payload_hash, expires_at, response_json)
      VALUES (:h, :exp, CAST(:j AS jsonb))
      ON CONFLICT (payload_hash) DO UPDATE
      SET expires_at = EXCLUDED.expires_at,
          response_json = EXCLUDED.response_json
    """)
    exp = datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)
    db.session.execute(q, {
        "h": CACHE_KEY_PREFIX + payload_hash,
        "exp": exp,
        "j": json.dumps(response_json, ensure_ascii=False),
    })
    db.session.commit()


def _strip_code_fences(out_text: str) -> str:
    out_text = out_text.strip()
    if out_text.startswith("```"):
        out_text = out_text.split("\n", 1)[1] if "\n" in out_text else out_text
        if out_text.endswith("```"):
            out_text = out_text.rsplit("```", 1)[0]
        out_text = out_text.strip()
        if out_text.lower().startswith("json"):
            out_text = out_text[4:].lstrip()
    return out_text


def get_cached_cockpit_advice(snapshot: dict) -> dict | None:
    """Cache-only lookup. Returns the cached advice dict if present and
    fresh, otherwise ``None``. Never calls the Anthropic API and never
    raises — safe to invoke during page render to enable synchronous
    server-side rendering of the Recommended Actions panel on cache hit
    (cockpit-brief §12.5).
    """
    try:
        payload = _clip_payload(snapshot or {})
        payload_hash = _hash_payload(payload)
        cached = _cache_get(payload_hash)
    except Exception:
        logger.exception("Cockpit advice cache lookup failed")
        return None
    if cached is None:
        return None
    if isinstance(cached, dict):
        return cached
    try:
        return json.loads(cached)
    except Exception:
        return None


def generate_cockpit_advice(snapshot: dict) -> dict:
    """Generate Greek-language sales advice tied to the gap-to-target.

    Mirrors ``ai_feedback_service.generate_feedback``'s contract — returns
    a dict with keys: summary, peer_context, key_findings, risks,
    opportunities, next_actions.

    Raises ``ValueError`` if the API key is missing (caller maps to 503).
    """
    client = _get_client()
    if client is None:
        raise ValueError(
            "Anthropic API is not configured. Set ANTHROPIC_API_KEY in Replit Secrets."
        )

    payload = _clip_payload(snapshot)
    payload_hash = _hash_payload(payload)

    cached = _cache_get(payload_hash)
    if cached:
        return cached if isinstance(cached, dict) else json.loads(cached)

    user_content = json.dumps(payload, ensure_ascii=False, default=str)
    model = _cfg("CLAUDE_MODEL", "claude-sonnet-4-5")

    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=GREEK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    out_text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            out_text = block.text
            break

    out_text = _strip_code_fences(out_text)

    try:
        out = json.loads(out_text)
    except Exception:
        out = {
            "summary": out_text[:500], "peer_context": "",
            "key_findings": [], "risks": [],
            "opportunities": [], "next_actions": [],
        }

    # Defensive: ensure all expected keys exist so the template never KeyErrors.
    for k in ("summary", "peer_context"):
        out.setdefault(k, "")
    for k in ("key_findings", "risks", "opportunities", "next_actions"):
        out.setdefault(k, [])

    _cache_set(payload_hash, out)
    return out
