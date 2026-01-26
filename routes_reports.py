import os
import io
import csv
import math
import sys
from flask import Blueprint, render_template, Response, flash, redirect, url_for
from flask_login import login_required, current_user

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

@reports_bp.route("/reserved-stock-777")
@login_required
def reserved_stock_777():
    from models import Ps365ReservedStock777
    rows = Ps365ReservedStock777.query.order_by(Ps365ReservedStock777.stock_reserved.desc(), Ps365ReservedStock777.item_code_365).all()
    seasons = sorted(set(r.season_name for r in rows if r.season_name))
    synced_at = rows[0].synced_at if rows else None
    return render_template("reports/reserved_stock_777.html", rows=rows, seasons=seasons, synced_at=synced_at, count=len(rows))

@reports_bp.route("/reserved-stock-777/download")
@login_required
def reserved_stock_777_download():
    from models import Ps365ReservedStock777
    rows = Ps365ReservedStock777.query.order_by(Ps365ReservedStock777.stock_reserved.desc(), Ps365ReservedStock777.item_code_365).all()
    if not rows:
        flash("No data found.", "warning")
        return redirect(url_for("reports.reserved_stock_777"))
    
    output = io.StringIO()
    fieldnames = ["item_code_365", "item_name", "supplier", "pieces_per_unit", "min_order_qty", "stock", "customer_order", "available", "on_po", "required"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        stock_val = float(r.stock or 0)
        reserved_val = float(r.stock_reserved or 0)
        ordered_val = float(r.stock_ordered or 0)
        req = max(0, reserved_val - stock_val - ordered_val)
        writer.writerow({
            "item_code_365": r.item_code_365,
            "item_name": r.item_name,
            "supplier": r.season_name or "",
            "pieces_per_unit": int(r.number_of_pieces or 0),
            "min_order_qty": int(r.number_field_5_value or 0),
            "stock": round(stock_val, 1),
            "customer_order": int(reserved_val),
            "available": int(r.available_stock or 0),
            "on_po": int(ordered_val),
            "required": int(math.ceil(req))
        })
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=reserved_stock_777.csv"})

@reports_bp.route("/reserved-stock-777/refresh")
@login_required
def reserved_stock_777_refresh():
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash("Access denied.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    import subprocess
    try:
        subprocess.run([sys.executable, "scripts/ps365_reserved_stock_report_777.py"], check=True)
        flash("Report refreshed successfully.", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("reports.reserved_stock_777"))
