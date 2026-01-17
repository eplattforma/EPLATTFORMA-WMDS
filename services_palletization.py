"""
Palletization services for order hints and pallet allocation.
"""
import math
from decimal import Decimal
from sqlalchemy import func
from app import db
from models import Invoice, InvoiceItem, WmsPackingProfile, WmsPallet, WmsPalletOrder, RouteStopInvoice, RouteStop
from pallet_masks import find_allocation, count_free_blocks


WEIGHT_PER_BLOCK_KG = 65
BASE_WEIGHT_PER_BLOCK_KG = 120
MAX_PALLET_WEIGHT_KG = 500


def get_order_pallet_hints(shipment_id: int) -> dict:
    """
    Get palletization hints for all invoices in a route.
    Returns dict keyed by invoice_no with hints for each order.
    """
    rsi_list = db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == shipment_id
    ).all()
    invoice_nos = [rsi.invoice_no for rsi in rsi_list]
    
    if not invoice_nos:
        return {}
    
    rsi_by_invoice = {rsi.invoice_no: rsi for rsi in rsi_list}
    
    results = db.session.query(
        InvoiceItem.invoice_no,
        func.sum(func.coalesce(InvoiceItem.line_weight, 0)).label('total_weight'),
        func.array_agg(InvoiceItem.item_code.distinct()).label('item_codes'),
        func.array_agg(InvoiceItem.location.distinct()).label('locations')
    ).filter(
        InvoiceItem.invoice_no.in_(invoice_nos)
    ).group_by(InvoiceItem.invoice_no).all()
    
    all_item_codes = set()
    for row in results:
        if row.item_codes:
            all_item_codes.update(row.item_codes)
    
    profiles = {p.item_code_365: p for p in WmsPackingProfile.query.filter(
        WmsPackingProfile.item_code_365.in_(list(all_item_codes))
    ).all()} if all_item_codes else {}
    
    assigned = {po.invoice_no: po for po in WmsPalletOrder.query.filter(
        WmsPalletOrder.invoice_no.in_(invoice_nos)
    ).all()}
    
    hints = {}
    for row in results:
        invoice_no = row.invoice_no
        total_weight = float(row.total_weight or 0)
        item_codes = row.item_codes or []
        locations = row.locations or []
        
        warnings = []
        base_weight = 0.0
        has_fragile = False
        has_crushable = False
        has_spill = False
        has_off_pallet = False
        
        for ic in item_codes:
            prof = profiles.get(ic)
            if prof:
                import json
                flags = json.loads(prof.flags_json) if prof.flags_json else []
                if 'FRAGILE' in flags or 'SEMI_FRAGILE' in flags:
                    has_fragile = True
                if 'CRUSHABLE' in flags or 'PRESSURE_MEDIUM' in flags:
                    has_crushable = True
                if 'SPILL_RISK' in flags:
                    has_spill = True
                if 'OFF_PALLET' in flags:
                    has_off_pallet = True
                if prof.pallet_role == 'BASE':
                    base_weight += total_weight / len(item_codes)
        
        base_corridors = []
        for loc in locations:
            if loc and len(loc) >= 2:
                corridor = loc[:2]
                if corridor in ('09', '10', '11', '12'):
                    if corridor not in base_corridors:
                        base_corridors.append(corridor)
        
        if has_fragile:
            warnings.append({'type': 'FRAGILE', 'icon': 'fa-wine-glass', 'color': 'danger'})
        if has_crushable:
            warnings.append({'type': 'CRUSHABLE', 'icon': 'fa-box', 'color': 'warning'})
        if has_spill:
            warnings.append({'type': 'SPILL_RISK', 'icon': 'fa-droplet', 'color': 'info'})
        if has_off_pallet:
            warnings.append({'type': 'COOLER_BAG', 'icon': 'fa-snowflake', 'color': 'primary'})
        
        blocks_from_weight = math.ceil(total_weight / WEIGHT_PER_BLOCK_KG) if total_weight > 0 else 1
        blocks_from_base = math.ceil(base_weight / BASE_WEIGHT_PER_BLOCK_KG) if base_weight > 0 else 0
        
        recommended_blocks = max(blocks_from_weight, blocks_from_base, 1)
        if has_fragile or has_crushable:
            recommended_blocks = max(recommended_blocks, 2)
        
        recommended_blocks = min(recommended_blocks, 8)
        
        split_required = total_weight > MAX_PALLET_WEIGHT_KG
        
        rsi = rsi_by_invoice.get(invoice_no)
        stop_seq = float(rsi.stop.seq_no) if rsi and rsi.stop and rsi.stop.seq_no else None
        
        hints[invoice_no] = {
            'invoice_no': invoice_no,
            'total_weight_kg': round(total_weight, 2),
            'base_weight_kg': round(base_weight, 2),
            'recommended_blocks': recommended_blocks,
            'warnings': warnings,
            'base_corridors': base_corridors,
            'base_rule': f"Corridors {', '.join(base_corridors)} contain cases: build base first" if base_corridors else None,
            'split_required': split_required,
            'stop_seq': stop_seq,
            'assigned_pallet_id': assigned.get(invoice_no, None) and assigned[invoice_no].pallet_id,
        }
    
    for rsi in rsi_list:
        if rsi.invoice_no not in hints:
            hints[rsi.invoice_no] = {
                'invoice_no': rsi.invoice_no,
                'total_weight_kg': 0,
                'base_weight_kg': 0,
                'recommended_blocks': 1,
                'warnings': [],
                'base_corridors': [],
                'base_rule': None,
                'split_required': False,
                'stop_seq': float(rsi.stop.seq_no) if rsi.stop and rsi.stop.seq_no else None,
                'assigned_pallet_id': assigned.get(rsi.invoice_no, None) and assigned[rsi.invoice_no].pallet_id,
            }
    
    return hints


def allocate_order_to_pallet(pallet: WmsPallet, invoice_no: str, blocks_requested: int, 
                             weight_kg: float = 0, stop_seq: float = None) -> WmsPalletOrder | None:
    """
    Allocate an invoice to a pallet with the specified number of blocks.
    Returns the created WmsPalletOrder or None if allocation fails.
    """
    if pallet.status != 'OPEN':
        return None
    
    new_weight = float(pallet.used_weight_kg or 0) + weight_kg
    if new_weight > float(pallet.max_weight_kg):
        return None
    
    mask = find_allocation(pallet.used_mask, blocks_requested)
    if mask is None:
        return None
    
    order = WmsPalletOrder(
        pallet_id=pallet.pallet_id,
        invoice_no=invoice_no,
        blocks_requested=blocks_requested,
        blocks_mask=mask,
        est_weight_kg=Decimal(str(weight_kg)) if weight_kg else None,
        stop_seq_no=Decimal(str(stop_seq)) if stop_seq else None,
    )
    db.session.add(order)
    
    pallet.used_mask = pallet.used_mask | mask
    pallet.used_weight_kg = Decimal(str(new_weight))
    
    return order


def unassign_order_from_pallet(invoice_no: str) -> bool:
    """
    Remove an invoice assignment from its pallet.
    Returns True if successful.
    """
    order = WmsPalletOrder.query.filter_by(invoice_no=invoice_no).first()
    if not order:
        return False
    
    pallet = order.pallet
    if pallet.status != 'OPEN':
        return False
    
    pallet.used_mask = pallet.used_mask & ~order.blocks_mask
    pallet.used_weight_kg = max(Decimal('0'), pallet.used_weight_kg - (order.est_weight_kg or Decimal('0')))
    
    db.session.delete(order)
    return True


def create_pallet_for_route(shipment_id: int) -> WmsPallet:
    """
    Create a new pallet for a route with auto-generated label.
    """
    existing = WmsPallet.query.filter_by(shipment_id=shipment_id).count()
    label = f"P{existing + 1}"
    
    pallet = WmsPallet(
        shipment_id=shipment_id,
        label=label,
        status='OPEN',
        max_weight_kg=Decimal('500'),
        max_height_m=Decimal('1.80'),
        used_mask=0,
        used_weight_kg=Decimal('0'),
    )
    db.session.add(pallet)
    return pallet


def toggle_pallet_seal(pallet: WmsPallet) -> str:
    """
    Toggle pallet between OPEN and SEALED status.
    Returns the new status.
    """
    if pallet.status == 'OPEN':
        pallet.status = 'SEALED'
    elif pallet.status == 'SEALED':
        pallet.status = 'OPEN'
    return pallet.status
