from datetime import datetime, timedelta
from app import db
from flask_login import UserMixin
from sqlalchemy import and_, String, Boolean, DateTime, CHAR
import pytz
from mixins import SoftDeleteMixin, ActivatableMixin
from timezone_utils import get_utc_now, get_utc_today
from db_types import UTCDateTime
from sorting_utils import sort_items_for_picking, sort_batch_items, get_sorting_config

def utc_now():
    """Return current UTC time for consistent database storage"""
    return get_utc_now()

# All timestamps in the database are stored in UTC
# Use get_utc_now() from timezone_utils for current UTC time

# Settings Table
class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    
    @classmethod
    def get(cls, session, key, default="true"):
        """Get a setting value by key with an optional default"""
        setting = session.query(cls).filter_by(key=key).first()
        return setting.value if setting else default
    
    @classmethod
    def set(cls, session, key, value):
        """Set a setting value by key"""
        setting = session.query(cls).filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = cls(key=key, value=value)
            session.add(setting)
        session.flush()
        
    @classmethod
    def get_json(cls, session, key, default=None):
        """Get a setting value as JSON"""
        import json
        if default is None:
            default = {}
        
        value = cls.get(session, key, json.dumps(default))
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
            
    @classmethod
    def set_json(cls, session, key, value):
        """Set a setting value as JSON"""
        import json
        json_value = json.dumps(value)
        return cls.set(session, key, json_value)

# User Model
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    username = db.Column(db.String(64), primary_key=True)
    password = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin', 'picker', 'warehouse_manager', 'driver'
    payment_type_code_365 = db.Column(db.String(50), nullable=True)  # Payment type code for PS365 API receipt sending
    cheque_payment_type_code_365 = db.Column(db.String(50), nullable=True)  # Cheque payment type code for PS365 API
    require_gps_check = db.Column(db.Boolean, default=True, server_default='true')  # Enable/disable GPS location checking for this user
    
    # ActivatableMixin fields defined directly for proper index support
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default='true')
    disabled_at = db.Column(db.DateTime, nullable=True)
    disabled_reason = db.Column(db.String(255), nullable=True)
    
    __table_args__ = (
        db.Index('idx_users_is_active', 'is_active'),
    )
    
    # Override get_id for Flask-Login
    def get_id(self):
        return self.username
    
    def disable(self, reason=None):
        """Disable/deactivate this user."""
        from datetime import datetime
        self.is_active = False
        self.disabled_at = datetime.utcnow()
        self.disabled_reason = reason
    
    def enable(self):
        """Re-enable/reactivate this user."""
        self.is_active = True
        self.disabled_at = None
        self.disabled_reason = None

# Invoices Table
class Invoice(db.Model, SoftDeleteMixin):
    __tablename__ = 'invoices'
    invoice_no = db.Column(db.String(50), primary_key=True)
    routing = db.Column(db.String(100), nullable=True)
    customer_name = db.Column(db.String(200), nullable=True)
    upload_date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    assigned_to = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    total_lines = db.Column(db.Integer, default=0)
    total_items = db.Column(db.Integer, default=0)
    total_weight = db.Column(db.Float, default=0)
    total_exp_time = db.Column(db.Float, default=0)
    status = db.Column(db.String(30), default='not_started')  # not_started / picking / awaiting_batch_items / awaiting_packing / ready_for_dispatch / shipped / out_for_delivery / delivered / delivery_failed / returned_to_warehouse
    status_updated_at = db.Column(UTCDateTime(), default=get_utc_now)  # When status was last changed
    current_item_index = db.Column(db.Integer, default=0)  # Track which item we're picking
    packing_complete_time = db.Column(UTCDateTime(), nullable=True)  # When packing was marked as complete
    picking_complete_time = db.Column(UTCDateTime(), nullable=True)  # When all items were picked (ready for packing stage)
    
    # Direct shipping fields (without shipments)
    shipped_at = db.Column(UTCDateTime(), nullable=True)  # When order was shipped
    shipped_by = db.Column(db.String(64), db.ForeignKey('users.username', name='fk_invoices_shipped_by'), nullable=True)  # Who shipped the order
    delivered_at = db.Column(UTCDateTime(), nullable=True)  # When order was delivered
    undelivered_reason = db.Column(db.Text, nullable=True)  # Reason if delivery failed
    
    # Route assignment fields for automated delivery route creation
    customer_code = db.Column(db.String(50), nullable=True)  # Customer code from ps_customers
    customer_code_365 = db.Column(db.String(50), nullable=True)  # PS365 customer code looked up from ps_customers table
    route_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=True)  # Assigned route/shipment
    stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id'), nullable=True)  # Assigned stop within route
    
    # Powersoft365 API fields
    total_grand = db.Column(db.Numeric(12, 2), nullable=True)  # Gross total from PS365 API
    total_sub = db.Column(db.Numeric(12, 2), nullable=True)  # Subtotal from PS365 API
    total_vat = db.Column(db.Numeric(12, 2), nullable=True)  # VAT from PS365 API
    ps365_synced_at = db.Column(UTCDateTime(), nullable=True)  # When invoice data was fetched from PS365
    
    # Relationship with items
    items = db.relationship('InvoiceItem', backref='invoice', cascade='all, delete-orphan')
    # Relationship with assigned picker
    assigned_picker = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_invoices')
    # Relationship with picker who shipped the order
    shipper = db.relationship('User', foreign_keys=[shipped_by], backref='shipped_invoices')
    # Relationship with picking exceptions
    exceptions = db.relationship('PickingException', backref='invoice', cascade='all, delete-orphan')

# Invoice Items Table
class InvoiceItem(db.Model):
    __tablename__ = 'invoice_items'
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), primary_key=True)
    item_code = db.Column(db.String(50), primary_key=True)
    location = db.Column(db.String(100), nullable=True)
    corridor = db.Column(db.String(10), nullable=True)  # Corridor number with leading zeros (e.g., "09", "10")
    barcode = db.Column(db.String(100), nullable=True)
    zone = db.Column(db.String(50), nullable=True)
    item_weight = db.Column(db.Float, nullable=True)  # Items.Weight
    item_name = db.Column(db.String(200), nullable=True)
    unit_type = db.Column(db.String(50), nullable=True)
    pack = db.Column(db.String(50), nullable=True)
    qty = db.Column(db.Integer, nullable=True)
    line_weight = db.Column(db.Float, nullable=True)  # Items.Weight × QTY
    exp_time = db.Column(db.Float, nullable=True)
    pieces_per_unit_snapshot = db.Column(db.Integer, nullable=True)  # From ps_items.pieces_per_unit_snapshot
    expected_pick_pieces = db.Column(db.Integer, nullable=True)  # Expected pieces to pick
    picked_qty = db.Column(db.Integer, nullable=True)  # Actual picked quantity
    is_picked = db.Column(db.Boolean, default=False)  # Whether this item has been picked
    pick_status = db.Column(db.String(20), default='not_picked')  # not_picked, picked, reset, skipped, skipped_pending
    reset_by = db.Column(db.String(64), nullable=True)  # Username of admin who reset the item
    reset_timestamp = db.Column(UTCDateTime(), nullable=True)  # When the item was reset
    reset_note = db.Column(db.String(500), nullable=True)  # Optional note about reset reason
    # Skip functionality fields
    skip_reason = db.Column(db.Text, nullable=True)  # Reason provided when skipping an item
    skip_timestamp = db.Column(UTCDateTime(), nullable=True)  # When the item was skipped
    skip_count = db.Column(db.Integer, default=0)  # How many times the item was skipped
    
    # Batch locking system
    locked_by_batch_id = db.Column(db.Integer, nullable=True)  # If set, this item is locked by a batch
    
    @property
    def display_qty(self):
        """Calculate display quantity: qty * number_of_pieces ONLY for VPACK items
        
        - VPACK items: Picker picks individual pieces, so multiply qty by number_of_pieces
        - PAC items: Picker picks the whole pack as a unit, so just show qty
        """
        if not self.qty:
            return self.qty
        
        from flask import g
        if not hasattr(g, 'dw_item_cache'):
            g.dw_item_cache = {}
            
        if self.item_code not in g.dw_item_cache:
            dw_item = DwItem.query.filter_by(item_code_365=self.item_code).first()
            g.dw_item_cache[self.item_code] = dw_item
            
        dw_item = g.dw_item_cache[self.item_code]
        if dw_item and dw_item.number_of_pieces and dw_item.number_of_pieces > 1:
            # Only multiply for VPACK items (variable packs where picker picks individual pieces)
            if dw_item.attribute_1_code_365 == 'VPACK':
                return self.qty * dw_item.number_of_pieces
        return self.qty
    
    @property
    def display_unit_type(self):
        """Return 'Pieces' only for VPACK items where quantity was multiplied, otherwise return original unit_type
        
        - VPACK items: Show 'Pieces' since picker picks individual pieces
        - PAC items: Show original unit_type (e.g., 'Pack') since picker picks whole packs
        """
        from flask import g
        if not hasattr(g, 'dw_item_cache'):
            g.dw_item_cache = {}
            
        if self.item_code not in g.dw_item_cache:
            dw_item = DwItem.query.filter_by(item_code_365=self.item_code).first()
            g.dw_item_cache[self.item_code] = dw_item
            
        dw_item = g.dw_item_cache[self.item_code]
        if dw_item and dw_item.number_of_pieces and dw_item.number_of_pieces > 1:
            # Only show 'Pieces' for VPACK items
            if dw_item.attribute_1_code_365 == 'VPACK':
                return 'Pieces'
        return self.unit_type or 'units'
    
# Picking Exceptions Table
class PickingException(db.Model):
    __tablename__ = 'picking_exceptions'
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    item_code = db.Column(db.String(50), nullable=False)
    expected_qty = db.Column(db.Integer, nullable=False)
    picked_qty = db.Column(db.Integer, nullable=False)
    picker_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    timestamp = db.Column(UTCDateTime(), default=get_utc_now)
    reason = db.Column(db.String(500), nullable=True)  # Optional reason for the exception
    
# Batch Picking Session Table
class BatchPickingSession(db.Model, SoftDeleteMixin):
    __tablename__ = 'batch_picking_sessions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    batch_number = db.Column(db.String(20), nullable=True, unique=True)  # Human-readable batch number (BATCH-YYYYMMDD-###)
    zones = db.Column(db.String(500), nullable=False)  # Comma-separated zones included in this batch
    corridors = db.Column(db.String(500), nullable=True)  # Comma-separated corridors included in this batch
    unit_types = db.Column(db.String(500), nullable=True)  # Comma-separated unit types included in this batch
    created_at = db.Column(UTCDateTime(), default=get_utc_now)
    created_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    assigned_to = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    status = db.Column(db.String(20), default='Created')  # Created / In Progress / Completed
    picking_mode = db.Column(db.String(20), nullable=False)  # Sequential / Consolidated
    current_invoice_index = db.Column(db.Integer, default=0)  # Track which invoice we're picking in Sequential mode
    current_item_index = db.Column(db.Integer, default=0)  # Track which item we're picking
    
    # Relationships
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_batch_sessions')
    picker = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_batch_sessions')
    invoices = db.relationship('BatchSessionInvoice', backref='session', cascade='all, delete-orphan')
    
    def _batch_item_filters(self, invoice_nos, zones_list, corridors_list, unit_types_list,
                           include_picked: bool, allow_unlocked: bool):
        """Centralized filter building for batch items"""
        from sqlalchemy import and_, or_
        
        filters = [
            InvoiceItem.invoice_no.in_(invoice_nos),
            InvoiceItem.zone.in_(zones_list),
        ]

        if allow_unlocked:
            # Only count items that are either unlocked OR locked by THIS batch
            filters.append(or_(
                InvoiceItem.locked_by_batch_id.is_(None),
                InvoiceItem.locked_by_batch_id == self.id
            ))
        else:
            filters.append(InvoiceItem.locked_by_batch_id == self.id)

        if not include_picked:
            filters.extend([
                InvoiceItem.is_picked.is_(False),
                InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
            ])

        if corridors_list:
            filters.append(InvoiceItem.corridor.in_(corridors_list))

        if unit_types_list:
            filters.append(InvoiceItem.unit_type.in_(unit_types_list))

        return filters

    def get_filtered_item_count(self):
        """Get the actual count of items that will be processed based on corridor filtering"""
        from sqlalchemy import and_
        
        batch_invoices = db.session.query(BatchSessionInvoice).filter_by(batch_session_id=self.id).all()
        invoice_nos = [bi.invoice_no for bi in batch_invoices]
        zones_list = [z.strip() for z in self.zones.split(',') if z.strip()]
        corridors_list = [c.strip() for c in self.corridors.split(',') if c.strip()] if self.corridors else []
        unit_types_list = [u.strip() for u in self.unit_types.split(',') if u.strip()] if self.unit_types else []
        
        if not invoice_nos or not zones_list:
            return 0
            
        filter_conditions = self._batch_item_filters(
            invoice_nos, zones_list, corridors_list, unit_types_list,
            include_picked=False, allow_unlocked=True
        )
        
        return db.session.query(InvoiceItem).filter(and_(*filter_conditions)).count()

    def get_filtered_items(self, include_picked=False):
        """Get all items that match batch criteria (zones, corridors, locks)"""
        from sqlalchemy import and_
        
        batch_invoices = db.session.query(BatchSessionInvoice).filter_by(batch_session_id=self.id).all()
        invoice_nos = [bi.invoice_no for bi in batch_invoices]
        zones_list = [z.strip() for z in self.zones.split(',') if z.strip()]
        corridors_list = [c.strip() for c in self.corridors.split(',') if c.strip()] if self.corridors else []
        unit_types_list = [u.strip() for u in self.unit_types.split(',') if u.strip()] if self.unit_types else []
        
        if not invoice_nos or not zones_list:
            return []
        
        filter_conditions = self._batch_item_filters(
            invoice_nos, zones_list, corridors_list, unit_types_list,
            include_picked=include_picked, allow_unlocked=False
        )
        
        return db.session.query(InvoiceItem).filter(and_(*filter_conditions)).all()

    def get_grouped_items(self, include_picked=False):
        """Get items grouped appropriately based on picking mode"""
        from sqlalchemy import and_
        
        # Get all invoices in this batch
        batch_invoices = db.session.query(BatchSessionInvoice).filter_by(batch_session_id=self.id).all()
        
        if self.picking_mode == 'Sequential':
            # For sequential mode, get invoices sorted by routing number descending
            invoice_routing_data = []
            for bi in batch_invoices:
                invoice = Invoice.query.filter_by(invoice_no=bi.invoice_no).first()
                routing = invoice.routing if invoice else None
                try:
                    routing_float = float(routing) if routing else -1
                except (ValueError, TypeError):
                    routing_float = -1
                invoice_routing_data.append((bi.invoice_no, routing_float))
            
            invoice_routing_data.sort(key=lambda x: x[1], reverse=True)
            invoice_nos = [item[0] for item in invoice_routing_data]
        else:
            invoice_nos = [bi.invoice_no for bi in batch_invoices]
        
        zones_list = [z.strip() for z in self.zones.split(',') if z.strip()]
        corridors_list = [c.strip() for c in self.corridors.split(',') if c.strip()] if self.corridors else []
        unit_types_list = [u.strip() for u in self.unit_types.split(',') if u.strip()] if self.unit_types else []
        
        if not invoice_nos or not zones_list:
            return []
            
        filter_conditions = self._batch_item_filters(
            invoice_nos, zones_list, corridors_list, unit_types_list,
            include_picked=include_picked, allow_unlocked=False
        )
        
        if self.picking_mode == 'Consolidated' or not self.picking_mode:
            all_batch_items = db.session.query(InvoiceItem).filter(and_(*filter_conditions)).all()
            
            # Sort items using admin configurable sorting settings
            all_batch_items = sort_items_for_picking(all_batch_items)
            
            grouped_items = {}
            for item in all_batch_items:
                # Group by item_code and location to prevent location merging issues
                key = (item.item_code, item.location or "")
                if key not in grouped_items:
                    grouped_items[key] = {
                        'item_code': item.item_code,
                        'location': item.location,
                        'barcode': item.barcode,
                        'zone': item.zone,
                        'item_name': item.item_name,
                        'unit_type': item.unit_type,
                        'pack': item.pack,
                        'total_qty': 0,
                        'source_items': []
                    }
                
                qty = item.qty or 0
                grouped_items[key]['total_qty'] += qty
                grouped_items[key]['source_items'].append({
                    'invoice_no': item.invoice_no,
                    'item_code': item.item_code,
                    'qty': qty,
                    'id': f"{item.invoice_no}-{item.item_code}"
                })
            
            # Sort grouped items using admin configurable sorting settings
            return sort_batch_items(list(grouped_items.values()))
            
        else:  # 'Sequential' mode - Complete one order at a time
            from flask import current_app
            
            # Group all items by invoice for sequential processing
            items_by_invoice = {}
            # Fetch all items without ORDER BY, will sort in Python using configurable settings
            all_items = InvoiceItem.query.filter(and_(*filter_conditions)).all()
            # Sort items using admin configurable sorting settings
            all_items = sort_items_for_picking(all_items)
            
            # Group items by invoice
            for item in all_items:
                if item.invoice_no not in items_by_invoice:
                    items_by_invoice[item.invoice_no] = []
                items_by_invoice[item.invoice_no].append(item)
            
            # 🔧 FIXED: Use the same invoice ordering as batch management for consistent indexing
            # Get ALL batch invoices in the correct routing order (not just those with items)
            from routes_batch import get_sorted_batch_invoices
            sorted_batch_invoices = get_sorted_batch_invoices(self)
            all_invoice_nos_ordered = [bi.invoice_no for bi in sorted_batch_invoices]
            
            current_app.logger.info(f"Sequential mode: All batch invoices in order: {all_invoice_nos_ordered}")
            
            # First handle the case where current_invoice_index is out of bounds
            if self.current_invoice_index >= len(all_invoice_nos_ordered):
                self.current_invoice_index = 0
            
            # Get the current invoice using the consistent indexing
            current_invoice = all_invoice_nos_ordered[self.current_invoice_index]
            current_app.logger.info(f"Sequential mode: invoice_index={self.current_invoice_index}, current_invoice={current_invoice}")
            
            # 🔧 FIXED: If current invoice has no items, find the next invoice with items
            if current_invoice not in items_by_invoice or not items_by_invoice[current_invoice]:
                current_app.logger.info(f"Current invoice {current_invoice} at index {self.current_invoice_index} has no items, searching for next invoice with items")
                
                # Search through remaining invoices starting from current index to find one with items
                for search_idx in range(self.current_invoice_index, len(all_invoice_nos_ordered)):
                    search_invoice = all_invoice_nos_ordered[search_idx]
                    if search_invoice in items_by_invoice and items_by_invoice[search_invoice]:
                        # Found an invoice with items - update the current index to this invoice
                        if search_idx != self.current_invoice_index:
                            # Only update if we're advancing to a different invoice
                            old_index = self.current_invoice_index
                            self.current_invoice_index = search_idx
                            self.current_item_index = 0  # Reset item index for new invoice
                            current_app.logger.info(f"🔄 SEQUENTIAL ADVANCEMENT: Advanced from index {old_index} to {search_idx} ({search_invoice}) which has {len(items_by_invoice[search_invoice])} items")
                            db.session.commit()  # Save the advancement
                            
                            # Clear the batch cache to force regeneration with new invoice
                            # Use try/except to handle case when called outside request context
                            try:
                                from flask import session, has_request_context
                                if has_request_context():
                                    fixed_batch_key = f'batch_items_{self.id}'
                                    if fixed_batch_key in session:
                                        session.pop(fixed_batch_key, None)
                                        current_app.logger.info(f"🧹 Cleared batch cache after advancement to ensure proper item list regeneration")
                            except RuntimeError:
                                # No request context available (e.g., background job or CLI)
                                pass
                        current_invoice = search_invoice
                        break
                else:
                    # No invoice found with items - batch completion
                    current_app.logger.info(f"No invoices found with items, batch should complete")
                    return []
            items = items_by_invoice[current_invoice]
            
            current_app.logger.info(f"Processing invoice {current_invoice} ({self.current_invoice_index + 1}/{len(all_invoice_nos_ordered)}) with {len(items)} items")
            
            # Get order details for the current invoice
            invoice_details = Invoice.query.filter_by(invoice_no=current_invoice).first()
            
            # Create result items with order information for notifications
            result_items = []
            for item in items:
                result_items.append({
                    'item_code': item.item_code,
                    'item_name': item.item_name,
                    'location': item.location,
                    'zone': item.zone,
                    'barcode': item.barcode,
                    'unit_type': item.unit_type,
                    'pack': item.pack,
                    'total_qty': item.qty,
                    'current_invoice': current_invoice,  # Add current invoice info
                    'invoice_position': f"{self.current_invoice_index + 1}/{len(all_invoice_nos_ordered)}",  # Add position info
                    'is_new_order': False,  # Will be set to True for first item of each order
                    # Add order details for notification
                    'customer_name': invoice_details.customer_name if invoice_details else None,
                    'order_total_items': invoice_details.total_items if invoice_details else None,
                    'order_total_weight': invoice_details.total_weight if invoice_details else None,
                    'source_items': [{
                        'invoice_no': item.invoice_no,
                        'item_code': item.item_code,
                        'qty': item.qty,
                        'id': item.invoice_no + '-' + item.item_code
                    }]
                })
            
            # Mark the first item as starting a new order
            if result_items:
                result_items[0]['is_new_order'] = True
                
            current_app.logger.info(f"Returning {len(result_items)} items for invoice {current_invoice}")
            return result_items

# Junction table to map multiple invoices to a batch picking session
class BatchSessionInvoice(db.Model):
    __tablename__ = 'batch_session_invoices'
    batch_session_id = db.Column(db.Integer, db.ForeignKey('batch_picking_sessions.id'), primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), primary_key=True)
    is_completed = db.Column(db.Boolean, default=False)
    
    # Allow retrieving the invoice directly
    invoice = db.relationship('Invoice')
    
# Track picked quantities for batch picking
class BatchPickedItem(db.Model):
    __tablename__ = 'batch_picked_items'
    id = db.Column(db.Integer, primary_key=True)
    batch_session_id = db.Column(db.Integer, db.ForeignKey('batch_picking_sessions.id'), nullable=False)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    item_code = db.Column(db.String(50), nullable=False)
    picked_qty = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(UTCDateTime(), default=get_utc_now)
    
    # Relationships
    session = db.relationship('BatchPickingSession')
    invoice = db.relationship('Invoice')
    
# This class was already defined elsewhere in the file

# Picker Shift Tracking
class Shift(db.Model):
    __tablename__ = 'shifts'
    id = db.Column(db.Integer, primary_key=True)
    picker_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    check_in_time = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    check_out_time = db.Column(UTCDateTime(), nullable=True)
    check_in_coordinates = db.Column(db.String(100), nullable=True)
    check_out_coordinates = db.Column(db.String(100), nullable=True)
    total_duration_minutes = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='active')  # active, completed, unclosed
    admin_adjusted = db.Column(db.Boolean, default=False)
    adjustment_note = db.Column(db.Text, nullable=True)
    adjustment_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    adjustment_time = db.Column(UTCDateTime(), nullable=True)
    
    # Relationships
    picker = db.relationship('User', foreign_keys=[picker_username], backref='shifts')
    admin = db.relationship('User', foreign_keys=[adjustment_by], backref='shift_adjustments')
    idle_periods = db.relationship('IdlePeriod', backref='shift', cascade='all, delete-orphan')
    
    def calculate_duration(self):
        """Calculate the duration of the shift in minutes"""
        if self.check_in_time and self.check_out_time:
            delta = self.check_out_time - self.check_in_time
            return int(delta.total_seconds() / 60)
        return None
    
    def total_idle_time(self):
        """Calculate the total idle time in minutes for this shift"""
        idle_list = db.session.query(IdlePeriod).filter_by(shift_id=self.id).all()
        total_minutes = 0
        
        for period in idle_list:
            # Only count completed idle periods (those with an end_time)
            if period.end_time:
                total_minutes += period.duration_minutes or 0
            else:
                # For incomplete periods (still ongoing), don't count them
                # Active breaks/idle should not be included in the total
                pass
        
        return total_minutes
    
    def current_idle_minutes(self):
        """Get the current idle time if picker is currently idle/on break, otherwise 0"""
        from timezone_utils import get_utc_now
        
        # Find active idle period (no end_time)
        active_idle = db.session.query(IdlePeriod).filter_by(
            shift_id=self.id,
            end_time=None
        ).first()
        
        if active_idle:
            # Calculate current duration of active idle period using UTC times
            now_utc = get_utc_now()
            delta = now_utc - active_idle.start_time
            return int(delta.total_seconds() / 60)
        
        # No active idle - picker is working
        return 0
    
    def break_count(self):
        """Count the number of manual breaks taken during this shift"""
        idle_list = db.session.query(IdlePeriod).filter_by(shift_id=self.id, is_break=True).all()
        return len(idle_list)
    
    def total_break_time(self):
        """Calculate total break time in minutes (only manual breaks, only completed ones)"""
        break_periods = db.session.query(IdlePeriod).filter_by(
            shift_id=self.id, 
            is_break=True
        ).all()
        total_minutes = 0
        
        for period in break_periods:
            # Only count completed breaks (those with an end_time)
            if period.end_time:
                total_minutes += period.duration_minutes or 0
        
        return total_minutes
    
    def working_time(self):
        """Calculate working time = total time - breaks (in minutes)"""
        from timezone_utils import get_utc_now
        
        # Calculate total elapsed time using UTC times
        if self.check_out_time:
            total_time = self.calculate_duration()
        else:
            # For active shifts, use current UTC time
            elapsed = get_utc_now() - self.check_in_time
            total_time = int(elapsed.total_seconds() / 60)
        
        if total_time is None:
            return 0
        
        # Subtract break time
        break_time = self.total_break_time()
        return max(0, total_time - break_time)

# Idle Periods and Breaks
class IdlePeriod(db.Model):
    __tablename__ = 'idle_periods'
    id = db.Column(db.Integer, primary_key=True)
    shift_id = db.Column(db.Integer, db.ForeignKey('shifts.id'), nullable=False)
    start_time = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    end_time = db.Column(UTCDateTime(), nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=True)
    is_break = db.Column(db.Boolean, default=False)
    break_reason = db.Column(db.String(200), nullable=True)
    
    def calculate_duration(self):
        """Calculate the duration of the idle period in minutes"""
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            return int(delta.total_seconds() / 60)
        return None

# Activity Logs for Tracking User Actions
class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    picker_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    timestamp = db.Column(UTCDateTime(), default=get_utc_now)
    activity_type = db.Column(db.String(50), nullable=True)  # e.g., 'item_pick', 'check_in', 'check_out', 'start_break', 'end_break', 'admin_action'
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=True)
    item_code = db.Column(db.String(50), nullable=True)
    details = db.Column(db.Text, nullable=True)
    
    # Relationships
    picker = db.relationship('User', backref='activities')

# Order Time Breakdown for detailed time analysis
class OrderTimeBreakdown(db.Model):
    __tablename__ = 'order_time_breakdown'
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    picker_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    
    # Time tracking fields
    picking_started = db.Column(UTCDateTime(), nullable=True)  # When picking first item started
    picking_completed = db.Column(UTCDateTime(), nullable=True)  # When all items picked
    packing_started = db.Column(UTCDateTime(), nullable=True)  # When packing started
    packing_completed = db.Column(UTCDateTime(), nullable=True)  # When packing finished
    
    # Calculated time durations (in minutes)
    total_walking_time = db.Column(db.Float, default=0.0)  # Time spent walking between locations
    total_picking_time = db.Column(db.Float, default=0.0)  # Time spent actually picking items
    total_packing_time = db.Column(db.Float, default=0.0)  # Time spent packing
    
    # Additional metrics
    total_items_picked = db.Column(db.Integer, default=0)
    total_locations_visited = db.Column(db.Integer, default=0)
    average_time_per_item = db.Column(db.Float, default=0.0)  # Average time per item picked
    
    # Timestamps
    created_at = db.Column(UTCDateTime(), default=get_utc_now)
    updated_at = db.Column(UTCDateTime(), default=get_utc_now, onupdate=get_utc_now)
    
    # Relationships
    invoice = db.relationship('Invoice', backref='time_breakdown')
    picker = db.relationship('User', backref='order_time_breakdowns')
    
    def calculate_times(self):
        """Calculate various time metrics"""
        if self.picking_started and self.picking_completed:
            total_picking_duration = self.picking_completed - self.picking_started
            total_minutes = total_picking_duration.total_seconds() / 60
            
            # Estimate walking vs picking time based on locations and items
            if self.total_locations_visited > 0 and self.total_items_picked > 0:
                # Estimate 30 seconds walking time per location change
                estimated_walking = (self.total_locations_visited * 0.5)  # 0.5 minutes per location
                self.total_walking_time = min(estimated_walking, total_minutes * 0.4)  # Max 40% walking
                self.total_picking_time = total_minutes - self.total_walking_time
            else:
                # Fallback: assume 70% picking, 30% walking
                self.total_picking_time = total_minutes * 0.7
                self.total_walking_time = total_minutes * 0.3
        
        if self.packing_started and self.packing_completed:
            packing_duration = self.packing_completed - self.packing_started
            self.total_packing_time = packing_duration.total_seconds() / 60
        
        if self.total_items_picked > 0 and self.total_picking_time > 0:
            self.average_time_per_item = self.total_picking_time / self.total_items_picked

# Item-level time tracking for detailed analysis
class ItemTimeTracking(db.Model):
    __tablename__ = 'item_time_tracking'
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    item_code = db.Column(db.String(50), nullable=False)
    picker_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    
    # Enhanced time tracking for AI analysis
    item_started = db.Column(UTCDateTime(), nullable=True)  # When picker started this item
    item_completed = db.Column(UTCDateTime(), nullable=True)  # When picker completed this item
    walking_time = db.Column(db.Float, default=0.0)  # Time to walk to location (seconds)
    picking_time = db.Column(db.Float, default=0.0)  # Time actually picking (seconds)
    confirmation_time = db.Column(db.Float, default=0.0)  # Time on confirmation screen (seconds)
    total_item_time = db.Column(db.Float, default=0.0)  # Total time for this item (seconds)
    
    # Location breakdown for AI analysis
    location = db.Column(db.String(100), nullable=True)
    zone = db.Column(db.String(50), nullable=True)
    corridor = db.Column(db.String(50), nullable=True)
    shelf = db.Column(db.String(50), nullable=True)
    level = db.Column(db.String(50), nullable=True)
    bin_location = db.Column(db.String(50), nullable=True)
    
    # Item characteristics for AI insights
    quantity_expected = db.Column(db.Integer, default=0)
    quantity_picked = db.Column(db.Integer, default=0)
    item_weight = db.Column(db.Float, nullable=True)
    item_name = db.Column(db.String(200), nullable=True)
    unit_type = db.Column(db.String(50), nullable=True)
    
    # Performance metrics for AI
    expected_time = db.Column(db.Float, default=0.0)  # Expected time from system
    efficiency_ratio = db.Column(db.Float, default=0.0)  # Actual vs expected ratio
    previous_location = db.Column(db.String(100), nullable=True)  # Previous pick location
    
    # Context for AI analysis
    order_sequence = db.Column(db.Integer, default=0)  # Position in picking order
    time_of_day = db.Column(db.String(10), nullable=True)  # morning/afternoon/evening
    day_of_week = db.Column(db.String(10), nullable=True)  # monday/tuesday/etc
    
    # Quality tracking for AI
    picked_correctly = db.Column(db.Boolean, default=True)
    was_skipped = db.Column(db.Boolean, default=False)
    skip_reason = db.Column(db.String(200), nullable=True)
    
    # Environmental context
    peak_hours = db.Column(db.Boolean, default=False)  # During busy periods
    concurrent_pickers = db.Column(db.Integer, default=1)  # Other active pickers
    
    # Timestamps
    created_at = db.Column(UTCDateTime(), default=get_utc_now)
    updated_at = db.Column(UTCDateTime(), default=get_utc_now, onupdate=get_utc_now)
    
    # Relationships
    invoice = db.relationship('Invoice')
    picker = db.relationship('User')
    
    def calculate_metrics(self):
        """Calculate efficiency and context metrics for AI analysis"""
        if self.item_started and self.item_completed:
            import pytz
            from datetime import timezone
            
            # Normalize both to UTC for accurate duration calculation (avoids DST issues)
            if self.item_started.tzinfo is None:
                item_started_utc = pytz.UTC.localize(self.item_started)
            else:
                item_started_utc = self.item_started.astimezone(timezone.utc)
                
            if self.item_completed.tzinfo is None:
                item_completed_utc = pytz.UTC.localize(self.item_completed)
            else:
                item_completed_utc = self.item_completed.astimezone(timezone.utc)
            
            # Duration between "arrived" (item_started) and "confirmed" (item_completed)
            delta_seconds = max((item_completed_utc - item_started_utc).total_seconds(), 0)
            
            # If picking_time not explicitly set, use delta as picking_time
            if not self.picking_time or self.picking_time <= 0:
                self.picking_time = delta_seconds
            
            walk = float(self.walking_time or 0.0)
            pick = float(self.picking_time or 0.0)
            conf = float(self.confirmation_time or 0.0)
            
            # Prefer explicit phase totals (walking + picking + confirmation)
            phase_total = walk + pick + conf
            self.total_item_time = phase_total if phase_total > 0 else delta_seconds
            
            # Calculate efficiency ratio using total_item_time
            if self.expected_time and self.expected_time > 0:
                self.efficiency_ratio = self.total_item_time / self.expected_time
            
            # Convert to Athens ONLY for context fields (time_of_day, day_of_week, peak_hours)
            athens_tz = pytz.timezone('Europe/Athens')
            item_started_local = item_started_utc.astimezone(athens_tz)
            
            # Set time of day context using local (Athens) time
            hour = item_started_local.hour
            if 6 <= hour < 12:
                self.time_of_day = 'morning'
            elif 12 <= hour < 18:
                self.time_of_day = 'afternoon'
            else:
                self.time_of_day = 'evening'
            
            # Set day of week in local time
            self.day_of_week = item_started_local.strftime('%A').lower()
            
            # Determine if peak hours (8-10am, 1-3pm) in local time
            self.peak_hours = (8 <= hour <= 10) or (13 <= hour <= 15)
    
    def to_ai_dict(self):
        """Convert to dictionary format for AI analysis"""
        return {
            'item_code': self.item_code,
            'location_data': {
                'zone': self.zone,
                'corridor': self.corridor,
                'shelf': self.shelf,
                'level': self.level,
                'bin': self.bin_location,
                'full_location': self.location,
                'previous_location': self.previous_location
            },
            'timing_data': {
                'walking_time': self.walking_time,
                'picking_time': self.picking_time,
                'confirmation_time': self.confirmation_time,
                'total_time': self.total_item_time,
                'expected_time': self.expected_time,
                'efficiency_ratio': self.efficiency_ratio
            },
            'item_data': {
                'weight': self.item_weight,
                'quantity_expected': self.quantity_expected,
                'quantity_picked': self.quantity_picked,
                'unit_type': self.unit_type,
                'name': self.item_name
            },
            'context_data': {
                'sequence': self.order_sequence,
                'time_of_day': self.time_of_day,
                'day_of_week': self.day_of_week,
                'picker': self.picker_username,
                'peak_hours': self.peak_hours,
                'concurrent_pickers': self.concurrent_pickers
            },
            'quality_data': {
                'picked_correctly': self.picked_correctly,
                'was_skipped': self.was_skipped,
                'skip_reason': self.skip_reason
            }
        }

# Time Tracking Alerts
class TimeTrackingAlert(db.Model):
    __tablename__ = 'time_tracking_alerts'
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    picker_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    alert_type = db.Column(db.String(50), nullable=False)  # 'warning', 'critical', 'exceeded'
    expected_duration = db.Column(db.Float, nullable=False)  # Expected time in minutes
    actual_duration = db.Column(db.Float, nullable=False)  # Actual time elapsed in minutes
    threshold_percentage = db.Column(db.Float, nullable=False)  # Percentage over expected (e.g. 150 = 50% over)
    created_at = db.Column(UTCDateTime(), default=get_utc_now)
    is_resolved = db.Column(db.Boolean, default=False)
    resolved_at = db.Column(UTCDateTime(), nullable=True)
    resolved_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    invoice = db.relationship('Invoice')
    picker = db.relationship('User', foreign_keys=[picker_username])
    resolver = db.relationship('User', foreign_keys=[resolved_by])


# Shipment Management Models
class Shipment(db.Model, SoftDeleteMixin):
    __tablename__ = 'shipments'
    
    id = db.Column(db.Integer, primary_key=True)
    driver_name = db.Column(db.String(100), nullable=False)
    route_name = db.Column(db.String(100))
    status = db.Column(db.String(20), nullable=False, default='created')  # created, PLANNED, DISPATCHED, IN_TRANSIT, COMPLETED, CANCELLED
    delivery_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(UTCDateTime(), default=get_utc_now)
    updated_at = db.Column(UTCDateTime(), default=get_utc_now, onupdate=get_utc_now)  # Last status update
    started_at = db.Column(UTCDateTime(), nullable=True)  # When driver started route
    completed_at = db.Column(UTCDateTime(), nullable=True)  # When route was completed
    
    # Driver Settlement fields
    settlement_status = db.Column(db.String(20), default='PENDING')  # PENDING, DRIVER_SUBMITTED, SETTLED
    driver_submitted_at = db.Column(UTCDateTime(), nullable=True)  # When driver submitted settlement
    cash_expected = db.Column(db.Numeric(12, 2), nullable=True)  # Total COD expected (computed from CODReceipts)
    cash_collected = db.Column(db.Numeric(12, 2), nullable=True)  # Sum of COD receipts actually collected (cash/cheque)
    cash_handed_in = db.Column(db.Numeric(12, 2), nullable=True)  # Actual cash from driver
    cash_variance = db.Column(db.Numeric(12, 2), nullable=True)  # Difference (handed_in - collected)
    cash_variance_note = db.Column(db.Text, nullable=True)  # Required if variance != 0
    returns_count = db.Column(db.Integer, default=0)  # Number of returned items
    returns_weight = db.Column(db.Float, nullable=True)  # Total weight of returns
    settlement_notes = db.Column(db.Text, nullable=True)  # Driver's settlement notes
    completion_reason = db.Column(db.String(50), nullable=True)  # normal, returned, emergency
    settlement_cleared_at = db.Column(UTCDateTime(), nullable=True)  # When settlement was cleared by admin
    settlement_cleared_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)  # Admin who cleared settlement
    
    # Reconciliation fields (administrative closeout separate from operational completion)
    reconciliation_status = db.Column(db.String(20), default='NOT_READY')  # NOT_READY, PENDING, IN_REVIEW, RECONCILED
    reconciled_at = db.Column(UTCDateTime(), nullable=True)
    reconciled_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    # Archiving fields (post-reconciliation)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    archived_at = db.Column(UTCDateTime(), nullable=True)
    archived_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    # Relationships
    reconciler = db.relationship('User', foreign_keys=[reconciled_by], backref='reconciled_routes')
    archiver = db.relationship('User', foreign_keys=[archived_by], backref='archived_routes')
    # DEPRECATED: shipment_orders relationship - use RouteStop/RouteStopInvoice instead
    # shipment_orders = db.relationship('ShipmentOrder', backref='shipment', lazy=True, cascade='all, delete-orphan')


class ShipmentOrder(db.Model):
    """
    DEPRECATED: This model is obsolete and scheduled for removal.
    Use RouteStopInvoice instead for invoice-to-route mapping.
    Table retained temporarily for data migration purposes only.
    DO NOT USE IN NEW CODE.
    """
    __tablename__ = 'shipment_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    invoice_no = db.Column(db.String(20), db.ForeignKey('invoices.invoice_no'), nullable=False)


# Route Stop (delivery route stops)
class RouteStop(db.Model, SoftDeleteMixin):
    __tablename__ = 'route_stop'
    
    route_stop_id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipments.id', ondelete='CASCADE'), nullable=False)
    seq_no = db.Column(db.Numeric(10, 2), nullable=False)
    
    # Customer grouping for automated route creation
    customer_code = db.Column(db.String(50), nullable=True)  # Customer code for auto-grouping
    
    stop_name = db.Column(db.Text)
    stop_addr = db.Column(db.Text)
    stop_city = db.Column(db.Text)
    stop_postcode = db.Column(db.Text)
    notes = db.Column(db.Text)
    window_start = db.Column(UTCDateTime())
    window_end = db.Column(UTCDateTime())
    
    # Contact fields for driver app
    website = db.Column(db.String(500), nullable=True)  # Customer website URL
    phone = db.Column(db.String(50), nullable=True)  # Phone number for calls/SMS
    
    # Delivery status tracking
    delivered_at = db.Column(UTCDateTime(), nullable=True)  # When stop was completed
    failed_at = db.Column(UTCDateTime(), nullable=True)  # When delivery failed
    failure_reason = db.Column(db.String(100), nullable=True)  # Closed, No Answer, Refused, etc.
    
    # Relationships
    shipment = db.relationship('Shipment', backref='route_stops')
    
    def __repr__(self):
        return f"<RouteStop {self.seq_no}: {self.stop_name or 'Unnamed'}>"


# Route Stop Invoice (invoices at each stop) - CANONICAL SOURCE for invoice-to-stop mapping
class RouteStopInvoice(db.Model):
    __tablename__ = 'route_stop_invoice'
    
    route_stop_invoice_id = db.Column(db.Integer, primary_key=True)
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id', ondelete='CASCADE'), nullable=False)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no', ondelete='RESTRICT'), nullable=False)
    
    # Status: PENDING, OUT_FOR_DELIVERY, DELIVERED, FAILED, PARTIAL, SKIPPED, RETURNED
    status = db.Column(db.String(50))
    weight_kg = db.Column(db.Float)
    notes = db.Column(db.Text)
    
    # Expected manifest fields (locked at dispatch for reconciliation)
    expected_payment_method = db.Column(db.String(20), nullable=True)  # CASH, DAY_CHEQUE, POST_DATED, ONLINE, CREDIT
    expected_amount = db.Column(db.Numeric(12, 2), nullable=True)
    manifest_locked_at = db.Column(UTCDateTime(), nullable=True)
    manifest_locked_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    # Discrepancy value (total monetary impact of exceptions on this invoice)
    discrepancy_value = db.Column(db.Numeric(10, 2), nullable=True, default=0)
    
    # Versioning columns for reroute-safe history
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default='true')
    effective_from = db.Column(UTCDateTime(), nullable=False, default=get_utc_now, server_default=db.func.now())
    effective_to = db.Column(UTCDateTime(), nullable=True)
    changed_by = db.Column(db.String(64), nullable=True)
    
    # Relationships
    stop = db.relationship('RouteStop', backref='invoices')
    invoice = db.relationship('Invoice', backref='route_stop_invoices')
    
    def __repr__(self):
        return f"<RouteStopInvoice {self.invoice_no} @ Stop {self.route_stop_id} active={self.is_active}>"


# Shipping Events Table (audit trail for shipping actions)
class ShippingEvent(db.Model):
    __tablename__ = 'shipping_events'
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    action = db.Column(db.String(20), nullable=False)  # 'shipped', 'unship' (for reversals)
    actor = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    timestamp = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    note = db.Column(db.Text, nullable=True)  # Optional note about the shipping action
    
    # Relationships
    invoice = db.relationship('Invoice', backref='shipping_events')
    user = db.relationship('User', backref='shipping_actions')
    
    def __repr__(self):
        return f"<ShippingEvent {self.action} {self.invoice_no} by {self.actor}>"

# Legacy Delivery Events Table (audit trail for simple delivery actions)  
class InvoiceDeliveryEvent(db.Model):
    __tablename__ = 'invoice_delivery_events'
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    action = db.Column(db.String(30), nullable=False)  # 'delivered', 'undelivered', 'delivery_failed', 'returned_to_warehouse'
    actor = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    timestamp = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    reason = db.Column(db.Text, nullable=True)  # Delivery failure reason or notes
    
    # Relationships
    invoice = db.relationship('Invoice', backref='invoice_delivery_events')
    user = db.relationship('User', backref='invoice_delivery_actions')
    
    def __repr__(self):
        return f"<InvoiceDeliveryEvent {self.action} {self.invoice_no} by {self.actor}>"


# Invoice Payment Expectations (for COD completeness tracking)
class InvoicePaymentExpectation(db.Model):
    __tablename__ = 'invoice_payment_expectations'
    
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no', ondelete='CASCADE'), primary_key=True)
    expected_payment_method = db.Column(db.String(20), nullable=False)  # CASH, CHEQUE, CARD, BANK_TRANSFER, CREDIT
    is_cod = db.Column(db.Boolean, nullable=False, default=False)  # True if payment on delivery expected
    expected_amount = db.Column(db.Numeric(12, 2), nullable=True)  # Default: invoice total
    
    # Snapshot fields for audit
    customer_code_365 = db.Column(db.String(50), nullable=True)
    terms_code = db.Column(db.String(50), nullable=True)
    due_days = db.Column(db.Integer, nullable=True)
    captured_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    
    # Relationship
    invoice = db.relationship('Invoice', backref=db.backref('payment_expectation', uselist=False))
    
    def __repr__(self):
        return f"<InvoicePaymentExpectation {self.invoice_no}: {self.expected_payment_method} cod={self.is_cod}>"


# Delivery Discrepancy Tracking
class DeliveryDiscrepancy(db.Model):
    __tablename__ = 'delivery_discrepancies'
    
    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    item_code_expected = db.Column(db.String(50), nullable=False)
    item_name = db.Column(db.String(200), nullable=True)
    qty_expected = db.Column(db.Integer, nullable=False)
    qty_actual = db.Column(db.Float, nullable=True)
    discrepancy_type = db.Column(db.String(50), nullable=False)
    reported_value = db.Column(db.Numeric(12, 2), nullable=True)  # Value recorded by driver at time of delivery
    deduct_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # Amount deducted from driver collection
    reported_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    reported_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    reported_source = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(20), default='reported', nullable=False)
    is_validated = db.Column(db.Boolean, default=False, nullable=False)
    validated_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    validated_at = db.Column(UTCDateTime(), nullable=True)
    is_resolved = db.Column(db.Boolean, default=False, nullable=False)
    resolved_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    resolved_at = db.Column(UTCDateTime(), nullable=True)
    resolution_action = db.Column(db.String(50), nullable=True)
    note = db.Column(db.Text, nullable=True)
    photo_paths = db.Column(db.Text, nullable=True)
    picker_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    picked_at = db.Column(UTCDateTime(), nullable=True)
    delivery_date = db.Column(db.Date, nullable=True)
    shelf_code_365 = db.Column(db.String(50), nullable=True)
    location = db.Column(db.String(100), nullable=True)
    
    # Substitution/Wrong Item tracking (actual item that was sent instead)
    actual_item_id = db.Column(db.Integer, nullable=True)
    actual_item_code = db.Column(db.Text, nullable=True)
    actual_item_name = db.Column(db.Text, nullable=True)
    actual_qty = db.Column(db.Numeric(12, 3), nullable=True)
    actual_barcode = db.Column(db.Text, nullable=True)
    
    # Warehouse verification (for reconciliation workflow)
    warehouse_checked_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    warehouse_checked_at = db.Column(UTCDateTime(), nullable=True)
    warehouse_result = db.Column(db.String(30), nullable=True)  # FOUND / RETURNED / LOST / DAMAGED / OTHER
    warehouse_note = db.Column(db.Text, nullable=True)
    
    # Credit note workflow
    credit_note_required = db.Column(db.Boolean, default=False, nullable=False)
    credit_note_no = db.Column(db.String(50), nullable=True)
    credit_note_amount = db.Column(db.Numeric(12, 2), nullable=True)
    credit_note_created_at = db.Column(UTCDateTime(), nullable=True)
    
    invoice = db.relationship('Invoice', backref='delivery_discrepancies')
    reporter = db.relationship('User', foreign_keys=[reported_by], backref='reported_discrepancies')
    validator = db.relationship('User', foreign_keys=[validated_by], backref='validated_discrepancies')
    resolver = db.relationship('User', foreign_keys=[resolved_by], backref='resolved_discrepancies')
    picker = db.relationship('User', foreign_keys=[picker_username], backref='picked_discrepancies')
    warehouse_checker = db.relationship('User', foreign_keys=[warehouse_checked_by], backref='warehouse_checked_discrepancies')
    events = db.relationship('DeliveryDiscrepancyEvent', backref='discrepancy', cascade='all, delete-orphan', order_by='DeliveryDiscrepancyEvent.timestamp.desc()')
    
    def __repr__(self):
        return f"<DeliveryDiscrepancy {self.id} - {self.invoice_no} {self.item_code_expected}>"

class DeliveryDiscrepancyEvent(db.Model):
    __tablename__ = 'delivery_discrepancy_events'
    
    id = db.Column(db.Integer, primary_key=True)
    discrepancy_id = db.Column(db.Integer, db.ForeignKey('delivery_discrepancies.id'), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)
    actor = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    timestamp = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    note = db.Column(db.Text, nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    
    user = db.relationship('User', backref='discrepancy_events')
    
    def __repr__(self):
        return f"<DeliveryDiscrepancyEvent {self.event_type} by {self.actor}>"

# Configurable Discrepancy Types
class DiscrepancyType(db.Model):
    __tablename__ = 'discrepancy_types'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    
    # Behavior flags for discrepancy-driven settlement
    deducts_from_collection = db.Column(db.Boolean, default=True, nullable=False)  # Deduct amount from driver collection
    cn_required = db.Column(db.Boolean, default=True, nullable=False)  # Credit note must be issued
    return_expected = db.Column(db.Boolean, default=False, nullable=False)  # Physical return expected from customer
    requires_actual_item = db.Column(db.Boolean, default=False, nullable=False)  # Requires actual_item_code/qty fields
    
    def __repr__(self):
        return f"<DiscrepancyType {self.name}>"

# Configurable Stock Resolution Actions
class StockResolution(db.Model):
    __tablename__ = 'stock_resolutions'
    
    id = db.Column(db.Integer, primary_key=True)
    discrepancy_type = db.Column(db.String(50), nullable=False)
    resolution_name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    
    def __repr__(self):
        return f"<StockResolution {self.discrepancy_type}: {self.resolution_name}>"

# PS365 Customer Data Model
class PSCustomer(db.Model, SoftDeleteMixin, ActivatableMixin):
    __tablename__ = 'ps_customers'
    
    customer_code_365 = db.Column(db.String(50), primary_key=True)
    customer_code_secondary = db.Column(db.Text, nullable=True)
    is_company = db.Column(db.Boolean, nullable=True)
    company_name = db.Column(db.Text, nullable=True)
    store_code_365 = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    tel_1 = db.Column(db.Text, nullable=True)
    mobile = db.Column(db.Text, nullable=True)
    sms = db.Column(db.Text, nullable=True)
    website = db.Column(db.Text, nullable=True)
    category_code_1_365 = db.Column(db.Text, nullable=True)
    category_1_name = db.Column(db.Text, nullable=True)
    category_code_2_365 = db.Column(db.Text, nullable=True)
    category_2_name = db.Column(db.Text, nullable=True)
    company_activity_code_365 = db.Column(db.Text, nullable=True)
    company_activity_name = db.Column(db.Text, nullable=True)
    credit_limit_amount = db.Column(db.Float, nullable=True)
    vat_registration_number = db.Column(db.Text, nullable=True)
    address_line_1 = db.Column(db.Text, nullable=True)
    address_line_2 = db.Column(db.Text, nullable=True)
    address_line_3 = db.Column(db.Text, nullable=True)
    postal_code = db.Column(db.Text, nullable=True)
    town = db.Column(db.Text, nullable=True)
    contact_last_name = db.Column(db.Text, nullable=True)
    contact_first_name = db.Column(db.Text, nullable=True)
    agent_code_365 = db.Column(db.Text, nullable=True)
    agent_name = db.Column(db.Text, nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    email = db.Column(db.Text, nullable=True)
    delivery_days = db.Column(db.Text, nullable=True)  # From text_field_4_value in PS365
    delivery_days_status = db.Column(db.String(20), default='EMPTY')  # OK, INVALID, EMPTY
    delivery_days_invalid_tokens = db.Column(db.Text, nullable=True)  # JSON list of bad tokens
    delivery_days_parsed_at = db.Column(UTCDateTime(), nullable=True)
    last_synced_at = db.Column(UTCDateTime(), nullable=True)
    reporting_group = db.Column(db.Text, nullable=True)
    
    # Relationships
    delivery_slots = db.relationship('CustomerDeliverySlot', backref='customer', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<PSCustomer {self.customer_code_365}: {self.company_name}>"

class CustomerDeliverySlot(db.Model):
    """Normalized delivery slots for efficient filtering"""
    __tablename__ = 'customer_delivery_slots'
    
    id = db.Column(db.Integer, primary_key=True)
    customer_code_365 = db.Column(db.String(50), db.ForeignKey('ps_customers.customer_code_365', ondelete='CASCADE'), nullable=False, index=True)
    dow = db.Column(db.Integer, nullable=False)  # 1-7
    week_code = db.Column(db.Integer, nullable=False)  # 1-2
    
    __table_args__ = (
        db.UniqueConstraint('customer_code_365', 'dow', 'week_code', name='uniq_customer_slot'),
        db.Index('ix_delivery_slots_dow_week', 'dow', 'week_code'),
    )
    
    def __repr__(self):
        return f"<CustomerDeliverySlot {self.customer_code_365}: {self.dow}-{self.week_code}>"

# Customer Receipt Models
class ReceiptSequence(db.Model):
    """Sequence table for generating unique receipt reference numbers"""
    __tablename__ = 'receipt_sequence'
    
    id = db.Column(db.Integer, primary_key=True)
    last_number = db.Column(db.Integer, nullable=False, default=1000000)  # Start from 1,000,000 -> next becomes R1000001
    updated_at = db.Column(UTCDateTime(), default=get_utc_now, onupdate=get_utc_now)
    
    def __repr__(self):
        return f"<ReceiptSequence last={self.last_number}>"

class ReceiptLog(db.Model):
    """Log of all customer receipts issued via Powersoft365 API"""
    __tablename__ = 'receipt_log'
    
    id = db.Column(db.Integer, primary_key=True)
    reference_number = db.Column(db.String(32), unique=True, nullable=False)  # Unique constraint for duplicate prevention
    customer_code_365 = db.Column(db.String(32), index=True, nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    comments = db.Column(db.String(1000))
    response_id = db.Column(db.String(128), nullable=True)  # Powersoft365 transaction code
    success = db.Column(db.Integer, default=0)  # 1/0
    request_json = db.Column(db.Text)  # stored for audit
    response_json = db.Column(db.Text)  # stored for audit
    created_at = db.Column(UTCDateTime(), default=get_utc_now)
    invoice_no = db.Column(db.String(500), nullable=True)  # Can store single or comma-separated invoice numbers
    driver_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)  # Driver who created receipt
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id'), nullable=True)  # Link to route stop for tracking
    
    def __repr__(self):
        return f"<ReceiptLog {self.reference_number}: {self.customer_code_365} ${self.amount}>"

class PaymentEntry(db.Model):
    __tablename__ = 'payment_entries'

    id = db.Column(db.Integer, primary_key=True)
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id', ondelete='CASCADE'), nullable=False, index=True)

    method = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    cheque_no = db.Column(db.String(64))
    cheque_date = db.Column(db.Date)

    commit_mode = db.Column(db.String(20), nullable=False)
    doc_type = db.Column(db.String(20), nullable=False)
    ps_status = db.Column(db.String(20), nullable=False, default='NEW')
    ps_reference = db.Column(db.String(64))
    ps_error = db.Column(db.Text)

    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    last_attempt_at = db.Column(db.DateTime)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=get_utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=get_utc_now, onupdate=get_utc_now)

    stop = db.relationship('RouteStop', backref='payment_entries')

    def to_dict(self):
        return {
            'id': self.id,
            'route_stop_id': self.route_stop_id,
            'method': self.method,
            'amount': float(self.amount or 0),
            'cheque_no': self.cheque_no,
            'cheque_date': self.cheque_date.isoformat() if self.cheque_date else None,
            'commit_mode': self.commit_mode,
            'doc_type': self.doc_type,
            'ps_status': self.ps_status,
            'ps_reference': self.ps_reference,
            'ps_error': self.ps_error,
            'attempt_count': self.attempt_count,
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f"<PaymentEntry {self.id} stop={self.route_stop_id} {self.method} {self.ps_status}>"


# Payment Terms Management Models
class PaymentCustomer(db.Model):
    """Customer reference for payment terms (separate from PS365 sync)"""
    __tablename__ = 'payment_customers'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, index=True, nullable=False)  # PS customer code
    name = db.Column(db.String(255), nullable=False)
    group = db.Column(db.String(100), index=True)
    
    def __repr__(self):
        return f"<PaymentCustomer {self.code}: {self.name}>"

class CreditTerms(db.Model):
    """Customer credit terms and payment method configurations"""
    __tablename__ = 'credit_terms'
    
    id = db.Column(db.Integer, primary_key=True)
    customer_code = db.Column(db.String(50), db.ForeignKey('payment_customers.code'), index=True, nullable=False)
    
    # Core terms
    terms_code = db.Column(db.String(50), nullable=False)  # e.g. COD, NET30, NET60
    due_days = db.Column(db.Integer, nullable=False, default=0)
    is_credit = db.Column(db.Boolean, nullable=False, default=False)
    
    # Limits & payment methods
    credit_limit = db.Column(db.Numeric(12, 2), nullable=True)  # Support decimal credit limits
    allow_cash = db.Column(db.Boolean, default=False)
    allow_card_pos = db.Column(db.Boolean, default=True)
    allow_bank_transfer = db.Column(db.Boolean, default=True)
    allow_cheque = db.Column(db.Boolean, default=False)
    cheque_days_allowed = db.Column(db.Integer, nullable=True)  # 0 = same-day only
    
    # Optional constraints / notes
    min_cash_allowed = db.Column(db.Integer, nullable=True)
    max_cash_allowed = db.Column(db.Integer, nullable=True)
    notes_for_driver = db.Column(db.Text, nullable=True)
    
    valid_from = db.Column(db.Date, default=get_utc_today)
    valid_to = db.Column(db.Date, nullable=True)  # NULL = active
    
    __table_args__ = (
        db.UniqueConstraint('customer_code', 'valid_from', name='uniq_terms_version'),
    )
    
    def __repr__(self):
        return f"<CreditTerms {self.customer_code}: {self.terms_code}>"

# Driver App Models

class DeliveryEvent(db.Model):
    """Audit trail for all delivery actions (start, pause, deliver, fail, etc.)"""
    __tablename__ = 'route_delivery_events'
    
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id'), nullable=True)
    event_type = db.Column(db.String(50), nullable=False)  # start, pause, resume, deliver, fail, return, complete
    payload = db.Column(db.JSON, nullable=True)  # Flexible JSON for event-specific data
    gps_lat = db.Column(db.Numeric(10, 8), nullable=True)
    gps_lng = db.Column(db.Numeric(11, 8), nullable=True)
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    actor_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    
    # Relationships
    route = db.relationship('Shipment', backref='route_delivery_events')
    stop = db.relationship('RouteStop', backref='route_delivery_events')
    actor = db.relationship('User', backref='route_delivery_events')
    
    def __repr__(self):
        return f"<DeliveryEvent {self.event_type} on Route {self.route_id}>"

class DeliveryLine(db.Model):
    """Actual delivered quantities per item (supports exception-only delivery)"""
    __tablename__ = 'delivery_lines'
    
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id'), nullable=False)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    item_code = db.Column(db.String(50), nullable=False)
    qty_ordered = db.Column(db.Numeric(10, 2), nullable=False)
    qty_delivered = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    
    # Relationships
    route = db.relationship('Shipment', backref='delivery_lines')
    stop = db.relationship('RouteStop', backref='delivery_lines')
    invoice = db.relationship('Invoice', backref='delivery_lines')
    
    def __repr__(self):
        return f"<DeliveryLine {self.invoice_no}-{self.item_code}: {self.qty_delivered}/{self.qty_ordered}>"

class CODReceipt(db.Model):
    """Cash On Delivery receipt tracking"""
    __tablename__ = 'cod_receipts'
    
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id'), nullable=False)
    driver_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    
    # Invoice references (can be multiple invoices per stop)
    invoice_nos = db.Column(db.JSON, nullable=False)  # Array of invoice numbers
    
    # COD amounts
    expected_amount = db.Column(db.Numeric(12, 2), nullable=False)  # Calculated from delivered items
    received_amount = db.Column(db.Numeric(12, 2), nullable=False)  # Actual amount collected
    variance = db.Column(db.Numeric(12, 2), nullable=True)  # Difference (calculated)
    
    # Payment details
    payment_method = db.Column(db.String(20), nullable=False, default='cash')  # cash, card, cheque, bank_transfer
    cheque_number = db.Column(db.String(50), nullable=True)
    cheque_date = db.Column(db.Date, nullable=True)
    note = db.Column(db.Text, nullable=True)
    
    # PS365 integration
    ps365_receipt_id = db.Column(db.String(128), nullable=True)  # Reference from PS365 API
    ps365_synced_at = db.Column(UTCDateTime(), nullable=True)
    ps365_reference_number = db.Column(db.String(128), nullable=True)
    
    # Document type & lifecycle
    doc_type = db.Column(db.String(30), nullable=False, default='official')  # official, pdc_ack, online_notice
    status = db.Column(db.String(20), nullable=False, default='DRAFT')  # DRAFT, ISSUED, VOIDED
    
    # Locking (set on first print)
    locked_at = db.Column(UTCDateTime(), nullable=True)
    locked_by = db.Column(db.String(64), db.ForeignKey('users.username', name='fk_cod_receipts_locked_by'), nullable=True)
    
    # Print tracking
    print_count = db.Column(db.Integer, nullable=False, default=0)
    first_printed_at = db.Column(UTCDateTime(), nullable=True)
    last_printed_at = db.Column(UTCDateTime(), nullable=True)
    
    # Void / reissue
    voided_at = db.Column(UTCDateTime(), nullable=True)
    voided_by = db.Column(db.String(64), db.ForeignKey('users.username', name='fk_cod_receipts_voided_by'), nullable=True)
    void_reason = db.Column(db.Text, nullable=True)
    replaced_by_cod_receipt_id = db.Column(db.Integer, db.ForeignKey('cod_receipts.id', name='fk_cod_receipts_replaced_by'), nullable=True)
    
    # Idempotency
    client_request_id = db.Column(db.String(128), nullable=True)
    
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    
    # Relationships
    route = db.relationship('Shipment', backref='cod_receipts')
    stop = db.relationship('RouteStop', backref='cod_receipts')
    driver = db.relationship('User', foreign_keys=[driver_username], backref='cod_receipts')
    locker = db.relationship('User', foreign_keys=[locked_by], backref='locked_cod_receipts')
    voider = db.relationship('User', foreign_keys=[voided_by], backref='voided_cod_receipts')
    replacement = db.relationship('CODReceipt', remote_side='CODReceipt.id', foreign_keys=[replaced_by_cod_receipt_id], backref='replaces')
    
    __table_args__ = (
        db.Index('idx_cod_receipts_status', 'status'),
        db.Index('idx_cod_receipts_doc_type', 'doc_type'),
        db.Index('idx_cod_receipts_client_request_id', 'client_request_id'),
    )
    
    def __repr__(self):
        return f"<CODReceipt Stop {self.route_stop_id}: ${self.received_amount}>"


class CODInvoiceAllocation(db.Model):
    """Per-invoice payment allocation for settlement reconciliation"""
    __tablename__ = 'cod_invoice_allocations'
    
    id = db.Column(db.Integer, primary_key=True)
    cod_receipt_id = db.Column(db.Integer, db.ForeignKey('cod_receipts.id', ondelete='CASCADE'), nullable=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    route_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    
    expected_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # Invoice total before deductions
    received_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # Actual amount collected
    deduct_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # Sum of discrepancy deductions
    
    payment_method = db.Column(db.String(30), nullable=False, default='cash')  # cash, cheque, online, postdated
    is_pending = db.Column(db.Boolean, nullable=False, default=False)  # True for online/postdated
    cheque_number = db.Column(db.String(50), nullable=True)
    cheque_date = db.Column(db.Date, nullable=True)
    
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    
    # Relationships
    invoice = db.relationship('Invoice', backref='payment_allocations')
    route = db.relationship('Shipment', backref='invoice_allocations')
    cod_receipt = db.relationship('CODReceipt', backref='allocations')
    
    def __repr__(self):
        return f"<CODInvoiceAllocation {self.invoice_no}: {self.payment_method} ${self.received_amount}>"

class PODRecord(db.Model):
    """Proof of Delivery records"""
    __tablename__ = 'pod_records'
    
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id'), nullable=False)
    
    # Invoice references
    invoice_nos = db.Column(db.JSON, nullable=False)  # Array of invoice numbers
    
    # POD details
    has_physical_signed_invoice = db.Column(db.Boolean, default=True)
    receiver_name = db.Column(db.String(200), nullable=True)
    receiver_relationship = db.Column(db.String(100), nullable=True)  # owner, manager, reception, etc.
    
    # Photo evidence (stored as JSON array of paths)
    photo_paths = db.Column(db.JSON, nullable=True)  # Array of file paths
    
    # GPS coordinates
    gps_lat = db.Column(db.Numeric(10, 8), nullable=True)
    gps_lng = db.Column(db.Numeric(11, 8), nullable=True)
    
    # Collection details
    collected_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    collected_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    route = db.relationship('Shipment', backref='pod_records')
    stop = db.relationship('RouteStop', backref='pod_records')
    collector = db.relationship('User', backref='pod_records')
    
    def __repr__(self):
        return f"<PODRecord Stop {self.route_stop_id} by {self.collected_by}>"


class RouteReturnHandover(db.Model):
    """Return handover tracking for failed delivery invoices"""
    __tablename__ = 'route_return_handover'
    
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('shipments.id'), nullable=False)
    route_stop_id = db.Column(db.Integer, db.ForeignKey('route_stop.route_stop_id'), nullable=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False)
    
    # Driver confirmation (when driver returns to warehouse)
    driver_confirmed_at = db.Column(UTCDateTime(), nullable=True)
    driver_username = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    # Warehouse receipt confirmation
    warehouse_received_at = db.Column(UTCDateTime(), nullable=True)
    received_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    # Details
    packages_count = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    photo_paths = db.Column(db.JSON, nullable=True)  # Evidence photos
    
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    
    # Relationships
    route = db.relationship('Shipment', backref='return_handovers')
    stop = db.relationship('RouteStop', backref='return_handovers')
    invoice = db.relationship('Invoice', backref='return_handovers')
    driver = db.relationship('User', foreign_keys=[driver_username], backref='driver_return_handovers')
    receiver = db.relationship('User', foreign_keys=[received_by], backref='warehouse_return_receipts')
    
    def __repr__(self):
        return f"<RouteReturnHandover {self.invoice_no} route={self.route_id}>"


class InvoicePostDeliveryCase(db.Model):
    """Post-delivery cases for warehouse intake management"""
    __tablename__ = 'invoice_post_delivery_cases'
    
    id = db.Column(db.BigInteger, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no', ondelete='CASCADE'), nullable=False)
    route_id = db.Column(db.BigInteger, db.ForeignKey('shipments.id', ondelete='SET NULL'), nullable=True)
    route_stop_id = db.Column(db.BigInteger, db.ForeignKey('route_stop.route_stop_id', ondelete='SET NULL'), nullable=True)
    
    # Case status: OPEN, VERIFIED, CN_ISSUED, CLOSED
    status = db.Column(db.String(50), nullable=False, default='OPEN')
    reason = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    # Credit note tracking
    credit_note_required = db.Column(db.Boolean, default=False, nullable=False)  # At least one discrepancy requires CN
    credit_note_expected_amount = db.Column(db.Numeric(12, 2), default=0)  # Sum of deduct_amount where CN required
    credit_note_no = db.Column(db.String(64), nullable=True)  # CN reference from PS365
    credit_note_issued_at = db.Column(UTCDateTime(), nullable=True)
    credit_note_issued_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    created_by = db.Column(db.String(100), nullable=True)
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    updated_at = db.Column(UTCDateTime(), default=get_utc_now, onupdate=get_utc_now, nullable=False)
    
    # Relationships
    invoice = db.relationship('Invoice', backref='post_delivery_cases')
    route = db.relationship('Shipment', backref='post_delivery_cases')
    stop = db.relationship('RouteStop', backref='post_delivery_cases')
    cn_issuer = db.relationship('User', foreign_keys=[credit_note_issued_by], backref='issued_credit_notes')
    
    def __repr__(self):
        return f"<InvoicePostDeliveryCase {self.id}: {self.invoice_no} - {self.status}>"


class InvoiceRouteHistory(db.Model):
    """Immutable audit trail of invoice routing movements"""
    __tablename__ = 'invoice_route_history'
    
    id = db.Column(db.BigInteger, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no', ondelete='CASCADE'), nullable=False)
    route_id = db.Column(db.BigInteger, db.ForeignKey('shipments.id', ondelete='SET NULL'), nullable=True)
    route_stop_id = db.Column(db.BigInteger, db.ForeignKey('route_stop.route_stop_id', ondelete='SET NULL'), nullable=True)
    
    # Action types: PARTIAL_DELIVERED, FAILED, SENT_TO_WAREHOUSE, INTAKE_RECEIVED, 
    # REROUTE_QUEUED, REROUTED, RETURN_TO_STOCK, CLOSED
    action = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    
    actor_username = db.Column(db.String(100), nullable=True)
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    
    # Relationships
    invoice = db.relationship('Invoice', backref='route_history')
    route = db.relationship('Shipment', backref='invoice_histories')
    stop = db.relationship('RouteStop', backref='invoice_histories')
    
    def __repr__(self):
        return f"<InvoiceRouteHistory {self.invoice_no}: {self.action}>"


class RerouteRequest(db.Model):
    """Reroute requests for invoices needing re-delivery"""
    __tablename__ = 'reroute_requests'
    
    id = db.Column(db.BigInteger, primary_key=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no', ondelete='CASCADE'), nullable=False)
    
    requested_by = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='OPEN')  # OPEN, ASSIGNED, DONE, CANCELLED
    notes = db.Column(db.Text, nullable=True)
    
    assigned_route_id = db.Column(db.BigInteger, db.ForeignKey('shipments.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    completed_at = db.Column(UTCDateTime(), nullable=True)
    
    # Relationships
    invoice = db.relationship('Invoice', backref='reroute_requests')
    assigned_route = db.relationship('Shipment', backref='reroute_requests')
    
    def __repr__(self):
        return f"<RerouteRequest {self.id}: {self.invoice_no} - {self.status}>"


# ===========================
# PO Receiving Models
# ===========================

class PurchaseOrder(db.Model, SoftDeleteMixin):
    """Purchase orders downloaded from Powersoft365 for receiving"""
    __tablename__ = 'purchase_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    code_365 = db.Column(db.String(100), nullable=True, index=True)
    shopping_cart_code = db.Column(db.String(100), nullable=True, index=True)
    supplier_code = db.Column(db.String(100), nullable=True)
    supplier_name = db.Column(db.String(200), nullable=True)
    status_code = db.Column(db.String(50), nullable=True)
    status_name = db.Column(db.String(100), nullable=True)
    order_date_local = db.Column(db.String(50), nullable=True)
    order_date_utc0 = db.Column(db.String(50), nullable=True)
    comments = db.Column(db.Text, nullable=True)
    total_sub = db.Column(db.Numeric(12, 2), nullable=True)
    total_discount = db.Column(db.Numeric(12, 2), nullable=True)
    total_vat = db.Column(db.Numeric(12, 2), nullable=True)
    total_grand = db.Column(db.Numeric(12, 2), nullable=True)
    
    downloaded_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    downloaded_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    # Archive tracking
    is_archived = db.Column(db.Boolean, default=False, nullable=False, index=True)
    archived_at = db.Column(UTCDateTime(), nullable=True)
    archived_by = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    
    # User-editable description
    description = db.Column(db.Text, nullable=True)
    
    # Relationships
    lines = db.relationship('PurchaseOrderLine', backref='purchase_order', cascade='all, delete-orphan', lazy='dynamic')
    sessions = db.relationship('ReceivingSession', backref='purchase_order', cascade='all, delete-orphan', lazy='dynamic')
    downloader = db.relationship('User', foreign_keys=[downloaded_by])
    archiver = db.relationship('User', foreign_keys=[archived_by])
    
    def __repr__(self):
        return f"<PurchaseOrder {self.code_365 or self.shopping_cart_code}>"


class PurchaseOrderLine(db.Model):
    """Line items in purchase orders"""
    __tablename__ = 'purchase_order_lines'
    
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id', ondelete='CASCADE'), nullable=False)
    
    line_number = db.Column(db.Integer, nullable=False)
    line_id_365 = db.Column(db.String(100), nullable=True, index=True)  # PS365 unique line identifier
    item_code_365 = db.Column(db.String(100), nullable=False, index=True)
    item_name = db.Column(db.String(500), nullable=True)
    item_barcode = db.Column(db.String(100), nullable=True)  # Barcode number from PS365
    supplier_item_code = db.Column(db.String(255), nullable=True)  # Supplier's item code from DwItem
    
    line_quantity = db.Column(db.Numeric(12, 4), nullable=True)
    line_price_excl_vat = db.Column(db.Numeric(12, 2), nullable=True)
    line_total_sub = db.Column(db.Numeric(12, 2), nullable=True)
    line_total_discount = db.Column(db.Numeric(12, 2), nullable=True)
    line_total_discount_percentage = db.Column(db.Numeric(5, 2), nullable=True)
    line_vat_code_365 = db.Column(db.String(50), nullable=True)
    line_total_vat = db.Column(db.Numeric(12, 2), nullable=True)
    line_total_vat_percentage = db.Column(db.Numeric(5, 2), nullable=True)
    line_total_grand = db.Column(db.Numeric(12, 2), nullable=True)
    
    # Item tracking requirements from PS365
    item_has_expiration_date = db.Column(db.Boolean, default=False, nullable=False)
    item_has_lot_number = db.Column(db.Boolean, default=False, nullable=False)
    item_has_serial_number = db.Column(db.Boolean, default=False, nullable=False)
    
    # Shelf location from PS365 (JSON array of shelf objects)
    shelf_locations = db.Column(db.Text, nullable=True)
    
    # Unit Information
    unit_type = db.Column(db.String(50), nullable=True)
    pieces_per_unit = db.Column(db.Integer, nullable=True)
    
    # Accurate stock fields for Store 777
    stock_qty = db.Column(db.Numeric(12, 4), nullable=True)
    stock_reserved_qty = db.Column(db.Numeric(12, 4), nullable=True)
    stock_ordered_qty = db.Column(db.Numeric(12, 4), nullable=True)
    available_qty = db.Column(db.Numeric(12, 4), nullable=True)
    stock_synced_at = db.Column(UTCDateTime(), nullable=True)
    
    # Relationships
    receiving_lines = db.relationship('ReceivingLine', backref='po_line', cascade='all, delete-orphan', lazy='dynamic')
    
    def __repr__(self):
        return f"<PurchaseOrderLine {self.line_number}: {self.item_code_365}>"


class ReceivingSession(db.Model):
    """Session for receiving a purchase order (can be paused/resumed)"""
    __tablename__ = 'receiving_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id', ondelete='CASCADE'), nullable=False)
    receipt_code = db.Column(db.String(50), nullable=False, unique=True, index=True)
    
    operator = db.Column(db.String(64), db.ForeignKey('users.username'), nullable=True)
    comments = db.Column(db.Text, nullable=True)
    started_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    finished_at = db.Column(UTCDateTime(), nullable=True)
    
    # Relationships
    lines = db.relationship('ReceivingLine', backref='session', cascade='all, delete-orphan', lazy='dynamic')
    operator_user = db.relationship('User', foreign_keys=[operator])
    
    def __repr__(self):
        return f"<ReceivingSession {self.receipt_code}>"


class ReceivingLine(db.Model):
    """Individual lots received (multiple lots per PO line supported)"""
    __tablename__ = 'receiving_lines'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('receiving_sessions.id', ondelete='CASCADE'), nullable=False)
    po_line_id = db.Column(db.Integer, db.ForeignKey('purchase_order_lines.id', ondelete='CASCADE'), nullable=False)
    
    barcode_scanned = db.Column(db.String(200), nullable=True)
    item_code_365 = db.Column(db.String(100), nullable=False)
    qty_received = db.Column(db.Numeric(12, 4), nullable=False)
    expiry_date = db.Column(db.Date, nullable=True)
    lot_note = db.Column(db.Text, nullable=True)
    
    received_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False)
    
    def __repr__(self):
        return f"<ReceivingLine {self.item_code_365}: {self.qty_received}>"


class StockPosition(db.Model):
    """Stock position data from Dropbox SerialsStockPositionReport1.xlsx"""
    __tablename__ = 'stock_positions'
    
    id = db.Column(db.Integer, primary_key=True)
    item_code = db.Column(db.String(100), nullable=False, index=True)
    item_description = db.Column(db.String(500), nullable=True)
    store_code = db.Column(db.String(50), nullable=False, index=True)
    store_name = db.Column(db.String(200), nullable=False, index=True)
    expiry_date = db.Column(db.String(20), nullable=True)
    stock_quantity = db.Column(db.Numeric(12, 4), nullable=False, default=0)
    
    imported_at = db.Column(UTCDateTime(), default=get_utc_now, nullable=False, index=True)
    
    def __repr__(self):
        return f"<StockPosition {self.item_code} @ {self.store_name}: {self.stock_quantity}>"


# ============================================================================
# DATA WAREHOUSE MODELS - PS365 dimension and fact tables
# ============================================================================

class DwItem(db.Model):
    """Data warehouse items table - contains all items from PS365 with dimensional keys"""
    __tablename__ = "ps_items_dw"

    item_code_365 = db.Column(db.String(64), primary_key=True)
    item_name = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean, nullable=False)

    category_code_365 = db.Column(db.String(64), nullable=True)
    brand_code_365 = db.Column(db.String(64), nullable=True)
    season_code_365 = db.Column(db.String(64), nullable=True)
    attribute_1_code_365 = db.Column(db.String(64), nullable=True)
    attribute_2_code_365 = db.Column(db.String(64), nullable=True)
    attribute_3_code_365 = db.Column(db.String(64), nullable=True)
    attribute_4_code_365 = db.Column(db.String(64), nullable=True)
    attribute_5_code_365 = db.Column(db.String(64), nullable=True)
    attribute_6_code_365 = db.Column(db.String(64), nullable=True)

    # Physical dimensions from PS365
    item_length = db.Column(db.Numeric(10, 3), nullable=True)
    item_width = db.Column(db.Numeric(10, 3), nullable=True)
    item_height = db.Column(db.Numeric(10, 3), nullable=True)
    item_weight = db.Column(db.Numeric(10, 3), nullable=True)
    number_of_pieces = db.Column(db.Integer, nullable=True)
    selling_qty = db.Column(db.Numeric(10, 3), nullable=True)
    
    # Barcode from PS365 (preferring label barcode)
    barcode = db.Column(db.String(100), nullable=True)
    
    # Supplier Item Code from PS365 (text_field_2_value)
    supplier_item_code = db.Column(db.String(255), nullable=True)
    min_order_qty = db.Column(db.Integer, nullable=True)  # Items.number_field_5_value

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    # WMS Operational Intelligence classification outputs
    wms_zone = db.Column(db.String(50), nullable=True)  # MAIN, SENSITIVE, SNACKS, CROSS_SHIPPING
    wms_unit_type = db.Column(db.String(50), nullable=True)  # item, pack, box, case, virtual_pack
    wms_fragility = db.Column(db.String(20), nullable=True)  # YES, SEMI, NO
    wms_stackability = db.Column(db.String(20), nullable=True)  # YES, LIMITED, NO
    wms_temperature_sensitivity = db.Column(db.String(30), nullable=True)  # normal, heat_sensitive, cool_required
    wms_pressure_sensitivity = db.Column(db.String(20), nullable=True)  # low, medium, high
    wms_shape_type = db.Column(db.String(30), nullable=True)  # cubic, flat, round, irregular
    wms_spill_risk = db.Column(db.Boolean, nullable=True)
    wms_pick_difficulty = db.Column(db.Integer, nullable=True)  # 1-5
    wms_shelf_height = db.Column(db.String(20), nullable=True)  # LOW, MID, HIGH
    wms_box_fit_rule = db.Column(db.String(30), nullable=True)  # BOTTOM, MIDDLE, TOP, COOLER_BAG

    # WMS classification audit/explainability fields
    wms_class_confidence = db.Column(db.Integer, nullable=True)  # 0-100 overall confidence
    wms_class_source = db.Column(db.String(30), nullable=True)  # RULES, CATEGORY_DEFAULT, MANUAL
    wms_class_notes = db.Column(db.Text, nullable=True)  # Human-readable summary
    wms_classified_at = db.Column(UTCDateTime(), nullable=True)  # UTC timestamp
    wms_class_evidence = db.Column(db.Text, nullable=True)  # JSON: per-attribute {value, confidence, reason}

    def __repr__(self):
        return f"<DwItem {self.item_code_365} - {self.item_name}>"

    def needs_review(self):
        """Check if item needs review based on critical attributes"""
        if not self.active:
            return False
        critical_attrs = [
            self.wms_fragility, self.wms_spill_risk, self.wms_pressure_sensitivity,
            self.wms_temperature_sensitivity, self.wms_box_fit_rule
        ]
        if any(attr is None for attr in critical_attrs):
            return True
        if self.wms_class_confidence is not None and self.wms_class_confidence < 60:
            return True
        return False


class DwItemCategory(db.Model):
    """Data warehouse item categories dimension"""
    __tablename__ = "dw_item_categories"

    category_code_365 = db.Column(db.String(64), primary_key=True)
    category_name = db.Column(db.String(255), nullable=False)
    parent_category_code = db.Column(db.String(64), nullable=True)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwItemCategory {self.category_code_365} - {self.category_name}>"


class DwBrand(db.Model):
    """Data warehouse brands dimension"""
    __tablename__ = "dw_brands"

    brand_code_365 = db.Column(db.String(64), primary_key=True)
    brand_name = db.Column(db.String(255), nullable=False)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwBrand {self.brand_code_365} - {self.brand_name}>"


class DwSeason(db.Model):
    """Data warehouse seasons dimension"""
    __tablename__ = "dw_seasons"

    season_code_365 = db.Column(db.String(64), primary_key=True)
    season_name = db.Column(db.String(255), nullable=False)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwSeason {self.season_code_365} - {self.season_name}>"


class SeasonSupplierSetting(db.Model):
    """Settings for season→supplier mapping including email configuration"""
    __tablename__ = "season_supplier_settings"

    season_code_365 = db.Column(db.String(50), primary_key=True)
    supplier_code = db.Column(db.String(50), nullable=True)

    email_to = db.Column(db.String(255), nullable=True)
    email_cc = db.Column(db.String(500), nullable=True)
    email_comment = db.Column(db.Text, nullable=True)

    updated_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now, onupdate=get_utc_now)

    def __repr__(self):
        return f"<SeasonSupplierSetting {self.season_code_365} -> {self.supplier_code}>"


class DwAttribute1(db.Model):
    """Data warehouse attribute 1 dimension"""
    __tablename__ = "dw_attribute1"

    attribute_1_code_365 = db.Column(db.String(64), primary_key=True)
    attribute_1_name = db.Column(db.String(255), nullable=False)
    attribute_1_secondary_code = db.Column(db.String(64), nullable=True)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwAttribute1 {self.attribute_1_code_365} - {self.attribute_1_name}>"


class DwAttribute2(db.Model):
    """Data warehouse attribute 2 dimension"""
    __tablename__ = "dw_attribute2"

    attribute_2_code_365 = db.Column(db.String(64), primary_key=True)
    attribute_2_name = db.Column(db.String(255), nullable=False)
    attribute_2_secondary_code = db.Column(db.String(64), nullable=True)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwAttribute2 {self.attribute_2_code_365} - {self.attribute_2_name}>"


class DwAttribute3(db.Model):
    """Data warehouse attribute 3 dimension"""
    __tablename__ = "dw_attribute3"

    attribute_3_code_365 = db.Column(db.String(64), primary_key=True)
    attribute_3_name = db.Column(db.String(255), nullable=False)
    attribute_3_secondary_code = db.Column(db.String(64), nullable=True)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwAttribute3 {self.attribute_3_code_365} - {self.attribute_3_name}>"


class DwAttribute4(db.Model):
    """Data warehouse attribute 4 dimension"""
    __tablename__ = "dw_attribute4"

    attribute_4_code_365 = db.Column(db.String(64), primary_key=True)
    attribute_4_name = db.Column(db.String(255), nullable=False)
    attribute_4_secondary_code = db.Column(db.String(64), nullable=True)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwAttribute4 {self.attribute_4_code_365} - {self.attribute_4_name}>"


class DwAttribute5(db.Model):
    """Data warehouse attribute 5 dimension"""
    __tablename__ = "dw_attribute5"

    attribute_5_code_365 = db.Column(db.String(64), primary_key=True)
    attribute_5_name = db.Column(db.String(255), nullable=False)
    attribute_5_secondary_code = db.Column(db.String(64), nullable=True)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwAttribute5 {self.attribute_5_code_365} - {self.attribute_5_name}>"


class DwAttribute6(db.Model):
    """Data warehouse attribute 6 dimension"""
    __tablename__ = "dw_attribute6"

    attribute_6_code_365 = db.Column(db.String(64), primary_key=True)
    attribute_6_name = db.Column(db.String(255), nullable=False)
    attribute_6_secondary_code = db.Column(db.String(64), nullable=True)

    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)

    def __repr__(self):
        return f"<DwAttribute6 {self.attribute_6_code_365} - {self.attribute_6_name}>"


class SyncState(db.Model):
    """
    Generic key/value sync state table.
    Used for tracking incremental item sync (items_last_change_id).
    """
    __tablename__ = "sync_state"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f"<SyncState {self.key}={self.value[:50]}...>"


class PS365SyncLog(db.Model):
    __tablename__ = "ps365_sync_log"
    __table_args__ = (
        db.Index('ix_ps365_sync_log_started', 'started_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    sync_type = db.Column(db.String(50), nullable=False)
    trigger = db.Column(db.String(20), nullable=False, default='manual')
    status = db.Column(db.String(20), nullable=False, default='RUNNING')
    started_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    finished_at = db.Column(UTCDateTime(), nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    items_found = db.Column(db.Integer, default=0)
    items_inserted = db.Column(db.Integer, default=0)
    items_updated = db.Column(db.Integer, default=0)
    items_skipped = db.Column(db.Integer, default=0)
    details = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<PS365SyncLog {self.id} {self.sync_type} {self.status}>"


# Invoice DW Models (Star Schema)

class DwInvoiceHeader(db.Model):
    """Invoice header dimension - one row per invoice"""
    __tablename__ = "dw_invoice_header"
    __table_args__ = (
        db.Index('ix_dw_invoice_header_date_customer', 'invoice_date_utc0', 'customer_code_365'),
        db.Index('ix_dw_invoice_header_date_store', 'invoice_date_utc0', 'store_code_365'),
    )
    
    invoice_no_365 = db.Column(db.String(64), primary_key=True)  # Primary key instead of id
    invoice_type = db.Column(db.String(64), nullable=False)
    invoice_date_utc0 = db.Column(db.Date, nullable=False, index=True)
    
    customer_code_365 = db.Column(db.String(64), nullable=True, index=True)
    store_code_365 = db.Column(db.String(64), nullable=True, index=True)
    user_code_365 = db.Column(db.String(64), nullable=True)
    
    total_sub = db.Column(db.Numeric(18, 4), nullable=True)
    total_discount = db.Column(db.Numeric(18, 4), nullable=True)
    total_net = db.Column(db.Numeric(18, 4), nullable=True)
    total_vat = db.Column(db.Numeric(18, 4), nullable=True)
    total_grand = db.Column(db.Numeric(18, 4), nullable=True)
    
    points_earned = db.Column(db.Numeric(18, 2), nullable=True)
    points_redeemed = db.Column(db.Numeric(18, 2), nullable=True)
    
    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    def __repr__(self):
        return f"<DwInvoiceHeader {self.invoice_no_365}>"


class DwInvoiceLine(db.Model):
    """Invoice lines fact table - one row per line item"""
    __tablename__ = "dw_invoice_line"
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    invoice_no_365 = db.Column(db.String(64), db.ForeignKey('dw_invoice_header.invoice_no_365'), nullable=False, index=True)
    line_number = db.Column(db.Integer, nullable=False, index=True)
    
    __table_args__ = (
        db.UniqueConstraint('invoice_no_365', 'line_number', name='unique_invoice_line'),
    )
    
    item_code_365 = db.Column(db.String(64), nullable=True, index=True)
    quantity = db.Column(db.Numeric(18, 4), nullable=True)
    
    price_excl = db.Column(db.Numeric(18, 4), nullable=True)
    price_incl = db.Column(db.Numeric(18, 4), nullable=True)
    discount_percent = db.Column(db.Numeric(18, 4), nullable=True)
    
    vat_code_365 = db.Column(db.String(20), nullable=True)
    vat_percent = db.Column(db.Numeric(6, 4), nullable=True)
    
    line_total_excl = db.Column(db.Numeric(18, 4), nullable=True)
    line_total_discount = db.Column(db.Numeric(18, 4), nullable=True)
    line_total_vat = db.Column(db.Numeric(18, 4), nullable=True)
    line_total_incl = db.Column(db.Numeric(18, 4), nullable=True)
    line_net_value = db.Column(db.Numeric(18, 4), nullable=True)
    
    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    def __repr__(self):
        return f"<DwInvoiceLine {self.invoice_no_365}:{self.line_number}>"


class DwStore(db.Model):
    """Store dimension"""
    __tablename__ = "dw_store"
    
    store_code_365 = db.Column(db.String(64), primary_key=True)
    store_name = db.Column(db.String(255), nullable=True)
    
    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    def __repr__(self):
        return f"<DwStore {self.store_code_365}>"


class DwCashier(db.Model):
    """Cashier/User dimension"""
    __tablename__ = "dw_cashier"
    
    user_code_365 = db.Column(db.String(64), primary_key=True)
    user_name = db.Column(db.String(255), nullable=True)
    
    attr_hash = db.Column(db.String(32), nullable=False)
    last_sync_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    def __repr__(self):
        return f"<DwCashier {self.user_code_365}>"


# ============================================================================
# WMS OPERATIONAL INTELLIGENCE MODELS
# ============================================================================

class WmsCategoryDefault(db.Model):
    """Category-level default classification values for Operational Intelligence"""
    __tablename__ = "wms_category_defaults"
    
    category_code_365 = db.Column(db.String(64), primary_key=True)
    
    default_zone = db.Column(db.String(50), nullable=True)
    default_fragility = db.Column(db.String(20), nullable=True)
    default_stackability = db.Column(db.String(20), nullable=True)
    default_temperature_sensitivity = db.Column(db.String(30), nullable=True)
    default_pressure_sensitivity = db.Column(db.String(20), nullable=True)
    default_shape_type = db.Column(db.String(30), nullable=True)
    default_spill_risk = db.Column(db.Boolean, nullable=True)
    default_pick_difficulty = db.Column(db.Integer, nullable=True)
    default_shelf_height = db.Column(db.String(20), nullable=True)
    default_box_fit_rule = db.Column(db.String(30), nullable=True)
    default_pack_mode = db.Column(db.String(30), nullable=True)  # DIRECT_PALLET, CARTON_HEAVY, CARTON_SMALL, OFF_PALLET
    
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    notes = db.Column(db.Text, nullable=True)
    updated_by = db.Column(db.String(100), nullable=True)
    updated_at = db.Column(UTCDateTime(), nullable=True, default=get_utc_now)
    
    def __repr__(self):
        return f"<WmsCategoryDefault {self.category_code_365}>"


class WmsItemOverride(db.Model):
    """SKU-level override values for Operational Intelligence classification"""
    __tablename__ = "wms_item_overrides"
    
    item_code_365 = db.Column(db.String(64), primary_key=True)
    
    zone_override = db.Column(db.String(50), nullable=True)
    unit_type_override = db.Column(db.String(50), nullable=True)
    fragility_override = db.Column(db.String(20), nullable=True)
    stackability_override = db.Column(db.String(20), nullable=True)
    temperature_sensitivity_override = db.Column(db.String(30), nullable=True)
    pressure_sensitivity_override = db.Column(db.String(20), nullable=True)
    shape_type_override = db.Column(db.String(30), nullable=True)
    spill_risk_override = db.Column(db.Boolean, nullable=True)
    pick_difficulty_override = db.Column(db.Integer, nullable=True)
    shelf_height_override = db.Column(db.String(20), nullable=True)
    box_fit_rule_override = db.Column(db.String(30), nullable=True)
    pack_mode_override = db.Column(db.String(30), nullable=True)  # DIRECT_PALLET, CARTON_HEAVY, CARTON_SMALL, OFF_PALLET
    
    override_reason = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    updated_by = db.Column(db.String(100), nullable=True)
    updated_at = db.Column(UTCDateTime(), nullable=True, default=get_utc_now)
    
    def __repr__(self):
        return f"<WmsItemOverride {self.item_code_365}>"


class WmsDynamicRule(db.Model):
    """Dynamic rule for rule-based classification in Operational Intelligence"""
    __tablename__ = "wms_dynamic_rules"
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    
    # Primary action (kept for backward compat and display)
    target_attr = db.Column(db.String(64), nullable=False)      # e.g. 'pressure_sensitivity'
    action_value = db.Column(db.String(100), nullable=False)    # stored as string; cast on apply
    
    # Multiple actions stored as JSON: [{"attr": "fragility", "value": "YES"}, ...]
    actions_json = db.Column(db.Text, nullable=True)
    
    confidence = db.Column(db.Integer, nullable=False, default=65)
    priority = db.Column(db.Integer, nullable=False, default=100)
    
    stop_processing = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    
    condition_json = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    updated_by = db.Column(db.String(100), nullable=True)
    updated_at = db.Column(UTCDateTime(), nullable=True, default=get_utc_now)
    
    def __repr__(self):
        return f"<WmsDynamicRule {self.id} {self.target_attr} prio={self.priority}>"
    
    def get_actions(self):
        """Return all actions as a list of dicts: [{"attr": ..., "value": ...}, ...]"""
        import json
        if self.actions_json:
            try:
                return json.loads(self.actions_json)
            except:
                pass
        # Fallback to single action for backward compat
        return [{"attr": self.target_attr, "value": self.action_value}]
    
    @property
    def actions_summary(self):
        """Return a summary of all actions for display."""
        actions = self.get_actions()
        if len(actions) == 1:
            return f"{actions[0]['attr']}={actions[0]['value']}"
        return ", ".join(f"{a['attr']}={a['value']}" for a in actions)
    
    def get_conditions(self):
        """Parse and return the conditions list from condition_json."""
        import json
        try:
            data = json.loads(self.condition_json)
            conditions = data.get('all', [])
            result = []
            for c in conditions:
                obj = type('Condition', (), {})()
                obj.field = c.get('field', '')
                obj.op = c.get('op', '')
                obj.value = c.get('value', '')
                result.append(obj)
            return result
        except:
            return []


class WmsClassificationRun(db.Model):
    """Log of classification runs for audit purposes"""
    __tablename__ = "wms_classification_runs"
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    started_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    finished_at = db.Column(UTCDateTime(), nullable=True)
    run_by = db.Column(db.String(100), nullable=True)
    mode = db.Column(db.String(30), nullable=True, default='moderate_60')
    
    active_items_scanned = db.Column(db.Integer, nullable=True)
    items_updated = db.Column(db.Integer, nullable=True)
    items_needing_review = db.Column(db.Integer, nullable=True)
    
    notes = db.Column(db.Text, nullable=True)
    
    def __repr__(self):
        return f"<WmsClassificationRun {self.id} by {self.run_by}>"


# ============================================================================
# OI TIME ESTIMATOR AUDIT MODELS
# ============================================================================

class OiEstimateRun(db.Model):
    """Audit log for OI time estimate runs - one row per invoice estimate"""
    __tablename__ = "oi_estimate_runs"
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    invoice_no = db.Column(db.String(50), db.ForeignKey('invoices.invoice_no'), nullable=False, index=True)
    estimator_version = db.Column(db.String(50), nullable=False)
    params_revision = db.Column(db.Integer, nullable=False)
    params_snapshot_json = db.Column(db.Text, nullable=True)
    estimated_total_seconds = db.Column(db.Float, nullable=True)
    estimated_pick_seconds = db.Column(db.Float, nullable=True)
    estimated_travel_seconds = db.Column(db.Float, nullable=True)
    breakdown_json = db.Column(db.Text, nullable=True)
    reason = db.Column(db.String(100), nullable=True)
    created_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    lines = db.relationship('OiEstimateLine', backref='run', lazy='dynamic', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f"<OiEstimateRun {self.id} invoice={self.invoice_no}>"


class OiEstimateLine(db.Model):
    """Audit log for OI time estimate per line - one row per invoice line per run"""
    __tablename__ = "oi_estimate_lines"
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(db.Integer, db.ForeignKey('oi_estimate_runs.id', ondelete='CASCADE'), nullable=False, index=True)
    invoice_no = db.Column(db.String(50), nullable=False, index=True)
    invoice_item_id = db.Column(db.Integer, nullable=True)
    item_code = db.Column(db.String(100), nullable=True, index=True)
    location = db.Column(db.String(100), nullable=True)
    unit_type_normalized = db.Column(db.String(50), nullable=True)
    qty = db.Column(db.Float, nullable=True)
    estimated_pick_seconds = db.Column(db.Float, nullable=True)
    estimated_walk_seconds = db.Column(db.Float, nullable=True)
    estimated_total_seconds = db.Column(db.Float, nullable=True)
    breakdown_json = db.Column(db.Text, nullable=True)
    
    def __repr__(self):
        return f"<OiEstimateLine {self.id} item={self.item_code}>"


# ============================================================================
# WMS PALLET MANAGEMENT MODELS
# ============================================================================

class WmsPackingProfile(db.Model):
    """Per-SKU packing profile derived from OI classification"""
    __tablename__ = "wms_packing_profile"
    
    item_code_365 = db.Column(db.String(50), primary_key=True)
    
    pallet_role = db.Column(db.String(20), nullable=False, default="MIDDLE")
    flags_json = db.Column(db.Text, nullable=True)
    
    unit_type = db.Column(db.String(20))
    fragility = db.Column(db.String(10))
    pressure_sensitivity = db.Column(db.String(10))
    stackability = db.Column(db.String(10))
    temperature_sensitivity = db.Column(db.String(20))
    spill_risk = db.Column(db.Boolean)
    box_fit_rule = db.Column(db.String(20))
    
    pack_mode = db.Column(db.String(20))
    loss_risk = db.Column(db.Boolean)
    carton_type_hint = db.Column(db.String(10))
    max_carton_weight_kg = db.Column(db.Numeric(10, 2))
    
    updated_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    def __repr__(self):
        return f"<WmsPackingProfile {self.item_code_365} role={self.pallet_role} pack={self.pack_mode}>"


class WmsPallet(db.Model, SoftDeleteMixin):
    """Pallet for a route/shipment"""
    __tablename__ = "wms_pallet"
    
    pallet_id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False)
    
    label = db.Column(db.String(50), nullable=False)
    lane_code = db.Column(db.String(10), nullable=True)
    lane_slot = db.Column(db.Integer, nullable=True)
    
    status = db.Column(db.String(20), nullable=False, default="OPEN")
    
    max_weight_kg = db.Column(db.Numeric(10, 2), nullable=False, default=500)
    max_height_m = db.Column(db.Numeric(10, 2), nullable=False, default=1.80)
    
    used_mask = db.Column(db.Integer, nullable=False, default=0)
    used_weight_kg = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    
    created_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    updated_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    shipment = db.relationship("Shipment", backref="wms_pallets")
    
    def __repr__(self):
        return f"<WmsPallet {self.pallet_id} {self.label} status={self.status}>"


class WmsPalletOrder(db.Model):
    """Assignment of an invoice to a pallet with block allocation"""
    __tablename__ = "wms_pallet_order"
    
    id = db.Column(db.Integer, primary_key=True)
    pallet_id = db.Column(db.Integer, db.ForeignKey("wms_pallet.pallet_id", ondelete="CASCADE"), nullable=False)
    
    invoice_no = db.Column(db.String(50), nullable=False)
    blocks_requested = db.Column(db.Integer, nullable=False)
    blocks_mask = db.Column(db.Integer, nullable=False)
    
    est_weight_kg = db.Column(db.Numeric(10, 2), nullable=True)
    stop_seq_no = db.Column(db.Numeric(10, 2), nullable=True)
    
    created_at = db.Column(UTCDateTime(), nullable=False, default=get_utc_now)
    
    __table_args__ = (
        db.UniqueConstraint("invoice_no", name="uq_pallet_order_invoice_no"),
        db.Index("ix_pallet_order_pallet_id", "pallet_id"),
    )
    
    pallet = db.relationship("WmsPallet", backref="orders")
    
    def __repr__(self):
        return f"<WmsPalletOrder {self.id} invoice={self.invoice_no} pallet={self.pallet_id}>"


class Ps365ReservedStock777(db.Model):
    """Reserved stock report for Store 777 - synced from PS365"""
    __tablename__ = "ps365_reserved_stock_777"

    item_code_365 = db.Column(db.String(64), primary_key=True)
    item_name = db.Column(db.String(255), nullable=False)

    season_name = db.Column(db.String(128), nullable=True)
    supplier_item_code = db.Column(db.String(255), nullable=True)  # text_field_2_value from PS365
    barcode = db.Column(db.String(100), nullable=True)  # Item barcode from PS365

    number_of_pieces = db.Column(db.Integer, nullable=True)
    number_field_5_value = db.Column(db.Integer, nullable=True)

    store_code_365 = db.Column(db.String(16), nullable=False, default="777")

    stock = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    stock_reserved = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    stock_ordered = db.Column(db.Numeric(18, 4), nullable=False, default=0)

    available_stock = db.Column(db.Numeric(18, 4), nullable=False, default=0)

    synced_at = db.Column(db.DateTime, nullable=False, default=utc_now, index=True)

    def __repr__(self):
        return f"<Ps365ReservedStock777 {self.item_code_365}>"


class BankTransaction(db.Model):
    __tablename__ = 'bank_transactions'
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(36), nullable=False, index=True)
    txn_date = db.Column(db.Date, nullable=True)
    description = db.Column(db.Text, nullable=True)
    reference = db.Column(db.String(200), nullable=True)
    credit = db.Column(db.Numeric(12, 2), nullable=True)
    debit = db.Column(db.Numeric(12, 2), nullable=True)
    balance = db.Column(db.Numeric(14, 2), nullable=True)
    raw_row = db.Column(db.Text, nullable=True)
    matched_allocation_id = db.Column(db.Integer, db.ForeignKey('cod_invoice_allocations.id'), nullable=True)
    match_status = db.Column(db.String(20), nullable=False, default='UNMATCHED')
    match_confidence = db.Column(db.String(20), nullable=True)
    match_reason = db.Column(db.String(200), nullable=True)
    dismissed = db.Column(db.Boolean, nullable=False, default=False)
    uploaded_by = db.Column(db.String(64), nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    allocation = db.relationship('CODInvoiceAllocation', backref='bank_matches', foreign_keys=[matched_allocation_id])


class CustomerBalanceCache(db.Model):
    __tablename__ = "customer_balance_cache"

    customer_code_365 = db.Column(db.String(50), primary_key=True)
    as_of_date = db.Column(db.Date, nullable=False)
    balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    drcr = db.Column(db.String(2), nullable=False, default="DR")
    signed_balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    ps_last_line_balance = db.Column(db.Numeric(12, 2), nullable=True)
    ps_last_balance_drcr = db.Column(db.String(2), nullable=True)
    fetched_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    @staticmethod
    def is_fresh(row, max_minutes: int):
        if not row:
            return False
        return row.fetched_at >= (datetime.utcnow() - timedelta(minutes=max_minutes))
