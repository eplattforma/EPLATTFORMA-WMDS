import os
import json
from flask import Blueprint, render_template, send_file, redirect, url_for, flash
from flask_login import login_required

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

CACHE_DIR = os.path.join(os.getcwd(), "reports_cache")
JSON_PATH = os.path.join(CACHE_DIR, "reserved_stock_777_latest.json")
CSV_PATH = os.path.join(CACHE_DIR, "reserved_stock_777_latest.csv")

@reports_bp.route("/reserved-stock-777")
@login_required
def reserved_stock_777():
    if not os.path.exists(JSON_PATH):
        return render_template("reports/reserved_stock_777.html", payload=None)

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)

    return render_template("reports/reserved_stock_777.html", payload=payload)

@reports_bp.route("/reserved-stock-777/download")
@login_required
def reserved_stock_777_download():
    if not os.path.exists(CSV_PATH):
        flash("CSV not found. Run the report generator first.", "warning")
        return redirect(url_for("reports.reserved_stock_777"))
    return send_file(CSV_PATH, as_attachment=True, download_name="reserved_stock_777_latest.csv")

@reports_bp.route("/reserved-stock-777/refresh")
@login_required
def reserved_stock_777_refresh():
    flash("Refresh is disabled in UI. Run: python reports/ps365_reserved_stock_777_report.py", "info")
    return redirect(url_for("reports.reserved_stock_777"))
