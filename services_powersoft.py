import os
import logging
from typing import Dict, Any, Optional
from models import Invoice, InvoiceItem, Setting, DwItem, DwAttribute1, DwAttribute3, db
from ps365_client import call_ps365
from datetime import datetime, timedelta

def _norm_code(v):
    """Normalize item code - strip whitespace, return None if empty"""
    if v is None:
        return None
    s = str(v).strip().upper()
    return s if s else None

def _norm_barcode(v):
    """Normalize barcode - strip whitespace, return None if empty"""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None

def _parse_qty_int(v):
    """Safely parse quantity as int"""
    if v is None or v == "":
        return 0
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0

def recalculate_invoice_totals(invoice_no: str, commit: bool = True) -> bool:
    """
    Recalculate and update invoice totals from its items.
    """
    try:
        invoice_record = Invoice.query.filter_by(invoice_no=invoice_no).first()
        if not invoice_record:
            logging.warning(f"Invoice {invoice_no} not found")
            return False
        
        invoice_items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
        
        # Calculate totals
        total_lines_count = len(invoice_items)
        total_items_count = sum(item.qty or 0 for item in invoice_items)
        total_weight_sum = sum((item.item_weight or 0) * (item.qty or 0) for item in invoice_items)
        total_exp_time_sum = sum(item.exp_time or 0 for item in invoice_items)
        
        # Update invoice record
        invoice_record.total_lines = total_lines_count
        invoice_record.total_items = total_items_count
        invoice_record.total_weight = total_weight_sum
        invoice_record.total_exp_time = total_exp_time_sum
        
        if commit:
            db.session.commit()
        
        logging.info(f"Recalculated totals for invoice {invoice_no}: lines={total_lines_count}, items={total_items_count}, weight={total_weight_sum}kg")
        return True
    
    except Exception as e:
        logging.error(f"Error recalculating totals for {invoice_no}: {str(e)}")
        if commit:
            db.session.rollback()
        return False

def _format_location_code(raw_loc):
    """Format shelf location code for display: 1006A01 -> 10-06-A 01"""
    if not raw_loc:
        return ""
    
    # Standardize format: remove leading/trailing spaces and ensure uppercase
    clean_loc = str(raw_loc).strip().upper().replace("-", "")
    
    # Format: 10-06-A 01 (Corridor-Aisle-Level Bin)
    # Expected pattern: 2 digits (corridor) + 2 digits (aisle) + 1 char (level) + 2 digits (bin)
    if len(clean_loc) == 7:
        return f"{clean_loc[0:2]}-{clean_loc[2:4]}-{clean_loc[4]} {clean_loc[5:7]}"
    
    return clean_loc

def sync_invoices_from_ps365(invoice_no_365: str = None, import_date: str = None) -> Dict[str, Any]:
    """
    Sync invoices from PS365 API using list_loyalty_invoices endpoint.
    """
    token_value = os.getenv('PS365_TOKEN', '')
    base_url_value = os.getenv('PS365_BASE_URL', '')
    
    if not token_value:
        return {"success": False, "error": "PS365_TOKEN is not configured."}
    if not base_url_value:
        return {"success": False, "error": "PS365_BASE_URL is not configured."}
    
    if (not invoice_no_365 or not invoice_no_365.strip()) and not import_date:
        return {"success": False, "error": "Invoice number or date is required."}
    
    invoice_no_365 = invoice_no_365.strip() if invoice_no_365 else ""
    
    # Prefetch attribute lookup tables once (avoid repeated queries)
    attr1_map = {a.attribute_1_code_365: a.attribute_1_name for a in DwAttribute1.query.all()}
    attr3_map = {a.attribute_3_code_365: a.attribute_3_name for a in DwAttribute3.query.all()}
    
    try:
        page = 1
        total_invoices_created = 0
        total_invoices_updated = 0
        total_invoices_processed = 0
        total_items_created = 0
        total_items_updated = 0
        total_items_processed = 0
        import_errors = 0
        invoices_found_in_api = 0
        
        if import_date:
            from_date = import_date
            to_date = import_date
        else:
            from_date = (datetime.utcnow() - timedelta(days=730)).strftime("%Y-%m-%d")
            to_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        while True:
            invoice_filter = invoice_no_365 if invoice_no_365 else ""
            
            payload = {
                "filter_define": {
                    "only_counted": "N",
                    "page_number": page,
                    "page_size": 100,
                    "invoice_type": "all",
                    "invoice_number_selection": invoice_filter,
                    "invoice_customer_code_selection": "",
                    "invoice_customer_name_selection": "",
                    "invoice_customer_email_selection": "",
                    "invoice_customer_phone_selection": "",
                    "from_date": from_date,
                    "to_date": to_date,
                    "session_date_from_utc0": "",
                    "session_date_to_utc0": "",
                }
            }
            
            try:
                response = call_ps365("list_loyalty_invoices", payload)
            except Exception as api_error:
                logging.error(f"PS365 API call failed: {str(api_error)}")
                return {"success": False, "error": f"PS365 API error: {str(api_error)}"}
            
            api_resp = response.get("api_response", {})
            if api_resp.get("response_code") != "1":
                logging.error(f"PS365 API error code: {api_resp.get('response_code')}")
                break
            
            invoices = response.get("list_invoices", []) or []
            logging.info(f"Page {page} sync_invoices_from_ps365: found {len(invoices)} invoices")
            if not invoices:
                break
            
            for inv in invoices:
                try:
                    # Parse nested invoice structure
                    inv_obj = inv.get("invoice", inv)
                    header = inv_obj.get("invoice_header", {})
                    lines = inv_obj.get("list_invoice_details", []) or []
                    
                    invoice_no_ps365 = header.get("invoice_no_365") or header.get("invoice_no") or header.get("document_no")
                    if not invoice_no_ps365:
                        continue
                        
                    if invoice_no_ps365.strip().upper().startswith('CR'):
                        continue

                    invoices_found_in_api += 1
                    
                    raw_date = header.get("invoice_date_utc0") or header.get("invoice_date_local") or header.get("invoice_date")
                    formatted_upload_date = datetime.now().strftime('%Y-%m-%d')
                    if raw_date:
                        try:
                            invoice_date_dt = datetime.strptime(raw_date.split(' ')[0], '%Y-%m-%d')
                            formatted_upload_date = invoice_date_dt.strftime('%Y-%m-%d')
                        except:
                            pass

                    customer_name = header.get("customer_name") or inv.get("customer_name")
                    total_grand_raw = header.get("total_grand") or inv.get("total_grand")
                    total_grand_value = None
                    try:
                        total_grand_value = float(total_grand_raw) if total_grand_raw is not None else None
                    except:
                        pass
                    
                    # 1. UPSERT INVOICE
                    invoice_record = Invoice.query.filter_by(invoice_no=invoice_no_ps365).first()
                    if not invoice_record:
                        invoice_record = Invoice(
                            invoice_no=invoice_no_ps365,
                            customer_name=customer_name or "",
                            upload_date=formatted_upload_date,
                            status='not_started',
                            total_grand=total_grand_value
                        )
                        db.session.add(invoice_record)
                        total_invoices_created += 1
                    else:
                        total_invoices_updated += 1
                        if total_grand_value is not None:
                            invoice_record.total_grand = total_grand_value
                    total_invoices_processed += 1
                    
                    db.session.flush()

                    # 2. AGGREGATE LINES BY ITEM_CODE (handles duplicates)
                    from collections import defaultdict
                    qty_by_code = defaultdict(int)
                    repr_line_by_code = {}
                    
                    for line in lines:
                        code = _norm_code(line.get("item_code_365") or line.get("item_code") or line.get("product_code"))
                        if not code:
                            continue
                        qty = _parse_qty_int(line.get("line_quantity") or line.get("qty") or line.get("quantity"))
                        qty_by_code[code] += qty
                        repr_line_by_code.setdefault(code, line)
                    
                    all_item_codes = list(qty_by_code.keys())
                    
                    if not all_item_codes:
                        db.session.commit()
                        continue

                    # Prefetch barcodes from DW
                    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(all_item_codes)).all()
                    dw_map = {d.item_code_365: d for d in dw_items}
                    
                    # Prefetch ALL existing items for this invoice (not just incoming codes)
                    all_existing_items = InvoiceItem.query.filter(
                        InvoiceItem.invoice_no == invoice_no_ps365
                    ).all()
                    existing_map = {it.item_code: it for it in all_existing_items}
                    existing_codes = set(existing_map.keys())
                    incoming_codes = set(all_item_codes)

                    # BATCH fetch shelf locations for ALL items in this invoice
                    shelf_map = {}
                    try:
                        from shelves_service import fetch_item_shelves
                        store_code = header.get("store_code_365")
                        if store_code:
                            shelves_result = fetch_item_shelves(str(store_code), all_item_codes)
                            for ic, shelves_list in (shelves_result or {}).items():
                                nic = _norm_code(ic)
                                if not nic or not shelves_list:
                                    continue
                                raw_loc = shelves_list[0].get("shelf_code_365") or shelves_list[0].get("shelf_name")
                                if raw_loc:
                                    shelf_map[nic] = _format_location_code(raw_loc)
                    except Exception as e:
                        logging.warning(f"Failed to batch fetch shelf locations: {e}")
                    
                    logging.info(f"Invoice {invoice_no_ps365}: shelf_map={len(shelf_map)}/{len(all_item_codes)} items")

                    # 3. PROCESS AGGREGATED ITEMS
                    for item_code in all_item_codes:
                        total_items_processed += 1
                        qty_int = qty_by_code[item_code]

                        dw = dw_map.get(item_code)
                        barcode = dw.barcode if dw else None
                        
                        if not barcode:
                            try:
                                from ps365_util import find_barcode_for_item_ps365
                                barcode = _norm_barcode(find_barcode_for_item_ps365(item_code, timeout=10))
                                if dw and barcode:
                                    dw.barcode = barcode
                            except:
                                pass

                        shelf_location = shelf_map.get(item_code)

                        item_name = dw.item_name if dw else None
                        item_weight = float(dw.item_weight) if (dw and dw.item_weight) else 0.0
                        unit_type = attr1_map.get(dw.attribute_1_code_365) if (dw and dw.attribute_1_code_365) else None
                        zone = attr3_map.get(dw.attribute_3_code_365) if (dw and dw.attribute_3_code_365) else None
                        number_of_pieces = int(dw.number_of_pieces) if (dw and dw.number_of_pieces) else None
                        selling_qty = int(dw.selling_qty) if (dw and dw.selling_qty) else None

                        expected_pieces = qty_int
                        if dw and dw.attribute_1_code_365 == "VPACK" and number_of_pieces:
                            expected_pieces = qty_int * number_of_pieces

                        exp_time_minutes = (15 + 16 * qty_int) / 60
                        try:
                            oi_params = Setting.get_json(db.session, "oi_time_params_v1", {})
                            pick_config = oi_params.get("pick", {})
                            base_pick_seconds = pick_config.get("base_by_unit_type", {}).get(unit_type.lower() if unit_type else "item", 3)
                            per_qty_seconds = pick_config.get("per_qty_by_unit_type", {}).get(unit_type.lower() if unit_type else "item", 1.6)
                            travel_config = oi_params.get("travel", {})
                            align_seconds = travel_config.get("sec_align_per_stop", 2)
                            total_seconds = align_seconds + base_pick_seconds + (per_qty_seconds * qty_int)
                            exp_time_minutes = total_seconds / 60
                        except:
                            pass

                        existing_item = existing_map.get(item_code)
                        if existing_item:
                            total_items_updated += 1
                            
                            # Check if qty reduced below picked_qty
                            if existing_item.picked_qty and qty_int is not None and existing_item.picked_qty > qty_int:
                                logging.warning(
                                    f"Invoice {invoice_no_ps365} item {item_code}: picked_qty {existing_item.picked_qty} > qty {qty_int}"
                                )
                            
                            # Check if item is locked by a batch
                            is_locked = existing_item.locked_by_batch_id is not None
                            
                            # Update demand/master data fields (safe to update)
                            existing_item.qty = qty_int
                            existing_item.item_name = item_name or existing_item.item_name
                            existing_item.item_weight = item_weight
                            if barcode: 
                                existing_item.barcode = barcode
                            existing_item.pack = str(selling_qty) if selling_qty else existing_item.pack
                            existing_item.line_weight = item_weight * qty_int
                            existing_item.exp_time = exp_time_minutes
                            existing_item.pieces_per_unit_snapshot = number_of_pieces
                            existing_item.expected_pick_pieces = expected_pieces
                            
                            # Only update location/zone/unit_type if NOT locked
                            if not is_locked:
                                if shelf_location:
                                    existing_item.location = shelf_location
                                if zone:
                                    existing_item.zone = zone
                                if unit_type:
                                    existing_item.unit_type = unit_type
                            # Preserve: picked_qty, is_picked, pick_status, locked_by_batch_id,
                            # reset_by, reset_timestamp, reset_note, skip_reason, skip_timestamp, skip_count, corridor
                        else:
                            new_item = InvoiceItem(
                                invoice_no=invoice_no_ps365,
                                item_code=item_code,
                                qty=qty_int,
                                item_name=item_name,
                                item_weight=item_weight,
                                zone=zone,
                                unit_type=unit_type,
                                barcode=barcode,
                                location=shelf_location,
                                pack=str(selling_qty) if selling_qty else None,
                                line_weight=item_weight * qty_int,
                                exp_time=exp_time_minutes,
                                pieces_per_unit_snapshot=number_of_pieces,
                                expected_pick_pieces=expected_pieces,
                                pick_status="not_picked",
                                is_picked=False,
                                picked_qty=0
                            )
                            db.session.add(new_item)
                            total_items_created += 1
                    
                    # 4. HANDLE ITEMS REMOVED FROM API
                    removed_codes = existing_codes - incoming_codes
                    for code in removed_codes:
                        it = existing_map[code]
                        if (it.picked_qty or 0) > 0 or it.locked_by_batch_id is not None:
                            logging.warning(f"Not deleting picked/locked item removed by API: {invoice_no_ps365} {code}")
                            continue
                        db.session.delete(it)

                    # 5. FINAL RECALC AND COMMIT
                    recalculate_invoice_totals(invoice_no_ps365, commit=False)
                    db.session.commit()
                    
                except Exception as inv_err:
                    db.session.rollback()
                    # Ensure counters are still updated even on partial failure
                    total_invoices_processed += 1
                    logging.error(f"Error processing invoice {inv.get('invoice_no_365')}: {inv_err}")
                    import_errors += 1

            if len(invoices) < 100:
                break
            page += 1

        logging.info(
            f"PS365 invoice sync finished: "
            f"invoices_processed={total_invoices_processed}, "
            f"invoices_created={total_invoices_created}, "
            f"invoices_updated={total_invoices_updated}, "
            f"items_processed={total_items_processed}, "
            f"items_created={total_items_created}, "
            f"items_updated={total_items_updated}, "
            f"errors={import_errors}"
        )

        return {
            "success": True,
            "invoices_processed": total_invoices_processed,
            "invoices_created": total_invoices_created,
            "invoices_updated": total_invoices_updated,
            "items_processed": total_items_processed,
            "items_created": total_items_created,
            "items_updated": total_items_updated,
            "invoices_found": invoices_found_in_api,
            "errors": import_errors
        }
    except Exception as e:
        logging.error(f"Sync failed: {e}")
        db.session.rollback()
        return {"success": False, "error": str(e)}

def sync_active_customers():
    """Sync active customers from PS365 API with pagination"""
    from models import PSCustomer, db
    from ps365_client import call_ps365
    import logging
    
    try:
        logging.info("Starting customer sync from PS365...")
        page = 1
        PAGE_SIZE = 100  # PS365 API max page size is 100
        total_synced = 0
        
        while True:
            payload = {
                "filter_define": {
                    "only_counted": "N",
                    "active_type": "active",
                    "page_number": page,
                    "page_size": PAGE_SIZE
                }
            }
            
            response = call_ps365("list_customers", payload, method="POST")
            api_resp = response.get("api_response", {})
            
            if api_resp.get("response_code") != "1":
                logging.error(f"Customer sync failed: {api_resp}")
                return False
                
            customers = response.get("list_customers", [])
            if not customers:
                break
                
            logging.info(f"Page {page}: Found {len(customers)} customers to sync")
            
            for cust_data in customers:
                customer = cust_data if not cust_data.get("customer") else cust_data.get("customer", {})
                code = customer.get("customer_code_365")
                if not code:
                    continue
                    
                existing = PSCustomer.query.filter_by(customer_code_365=code).first()
                if not existing:
                    existing = PSCustomer(customer_code_365=code)
                    db.session.add(existing)
                
                existing.customer_name = customer.get("company_name") or f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
                existing.customer_email = customer.get("email")
                existing.customer_phone = customer.get("mobile") or customer.get("tel_1")
                existing.last_synced_at = datetime.utcnow()
                total_synced += 1
            
            db.session.commit()
            
            if len(customers) < PAGE_SIZE:
                break
            page += 1
            
        logging.info(f"Customer sync completed successfully. Total synced: {total_synced}")
        return True
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error syncing customers: {e}")
        return False

def upsert_single_customer(customer_code):
    """Sync a single customer by code"""
    from models import PSCustomer, db
    from ps365_client import call_ps365
    import logging
    
    try:
        payload = {
            "filter_define": {
                "only_counted": "N",
                "customer_code_365_selection": customer_code,
                "page_number": 1,
                "page_size": 1
            }
        }
        
        response = call_ps365("list_customers", payload, method="POST")
        customers = response.get("list_customers", [])
        
        if not customers:
            return None
        
        cust_data = customers[0]
        customer_data = cust_data if not cust_data.get("customer") else cust_data.get("customer", {})
        code = customer_data.get("customer_code_365")
        
        existing = PSCustomer.query.filter_by(customer_code_365=code).first()
        if not existing:
            existing = PSCustomer(customer_code_365=code)
            db.session.add(existing)
            
        existing.customer_name = customer_data.get("customer_name")
        existing.customer_email = customer_data.get("customer_email")
        existing.customer_phone = customer_data.get("customer_phone")
        existing.last_synced_at = datetime.utcnow()
        
        db.session.commit()
        return existing
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error upserting customer {customer_code}: {e}")
        return None

def get_customer_by_code(customer_code):
    """Retrieve customer by code, syncing from PS365 if not found locally"""
    from models import PSCustomer
    
    customer = PSCustomer.query.filter_by(customer_code_365=customer_code).first()
    if not customer:
        customer = upsert_single_customer(customer_code)
        
    return customer
