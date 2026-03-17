import json
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from app import db

from services.communications_service import (
    resolve_customer_context, normalize_phone, render_template_for_customer,
    create_comm_log, update_comm_log_status, send_microsms,
    build_launch_url, get_customer_comm_history, get_enabled_templates,
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

    return render_template(
        "admin/communications_bulk_send.html",
        customers=customers,
        templates=templates,
        valid_count=valid_count,
        invalid_count=invalid_count,
        total_selected=len(codes),
        source_screen=source,
    )


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
