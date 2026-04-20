import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from app import db

from services.communications_service import (
    resolve_customer_context, normalize_phone, render_template_for_customer,
    create_comm_log, update_comm_log_status, send_microsms,
    build_launch_url, get_customer_comm_history, get_enabled_templates,
    get_template_full, build_preview_rows, send_finalized_sms,
)
from services.sms_translation_service import generate_translated_draft
from services.onesignal_service import (
    refresh_customer_push_identity, get_cached_push_identity,
    send_push_to_customer, bulk_send_push,
)

communications_bp = Blueprint("communications", __name__, url_prefix="/admin/communications")


def _role_ok():
    return getattr(current_user, "role", None) in ("admin", "warehouse_manager", "crm_admin")


@communications_bp.route("/compose", methods=["GET"])
@login_required
def compose():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(url_for("admin_dashboard"))

    ctx_type = request.args.get("ctx", "customer")
    ctx_id = request.args.get("id", "").strip()
    tpl_code = request.args.get("tpl", "").strip() or None
    source = request.args.get("source", "").strip() or "customer_profile"

    if not ctx_id:
        flash("Customer code is required.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))

    customer = resolve_customer_context(ctx_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))

    templates = get_enabled_templates()
    history = get_customer_comm_history(ctx_id)

    rendered = None
    if tpl_code:
        rendered = render_template_for_customer(tpl_code, customer)

    return render_template(
        "admin/communications_compose.html",
        customer=customer,
        templates=templates,
        history=history,
        rendered=rendered,
        selected_tpl=tpl_code,
        source_screen=source,
        ctx_type=ctx_type,
    )


@communications_bp.route("/preview", methods=["POST"])
@login_required
def preview():
    if not _role_ok():
        return Response(json.dumps({"error": "Not authorized"}), mimetype="application/json", status=403)

    cc = request.form.get("customer_code_365", "").strip()
    tpl_code = request.form.get("template_code", "").strip()

    if not cc or not tpl_code:
        return Response(json.dumps({"error": "Missing parameters"}), mimetype="application/json")

    customer = resolve_customer_context(cc)
    if not customer:
        return Response(json.dumps({"error": "Customer not found"}), mimetype="application/json")

    result = render_template_for_customer(tpl_code, customer)
    if "error" in result:
        return Response(json.dumps(result), mimetype="application/json")

    result["phone_valid"] = customer.get("phone_valid", False)
    result["phone_display"] = customer.get("primary_display", "")
    return Response(json.dumps(result), mimetype="application/json")


@communications_bp.route("/send-microsms", methods=["POST"])
@login_required
def route_send_microsms():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    cc = request.form.get("customer_code_365", "").strip()
    message = request.form.get("message", "").strip()
    tpl_code = request.form.get("template_code", "").strip() or None
    source = request.form.get("source_screen", "").strip() or "customer_profile"

    if not message:
        return Response(json.dumps({"ok": False, "error": "Message is required"}), mimetype="application/json")

    customer = resolve_customer_context(cc)
    if not customer:
        return Response(json.dumps({"ok": False, "error": "Customer not found"}), mimetype="application/json")

    if not customer.get("phone_valid"):
        return Response(json.dumps({"ok": False, "error": "No valid mobile number"}), mimetype="application/json")

    sender = request.form.get("sender_title", "").strip()
    if not sender:
        import os
        sender = os.getenv("MICROSMS_SENDER", "EPLATTFORMA")

    tpl_title = None
    if tpl_code:
        tpl_row = db.session.execute(db.text("SELECT title FROM sms_template WHERE code = :c"), {"c": tpl_code}).mappings().first()
        if tpl_row:
            tpl_title = tpl_row["title"]

    result = send_microsms(
        mobile_e164=customer["primary_e164"],
        sender_title=sender,
        message=message,
        customer_code_365=cc,
        customer_name=customer.get("customer_name"),
        template_code=tpl_code,
        template_title=tpl_title,
        source_screen=source,
        context_type="customer",
        context_id=cc,
        username=getattr(current_user, "username", None),
    )

    return Response(json.dumps(result), mimetype="application/json")


@communications_bp.route("/launch-phone-sms", methods=["POST"])
@login_required
def launch_phone_sms():
    return _handle_launch("phone_sms")


@communications_bp.route("/launch-call", methods=["POST"])
@login_required
def launch_call():
    return _handle_launch("phone_call")


@communications_bp.route("/launch-whatsapp", methods=["POST"])
@login_required
def launch_whatsapp():
    return _handle_launch("whatsapp")


@communications_bp.route("/launch-viber", methods=["POST"])
@login_required
def launch_viber():
    return _handle_launch("viber")


def _handle_launch(channel):
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    cc = request.form.get("customer_code_365", "").strip()
    message = request.form.get("message", "").strip() or None
    tpl_code = request.form.get("template_code", "").strip() or None
    source = request.form.get("source_screen", "").strip() or "customer_profile"

    customer = resolve_customer_context(cc)
    if not customer:
        return Response(json.dumps({"ok": False, "error": "Customer not found"}), mimetype="application/json")

    if not customer.get("phone_valid"):
        return Response(json.dumps({"ok": False, "error": "No valid phone number"}), mimetype="application/json")

    url = build_launch_url(channel, customer["primary_e164"], message)

    tpl_title = None
    if tpl_code:
        tpl_row = db.session.execute(db.text("SELECT title FROM sms_template WHERE code = :c"), {"c": tpl_code}).mappings().first()
        if tpl_row:
            tpl_title = tpl_row["title"]

    log_id = create_comm_log(
        channel=channel,
        customer_code_365=cc,
        customer_name=customer.get("customer_name"),
        source_screen=source,
        context_type="customer",
        context_id=cc,
        template_code=tpl_code,
        template_title=tpl_title,
        recipient_number=customer.get("mobile_number"),
        message_text=message,
        status='launched',
        launch_url=url,
        username=getattr(current_user, "username", None),
    )

    return Response(json.dumps({
        "ok": True,
        "launch_url": url,
        "log_id": log_id,
        "channel": channel,
    }), mimetype="application/json")


@communications_bp.route("/log-outcome", methods=["POST"])
@login_required
def log_outcome():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    log_id = request.form.get("log_id", "").strip()
    status = request.form.get("status", "").strip()
    note = request.form.get("outcome_note", "").strip() or None

    if not log_id or not status:
        return Response(json.dumps({"ok": False, "error": "Missing log_id or status"}), mimetype="application/json")

    try:
        update_comm_log_status(int(log_id), status, outcome_note=note)
        return Response(json.dumps({"ok": True}), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}), mimetype="application/json")


@communications_bp.route("/history/customer/<customer_code>", methods=["GET"])
@login_required
def customer_history(customer_code):
    if not _role_ok():
        return Response(json.dumps([]), mimetype="application/json", status=403)

    history = get_customer_comm_history(customer_code)
    return Response(json.dumps(history, default=str), mimetype="application/json")


@communications_bp.route("/bulk/prepare", methods=["POST"])
@login_required
def bulk_prepare():
    if not _role_ok():
        flash("Not authorized.", "danger")
        return redirect(request.referrer or url_for("admin_dashboard"))

    codes = request.form.getlist("customer_codes[]")
    if not codes:
        codes_str = request.form.get("customer_codes", "")
        codes = [c.strip() for c in codes_str.split(",") if c.strip()]

    source = request.form.get("source_screen", "order_review")

    customers = []
    valid_count = 0
    invalid_count = 0

    for code in codes:
        ctx = resolve_customer_context(code)
        if ctx:
            ctx["selected"] = True
            if ctx.get("phone_valid"):
                valid_count += 1
            else:
                invalid_count += 1
            customers.append(ctx)
        else:
            invalid_count += 1

    templates = get_enabled_templates(channel_filter='microsms', bulk_only=True)

    # Enrich templates with offer image/url/title for the UI.
    enriched_templates = []
    for t in templates:
        full = get_template_full(t.get("code"))
        if full:
            t = dict(t)
            t["offer_image_url"] = full.get("offer_image_url") or ""
            t["offer_title"] = full.get("offer_title") or ""
        enriched_templates.append(t)

    return render_template(
        "admin/communications_bulk_send.html",
        customers=customers,
        templates=enriched_templates,
        valid_count=valid_count,
        invalid_count=invalid_count,
        total_selected=len(codes),
        source_screen=source,
    )


@communications_bp.route("/translate-draft", methods=["POST"])
@login_required
def route_translate_draft():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}),
                        mimetype="application/json", status=403)
    text = (request.form.get("text") or "").strip()
    src = (request.form.get("source_lang") or "el").strip().lower()
    tgt = (request.form.get("target_lang") or "en").strip().lower()
    if not text:
        return Response(json.dumps({"ok": False, "error": "Text is required"}),
                        mimetype="application/json")
    result = generate_translated_draft(text, source_lang=src, target_lang=tgt)
    return Response(json.dumps(result), mimetype="application/json")


@communications_bp.route("/bulk/preview", methods=["POST"])
@login_required
def bulk_preview():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}),
                        mimetype="application/json", status=403)
    codes = request.form.getlist("customer_codes[]")
    if not codes:
        codes_str = request.form.get("customer_codes", "")
        codes = [c.strip() for c in codes_str.split(",") if c.strip()]
    greek_draft = request.form.get("greek_draft", "")
    english_draft = request.form.get("english_draft", "") or None
    tpl_code = (request.form.get("template_code") or "").strip() or None

    rows = build_preview_rows(codes, greek_draft, english_draft, tpl_code)

    template_meta = None
    if tpl_code:
        full = get_template_full(tpl_code)
        if full:
            template_meta = {
                "offer_image_url": full.get("offer_image_url") or "",
                "offer_title": full.get("offer_title") or "",
            }

    return Response(json.dumps({
        "ok": True,
        "rows": rows,
        "template_meta": template_meta,
    }, default=str), mimetype="application/json")


@communications_bp.route("/bulk/send-finalized", methods=["POST"])
@login_required
def bulk_send_finalized():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}),
                        mimetype="application/json", status=403)

    payload_raw = request.form.get("payload", "").strip()
    if not payload_raw:
        return Response(json.dumps({"ok": False, "error": "Missing payload"}),
                        mimetype="application/json")
    try:
        payload = json.loads(payload_raw)
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": f"Bad payload: {e}"}),
                        mimetype="application/json")

    rows = payload.get("rows") or []
    tpl_code = (payload.get("template_code") or "").strip() or None
    source = payload.get("source_screen") or "bulk_send"

    if not rows:
        return Response(json.dumps({"ok": False, "error": "No recipients"}),
                        mimetype="application/json")

    tpl_full = get_template_full(tpl_code) if tpl_code else None
    sender = (
        (tpl_full.get("sender_title") if tpl_full else None)
        or os.getenv("MICROSMS_SENDER", "EPLATTFORMA")
    )
    tpl_title = tpl_full.get("title") if tpl_full else None

    batch_id = f"bulk-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    username = getattr(current_user, "username", None)

    total_sent = 0
    total_failed = 0
    total_skipped = 0
    results = []

    for r in rows:
        code = r.get("customer_code")
        text = (r.get("final_text") or "").strip()
        e164 = (r.get("mobile_e164") or "").strip()
        lang = r.get("language") or "el"
        cname = r.get("customer_name") or None

        if not e164 or not text:
            total_skipped += 1
            results.append({"code": code, "status": "skipped",
                            "reason": "missing phone or text"})
            continue

        result = send_finalized_sms(
            mobile_e164=e164,
            sender_title=sender,
            final_text=text,
            customer_code_365=code,
            customer_name=cname,
            template_code=tpl_code,
            template_title=tpl_title,
            source_screen=source,
            batch_id=batch_id,
            username=username,
            language=lang,
        )

        if result.get("ok"):
            total_sent += 1
            results.append({"code": code, "status": "sent", "language": lang})
        else:
            total_failed += 1
            results.append({"code": code, "status": "failed",
                            "language": lang, "reason": result.get("error")})

    try:
        db.session.execute(db.text("""
            INSERT INTO crm_communication_batch (
                created_by_username, source_screen, channel,
                template_code, template_title,
                total_selected, total_valid, total_sent, total_failed, total_skipped,
                batch_id
            ) VALUES (
                :user, :source, 'microsms',
                :tpl_code, :tpl_title,
                :total_sel, :total_valid, :total_sent, :total_failed, :total_skip,
                :batch
            )
        """), {
            "user": username, "source": source,
            "tpl_code": tpl_code, "tpl_title": tpl_title,
            "total_sel": len(rows), "total_valid": total_sent + total_failed,
            "total_sent": total_sent, "total_failed": total_failed,
            "total_skip": total_skipped, "batch": batch_id,
        })
        db.session.commit()
    except Exception:
        db.session.rollback()

    return Response(json.dumps({
        "ok": True,
        "batch_id": batch_id,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "total_skipped": total_skipped,
        "results": results,
    }), mimetype="application/json")


@communications_bp.route("/customer/set-language", methods=["POST"])
@login_required
def customer_set_language():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}),
                        mimetype="application/json", status=403)
    cc = (request.form.get("customer_code_365") or "").strip()
    lang = (request.form.get("preferred_language") or "").strip().lower()
    if not cc or lang not in ("el", "en"):
        return Response(json.dumps({"ok": False, "error": "Bad parameters"}),
                        mimetype="application/json")
    try:
        db.session.execute(db.text(
            "UPDATE ps_customers SET preferred_language = :l WHERE customer_code_365 = :c"
        ), {"l": lang, "c": cc})
        db.session.commit()
        return Response(json.dumps({"ok": True, "preferred_language": lang}),
                        mimetype="application/json")
    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"ok": False, "error": str(e)}),
                        mimetype="application/json")


@communications_bp.route("/bulk/send-microsms", methods=["POST"])
@login_required
def bulk_send_microsms():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    codes = request.form.getlist("customer_codes[]")
    tpl_code = request.form.get("template_code", "").strip()
    source = request.form.get("source_screen", "order_review")

    if not codes or not tpl_code:
        return Response(json.dumps({"ok": False, "error": "Missing customer codes or template"}), mimetype="application/json")

    tpl_row = db.session.execute(db.text(
        "SELECT code, title, sender_title, body FROM sms_template WHERE code = :c AND is_enabled = true AND is_bulk_allowed = true"
    ), {"c": tpl_code}).mappings().first()

    if not tpl_row:
        return Response(json.dumps({"ok": False, "error": "Template not found or not bulk-enabled"}), mimetype="application/json")

    import os
    batch_id = f"bulk-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    sender = (tpl_row.get("sender_title") or os.getenv("MICROSMS_SENDER", "EPLATTFORMA")).strip()
    username = getattr(current_user, "username", None)

    total_sent = 0
    total_failed = 0
    total_skipped = 0
    results = []

    for code in codes:
        ctx = resolve_customer_context(code)
        if not ctx or not ctx.get("phone_valid"):
            total_skipped += 1
            results.append({"code": code, "status": "skipped", "reason": "invalid phone"})
            continue

        rendered = render_template_for_customer(tpl_code, ctx)
        if "error" in rendered:
            total_skipped += 1
            results.append({"code": code, "status": "skipped", "reason": rendered["error"]})
            continue

        result = send_microsms(
            mobile_e164=ctx["primary_e164"],
            sender_title=sender,
            message=rendered["rendered_body"],
            customer_code_365=code,
            customer_name=ctx.get("customer_name"),
            template_code=tpl_code,
            template_title=tpl_row["title"],
            source_screen=source,
            context_type="order_review",
            context_id=code,
            batch_id=batch_id,
            username=username,
        )

        if result["ok"]:
            total_sent += 1
            results.append({"code": code, "status": "sent"})
        else:
            total_failed += 1
            results.append({"code": code, "status": "failed", "reason": result.get("error")})

    db.session.execute(db.text("""
        INSERT INTO crm_communication_batch (
            created_by_username, source_screen, channel,
            template_code, template_title,
            total_selected, total_valid, total_sent, total_failed, total_skipped,
            batch_id
        ) VALUES (
            :user, :source, 'microsms',
            :tpl_code, :tpl_title,
            :total_sel, :total_valid, :total_sent, :total_failed, :total_skip,
            :batch
        )
    """), {
        "user": username, "source": source,
        "tpl_code": tpl_code, "tpl_title": tpl_row["title"],
        "total_sel": len(codes), "total_valid": total_sent + total_failed,
        "total_sent": total_sent, "total_failed": total_failed, "total_skip": total_skipped,
        "batch": batch_id,
    })
    db.session.commit()

    return Response(json.dumps({
        "ok": True,
        "batch_id": batch_id,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "total_skipped": total_skipped,
        "results": results,
    }), mimetype="application/json")


@communications_bp.route("/push/verify", methods=["POST"])
@login_required
def push_verify():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    cc = request.form.get("customer_code_365", "").strip()
    if not cc:
        return Response(json.dumps({"ok": False, "error": "Missing customer_code_365"}), mimetype="application/json")

    result = refresh_customer_push_identity(cc)
    return Response(json.dumps({
        "ok": True,
        "push_available": result["push_available"],
        "push_subscription_count": result["push_subscription_count"],
        "verified_at": result["verified_at"],
        "error": result.get("error"),
    }, default=str), mimetype="application/json")


@communications_bp.route("/push/cached-status", methods=["POST"])
@login_required
def push_cached_status():
    if not _role_ok():
        return Response(json.dumps({"ok": False}), mimetype="application/json", status=403)

    cc = request.form.get("customer_code_365", "").strip()
    if not cc:
        return Response(json.dumps({"ok": False}), mimetype="application/json")

    cached = get_cached_push_identity(cc)
    return Response(json.dumps({"ok": True, **cached}, default=str), mimetype="application/json")


@communications_bp.route("/push/send", methods=["POST"])
@login_required
def push_send():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    cc = request.form.get("customer_code_365", "").strip()
    title = request.form.get("title", "").strip()
    message = request.form.get("message", "").strip()
    push_url = request.form.get("url", "").strip() or None
    tpl_code = request.form.get("template_code", "").strip() or None
    source = request.form.get("source_screen", "").strip() or "customer_profile"

    push_target_type = request.form.get("push_target_type", "").strip() or None
    category_id = request.form.get("category_id", "").strip() or None
    product_id = request.form.get("product_id", "").strip() or None
    deep_link = request.form.get("deep_link", "").strip() or None

    if not cc or not message:
        return Response(json.dumps({"ok": False, "error": "Customer code and message are required"}), mimetype="application/json")

    if not title:
        title = "Notification"

    VALID_PUSH_TARGETS = {"none", "category", "product", "custom_deeplink"}
    if push_target_type and push_target_type not in VALID_PUSH_TARGETS:
        return Response(json.dumps({"ok": False, "error": f"Invalid push target type: {push_target_type}"}), mimetype="application/json")

    if push_target_type == "none" or not push_target_type:
        push_target_type = None
        category_id = None
        product_id = None
        deep_link = None
    elif push_target_type == "category":
        if not category_id or not category_id.isdigit():
            return Response(json.dumps({"ok": False, "error": "Category target requires a numeric category ID"}), mimetype="application/json")
        product_id = None
        deep_link = None
    elif push_target_type == "product":
        if not product_id or not product_id.isdigit():
            return Response(json.dumps({"ok": False, "error": "Product target requires a numeric product ID"}), mimetype="application/json")
        category_id = None
        deep_link = None
    elif push_target_type == "custom_deeplink":
        if not deep_link:
            return Response(json.dumps({"ok": False, "error": "Custom deep link target requires a deep link URL"}), mimetype="application/json")
        category_id = None
        product_id = None

    result = send_push_to_customer(
        customer_code_365=cc,
        title=title,
        body=message,
        url=push_url,
        source_screen=source,
        template_code=tpl_code,
        username=getattr(current_user, "username", None),
        push_target_type=push_target_type if push_target_type and push_target_type != "none" else None,
        category_id=category_id,
        product_id=product_id,
        deep_link=deep_link,
    )

    return Response(json.dumps(result, default=str), mimetype="application/json")


@communications_bp.route("/push/bulk-send", methods=["POST"])
@login_required
def push_bulk_send():
    if not _role_ok():
        return Response(json.dumps({"ok": False, "error": "Not authorized"}), mimetype="application/json", status=403)

    codes = request.form.getlist("customer_codes[]")
    if not codes:
        codes_str = request.form.get("customer_codes", "")
        codes = [c.strip() for c in codes_str.split(",") if c.strip()]

    title = request.form.get("title", "Notification").strip()
    message = request.form.get("message", "").strip()
    tpl_code = request.form.get("template_code", "").strip() or None
    source = request.form.get("source_screen", "order_review")

    if not codes or not message:
        return Response(json.dumps({"ok": False, "error": "Missing customer codes or message"}), mimetype="application/json")

    result = bulk_send_push(
        customer_codes=codes,
        title=title,
        body=message,
        template_code=tpl_code,
        source_screen=source,
        username=getattr(current_user, "username", None),
    )

    return Response(json.dumps(result, default=str), mimetype="application/json")


@communications_bp.route("/push/history/<customer_code>", methods=["GET"])
@login_required
def push_history(customer_code):
    if not _role_ok():
        return Response(json.dumps([]), mimetype="application/json", status=403)

    rows = db.session.execute(db.text("""
        SELECT id, created_at, created_by_username, status,
               template_code, message_text, outcome_note,
               provider_message_id, extra_json
        FROM crm_communication_log
        WHERE customer_code_365 = :cc AND channel = 'onesignal_push'
        ORDER BY created_at DESC
        LIMIT 30
    """), {"cc": customer_code}).mappings().all()

    return Response(json.dumps([dict(r) for r in rows], default=str), mimetype="application/json")
