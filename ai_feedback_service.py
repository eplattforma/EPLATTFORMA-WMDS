import os, json, hashlib
from datetime import datetime, timedelta, timezone

from openai import OpenAI
from sqlalchemy import text
from app import db

AI_INTEGRATIONS_OPENAI_API_KEY = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
AI_INTEGRATIONS_OPENAI_BASE_URL = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
OPENAI_MODEL = "gpt-4o-mini"

client = OpenAI(
    api_key=AI_INTEGRATIONS_OPENAI_API_KEY,
    base_url=AI_INTEGRATIONS_OPENAI_BASE_URL
)

CACHE_TTL_HOURS = 12
MAX_ROWS = 50

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "peer_context": {"type": "string"},
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "expected_impact": {"type": "string"},
                    "confidence": {"type": "number"}
                },
                "required": ["title", "why", "expected_impact", "confidence"]
            }
        },
        "next_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                    "action": {"type": "string"},
                    "script_hint": {"type": "string"}
                },
                "required": ["priority", "action", "script_hint"]
            }
        }
    },
    "required": ["summary", "peer_context", "key_findings", "risks", "opportunities", "next_actions"]
}


def _hash_payload(payload: dict) -> str:
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _clip_payload(payload: dict) -> dict:
    p = dict(payload)
    for k in ["white_space", "lapsed_items", "top_items", "price_outliers", "white_space_top", "lapsed_top", "trend_monthly", "category_mix", "price_vs_peers", "item_recency"]:
        if isinstance(p.get(k), list):
            p[k] = p[k][:MAX_ROWS]
    return p


def get_cached_feedback(payload_hash: str):
    q = text("""
      SELECT response_json
      FROM ai_feedback_cache
      WHERE payload_hash = :h AND expires_at > NOW()
      LIMIT 1
    """)
    row = db.session.execute(q, {"h": payload_hash}).fetchone()
    return row[0] if row else None


def save_cached_feedback(payload_hash: str, response_json: dict):
    q = text("""
      INSERT INTO ai_feedback_cache(payload_hash, expires_at, response_json)
      VALUES (:h, :exp, CAST(:j AS jsonb))
      ON CONFLICT (payload_hash) DO UPDATE
      SET expires_at = EXCLUDED.expires_at,
          response_json = EXCLUDED.response_json
    """)
    exp = datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)
    db.session.execute(q, {"h": payload_hash, "exp": exp, "j": json.dumps(response_json)})
    db.session.commit()


SYSTEM_PROMPT = (
    "You are a B2B sales analytics assistant for a wholesale distribution company. "
    "You receive a compact JSON snapshot of a Customer vs Peer Group benchmark dashboard. "
    "Return actionable insights for a sales rep. "
    "Write in English. Keep metric/section names exactly as provided (e.g., Sales Index, Price Index, Penetration). "
    "Be concrete and reference specific numbers from the snapshot. "
    "Focus on practical actions: what to say to the customer, what products to push, what risks to address.\n\n"
    "You MUST return valid JSON with EXACTLY these top-level keys:\n"
    "{\n"
    '  "summary": "string - 2-3 sentence executive summary of the customer vs peers",\n'
    '  "peer_context": "string - describe the peer group and how this customer compares",\n'
    '  "key_findings": ["string array - 3-5 bullet-point findings with specific numbers"],\n'
    '  "risks": ["string array - 2-4 risks or concerns to address"],\n'
    '  "opportunities": [{"title": "string", "why": "string explanation", "expected_impact": "string", "confidence": 0.85}],\n'
    '  "next_actions": [{"priority": "P0 or P1 or P2", "action": "string - what to do", "script_hint": "string - what to say to the customer"}]\n'
    "}\n\n"
    "Do NOT nest these keys inside any wrapper object. The response must have summary, peer_context, key_findings, risks, opportunities, and next_actions as TOP-LEVEL keys."
)


def generate_feedback(report_snapshot: dict) -> dict:
    if not AI_INTEGRATIONS_OPENAI_API_KEY or not AI_INTEGRATIONS_OPENAI_BASE_URL:
        raise ValueError("AI Integrations are not configured. Please ensure the OpenAI integration is set up.")

    snapshot = _clip_payload(report_snapshot)
    payload_hash = _hash_payload(snapshot)

    cached = get_cached_feedback(payload_hash)
    if cached:
        return cached if isinstance(cached, dict) else json.loads(cached)

    user_content = json.dumps(snapshot, ensure_ascii=False)

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=4096
    )

    out_text = resp.choices[0].message.content or "{}"
    try:
        out = json.loads(out_text)
    except Exception:
        out = {
            "summary": out_text,
            "peer_context": "",
            "key_findings": [],
            "risks": [],
            "opportunities": [],
            "next_actions": []
        }

    save_cached_feedback(payload_hash, out)
    return out
