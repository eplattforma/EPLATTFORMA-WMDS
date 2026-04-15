import os
import re
import uuid
import logging
from datetime import datetime
from urllib.parse import quote

from app import db
from jinja2 import Environment, StrictUndefined, DebugUndefined

logger = logging.getLogger(__name__)

jinja_env = Environment(undefined=StrictUndefined, autoescape=False)

MICROSMS_DIRECT_URL = "https://api.microsms.net/sendapidirect.asp"

VALID_CHANNELS = ('microsms', 'phone_sms', 'phone_call', 'whatsapp', 'viber', 'onesignal_push')

_GR_DAYS = ['ΔΕΥΤΕΡΑ', 'ΤΡΙΤΗ', 'ΤΕΤΑΡΤΗ', 'ΠΕΜΠΤΗ', 'ΠΑΡΑΣΚΕΥΗ', 'ΣΑΒΒΑΤΟ', 'ΚΥΡΙΑΚΗ']
_GR_MONTHS = ['ΙΑΝ', 'ΦΕΒ', 'ΜΑΡ', 'ΑΠΡ', 'ΜΑΙ', 'ΙΟΥΝ', 'ΙΟΥΛ', 'ΑΥΓ', 'ΣΕΠ', 'ΟΚΤ', 'ΝΟΕ', 'ΔΕΚ']

def _greek_date_str(dt):
    if dt is None:
        return ''
    return f"{_GR_DAYS[dt.weekday()]} {dt.day} {_GR_MONTHS[dt.month - 1]}"

VALID_STATUSES = (
    'initiated', 'launched', 'sent', 'delivered', 'failed',
    'answered', 'no_answer', 'callback_requested', 'wrong_number',
    'left_message', 'cancelled', 'completed',
    'skipped_no_subscription',
)

ERR_MAP = {
    1: "Configuration Error",
    32: "Username disabled",
    37: "Account must be activated",
    43: "Username failed",
    47: "Incorrect Password",
    49: "Not available balance",
    50: "IP is Blocked",
    51: "User not authorized",
    130: "XML Error",
    137: "Title cannot be empty",
    138: "Message cannot be empty",
    142: "Message length exceeds maximum",
    162: "Mobile number is empty",
    163: "Mobile number is not numeric",
    165: "Error regarding mobile number",
    181: "Mobile number is in blacklist",
    183: "Country not available",
}


def normalize_phone(number, default_country='CY'):
    raw = (number or '').strip()
    if not raw:
        return {"raw": raw, "normalized": "", "digits_only": "", "display": "",
                "e164": "", "valid": False, "reason": "empty"}

    cleaned = re.sub(r'[\s\-\(\)]', '', raw)

    if cleaned.startswith('+'):
        digits = cleaned[1:]
        has_country = True
    elif cleaned.startswith('00'):
        digits = cleaned[2:]
        has_country = True
    else:
        digits = re.sub(r'[^\d]', '', cleaned)
        has_country = False

    digits = re.sub(r'[^\d]', '', digits)

    if not digits or len(digits) < 7:
        return {"raw": raw, "normalized": digits, "digits_only": digits,
                "display": raw, "e164": "", "valid": False, "reason": "too_short"}

    if has_country:
        e164 = '+' + digits
        if digits.startswith('357'):
            local = digits[3:]
            display = f"+357 {local[:2]} {local[2:]}" if len(local) >= 4 else f"+357 {local}"
        else:
            display = '+' + digits
    else:
        if default_country == 'CY' and len(digits) == 8:
            e164 = '+357' + digits
            display = f"+357 {digits[:2]} {digits[2:]}"
        else:
            e164 = '+' + digits
            display = '+' + digits

    valid = bool(re.fullmatch(r'\d{8,15}', re.sub(r'[^\d]', '', e164.lstrip('+'))))

    return {
        "raw": raw,
        "normalized": digits,
        "digits_only": re.sub(r'[^\d]', '', e164.lstrip('+')),
        "display": display,
        "e164": e164,
        "valid": valid,
        "reason": None if valid else "invalid_format",
    }


def resolve_customer_context(customer_code_365):
    row = db.session.execute(db.text("""
        SELECT customer_code_365,
               COALESCE(NULLIF(company_name,''), customer_code_365) AS customer_name,
               COALESCE(NULLIF(contact_first_name,''), '') AS contact_first_name,
               COALESCE(NULLIF(sms,''), NULLIF(mobile,''), NULLIF(tel_1,''), '') AS mobile_number,
               COALESCE(NULLIF(sms,''), '') AS sms_number,
               COALESCE(NULLIF(tel_1,''), '') AS tel_1,
               COALESCE(delivery_days, '') AS delivery_days
        FROM ps_customers
        WHERE customer_code_365 = :cid
    """), {"cid": customer_code_365}).mappings().first()

    if not row:
        return None

    ctx = dict(row)

    ctx["delivery_date"] = ""
    ctx["delivery_date_formatted"] = ""
    try:
        dd_raw = (row["delivery_days"] or "").strip()
        if dd_raw:
            from services.crm_order_window import next_delivery_date_for_slot
            from datetime import date as _date
            best = None
            for token in dd_raw.split(","):
                token = token.strip()
                if len(token) >= 2 and token.isdigit():
                    dow_int = int(token[0])
                    week_code = int(token[1])
                    nd = next_delivery_date_for_slot(dow_int, week_code, from_date=_date.today())
                    if best is None or nd < best:
                        best = nd
            if best:
                ctx["delivery_date"] = best.strftime('%a %d-%b')
                ctx["delivery_date_formatted"] = _greek_date_str(best)
    except Exception:
        pass

    ctx["today_formatted"] = _greek_date_str(datetime.now())

    phone_info = normalize_phone(ctx.get("mobile_number") or ctx.get("sms_number") or "")
    ctx["phone_normalized"] = phone_info
    ctx["primary_e164"] = phone_info["e164"]
    ctx["primary_display"] = phone_info["display"]
    ctx["phone_valid"] = phone_info["valid"]

    return ctx


def render_template_for_customer(template_code, customer_ctx, extra_ctx=None):
    tpl_row = db.session.execute(db.text("""
        SELECT code, title, sender_title, body, call_script, force_unicode,
               allow_microsms, allow_phone_sms, allow_call, allow_whatsapp, allow_viber
        FROM sms_template
        WHERE code = :c AND is_enabled = TRUE
    """), {"c": template_code}).mappings().first()

    if not tpl_row:
        return {"error": "Template not found or disabled"}

    ctx = dict(customer_ctx or {})
    if extra_ctx:
        ctx.update(extra_ctx)

    body = tpl_row["body"] or ""
    rendered_body, warning = _render_body(body, ctx)

    rendered_script = None
    if tpl_row.get("call_script"):
        rendered_script, _ = _render_body(tpl_row["call_script"], ctx)

    return {
        "code": tpl_row["code"],
        "title": tpl_row["title"],
        "sender_title": tpl_row.get("sender_title") or os.getenv("MICROSMS_SENDER", "EPLATTFORMA"),
        "rendered_body": rendered_body,
        "rendered_call_script": rendered_script,
        "warning": warning,
        "force_unicode": bool(tpl_row.get("force_unicode")),
        "allow_microsms": bool(tpl_row.get("allow_microsms", True)),
        "allow_phone_sms": bool(tpl_row.get("allow_phone_sms", True)),
        "allow_call": bool(tpl_row.get("allow_call", False)),
        "allow_whatsapp": bool(tpl_row.get("allow_whatsapp", True)),
        "allow_viber": bool(tpl_row.get("allow_viber", True)),
    }


def _render_body(body, ctx):
    from jinja2 import UndefinedError
    tpl = jinja_env.from_string(body or "")
    try:
        return tpl.render(**(ctx or {})).strip(), None
    except UndefinedError as e:
        safe_env = Environment(undefined=DebugUndefined, autoescape=False)
        fallback = safe_env.from_string(body or "").render(**(ctx or {})).strip()
        return fallback, str(e)


def create_comm_log(channel, customer_code_365=None, customer_name=None,
                    source_screen=None, context_type=None, context_id=None,
                    template_code=None, template_title=None,
                    recipient_number=None, message_text=None,
                    status='initiated', batch_id=None, launch_url=None,
                    extra_json=None, username=None,
                    push_target_type=None, push_target_id=None,
                    push_deep_link=None, push_data_json=None):

    phone = normalize_phone(recipient_number)

    db.session.execute(db.text("""
        INSERT INTO crm_communication_log (
            created_by_username, customer_code_365, customer_name,
            context_type, context_id, source_screen,
            channel, direction, template_code, template_title,
            recipient_number, recipient_number_normalized,
            message_text, status, batch_id, launch_url, extra_json,
            push_target_type, push_target_id, push_deep_link, push_data_json
        ) VALUES (
            :username, :cc, :cn,
            :ctx_type, :ctx_id, :source,
            :channel, 'outbound', :tpl_code, :tpl_title,
            :recip, :recip_norm,
            :msg, :status, :batch, :launch, :extra,
            :push_tt, :push_tid, :push_dl, CAST(:push_dj AS jsonb)
        )
        RETURNING id
    """), {
        "username": username,
        "cc": customer_code_365, "cn": customer_name,
        "ctx_type": context_type, "ctx_id": context_id,
        "source": source_screen,
        "channel": channel,
        "tpl_code": template_code, "tpl_title": template_title,
        "recip": recipient_number, "recip_norm": phone.get("digits_only", ""),
        "msg": message_text, "status": status,
        "batch": batch_id, "launch": launch_url,
        "extra": extra_json,
        "push_tt": push_target_type, "push_tid": push_target_id,
        "push_dl": push_deep_link, "push_dj": push_data_json,
    })
    row = db.session.execute(db.text("SELECT lastval()")).scalar()
    db.session.commit()
    return row


def update_comm_log_status(log_id, status, outcome_note=None, provider_fields=None):
    sets = ["status = :status", "updated_at = NOW()"]
    params = {"id": log_id, "status": status}

    if outcome_note is not None:
        sets.append("outcome_note = :note")
        params["note"] = outcome_note

    if provider_fields:
        for key in ('provider_name', 'provider_message_id', 'provider_error_code', 'provider_raw_response'):
            if key in provider_fields:
                sets.append(f"{key} = :{key}")
                params[key] = provider_fields[key]

    sql = f"UPDATE crm_communication_log SET {', '.join(sets)} WHERE id = :id"
    db.session.execute(db.text(sql), params)
    db.session.commit()


def send_microsms(mobile_e164, sender_title, message, customer_code_365=None,
                  customer_name=None, template_code=None, template_title=None,
                  source_screen=None, context_type=None, context_id=None,
                  batch_id=None, username=None):
    import requests as req_lib

    if not batch_id:
        batch_id = f"sms-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    phone = normalize_phone(mobile_e164)
    mob_digits = phone["digits_only"]

    log_id = create_comm_log(
        channel='microsms',
        customer_code_365=customer_code_365,
        customer_name=customer_name,
        source_screen=source_screen,
        context_type=context_type or 'customer',
        context_id=context_id or customer_code_365,
        template_code=template_code,
        template_title=template_title,
        recipient_number=mobile_e164,
        message_text=message,
        status='initiated',
        batch_id=batch_id,
        username=username,
    )

    raw = ""
    status = "UNKNOWN"
    msgid = None
    err_code = None

    try:
        usr = os.getenv("MICROSMS_USER", "").strip()
        psw = os.getenv("MICROSMS_PASS", "").strip()
        if not usr or not psw:
            raise RuntimeError("Missing MICROSMS_USER / MICROSMS_PASS.")

        params = {
            "usr": usr, "psw": psw,
            "mobnu": mob_digits, "title": sender_title or "",
            "message": message or "", "Batchid": batch_id,
            "Dtype": "1",
        }
        r = req_lib.get(MICROSMS_DIRECT_URL, params=params, timeout=15)
        r.raise_for_status()
        raw = r.text

        t = raw.strip()
        if t.lower().startswith("ok"):
            status = "sent"
            msgid = next((p for p in t.split() if p.isdigit()), None)
        elif t.lower().startswith("error"):
            status = "failed"
            code = next((p for p in t.split() if p.isdigit()), None)
            err_code = int(code) if code else None
        else:
            status = "failed"
    except Exception as e:
        status = "failed"
        raw = f"EXCEPTION: {type(e).__name__}: {e}"

    update_comm_log_status(log_id, status, provider_fields={
        "provider_name": "microsms",
        "provider_message_id": msgid,
        "provider_error_code": str(err_code) if err_code else None,
        "provider_raw_response": raw,
    })

    _write_legacy_sms_log(
        username=username, customer_code_365=customer_code_365,
        customer_name=customer_name, mobile=mob_digits,
        sender=sender_title, message=message, batch_id=batch_id,
        status="OK" if status == "sent" else "ERROR",
        msgid=msgid, err_code=err_code, raw=raw,
        template_code=template_code,
        context_type=context_type, context_id=context_id,
    )

    return {
        "ok": status == "sent",
        "log_id": log_id,
        "batch_id": batch_id,
        "message_id": msgid,
        "status": status,
        "error": ERR_MAP.get(err_code, raw) if status == "failed" else None,
    }


def _write_legacy_sms_log(username=None, customer_code_365=None, customer_name=None,
                          mobile=None, sender=None, message=None, batch_id=None,
                          status=None, msgid=None, err_code=None, raw=None,
                          template_code=None, context_type=None, context_id=None):
    try:
        db.session.execute(db.text("""
            INSERT INTO sms_log (
              created_by_username,
              context_type, context_id, template_code,
              customer_code_365, customer_name,
              mobile_number, sender_title, batch_id,
              unicode_mode, message_text,
              provider_status, provider_message_id, provider_error_code, provider_raw_response
            ) VALUES (
              :u, :ct, :cid, :tpl,
              :cc, :cn,
              :mob, :sender, :batch,
              :uni, :msg,
              :st, :msgid, :ecode, :raw
            )
        """), {
            "u": username,
            "ct": context_type or "customer", "cid": context_id or customer_code_365,
            "tpl": template_code,
            "cc": customer_code_365, "cn": customer_name,
            "mob": mobile, "sender": sender, "batch": batch_id,
            "uni": False, "msg": message,
            "st": status, "msgid": msgid,
            "ecode": err_code, "raw": raw,
        })
        db.session.commit()
    except Exception as e:
        logger.warning(f"Legacy sms_log write failed: {e}")
        db.session.rollback()


def build_launch_url(channel, e164_number, message_text=None):
    num = (e164_number or '').strip()
    digits = re.sub(r'[^\d]', '', num)

    if channel == 'phone_sms':
        body_part = f"?body={quote(message_text)}" if message_text else ""
        return f"sms:{num}{body_part}"

    elif channel == 'phone_call':
        return f"tel:{num}"

    elif channel == 'whatsapp':
        wa_num = digits
        if wa_num.startswith('357'):
            pass
        text_part = f"?text={quote(message_text)}" if message_text else ""
        return f"https://wa.me/{wa_num}{text_part}"

    elif channel == 'viber':
        return f"viber://chat?number={quote(num)}"

    return ""


def get_customer_comm_history(customer_code_365, limit=30):
    rows = db.session.execute(db.text("""
        SELECT id, created_at, created_by_username, channel, status,
               template_code, template_title, message_text,
               outcome_note, recipient_number, source_screen,
               provider_message_id, dlr_status,
               push_target_type, push_target_id, push_deep_link
        FROM crm_communication_log
        WHERE customer_code_365 = :cc
        ORDER BY created_at DESC
        LIMIT :lim
    """), {"cc": customer_code_365, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def get_enabled_templates(channel_filter=None, bulk_only=False):
    sql = "SELECT id, code, title, body, sender_title, call_script, allow_microsms, allow_phone_sms, allow_call, allow_whatsapp, allow_viber, is_bulk_allowed, sort_order FROM sms_template WHERE is_enabled = true"
    if bulk_only:
        sql += " AND is_bulk_allowed = true"
    if channel_filter:
        sql += f" AND allow_{channel_filter} = true"
    sql += " ORDER BY COALESCE(sort_order, 999), title"
    rows = db.session.execute(db.text(sql)).mappings().all()
    return [dict(r) for r in rows]
