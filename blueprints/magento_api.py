import logging
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

magento_api_bp = Blueprint("magento_api", __name__, url_prefix="/api/magento")


@magento_api_bp.route("/customer-last-login", methods=["GET"])
@login_required
def customer_last_login():
    if current_user.role not in ["admin", "warehouse_manager"]:
        return jsonify({"ok": False, "error": "Access denied"}), 403

    customer_id = request.args.get("customer_id")
    email = request.args.get("email")
    ps365_code = request.args.get("ps365_code")

    if not any([customer_id, email, ps365_code]):
        return jsonify({
            "ok": False,
            "error": "At least one filter required: customer_id, email, or ps365_code"
        }), 400

    logger.info("customer-last-login called: customer_id=%s email=%s ps365_code=%s",
                customer_id, email, ps365_code)

    from services.magento_login_logs_db import get_customer_last_login
    try:
        result = get_customer_last_login(
            customer_id=customer_id,
            email=email,
            ps365_code=ps365_code,
        )
        if result is None:
            return jsonify({"ok": True, "found": False, "login": None})
        return jsonify({"ok": True, "found": True, "login": result})
    except ValueError as e:
        logger.warning("customer-last-login validation error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 400
    except RuntimeError as e:
        logger.error("customer-last-login runtime error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 502
    except Exception as e:
        logger.error("customer-last-login error: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "Failed to fetch login data"}), 500
