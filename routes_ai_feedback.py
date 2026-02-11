import json
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

ai_feedback_bp = Blueprint("ai_feedback", __name__, url_prefix="/api/ai")

ALLOWED_ROLES = {"admin", "warehouse_manager"}


@ai_feedback_bp.route("/feedback", methods=["POST"])
@login_required
def feedback():
    if getattr(current_user, "role", None) not in ALLOWED_ROLES:
        return jsonify({"error": "Access denied"}), 403

    data = request.get_json(silent=True) or {}
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, dict):
        return jsonify({"error": "Missing snapshot data"}), 400

    raw = json.dumps(snapshot, ensure_ascii=False)
    if len(raw) > 250_000:
        return jsonify({"error": "Payload too large; send aggregates only"}), 400

    try:
        from ai_feedback_service import generate_feedback
        out = generate_feedback(snapshot)
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
