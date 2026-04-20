"""
SMS draft translation helper.

Uses the Replit AI integrations OpenAI proxy to translate a Greek SMS draft
into English (or vice-versa) while preserving Jinja-style placeholders
exactly (e.g. {{customer_name}}, {{offer_link}}).
"""
import os
import re
import logging

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.environ.get("SMS_TRANSLATE_MODEL", "gpt-4o-mini")

_PLACEHOLDER_RE = re.compile(r"\{\{\s*[a-zA-Z0-9_]+\s*\}\}")


def _get_client():
    from openai import OpenAI
    return OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
    )


_LANG_NAMES = {
    "el": "Greek",
    "en": "English",
}


def generate_translated_draft(text: str, source_lang: str = "el",
                              target_lang: str = "en") -> dict:
    """Translate an SMS draft, preserving all {{placeholder}} tokens.

    Returns dict with keys:
        ok (bool), text (str), error (str|None), warnings (list[str])
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "text": "", "error": "Source draft is empty", "warnings": []}

    src_name = _LANG_NAMES.get(source_lang, source_lang)
    tgt_name = _LANG_NAMES.get(target_lang, target_lang)

    src_placeholders = sorted(set(_PLACEHOLDER_RE.findall(text)))

    system_prompt = (
        f"You translate short SMS marketing/transactional messages from {src_name} to {tgt_name}. "
        "Hard rules:\n"
        "1. Preserve every {{placeholder}} token EXACTLY as it appears, including spacing inside the braces.\n"
        "2. Do NOT translate the placeholder names.\n"
        "3. Keep the message concise and natural for an SMS.\n"
        "4. Do not add any prefix, suffix, quotes, or commentary. Output only the translated message body."
    )

    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        translated = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.exception("SMS translation failed")
        return {"ok": False, "text": "", "error": str(e), "warnings": []}

    # Strip accidental wrapping quotes if the model added any.
    if len(translated) >= 2 and translated[0] in ("'", '"', "“", "‘") and translated[-1] in ("'", '"', "”", "’"):
        translated = translated[1:-1].strip()

    warnings = []
    tgt_placeholders = sorted(set(_PLACEHOLDER_RE.findall(translated)))
    if tgt_placeholders != src_placeholders:
        missing = set(src_placeholders) - set(tgt_placeholders)
        extra = set(tgt_placeholders) - set(src_placeholders)
        if missing:
            warnings.append(f"Missing placeholders in translation: {', '.join(sorted(missing))}")
        if extra:
            warnings.append(f"Unexpected placeholders in translation: {', '.join(sorted(extra))}")

    return {"ok": True, "text": translated, "translated": translated,
            "error": None, "warnings": warnings}
