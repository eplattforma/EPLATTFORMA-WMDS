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

_GR_DAYS = ['ΔΕΥΤΕΡΑ', 'ΤΡΙΤΗ', 'ΤΕΤΑΡΤΗ', 'ΠΕΜΠΤΗ', 'ΠΑΡΑΣΚΕΥΗ', 'ΣΑΒΒΑΤΟ', 'ΚΥΡΙΑΚΗ']
_GR_MONTHS = ['ΙΑΝ', 'ΦΕΒ', 'ΜΑΡ', 'ΑΠΡ', 'ΜΑΙ', 'ΙΟΥΝ', 'ΙΟΥΛ', 'ΑΥΓ', 'ΣΕΠ', 'ΟΚΤ', 'ΝΟΕ', 'ΔΕΚ']

def _greek_date_str(dt):
    """Format a date/datetime as e.g. ΤΡΙΤΗ 15 ΑΠΡ in Greek."""
    if dt is None:
        return ''
    return f"{_GR_DAYS[dt.weekday()]} {dt.day} {_GR_MONTHS[dt.month - 1]}"

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
                   COALESCE(NULLIF(sms,''), NULLIF(mobile,''), NULLIF(tel_1,''), '') AS mobile_number,
                   COALESCE(delivery_days, '') AS delivery_days
            FROM ps_customers
            WHERE customer_code_365 = :cid
        """), {"cid": ctx_id}).mappings().first()
        if not row:
            raise ValueError("Customer not found.")
        ctx = dict(row)
        ctx["delivery_date"] = ""
        ctx["delivery_date_formatted"] = ""
        try:
            dd_raw = (row["delivery_days"] or "").strip()
            if dd_raw:
                from services.crm_order_window import next_delivery_date_for_slot
                from services.crm_delivery_overrides import resolve_effective_delivery
                from datetime import date as _date
                best = None
                for token in dd_raw.split(","):
                    token = token.strip()
                    if len(token) >= 2 and token.isdigit():
                        dow_int = int(token[0])
                        week_code = int(token[1])
                        natural = next_delivery_date_for_slot(dow_int, week_code, from_date=_date.today())
                        effective, _ov = resolve_effective_delivery(ctx_id, natural)
                        if best is None or effective < best:
                            best = effective
                if best:
                    ctx["delivery_date"] = best.strftime('%a %d-%b')
                    ctx["delivery_date_formatted"] = _greek_date_str(best)
        except Exception:
            pass
        ctx["today_formatted"] = _greek_date_str(datetime.now())
        return ctx

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


@sms_bp.route("/send-json", methods=["POST"])
@login_required
def sms_send_json():
    import json
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    sender = (request.form.get("sender_title") or os.getenv("MICROSMS_SENDER", "EPLATTFORMA")).strip()
    message = (request.form.get("message") or "").strip()
    unicode_mode = _needs_unicode(message)

    mobile = _normalize_mob(request.form.get("mobile_number") or "")
    if not message:
        return Response(json.dumps({"ok": False, "error": "Message is required"}), mimetype="application/json")
    if not mobile or not _is_valid_mob(mobile):
        return Response(json.dumps({"ok": False, "error": "Invalid mobile number"}), mimetype="application/json")

    customer_code_365 = request.form.get("customer_code_365") or None
    customer_name = request.form.get("customer_name") or None
    tpl_code = request.form.get("tpl_code") or None

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
        "ct": "customer", "cid": customer_code_365, "tpl": tpl_code,
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
        return Response(json.dumps({"ok": True, "message_id": msgid, "batch_id": batch_id}), mimetype="application/json")
    else:
        human = ERR_MAP.get(err_code, "") if err_code else ""
        return Response(json.dumps({"ok": False, "error": f"SMS failed. {('Error ' + str(err_code) + ' ' + human).strip()}"}), mimetype="application/json")


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
               COALESCE(NULLIF(sms,''), NULLIF(mobile,''), NULLIF(tel_1,''), '') AS mobile_number
        FROM ps_customers
        WHERE active = true AND deleted_at IS NULL
          AND (company_name ILIKE :q OR customer_code_365 ILIKE :q
               OR mobile ILIKE :q OR sms ILIKE :q)
        ORDER BY company_name
        LIMIT 15
    """), {"q": like}).mappings().all()

    results = [dict(r) for r in rows]
    return Response(json.dumps(results), mimetype="application/json")


@sms_bp.route("/templates", methods=["GET"])
@login_required
def sms_templates():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("admin_dashboard"))

    rows = db.session.execute(db.text("""
        SELECT id, code, title, sender_title, body, force_unicode, is_enabled,
               allow_microsms, allow_phone_sms, allow_call, allow_whatsapp, allow_viber,
               allow_onesignal_push,
               call_script, is_bulk_allowed, sort_order,
               offer_image_url, offer_title, offer_link_slug,
               created_at, updated_at
        FROM sms_template
        ORDER BY COALESCE(sort_order, 999), title
    """)).mappings().all()

    templates = []
    for r in rows:
        templates.append({
            "id": r.id,
            "code": r.code,
            "title": r.title,
            "sender_title": r.sender_title or "",
            "body": r.body,
            "force_unicode": bool(r.force_unicode),
            "is_enabled": bool(r.is_enabled),
            "allow_microsms": bool(r.allow_microsms) if r.allow_microsms is not None else True,
            "allow_phone_sms": bool(r.allow_phone_sms),
            "allow_call": bool(r.allow_call),
            "allow_whatsapp": bool(r.allow_whatsapp),
            "allow_viber": bool(r.allow_viber),
            "allow_onesignal_push": bool(r.allow_onesignal_push) if r.allow_onesignal_push is not None else False,
            "call_script": r.call_script or "",
            "is_bulk_allowed": bool(r.is_bulk_allowed),
            "sort_order": r.sort_order or 0,
            "offer_image_url": r.offer_image_url or "",
            "offer_title": r.offer_title or "",
            "offer_link_slug": r.offer_link_slug or "",
        })

    return render_template("admin/sms_templates.html", templates=templates)


@sms_bp.route("/templates/save", methods=["POST"])
@login_required
def sms_template_save():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("sms.sms_templates"))

    tpl_id = request.form.get("id", "").strip()
    code = request.form.get("code", "").strip().upper()
    title = request.form.get("title", "").strip()
    sender_title = request.form.get("sender_title", "").strip() or None
    body = request.form.get("body", "").strip()
    force_unicode = request.form.get("force_unicode") == "on"
    is_enabled = request.form.get("is_enabled") == "on"
    allow_microsms = request.form.get("allow_microsms") == "on"
    allow_phone_sms = request.form.get("allow_phone_sms") == "on"
    allow_call = request.form.get("allow_call") == "on"
    allow_whatsapp = request.form.get("allow_whatsapp") == "on"
    allow_viber = request.form.get("allow_viber") == "on"
    allow_onesignal_push = request.form.get("allow_onesignal_push") == "on"
    call_script = request.form.get("call_script", "").strip() or None
    is_bulk_allowed = request.form.get("is_bulk_allowed") == "on"
    offer_image_url = (request.form.get("offer_image_url") or "").strip() or None
    offer_title = (request.form.get("offer_title") or "").strip() or None
    offer_link_slug = (request.form.get("offer_link_slug") or "").strip() or None
    sort_order = request.form.get("sort_order", "0").strip()
    try:
        sort_order = int(sort_order)
    except ValueError:
        sort_order = 0

    if not code or not title or not body:
        flash("Code, title and body are required.", "danger")
        return redirect(url_for("sms.sms_templates"))

    code = re.sub(r'[^A-Z0-9_]', '_', code)

    # Optional file upload — saves to /static/uploads/sms_offers/ and overrides URL.
    offer_image_path = None
    upload = request.files.get("offer_image_file")
    if upload and upload.filename:
        import os as _os
        from werkzeug.utils import secure_filename
        from flask import current_app
        ext = _os.path.splitext(upload.filename)[1].lower()
        allowed = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        if ext not in allowed:
            flash(f"Image type {ext} not allowed.", "warning")
        else:
            safe = secure_filename(upload.filename) or f"offer{ext}"
            stem, _ = _os.path.splitext(safe)
            fname = f"{code.lower()}_{int(datetime.utcnow().timestamp())}_{stem[:40]}{ext}"
            target_dir = _os.path.join(current_app.root_path, "static", "uploads", "sms_offers")
            _os.makedirs(target_dir, exist_ok=True)
            disk_path = _os.path.join(target_dir, fname)
            upload.save(disk_path)
            offer_image_path = f"static/uploads/sms_offers/{fname}"
            try:
                offer_image_url = url_for("static",
                                          filename=f"uploads/sms_offers/{fname}",
                                          _external=True)
            except Exception:
                offer_image_url = f"/static/uploads/sms_offers/{fname}"

    params = {
        "code": code, "title": title, "sender": sender_title,
        "body": body, "fu": force_unicode, "en": is_enabled,
        "a_micro": allow_microsms, "a_phone": allow_phone_sms,
        "a_call": allow_call, "a_wa": allow_whatsapp, "a_viber": allow_viber,
        "a_push": allow_onesignal_push,
        "cs": call_script, "bulk": is_bulk_allowed, "so": sort_order,
        "oimg_path": offer_image_path, "oimg_url": offer_image_url,
        "oslug": offer_link_slug, "otitle": offer_title,
    }

    try:
        if tpl_id:
            params["id"] = int(tpl_id)
            db.session.execute(db.text("""
                UPDATE sms_template
                SET code = :code, title = :title, sender_title = :sender,
                    body = :body, force_unicode = :fu, is_enabled = :en,
                    allow_microsms = :a_micro, allow_phone_sms = :a_phone,
                    allow_call = :a_call, allow_whatsapp = :a_wa, allow_viber = :a_viber,
                    allow_onesignal_push = :a_push,
                    call_script = :cs, is_bulk_allowed = :bulk, sort_order = :so,
                    offer_image_path = COALESCE(:oimg_path, offer_image_path),
                    offer_image_url = :oimg_url,
                    offer_link_slug = :oslug,
                    offer_title = :otitle,
                    updated_at = NOW()
                WHERE id = :id
            """), params)
            flash(f"Template '{title}' updated.", "success")
        else:
            db.session.execute(db.text("""
                INSERT INTO sms_template (code, title, sender_title, body, force_unicode, is_enabled,
                    allow_microsms, allow_phone_sms, allow_call, allow_whatsapp, allow_viber,
                    allow_onesignal_push, call_script, is_bulk_allowed, sort_order,
                    offer_image_path, offer_image_url, offer_link_slug, offer_title)
                VALUES (:code, :title, :sender, :body, :fu, :en,
                    :a_micro, :a_phone, :a_call, :a_wa, :a_viber,
                    :a_push, :cs, :bulk, :so,
                    :oimg_path, :oimg_url, :oslug, :otitle)
            """), params)
            flash(f"Template '{title}' created.", "success")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving template: {e}", "danger")

    return redirect(url_for("sms.sms_templates"))


@sms_bp.route("/templates/delete/<int:tpl_id>", methods=["POST"])
@login_required
def sms_template_delete(tpl_id):
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("sms.sms_templates"))

    try:
        db.session.execute(db.text("DELETE FROM sms_template WHERE id = :id"), {"id": tpl_id})
        db.session.commit()
        flash("Template deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting template: {e}", "danger")

    return redirect(url_for("sms.sms_templates"))


@sms_bp.route("/templates/list-json", methods=["GET"])
@login_required
def sms_templates_json():
    if not _role_ok():
        return Response("[]", mimetype="application/json", status=403)

    import json
    rows = db.session.execute(db.text("""
        SELECT id, code, title, body, sender_title
        FROM sms_template
        WHERE is_enabled = true
        ORDER BY title
    """)).mappings().all()

    return Response(json.dumps([dict(r) for r in rows]), mimetype="application/json")


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

        db.session.execute(db.text("""
            UPDATE crm_communication_log
            SET dlr_status = :st,
                dlr_received_at = :ts,
                updated_at = :ts
            WHERE recipient_number_normalized = :mob
              AND batch_id = :batch
              AND channel = 'microsms'
        """), {"st": smsstatus, "ts": now, "mob": mob, "batch": batchid})

    db.session.commit()
    return Response("ok", status=200)
