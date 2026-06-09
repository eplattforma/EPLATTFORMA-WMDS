"""
Route Planner Blueprint
=======================
Standalone planning tool — does NOT read or write to shipment/route records.
Accessible at /route-planner/
"""

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from models import PSCustomer
from services.route_optimizer_planner import optimize_route
import logging

route_planner_bp = Blueprint("route_planner", __name__, url_prefix="/route-planner")
logger = logging.getLogger(__name__)


@route_planner_bp.route("/")
@login_required
def index():
    return render_template("route_planner.html")


@route_planner_bp.route("/api/search")
@login_required
def api_search():
    q = request.args.get("q", "").strip()
    include_no_coords = request.args.get("include_no_coords", "false").lower() == "true"

    if len(q) < 2:
        return jsonify([])

    base = PSCustomer.query.filter(
        (PSCustomer.company_name.ilike(f"%{q}%")) |
        (PSCustomer.customer_code_365.ilike(f"%{q}%"))
    ).filter(PSCustomer.active == True)

    if not include_no_coords:
        base = base.filter(
            PSCustomer.latitude.isnot(None),
            PSCustomer.longitude.isnot(None),
        )

    customers = base.order_by(PSCustomer.company_name).limit(25).all()

    return jsonify([
        {
            "customer_code": c.customer_code_365,
            "company_name": c.company_name or c.customer_code_365,
            "address": " · ".join(filter(None, [c.address_line_1, c.town])),
            "lat": c.latitude,
            "lng": c.longitude,
            "has_coords": c.latitude is not None and c.longitude is not None,
        }
        for c in customers
    ])


@route_planner_bp.route("/api/optimize", methods=["POST"])
@login_required
def api_optimize():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    stops = data.get("stops", [])
    settings = data.get("settings", {})

    if len(stops) < 2:
        return jsonify({"error": "At least 2 stops are required."}), 400

    missing_coords = [
        s.get("company_name", s.get("customer_code", "?"))
        for s in stops
        if not s.get("lat") or not s.get("lng")
    ]
    if missing_coords:
        return jsonify({
            "error": f"Missing GPS coordinates for: {', '.join(missing_coords)}"
        }), 400

    try:
        result = optimize_route(
            stops_input=stops,
            start_time_str=settings.get("start_time", "08:00"),
            avg_speed=float(settings.get("avg_speed", 30)),
            stop_duration=float(settings.get("stop_duration", 5)),
        )
        return jsonify(result)
    except Exception:
        logger.exception("Route optimisation failed")
        return jsonify({"error": "Optimisation failed — check server logs."}), 500
