"""
DW Analytics routes for customer opportunity analysis
"""
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from flask_login import login_required, current_user
import logging
import threading
from sqlalchemy import text

from app import app, db
from dw_analytics_models import (
    DwRecoBasket,
    DwCategoryPenetration,
    DwShareOfWallet,
    DwChurnRisk,
)
from models import DwItem
from dw_analytics_jobs import run_all_analytics

logger = logging.getLogger(__name__)

analytics_bp = Blueprint("analytics", __name__, url_prefix="/analytics")


@analytics_bp.route("/", methods=["GET"])
@login_required
def analytics_home():
    """Main analytics page - select customer to view insights"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    search_query = request.args.get("search", "").strip()
    
    # Get all customers with their codes and company names
    base_query = """
        SELECT DISTINCT 
            h.customer_code_365,
            COALESCE(pc.company_name, h.customer_code_365) as company_name
        FROM dw_invoice_header h
        LEFT JOIN ps_customers pc ON h.customer_code_365 = pc.customer_code_365
        ORDER BY COALESCE(pc.company_name, h.customer_code_365)
    """
    
    all_customers_raw = db.session.execute(text(base_query)).fetchall()
    # Convert to list of tuples (code, company_name)
    all_customers = [(c[0], c[1]) for c in all_customers_raw]
    
    # Filter customers based on search query (3+ characters, matching code or company name)
    customers = all_customers
    if search_query and len(search_query) >= 3:
        search_upper = search_query.upper()
        customers = [
            c for c in all_customers
            if search_upper in c[0].upper() or search_upper in c[1].upper()
        ]

    selected_customer = request.args.get("customer")
    selected_customer_name = None

    customer_data = None
    if selected_customer:
        # Get customer name
        customer_name_result = db.session.execute(text("""
            SELECT DISTINCT 
                COALESCE(pc.company_name, h.customer_code_365)
            FROM dw_invoice_header h
            LEFT JOIN ps_customers pc ON h.customer_code_365 = pc.customer_code_365
            WHERE h.customer_code_365 = :cust
            LIMIT 1
        """), {"cust": selected_customer}).fetchone()
        
        if customer_name_result:
            selected_customer_name = customer_name_result[0]
        
        # Share of wallet
        sow = DwShareOfWallet.query.filter_by(
            customer_code_365=selected_customer
        ).first()

        # Missing categories (has_category = 0)
        missing_categories = DwCategoryPenetration.query.filter_by(
            customer_code_365=selected_customer, has_category=0
        ).all()

        # Churn risks (churn_flag = 1)
        churn_risks = DwChurnRisk.query.filter_by(
            customer_code_365=selected_customer, churn_flag=1
        ).all()

        # Recommended items:
        # 1. find items this customer already buys
        items_rows = db.session.execute(text("""
            SELECT DISTINCT l.item_code_365
            FROM dw_invoice_line l
            JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
            WHERE h.customer_code_365 = :cust
        """), {"cust": selected_customer}).fetchall()
        customer_items = [r[0] for r in items_rows]

        if customer_items:
            # Get recommendations with product names
            recos_raw = (
                DwRecoBasket.query
                .filter(DwRecoBasket.from_item_code.in_(customer_items))
                .order_by(DwRecoBasket.lift.desc())
                .limit(50)
                .all()
            )
            # Fetch item names for from and to codes
            recos = []
            for reco in recos_raw:
                from_item = DwItem.query.filter_by(item_code_365=reco.from_item_code).first()
                to_item = DwItem.query.filter_by(item_code_365=reco.to_item_code).first()
                recos.append({
                    'from_item_code': reco.from_item_code,
                    'from_item_name': from_item.item_name if from_item else 'Unknown',
                    'to_item_code': reco.to_item_code,
                    'to_item_name': to_item.item_name if to_item else 'Unknown',
                    'confidence': reco.confidence,
                    'lift': reco.lift,
                })
        else:
            recos = []

        customer_data = {
            "share_of_wallet": sow,
            "missing_categories": missing_categories,
            "churn_risks": churn_risks,
            "recommendations": recos,
        }

    return render_template(
        "analytics_home.html",
        customers=customers,
        selected_customer=selected_customer,
        selected_customer_name=selected_customer_name,
        customer_data=customer_data,
        search_query=search_query,
    )


@analytics_bp.route("/run-jobs", methods=["POST"])
@login_required
def run_analytics_jobs():
    """Trigger analytics job execution in background"""
    if current_user.role != 'admin':
        return jsonify({"error": "Access denied"}), 403
    
    def worker():
        try:
            with app.app_context():
                run_all_analytics()
                logger.info("Analytics jobs completed successfully")
        except Exception as e:
            logger.error(f"Error running analytics jobs: {str(e)}", exc_info=True)
    
    thread = threading.Thread(target=worker, daemon=False)
    thread.start()
    
    flash("Analytics jobs started in background. Please wait a moment and refresh.", "info")
    return redirect(url_for("analytics.analytics_home"))


@analytics_bp.route("/top-opportunities", methods=["GET"])
@login_required
def top_opportunities():
    """View top cross-sell opportunities globally"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    # Top wallet gaps
    top_gaps = DwShareOfWallet.query.order_by(
        DwShareOfWallet.opportunity_gap.desc()
    ).limit(20).all()
    
    # Top rules with product names
    top_rules_raw = DwRecoBasket.query.order_by(
        DwRecoBasket.lift.desc().nullslast()
    ).limit(20).all()
    
    top_rules = []
    for rule in top_rules_raw:
        from_item = DwItem.query.filter_by(item_code_365=rule.from_item_code).first()
        to_item = DwItem.query.filter_by(item_code_365=rule.to_item_code).first()
        top_rules.append({
            'from_item_code': rule.from_item_code,
            'from_item_name': from_item.item_name if from_item else 'Unknown',
            'to_item_code': rule.to_item_code,
            'to_item_name': to_item.item_name if to_item else 'Unknown',
            'support': rule.support,
            'confidence': rule.confidence,
            'lift': rule.lift,
        })
    
    # Top churn risks
    top_churns = DwChurnRisk.query.filter_by(churn_flag=1).order_by(
        DwChurnRisk.drop_pct.desc()
    ).limit(20).all()
    
    return render_template(
        "analytics_opportunities.html",
        top_gaps=top_gaps,
        top_rules=top_rules,
        top_churns=top_churns,
    )


@analytics_bp.route("/help", methods=["GET"])
@login_required
def analytics_help():
    """Show FYI/Help page explaining analytics features"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    return render_template("analytics_help.html")
