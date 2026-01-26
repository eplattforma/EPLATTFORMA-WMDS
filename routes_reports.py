import os
import subprocess
from flask import Blueprint, render_template, send_file, redirect, url_for, flash, Response
from flask_login import login_required, current_user

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

CACHE_DIR = os.path.join(os.getcwd(), "reports_cache")
CSV_PATH = os.path.join(CACHE_DIR, "reserved_stock_777_latest.csv")


@reports_bp.route("/reserved-stock-777")
@login_required
def reserved_stock_777():
    from models import Ps365ReservedStock777
    
    rows = Ps365ReservedStock777.query.order_by(
        Ps365ReservedStock777.stock_reserved.desc(),
        Ps365ReservedStock777.item_code_365
    ).all()
    
    seasons = sorted(set(r.season_name for r in rows if r.season_name))
    
    synced_at = None
    if rows:
        synced_at = rows[0].synced_at
    
    return render_template(
        "reports/reserved_stock_777.html",
        rows=rows,
        seasons=seasons,
        synced_at=synced_at,
        count=len(rows)
    )


@reports_bp.route("/reserved-stock-777/download")
@login_required
def reserved_stock_777_download():
    import csv
    import io
    from models import Ps365ReservedStock777
    
    rows = Ps365ReservedStock777.query.order_by(
        Ps365ReservedStock777.stock_reserved.desc(),
        Ps365ReservedStock777.item_code_365
    ).all()
    
    if not rows:
        flash("No data found. Run the report sync first.", "warning")
        return redirect(url_for("reports.reserved_stock_777"))
    
    output = io.StringIO()
    fieldnames = [
        "item_code_365", "item_name", "season_code_365", "season_name",
        "number_of_pieces", "number_field_5_value",
        "stock", "stock_reserved", "available_stock", "stock_ordered", "stock_on_transfer"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for r in rows:
        writer.writerow({
            "item_code_365": r.item_code_365,
            "item_name": r.item_name,
            "season_code_365": r.season_code_365 or "",
            "season_name": r.season_name or "",
            "number_of_pieces": str(r.number_of_pieces or 0),
            "number_field_5_value": str(r.number_field_5_value or 0),
            "stock": str(r.stock),
            "stock_reserved": str(r.stock_reserved),
            "available_stock": str(r.available_stock),
            "stock_ordered": str(r.stock_ordered),
            "stock_on_transfer": str(r.stock_on_transfer),
        })
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=reserved_stock_777.csv"}
    )


@reports_bp.route("/reserved-stock-777/refresh")
@login_required
def reserved_stock_777_refresh():
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash("Only admins can refresh the report.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    flash("To refresh: Run 'python scripts/ps365_reserved_stock_report_777.py' from the shell.", "info")
    return redirect(url_for("reports.reserved_stock_777"))
