"""
Pallets Blueprint - Routes for pallet management in delivery routes.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from functools import wraps
from decimal import Decimal

from app import db
from models import Shipment, Invoice, WmsPallet, WmsPalletOrder, RouteStopInvoice
from services_palletization import (
    get_order_pallet_hints, allocate_order_to_pallet, unassign_order_from_pallet,
    create_pallet_for_route, toggle_pallet_seal
)
from services_order_packing_summary import get_order_packing_summary
from pallet_masks import count_free_blocks, mask_to_grid_display
from timezone_utils import get_utc_now

bp = Blueprint('pallets', __name__)


def admin_or_warehouse_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.role not in ['admin', 'warehouse_manager']:
            flash('Access denied.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@bp.route('/<int:shipment_id>/pallets')
@login_required
@admin_or_warehouse_required
def route_pallets(shipment_id):
    """Pallet management dashboard for a route."""
    shipment = Shipment.query.get_or_404(shipment_id)
    
    pallets = WmsPallet.query.filter_by(shipment_id=shipment_id).order_by(WmsPallet.label).all()
    
    hints = get_order_pallet_hints(shipment_id)
    
    assigned_invoice_nos = set()
    for pallet in pallets:
        for order in pallet.orders:
            assigned_invoice_nos.add(order.invoice_no)
    
    unassigned_orders = []
    for invoice_no, hint in hints.items():
        if invoice_no not in assigned_invoice_nos:
            invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
            packing = get_order_packing_summary(invoice_no)
            unassigned_orders.append({
                'invoice_no': invoice_no,
                'customer_name': invoice.customer_name if invoice else 'Unknown',
                'hint': hint,
                'packing': packing,
            })
    
    unassigned_orders.sort(key=lambda x: x['hint'].get('stop_seq') or 999)
    
    pallet_data = []
    for pallet in pallets:
        grid = mask_to_grid_display(pallet.used_mask)
        free_blocks = count_free_blocks(pallet.used_mask)
        used_blocks = 8 - free_blocks
        
        order_masks = {}
        for order in pallet.orders:
            for i in range(8):
                if order.blocks_mask & (1 << i):
                    order_masks[i] = {
                        'invoice_no': order.invoice_no,
                        'hint': hints.get(order.invoice_no, {}),
                    }
        
        pallet_data.append({
            'pallet': pallet,
            'grid': grid,
            'free_blocks': free_blocks,
            'used_blocks': used_blocks,
            'order_masks': order_masks,
            'weight_pct': int((float(pallet.used_weight_kg or 0) / float(pallet.max_weight_kg or 500)) * 100),
        })
    
    open_pallets = [p['pallet'] for p in pallet_data if p['pallet'].status == 'OPEN']
    
    return render_template('route_pallets.html',
                          shipment=shipment,
                          pallets=pallet_data,
                          open_pallets=open_pallets,
                          unassigned_orders=unassigned_orders,
                          hints=hints)


@bp.route('/<int:shipment_id>/pallets/create', methods=['POST'])
@login_required
@admin_or_warehouse_required
def create_pallet(shipment_id):
    """Create a new pallet for the route."""
    shipment = Shipment.query.get_or_404(shipment_id)
    
    pallet = create_pallet_for_route(shipment_id)
    db.session.commit()
    
    flash(f'Pallet {pallet.label} created.', 'success')
    return redirect(url_for('pallets.route_pallets', shipment_id=shipment_id))


@bp.route('/<int:shipment_id>/pallets/assign', methods=['POST'])
@login_required
@admin_or_warehouse_required
def assign_order(shipment_id):
    """Assign an invoice to a pallet."""
    shipment = Shipment.query.get_or_404(shipment_id)
    
    data = request.get_json() if request.is_json else request.form
    
    pallet_id = int(data.get('pallet_id'))
    invoice_no = data.get('invoice_no')
    blocks_requested = int(data.get('blocks', 1))
    weight_kg = float(data.get('weight_kg', 0))
    stop_seq = float(data.get('stop_seq')) if data.get('stop_seq') else None
    
    pallet = WmsPallet.query.get_or_404(pallet_id)
    if pallet.shipment_id != shipment_id:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Pallet not in this route'}), 400
        flash('Pallet not in this route.', 'danger')
        return redirect(url_for('pallets.route_pallets', shipment_id=shipment_id))
    
    existing = WmsPalletOrder.query.filter_by(invoice_no=invoice_no).first()
    if existing:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Invoice already assigned'}), 400
        flash('Invoice already assigned to a pallet.', 'warning')
        return redirect(url_for('pallets.route_pallets', shipment_id=shipment_id))
    
    order = allocate_order_to_pallet(pallet, invoice_no, blocks_requested, weight_kg, stop_seq)
    
    if order:
        db.session.commit()
        if request.is_json:
            return jsonify({'success': True, 'order_id': order.id})
        flash(f'Invoice {invoice_no} assigned to {pallet.label}.', 'success')
    else:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Cannot allocate - pallet full or sealed'}), 400
        flash('Cannot allocate invoice to this pallet. Check space and weight.', 'danger')
    
    return redirect(url_for('pallets.route_pallets', shipment_id=shipment_id))


@bp.route('/<int:shipment_id>/pallets/unassign', methods=['POST'])
@login_required
@admin_or_warehouse_required
def unassign_order(shipment_id):
    """Remove an invoice from its pallet."""
    shipment = Shipment.query.get_or_404(shipment_id)
    
    data = request.get_json() if request.is_json else request.form
    invoice_no = data.get('invoice_no')
    
    success = unassign_order_from_pallet(invoice_no)
    
    if success:
        db.session.commit()
        if request.is_json:
            return jsonify({'success': True})
        flash(f'Invoice {invoice_no} removed from pallet.', 'success')
    else:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Cannot unassign - pallet sealed or not found'}), 400
        flash('Cannot remove invoice. Pallet may be sealed.', 'warning')
    
    return redirect(url_for('pallets.route_pallets', shipment_id=shipment_id))


@bp.route('/<int:shipment_id>/pallets/seal', methods=['POST'])
@login_required
@admin_or_warehouse_required
def toggle_seal(shipment_id):
    """Toggle pallet seal status."""
    shipment = Shipment.query.get_or_404(shipment_id)
    
    data = request.get_json() if request.is_json else request.form
    pallet_id = int(data.get('pallet_id'))
    
    pallet = WmsPallet.query.get_or_404(pallet_id)
    if pallet.shipment_id != shipment_id:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Pallet not in this route'}), 400
        flash('Pallet not in this route.', 'danger')
        return redirect(url_for('pallets.route_pallets', shipment_id=shipment_id))
    
    new_status = toggle_pallet_seal(pallet)
    pallet.updated_at = get_utc_now()
    db.session.commit()
    
    if request.is_json:
        return jsonify({'success': True, 'status': new_status})
    
    flash(f'Pallet {pallet.label} is now {new_status}.', 'success')
    return redirect(url_for('pallets.route_pallets', shipment_id=shipment_id))
