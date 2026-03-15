import os
import re
import uuid
from datetime import datetime
import requests
from jinja2 import Environment, StrictUndefined
from flask import Blueprint, render_template, request, flash, redirect, url_for, Response
from flask_login import login_required, current_user
from app import db

sms_bp = Blueprint("sms", __name__, url_prefix="/admin/sms")

MICROSMS_DIRECT_URL = "https://api.microsms.net/sendapidirect.asp"
BALANCE_URL = "https://ssl.microsms.net/getbalance.asp"

jinja_env = Environment(undefined=StrictUndefined, autoescape=False)

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

def _role_ok():
    return getattr(current_user, "role", None) in ("admin", "warehouse_manager", "crm_admin")

def _normalize_mob(n: str) -> str:
    if not n:
        return ""
    n = n.strip().replace(" ", "").replace("-", "")
    if n.startswith("+"):
        n = n[1:]
    return n

def _is_valid_mob(n: str) -> bool:
    return bool(re.fullmatch(r"\d{8,15}", n))

def _needs_unicode(s: str) -> bool:
    return False

def _render_db_template(body: str, ctx: dict) -> tuple:
    from jinja2 import UndefinedError
    tpl = jinja_env.from_string(body or "")
    try:
        return tpl.render(**(ctx or {})).strip(), None
    except UndefinedError as e:
        from jinja2 import DebugUndefined
        safe_env = Environment(undefined=DebugUndefined, autoescape=False)
        fallback = safe_env.from_string(body or "").render(**(ctx or {})).strip()
        return fallback, str(e)

def _parse_provider_response(text: str):
    t = (text or "").strip()
    if t.lower().startswith("ok"):
        msgid = next((p for p in t.split() if p.isdigit()), None)
        return ("OK", msgid, None)
    if t.lower().startswith("error"):
        code = next((p for p in t.split() if p.isdigit()), None)
        return ("ERROR", None, int(code) if code else None)
    return ("UNKNOWN", None, None)

def _microsms_send_direct(mobile: str, title: str, message: str, unicode_mode: bool, batch_id: str):
    usr = os.getenv("MICROSMS_USER", "").strip()
    psw = os.getenv("MICROSMS_PASS", "").strip()
    if not usr or not psw:
        raise RuntimeError("Missing MICROSMS_USER / MICROSMS_PASS.")

    params = {
        "usr": usr,
        "psw": psw,
        "mobnu": mobile,
        "title": title or "",
        "message": message or "",
        "Batchid": batch_id or "",
        "Dtype": "4" if unicode_mode else "1",
    }
    r = requests.get(MICROSMS_DIRECT_URL, params=params, timeout=15)
    r.raise_for_status()
    return r.text


def _resolve_context(ctx_type: str, ctx_id: str) -> dict:
    ctx_type = (ctx_type or "").strip().lower()
    ctx_id = (ctx_id or "").strip()

    if ctx_type == "customer":
        row = db.session.execute(db.text("""
            SELECT customer_code_365,
                   COALESCE(NULLIF(company_name,''), customer_code_365) AS customer_name,
                   COALESCE(NULLIF(contact_first_name,''), '') AS contact_first_name,
                   COALESCE(NULLIF(mobile,''), NULLIF(sms,''), NULLIF(tel_1,''), '') AS mobile_number
            FROM ps_customers
            WHERE customer_code_365 = :cid
        """), {"cid": ctx_id}).mappings().first()
        if not row:
            raise ValueError("Customer not found.")
        return dict(row)

    raise ValueError(f"Unsupported context type: {ctx_type}")


@sms_bp.route("/", methods=["GET"])
@login_required
def sms_home():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("admin_dashboard"))

    history = db.session.execute(db.text("""
        SELECT id, created_at, created_by_username,
               context_type, context_id, template_code,
               customer_code_365, customer_name, mobile_number,
               batch_id, provider_status, provider_message_id, provider_error_code, dlr_status
        FROM sms_log
        ORDER BY created_at DESC
        LIMIT 80
    """)).mappings().all()

    templates = db.session.execute(db.text("""
        SELECT code, title, is_enabled
        FROM sms_template
        ORDER BY title
    """)).mappings().all()

    return render_template(
        "admin/sms_home.html",
        history=history,
        templates=templates,
        default_sender=os.getenv("MICROSMS_SENDER", "EPLATTFORMA")
    )


@sms_bp.route("/compose", methods=["GET"])
@login_required
def sms_compose():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("admin_dashboard"))

    ctx_type = request.args.get("ctx")
    ctx_id = request.args.get("id")
    tpl_code = (request.args.get("tpl") or "").strip() or None

    try:
        ctx = _resolve_context(ctx_type, ctx_id)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(request.referrer or url_for("sms.sms_home"))

    RESERVED_PARAMS = {"ctx", "id", "tpl"}
    for k, v in request.args.items():
        if k not in RESERVED_PARAMS and k not in ctx:
            ctx[k] = v

    mobile = _normalize_mob(ctx.get("mobile_number") or "")
    if not mobile or not _is_valid_mob(mobile):
        flash("Customer has no valid mobile number.", "danger")
        return redirect(request.referrer or url_for("sms.sms_home"))

    sender = os.getenv("MICROSMS_SENDER", "EPLATTFORMA")
    message = ""
    unicode_mode = False

    if tpl_code:
        tpl = db.session.execute(db.text("""
            SELECT code, title, sender_title, body, force_unicode
            FROM sms_template
            WHERE code = :c AND is_enabled = TRUE
        """), {"c": tpl_code}).mappings().first()

        if not tpl:
            flash("Template not found or disabled.", "danger")
            return redirect(request.referrer or url_for("sms.sms_home"))

        sender = (tpl.get("sender_title") or sender).strip()
        message, tpl_err = _render_db_template(tpl["body"], ctx)
        if tpl_err:
            flash(f"Template has missing placeholders ({tpl_err}). Edit the message before sending.", "warning")
        unicode_mode = bool(tpl.get("force_unicode")) or _needs_unicode(message)

    from models import Setting
    bank_account_no = Setting.get(db.session, 'bank_account_no', '')
    raw_iban = Setting.get(db.session, 'bank_iban', '').replace(' ', '')
    bank_iban = ' '.join(raw_iban[i:i+4] for i in range(0, len(raw_iban), 4))
    bank_bic = Setting.get(db.session, 'bank_bic', '')
    bank_beneficiary = Setting.get(db.session, 'bank_beneficiary', '')

    return render_template(
        "admin/sms_compose.html",
        ctx_type=ctx_type, ctx_id=ctx_id, tpl_code=tpl_code,
        customer_code_365=ctx.get("customer_code_365"),
        customer_name=ctx.get("customer_name"),
        contact_first_name=ctx.get("contact_first_name", ""),
        mobile_number=mobile,
        sender_title=sender,
        message=message,
        unicode_mode=unicode_mode,
        bank_account_no=bank_account_no,
        bank_iban=bank_iban,
        bank_bic=bank_bic,
        bank_beneficiary=bank_beneficiary
    )


@sms_bp.route("/send", methods=["POST"])
@login_required
def sms_send():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("sms.sms_home"))

    sender = (request.form.get("sender_title") or os.getenv("MICROSMS_SENDER", "")).strip()
    message = (request.form.get("message") or "").strip()
    unicode_mode = _needs_unicode(message)

    mobile = _normalize_mob(request.form.get("mobile_number") or "")
    if not sender:
        flash("Sender title is required.", "danger")
        return redirect(request.referrer or url_for("sms.sms_home"))
    if not message:
        flash("Message is required.", "danger")
        return redirect(request.referrer or url_for("sms.sms_home"))
    if not mobile or not _is_valid_mob(mobile):
        flash("Invalid mobile number.", "danger")
        return redirect(request.referrer or url_for("sms.sms_home"))

    ctx_type = request.form.get("ctx_type")
    ctx_id = request.form.get("ctx_id")
    tpl_code = request.form.get("tpl_code")

    customer_code_365 = request.form.get("customer_code_365") or None
    customer_name = request.form.get("customer_name") or None

    batch_id = f"sms-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    raw = ""
    status = "UNKNOWN"
    msgid = None
    err_code = None

    try:
        raw = _microsms_send_direct(mobile, sender, message, unicode_mode, batch_id)
        status, msgid, err_code = _parse_provider_response(raw)
    except Exception as e:
        status = "ERROR"
        raw = f"EXCEPTION: {type(e).__name__}: {e}"

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
        "u": getattr(current_user, "username", None),
        "ct": ctx_type, "cid": ctx_id, "tpl": tpl_code,
        "cc": customer_code_365, "cn": customer_name,
        "mob": mobile,
        "sender": sender,
        "batch": batch_id,
        "uni": unicode_mode,
        "msg": message,
        "st": status,
        "msgid": msgid,
        "ecode": err_code,
        "raw": raw
    })
    db.session.commit()

    if status == "OK":
        flash(f"SMS sent OK. MsgID: {msgid} (Batch: {batch_id})", "success")
    else:
        human = ERR_MAP.get(err_code, "") if err_code else ""
        flash(f"SMS failed. {('Error ' + str(err_code) + ' ' + human).strip()} (Batch: {batch_id})", "danger")

    return redirect(url_for("sms.sms_home"))


@sms_bp.route("/search-customer", methods=["GET"])
@login_required
def sms_search_customer():
    if not _role_ok():
        return Response("[]", mimetype="application/json", status=403)

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return Response("[]", mimetype="application/json")

    import json
    like = f"%{q}%"
    rows = db.session.execute(db.text("""
        SELECT customer_code_365,
               COALESCE(NULLIF(company_name,''), customer_code_365) AS customer_name,
               COALESCE(NULLIF(mobile,''), NULLIF(sms,''), NULLIF(tel_1,''), '') AS mobile_number
        FROM ps_customers
        WHERE active = true AND deleted_at IS NULL
          AND (company_name ILIKE :q OR customer_code_365 ILIKE :q
               OR mobile ILIKE :q OR sms ILIKE :q)
        ORDER BY company_name
        LIMIT 15
    """), {"q": like}).mappings().all()

    results = [dict(r) for r in rows]
    return Response(json.dumps(results), mimetype="application/json")


@sms_bp.route("/balance", methods=["GET"])
@login_required
def sms_balance():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("sms.sms_home"))

    usr = os.getenv("MICROSMS_USER", "").strip()
    psw = os.getenv("MICROSMS_PASS", "").strip()
    try:
        r = requests.get(BALANCE_URL, params={"usr": usr, "psw": psw}, timeout=15)
        r.raise_for_status()
        flash(f"Balance: {r.text.strip()}", "info")
    except Exception as e:
        flash(f"Balance check failed: {e}", "danger")

    return redirect(url_for("sms.sms_home"))


@sms_bp.route("/dlr", methods=["POST"])
def microsms_dlr():
    token = os.getenv("MICROSMS_DLR_TOKEN", "").strip()
    if token and request.args.get("token") != token:
        return Response("forbidden", status=403)

    raw_xml = request.data.decode("utf-8", errors="replace")
    records = re.findall(r'phonenumber="([^"]+)"\s+smsstatus="([^"]+)"\s+batchid="([^"]+)"', raw_xml)

    now = datetime.utcnow()
    for phone, smsstatus, batchid in records:
        mob = _normalize_mob(phone)
        db.session.execute(db.text("""
            UPDATE sms_log
            SET dlr_status = :st,
                dlr_received_at = :ts,
                dlr_raw_xml = :raw
            WHERE mobile_number = :mob
              AND batch_id = :batch
        """), {"st": smsstatus, "ts": now, "raw": raw_xml, "mob": mob, "batch": batchid})

    db.session.commit()
    return Response("ok", status=200)
