"""
Flask blueprint for invoice status updates in routes
"""
from flask import Blueprint, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from models import RouteStopInvoice, RouteStop
import services

bp = Blueprint("route_invoices", __name__)


@bp.route("/stops/<int:route_stop_id>/invoices/<invoice_no>/status", methods=["POST"])
@login_required
def update_status(route_stop_id, invoice_no):
    """Update invoice status within a route stop"""
    data = request.form
    status = data.get("status")
    mirror = data.get("mirror_invoice", "true").lower() == "true"
    
    if not status:
        if request.is_json or request.accept_mimetypes.best == 'application/json':
            return jsonify({"error": "Status is required"}), 400
        flash("Status is required", "error")
        return redirect(request.referrer or url_for("delivery_dashboard.dashboard"))
    
    try:
        rsi = services.set_invoice_in_stop_status(route_stop_id, invoice_no, status, mirror)
        
        if request.is_json or request.accept_mimetypes.best == 'application/json':
            return jsonify({
                "success": True,
                "invoice_no": invoice_no,
                "status": rsi.status
            })
        
        flash(f"Invoice {invoice_no} status updated to {status}", "success")
        return redirect(request.referrer or url_for("delivery_dashboard.dashboard"))
        
    except Exception as e:
        if request.is_json or request.accept_mimetypes.best == 'application/json':
            return jsonify({"error": str(e)}), 500
        flash(f"Error updating status: {str(e)}", "error")
        return redirect(request.referrer or url_for("delivery_dashboard.dashboard"))


@bp.route("/invoices/<invoice_no>/status", methods=["GET"])
@login_required
def get_status(invoice_no):
    """Get current status of invoice in all routes"""
    route_statuses = RouteStopInvoice.query.filter_by(invoice_no=invoice_no).all()
    
    results = []
    for rsi in route_statuses:
        stop = rsi.stop
        results.append({
            "route_stop_id": stop.route_stop_id,
            "seq_no": stop.seq_no,
            "stop_name": stop.stop_name,
            "shipment_id": stop.shipment_id,
            "status": rsi.status
        })
    
    return jsonify(results)
