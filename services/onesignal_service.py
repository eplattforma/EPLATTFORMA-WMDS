import os
import json
import uuid
import logging
from datetime import datetime

import requests as req_lib
from app import db
from services.communications_service import (
    create_comm_log, update_comm_log_status, resolve_customer_context,
    render_template_for_customer,
)

logger = logging.getLogger(__name__)

ONESIGNAL_API_BASE = os.getenv("ONESIGNAL_API_BASE", "https://onesignal.com/api/v1")


def _headers():
    api_key = os.getenv("ONESIGNAL_API_KEY", "").strip()
    return {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _app_id():
    return os.getenv("ONESIGNAL_APP_ID", "").strip()


def _resolve_magento_id(customer_code_365):
    row = db.session.execute(db.text("""
        SELECT DISTINCT magento_customer_id
        FROM magento_customer_login_log
        WHERE customer_code_365 = :cc
        LIMIT 1
    """), {"cc": customer_code_365}).first()
    if row:
        return str(row[0])
    row2 = db.session.execute(db.text("""
        SELECT DISTINCT magento_customer_id
        FROM magento_customer_last_login_current
        WHERE customer_code_365 = :cc
        LIMIT 1
    """), {"cc": customer_code_365}).first()
    if row2:
        return str(row2[0])
    return None


def view_user_by_external_id(customer_code_365):
    app_id = _app_id()
    if not app_id:
        return {"error": "ONESIGNAL_APP_ID not configured", "status_code": 0}

    magento_id = _resolve_magento_id(customer_code_365)
    if not magento_id:
        logger.info(f"No Magento ID found for customer {customer_code_365}")
        return {"error": "no_magento_id", "status_code": 0,
                "detail": "Customer has no linked Magento account"}

    url = f"{ONESIGNAL_API_BASE}/players?app_id={app_id}&limit=300"

    try:
        r = req_lib.get(url, headers=_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        players = data.get("players", [])
        total = data.get("total_count", 0)
        matching = [p for p in players if str(p.get("external_user_id", "")) == magento_id]

        if not matching and total > 300:
            offset = 300
            while offset < total and not matching:
                batch_url = f"{ONESIGNAL_API_BASE}/players?app_id={app_id}&limit=300&offset={offset}"
                rb = req_lib.get(batch_url, headers=_headers(), timeout=15)
                rb.raise_for_status()
                batch = rb.json().get("players", [])
                if not batch:
                    break
                matching = [p for p in batch if str(p.get("external_user_id", "")) == magento_id]
                offset += 300

        if not matching:
            return {"error": "user_not_found", "status_code": 404,
                    "magento_id": magento_id}

        def _is_push_active(player):
            nt = player.get("notification_types")
            if nt is not None and int(nt) < 0:
                return False
            if player.get("invalid_identifier"):
                return False
            if not player.get("identifier"):
                return False
            return True

        return {
            "subscriptions": [
                {
                    "type": "iOSPush" if p.get("device_type") == 0 else "AndroidPush" if p.get("device_type") == 1 else "Push",
                    "enabled": _is_push_active(p),
                    "id": p.get("id"),
                }
                for p in matching
            ],
            "magento_id": magento_id,
            "external_id": magento_id,
        }
    except req_lib.exceptions.HTTPError as e:
        logger.warning(f"OneSignal view user HTTP error for {customer_code_365}: {e}")
        return {"error": str(e), "status_code": getattr(e.response, 'status_code', 0)}
    except req_lib.exceptions.Timeout:
        logger.warning(f"OneSignal view user timeout for {customer_code_365}")
        return {"error": "timeout", "status_code": 0}
    except Exception as e:
        logger.warning(f"OneSignal view user error for {customer_code_365}: {e}")
        return {"error": str(e), "status_code": 0}


def extract_active_push_status(user_payload):
    if not user_payload or "error" in user_payload:
        return {"push_available": False, "push_subscription_count": 0}

    subscriptions = user_payload.get("subscriptions", [])
    if not subscriptions:
        properties = user_payload.get("properties", {})
        if isinstance(properties, dict):
            subscriptions = properties.get("subscriptions", [])

    active_push_count = 0
    for sub in subscriptions:
        sub_type = sub.get("type", "")
        is_push = sub_type in (
            "AndroidPush", "iOSPush", "ChromeExtensionPush",
            "ChromePush", "FirefoxPush", "SafariPush",
            "WindowsPush", "HuaweiPush", "macOSPush",
        )
        if not is_push:
            is_push = "Push" in sub_type or sub_type == "Email" and False

        enabled = sub.get("enabled", False)
        if is_push and enabled:
            active_push_count += 1

    return {
        "push_available": active_push_count > 0,
        "push_subscription_count": active_push_count,
    }


def refresh_customer_push_identity(customer_code_365):
    user_payload = view_user_by_external_id(customer_code_365)

    push_status = extract_active_push_status(user_payload)
    push_available = push_status["push_available"]
    push_sub_count = push_status["push_subscription_count"]

    magento_id = user_payload.get("magento_id") or user_payload.get("external_id") or customer_code_365

    now = datetime.utcnow()

    safe_response = None
    if "error" not in user_payload:
        try:
            safe_response = json.dumps(user_payload, default=str)
            if len(safe_response) > 10000:
                safe_response = json.dumps({
                    "subscriptions_count": len(user_payload.get("subscriptions", [])),
                    "push_available": push_available,
                    "truncated": True,
                }, default=str)
        except Exception:
            safe_response = json.dumps({"parse_error": True})
    else:
        safe_response = json.dumps(user_payload, default=str)

    try:
        db.session.execute(db.text("""
            INSERT INTO customer_push_identity (
                customer_code_365, onesignal_external_id,
                push_available, push_subscription_count,
                last_verified_at, last_provider_response,
                created_at, updated_at
            ) VALUES (
                :cc, :ext_id,
                :avail, :sub_count,
                :now, CAST(:resp AS jsonb),
                :now, :now
            )
            ON CONFLICT (customer_code_365) DO UPDATE SET
                push_available = :avail,
                push_subscription_count = :sub_count,
                last_verified_at = :now,
                last_provider_response = CAST(:resp AS jsonb),
                updated_at = :now
        """), {
            "cc": customer_code_365,
            "ext_id": magento_id,
            "avail": push_available,
            "sub_count": push_sub_count,
            "now": now,
            "resp": safe_response,
        })
        db.session.commit()
    except Exception as e:
        logger.warning(f"Failed to upsert customer_push_identity: {e}")
        db.session.rollback()

    return {
        "customer_code_365": customer_code_365,
        "magento_id": magento_id,
        "push_available": push_available,
        "push_subscription_count": push_sub_count,
        "verified_at": now.isoformat(),
        "error": user_payload.get("error"),
    }


def get_cached_push_identity(customer_code_365):
    row = db.session.execute(db.text("""
        SELECT push_available, push_subscription_count, last_verified_at
        FROM customer_push_identity
        WHERE customer_code_365 = :cc
    """), {"cc": customer_code_365}).mappings().first()
    if row:
        return {
            "push_available": bool(row["push_available"]),
            "push_subscription_count": row["push_subscription_count"],
            "verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
            "cached": True,
        }
    return {
        "push_available": False,
        "push_subscription_count": 0,
        "verified_at": None,
        "cached": False,
        "status_label": "unknown",
    }


def send_push_to_customer(customer_code_365, title, body, url=None,
                          source_screen=None, template_code=None,
                          username=None):
    identity = refresh_customer_push_identity(customer_code_365)

    customer_ctx = resolve_customer_context(customer_code_365)
    customer_name = customer_ctx.get("customer_name") if customer_ctx else customer_code_365

    tpl_title = None
    if template_code:
        tpl_row = db.session.execute(db.text(
            "SELECT title FROM sms_template WHERE code = :c"
        ), {"c": template_code}).mappings().first()
        if tpl_row:
            tpl_title = tpl_row["title"]

    if not identity["push_available"]:
        log_id = create_comm_log(
            channel='onesignal_push',
            customer_code_365=customer_code_365,
            customer_name=customer_name,
            source_screen=source_screen,
            context_type='customer',
            context_id=customer_code_365,
            template_code=template_code,
            template_title=tpl_title,
            message_text=body,
            status='skipped_no_subscription',
            username=username,
            extra_json=json.dumps({
                "push_title": title,
                "push_url": url,
                "verification": identity,
            }, default=str),
        )
        return {
            "ok": False,
            "log_id": log_id,
            "status": "skipped_no_subscription",
            "error": "No active push subscription for this customer",
            "push_available": False,
        }

    log_id = create_comm_log(
        channel='onesignal_push',
        customer_code_365=customer_code_365,
        customer_name=customer_name,
        source_screen=source_screen,
        context_type='customer',
        context_id=customer_code_365,
        template_code=template_code,
        template_title=tpl_title,
        message_text=body,
        status='initiated',
        username=username,
        extra_json=json.dumps({
            "push_title": title,
            "push_url": url,
        }, default=str),
    )

    magento_id = identity.get("magento_id") or _resolve_magento_id(customer_code_365) or customer_code_365

    app_id = _app_id()
    payload = {
        "app_id": app_id,
        "include_aliases": {
            "external_id": [str(magento_id)]
        },
        "target_channel": "push",
        "headings": {"en": title or "Notification"},
        "contents": {"en": body or ""},
    }
    if url:
        payload["url"] = url

    raw_response = ""
    provider_msg_id = None
    status = "failed"

    try:
        r = req_lib.post(
            f"{ONESIGNAL_API_BASE}/notifications",
            headers=_headers(),
            json=payload,
            timeout=15,
        )
        raw_response = r.text
        data = r.json()

        if r.status_code in (200, 201) and data.get("id"):
            status = "sent"
            provider_msg_id = data.get("id")
        else:
            status = "failed"
            errors = data.get("errors", [])
            if errors:
                raw_response = json.dumps(errors)
    except req_lib.exceptions.Timeout:
        raw_response = "timeout"
    except Exception as e:
        raw_response = f"EXCEPTION: {type(e).__name__}: {e}"

    update_comm_log_status(log_id, status, provider_fields={
        "provider_name": "onesignal",
        "provider_message_id": provider_msg_id,
        "provider_raw_response": raw_response[:2000],
    })

    return {
        "ok": status == "sent",
        "log_id": log_id,
        "status": status,
        "message_id": provider_msg_id,
        "error": raw_response if status == "failed" else None,
        "push_available": True,
    }


def bulk_send_push(customer_codes, title, body, template_code=None,
                   source_screen='order_review', username=None):
    counters = {
        "selected": len(customer_codes),
        "verified": 0,
        "sent": 0,
        "failed": 0,
        "skipped_no_subscription": 0,
    }
    results = []

    batch_id = f"push-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    for code in customer_codes:
        result = send_push_to_customer(
            customer_code_365=code,
            title=title,
            body=body,
            source_screen=source_screen,
            template_code=template_code,
            username=username,
        )

        if result.get("push_available"):
            counters["verified"] += 1

        if result["status"] == "sent":
            counters["sent"] += 1
            results.append({"code": code, "status": "sent"})
        elif result["status"] == "skipped_no_subscription":
            counters["skipped_no_subscription"] += 1
            results.append({"code": code, "status": "skipped_no_subscription"})
        else:
            counters["failed"] += 1
            results.append({"code": code, "status": "failed", "error": result.get("error")})

    try:
        db.session.execute(db.text("""
            INSERT INTO crm_communication_batch (
                created_by_username, source_screen, channel,
                template_code, total_selected, total_valid,
                total_sent, total_failed, total_skipped,
                batch_id
            ) VALUES (
                :user, :source, 'onesignal_push',
                :tpl, :sel, :valid,
                :sent, :failed, :skip,
                :batch
            )
        """), {
            "user": username, "source": source_screen,
            "tpl": template_code,
            "sel": counters["selected"], "valid": counters["verified"],
            "sent": counters["sent"], "failed": counters["failed"],
            "skip": counters["skipped_no_subscription"],
            "batch": batch_id,
        })
        db.session.commit()
    except Exception as e:
        logger.warning(f"Failed to write push batch record: {e}")
        db.session.rollback()

    return {
        "ok": True,
        "batch_id": batch_id,
        "counters": counters,
        "results": results,
    }
