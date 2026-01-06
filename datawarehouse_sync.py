import json
import logging
import os
from datetime import datetime, timedelta, timezone
import time
from logging.handlers import RotatingFileHandler
from sqlalchemy.orm import Session
from requests.exceptions import HTTPError
import pytz

from timezone_utils import utc_now_for_db
from models import (
    DwItem, DwItemCategory, DwBrand, DwSeason, DwAttribute1, DwAttribute2, 
    DwAttribute3, DwAttribute4, DwAttribute5, DwAttribute6, SyncState,
    DwInvoiceHeader, DwInvoiceLine, DwStore, DwCashier
)
from ps365_client import call_ps365

logger = logging.getLogger(__name__)


class TZFormatter(logging.Formatter):
    """Custom formatter that converts times to Africa/Cairo timezone"""
    converter = None
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tz = pytz.timezone('Africa/Cairo')
    
    def formatTime(self, record, datefmt=None):
        """Convert log time to Africa/Cairo timezone"""
        dt = datetime.fromtimestamp(record.created, tz=pytz.UTC)
        dt_local = dt.astimezone(self.tz)
        if datefmt:
            return dt_local.strftime(datefmt)
        return dt_local.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]


def _cleanup_old_logs(logs_dir: str, days: int = 7):
    """Delete log files older than specified days"""
    import glob
    try:
        cutoff_time = time.time() - (days * 24 * 60 * 60)  # Convert days to seconds
        for log_file in glob.glob(os.path.join(logs_dir, "*.log")):
            if os.path.getmtime(log_file) < cutoff_time:
                os.remove(log_file)
                logger.info(f"Deleted old log: {os.path.basename(log_file)}")
    except Exception as e:
        logger.warning(f"Error cleaning up old logs: {e}")


def _setup_file_logging(log_name: str):
    """Setup file-based logging for sync operations"""
    logs_dir = "logs"
    os.makedirs(logs_dir, exist_ok=True)
    
    # Clean up logs older than 7 days
    _cleanup_old_logs(logs_dir, days=7)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(logs_dir, f"{log_name}_{timestamp}.log")
    
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    formatter = TZFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return log_file
PAGE_SIZE = 100  # PS365 docs: maximum allowed is 100 rows


def _compute_hash(data: dict) -> str:
    """Stable, unicode-safe hash"""
    import hashlib
    return hashlib.md5(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _upsert_dimension(session: Session, model, key_field: str, data: dict):
    """
    Generic upsert for dimension tables with attr_hash + last_sync_at.
    'data' must already contain 'attr_hash'.
    """
    key_value = data[key_field]
    existing = session.get(model, key_value)

    if existing and existing.attr_hash == data["attr_hash"]:
        # nothing changed
        return

    now = utc_now_for_db()
    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
        existing.last_sync_at = now
    else:
        obj = model(**data, last_sync_at=now)
        session.add(obj)


# ----------------------
# TEST: Fetch single item for debugging
# ----------------------

def test_fetch_single_item(session: Session):
    """Debug function - fetch just ONE item to test if API and DB work"""
    logger.info("=== TEST: Fetching single item ===")
    try:
        response = call_ps365("list_items", {
            "filter_define": {
                "only_counted": "N",
                "page_number": 1,
                "page_size": 1,
                "active_type": "all",
                "ecommerce_type": "all",
                "categories_selection": "",
                "departments_selection": "",
                "items_supplier_selection": "",
                "brands_selection": "",
                "seasons_selection": "",
                "models_selection": "",
                "items_selection": "",
                "colours_selection": "",
                "sizes_selection": "",
                "sizes_group_selection": "",
                "attributes_1_selection": "",
                "attributes_2_selection": "",
                "attributes_3_selection": "",
                "attributes_4_selection": "",
                "attributes_5_selection": "",
                "attributes_6_selection": "",
                "keyword_search_item_code_365": "",
                "keyword_search_item_name": "",
                "text_field_value_1_selection": "",
                "text_field_value_2_selection": "",
                "text_field_value_3_selection": "",
                "text_field_value_4_selection": "",
                "text_field_value_5_selection": "",
                "number_field_value_1_selection": "",
                "number_field_value_2_selection": "",
                "number_field_value_3_selection": "",
                "number_field_value_4_selection": "",
                "number_field_value_5_selection": "",
                "date_field_value_1_selection": "",
                "date_field_value_2_selection": "",
                "date_field_value_3_selection": "",
                "date_field_value_4_selection": "",
                "date_field_value_5_selection": "",
                "boolean_field_value_1": "",
                "boolean_field_value_2": "",
                "boolean_field_value_3": "",
                "boolean_field_value_4": "",
                "boolean_field_value_5": "",
                "date_field_1_value_from": "",
                "date_field_1_value_to": "",
                "date_field_2_value_from": "",
                "date_field_2_value_to": "",
                "date_field_3_value_from": "",
                "date_field_3_value_to": "",
                "date_field_4_value_from": "",
                "date_field_4_value_to": "",
                "date_field_5_value_from": "",
                "date_field_5_value_to": "",
                "last_modified_from": "",
                "last_modified_to": "",
                "creation_date_from": "",
                "creattion_date_to": "",
                "display_fields": "item_code_365,item_name,active,category_code_365,brand_code_365,season_code_365,attribute_1_code_365,attribute_2_code_365,attribute_3_code_365,attribute_4_code_365,attribute_5_code_365,attribute_6_code_365,item_length,item_width,item_height,item_weight,number_of_pieces,number_field_1_value",
            },
        })
        
        logger.info(f"API Response keys: {response.keys()}")
        items = response.get("list_items", [])
        logger.info(f"Got {len(items)} items from API")
        
        if not items:
            logger.warning("No items returned from API!")
            return
        
        item = items[0]
        logger.info(f"Item data: {json.dumps(item, indent=2)[:500]}")
        
        code = item.get("item_code_365", "")
        logger.info(f"Processing item code: {code}")
        
        core = {
            "item_code_365": code,
            "item_name": item.get("item_name", ""),
            "active": bool(item.get("active", True)),
            "category_code_365": item.get("category_code_365"),
            "brand_code_365": item.get("brand_code_365"),
            "season_code_365": item.get("season_code_365"),
            "attribute_1_code_365": item.get("attribute_1_code_365"),
            "attribute_2_code_365": item.get("attribute_2_code_365"),
            "attribute_3_code_365": item.get("attribute_3_code_365"),
            "attribute_4_code_365": item.get("attribute_4_code_365"),
            "attribute_5_code_365": item.get("attribute_5_code_365"),
            "attribute_6_code_365": item.get("attribute_6_code_365"),
            "item_length": float(item.get("item_length", 0)) if item.get("item_length") else None,
            "item_width": float(item.get("item_width", 0)) if item.get("item_width") else None,
            "item_height": float(item.get("item_height", 0)) if item.get("item_height") else None,
            "item_weight": float(item.get("item_weight", 0)) if item.get("item_weight") else None,
            "number_of_pieces": int(item.get("number_of_pieces", 0)) if item.get("number_of_pieces") else None,
            "selling_qty": float(item.get("number_field_1_value", 0)) if item.get("number_field_1_value") else None,
        }
        
        attr_hash = _compute_hash(core)
        obj = DwItem(**core, attr_hash=attr_hash, last_sync_at=datetime.now())
        session.add(obj)
        session.commit()
        
        logger.info(f"✅ Successfully inserted item: {code}")
        
    except Exception as e:
        logger.error(f"Error in test_fetch_single_item: {str(e)}", exc_info=True)
        raise


# ----------------------
# FULL UPDATE
# ----------------------

def full_dw_update(session: Session):
    """Full refresh of items + all dimensions. Called from menu option."""
    import io
    import logging as py_logging
    
    # Set up output capture for status tracking
    log_capture = io.StringIO()
    handler = py_logging.StreamHandler(log_capture)
    handler.setLevel(py_logging.INFO)
    formatter = py_logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    sync_logger = py_logging.getLogger('datawarehouse_sync')
    sync_logger.addHandler(handler)
    
    def _update_status(msg):
        """Update sync status in database"""
        try:
            # Use a fresh query to avoid session conflicts
            from app import db as app_db, app
            with app.app_context():
                status_obj = app_db.session.get(SyncState, "full_sync_status")
                if status_obj:
                    status_obj.value = f"RUNNING|{msg}"
                output_obj = app_db.session.get(SyncState, "full_sync_output")
                if not output_obj:
                    output_obj = SyncState(key="full_sync_output", value="")
                    app_db.session.add(output_obj)
                output_obj.value = log_capture.getvalue()
                app_db.session.commit()
        except Exception as e:
            logger.error(f"Error updating sync status: {str(e)}", exc_info=True)

    # 1) Categories (optional - skip if not available)
    try:
        logger.info("Syncing item categories...")
        response = call_ps365("list_item_categories", {}, method="GET")
        items = response.get("list_item_categories", [])

        for c in items:
            payload = {
                "category_code_365": c.get("item_category_code_365"),
                "category_name": c.get("item_category_name", ""),
                "parent_category_code": c.get("item_category_parent_code_365"),
            }
            if payload["category_code_365"]:
                payload["attr_hash"] = _compute_hash(payload)
                _upsert_dimension(session, DwItemCategory, "category_code_365", payload)

        session.commit()
        logger.info(f"Item categories synced successfully. Total: {len(items)}")
    except HTTPError as e:
        if e.response.status_code == 404:
            logger.warning("list_item_categories endpoint not available (404)")
        else:
            raise

    # 2) Brands (optional - skip if not available)
    try:
        logger.info("Syncing brands...")
        response = call_ps365("list_brands", {}, method="GET")
        brands = response.get("list_brands", [])

        for b in brands:
            payload = {
                "brand_code_365": b.get("brand_code_365"),
                "brand_name": b.get("brand_name", ""),
            }
            if payload["brand_code_365"]:
                payload["attr_hash"] = _compute_hash(payload)
                _upsert_dimension(session, DwBrand, "brand_code_365", payload)

        session.commit()
        logger.info("Brands synced successfully")
    except HTTPError as e:
        if e.response.status_code == 404:
            logger.warning("list_brands endpoint not available (404)")
        else:
            raise

    # 3) Seasons (optional - skip if not available)
    try:
        logger.info("Syncing seasons...")
        response = call_ps365("list_seasons", {}, method="GET")
        seasons = response.get("list_seasons", [])

        for s in seasons:
            payload = {
                "season_code_365": s.get("season_code_365"),
                "season_name": s.get("season_name", ""),
            }
            if payload["season_code_365"]:
                payload["attr_hash"] = _compute_hash(payload)
                _upsert_dimension(session, DwSeason, "season_code_365", payload)

        session.commit()
        logger.info("Seasons synced successfully")
    except HTTPError as e:
        if e.response.status_code == 404:
            logger.warning("list_seasons endpoint not available (404)")
        else:
            raise

    # 4) Attributes 1-6 (optional - skip if not available)
    for attr_no in range(1, 7):
        try:
            logger.info(f"Syncing attribute {attr_no}...")
            # First, get the total count
            count_response = call_ps365("list_attributes", {
                "attributeNo": attr_no,
            }, method="GET")
            total_count = count_response.get("total_count_list_attributes", 0)
            logger.info(f"Total attributes for attribute {attr_no}: {total_count}")
            
            # Map attribute number to model and field names
            attr_models = {
                1: (DwAttribute1, "attribute_1_code_365", "attribute_1_name", "attribute_1_secondary_code"),
                2: (DwAttribute2, "attribute_2_code_365", "attribute_2_name", "attribute_2_secondary_code"),
                3: (DwAttribute3, "attribute_3_code_365", "attribute_3_name", "attribute_3_secondary_code"),
                4: (DwAttribute4, "attribute_4_code_365", "attribute_4_name", "attribute_4_secondary_code"),
                5: (DwAttribute5, "attribute_5_code_365", "attribute_5_name", "attribute_5_secondary_code"),
                6: (DwAttribute6, "attribute_6_code_365", "attribute_6_name", "attribute_6_secondary_code"),
            }
            model, code_field, name_field, secondary_field = attr_models[attr_no]
            
            # Then paginate through results
            page = 1
            while True:
                response = call_ps365("list_attributes", {
                    "attributeNo": attr_no,
                    "page_number": page,
                    "page_size": PAGE_SIZE,
                }, method="GET")
                attrs = response.get("list_attributes", [])
                if not attrs:
                    break

                for a in attrs:
                    payload = {
                        code_field: a.get("attribute_code_365"),
                        name_field: a.get("attribute_name", ""),
                        secondary_field: a.get("attribute_secondary_code"),
                    }
                    if payload[code_field]:
                        payload["attr_hash"] = _compute_hash(payload)
                        _upsert_dimension(session, model, code_field, payload)

                session.commit()
                page += 1
            logger.info(f"Attribute {attr_no} synced successfully")
        except HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"list_attributes endpoint not available for attribute {attr_no} (404)")
            else:
                raise

    # 5) Items – ALL items (required) - Uses proven working approach
    logger.info("Syncing items...")
    
    page = 1
    total_inserted = 0
    total_skipped = 0
    now = utc_now_for_db()
    
    while True:
        try:
            logger.info(f"Fetching page {page}...")
            
            response = call_ps365("list_items", {
                "filter_define": {
                    "only_counted": "N",
                    "page_number": page,
                    "page_size": PAGE_SIZE,
                    "active_type": "all",
                    "ecommerce_type": "all",
                    "categories_selection": "",
                    "departments_selection": "",
                    "items_supplier_selection": "",
                    "brands_selection": "",
                    "seasons_selection": "",
                    "models_selection": "",
                    "items_selection": "",
                    "colours_selection": "",
                    "sizes_selection": "",
                    "sizes_group_selection": "",
                    "attributes_1_selection": "",
                    "attributes_2_selection": "",
                    "attributes_3_selection": "",
                    "attributes_4_selection": "",
                    "attributes_5_selection": "",
                    "attributes_6_selection": "",
                    "keyword_search_item_code_365": "",
                    "keyword_search_item_name": "",
                    "text_field_value_1_selection": "",
                    "text_field_value_2_selection": "",
                    "text_field_value_3_selection": "",
                    "text_field_value_4_selection": "",
                    "text_field_value_5_selection": "",
                    "number_field_value_1_selection": "",
                    "number_field_value_2_selection": "",
                    "number_field_value_3_selection": "",
                    "number_field_value_4_selection": "",
                    "number_field_value_5_selection": "",
                    "date_field_value_1_selection": "",
                    "date_field_value_2_selection": "",
                    "date_field_value_3_selection": "",
                    "date_field_value_4_selection": "",
                    "date_field_value_5_selection": "",
                    "boolean_field_value_1": "",
                    "boolean_field_value_2": "",
                    "boolean_field_value_3": "",
                    "boolean_field_value_4": "",
                    "boolean_field_value_5": "",
                    "date_field_1_value_from": "",
                    "date_field_1_value_to": "",
                    "date_field_2_value_from": "",
                    "date_field_2_value_to": "",
                    "date_field_3_value_from": "",
                    "date_field_3_value_to": "",
                    "date_field_4_value_from": "",
                    "date_field_4_value_to": "",
                    "date_field_5_value_from": "",
                    "date_field_5_value_to": "",
                    "last_modified_from": "",
                    "last_modified_to": "",
                    "creation_date_from": "",
                    "creattion_date_to": "",
                    "display_fields": "item_code_365,item_name,active,category_code_365,brand_code_365,season_code_365,attribute_1_code_365,attribute_2_code_365,attribute_3_code_365,attribute_4_code_365,attribute_5_code_365,attribute_6_code_365,item_length,item_width,item_height,item_weight,number_of_pieces,number_field_1_value",
                },
            })
            
            api_response = response.get("api_response", {})
            if api_response.get("response_code") != "1":
                logger.error(f"API Error: {api_response.get('response_msg')}")
                break
            
            items = response.get("list_items", [])
            logger.info(f"Page {page}: Got {len(items)} items from API")
            
            if not items:
                logger.info("No more items - pagination complete")
                break
            
            page_inserted = 0
            page_skipped = 0
            
            for item in items:
                try:
                    code = item.get("item_code_365", "").upper()
                    if not code:
                        page_skipped += 1
                        total_skipped += 1
                        continue
                    
                    # Prepare item data
                    core = {
                        "item_code_365": code,
                        "item_name": item.get("item_name", ""),
                        "active": bool(item.get("active", True)),
                        "category_code_365": item.get("category_code_365"),
                        "brand_code_365": item.get("brand_code_365"),
                        "season_code_365": item.get("season_code_365"),
                        "attribute_1_code_365": item.get("attribute_1_code_365"),
                        "attribute_2_code_365": item.get("attribute_2_code_365"),
                        "attribute_3_code_365": item.get("attribute_3_code_365"),
                        "attribute_4_code_365": item.get("attribute_4_code_365"),
                        "attribute_5_code_365": item.get("attribute_5_code_365"),
                        "attribute_6_code_365": item.get("attribute_6_code_365"),
                        "item_length": float(item.get("item_length")) if item.get("item_length") else None,
                        "item_width": float(item.get("item_width")) if item.get("item_width") else None,
                        "item_height": float(item.get("item_height")) if item.get("item_height") else None,
                        "item_weight": float(item.get("item_weight")) if item.get("item_weight") else None,
                        "number_of_pieces": int(float(item.get("number_of_pieces"))) if item.get("number_of_pieces") else None,
                        "selling_qty": float(item.get("number_field_1_value")) if item.get("number_field_1_value") else None,
                    }
                    
                    attr_hash = _compute_hash(core)
                    
                    # Check if already exists
                    existing = session.query(DwItem).filter_by(item_code_365=code).first()
                    if existing:
                        # Check if data has changed
                        if existing.attr_hash != attr_hash:
                            # UPDATE: Data has changed, update the record
                            logger.info(f"UPDATING {code}: hash changed from {existing.attr_hash} to {attr_hash}")
                            logger.info(f"  Current attr1={existing.attribute_1_code_365}, API attr1={core['attribute_1_code_365']}")
                            logger.info(f"  Current selling_qty={existing.selling_qty}, API selling_qty={core['selling_qty']}")
                            
                            existing.item_name = core["item_name"]
                            existing.active = core["active"]
                            existing.category_code_365 = core["category_code_365"]
                            existing.brand_code_365 = core["brand_code_365"]
                            existing.season_code_365 = core["season_code_365"]
                            existing.attribute_1_code_365 = core["attribute_1_code_365"]
                            existing.attribute_2_code_365 = core["attribute_2_code_365"]
                            existing.attribute_3_code_365 = core["attribute_3_code_365"]
                            existing.attribute_4_code_365 = core["attribute_4_code_365"]
                            existing.attribute_5_code_365 = core["attribute_5_code_365"]
                            existing.attribute_6_code_365 = core["attribute_6_code_365"]
                            existing.item_length = core["item_length"]
                            existing.item_width = core["item_width"]
                            existing.item_height = core["item_height"]
                            existing.item_weight = core["item_weight"]
                            existing.number_of_pieces = core["number_of_pieces"]
                            existing.selling_qty = core["selling_qty"]
                            existing.attr_hash = attr_hash
                            existing.last_sync_at = now
                            page_inserted += 1
                            total_inserted += 1
                        else:
                            # No change, skip
                            page_skipped += 1
                            total_skipped += 1
                            logger.debug(f"SKIPPED {code}: hash unchanged ({existing.attr_hash})")
                        continue
                    
                    # INSERT: New record
                    obj = DwItem(**core, attr_hash=attr_hash, last_sync_at=now)
                    session.add(obj)
                    page_inserted += 1
                    total_inserted += 1
                    
                except Exception as e:
                    logger.error(f"Error processing item {code}: {str(e)}")
                    page_skipped += 1
                    total_skipped += 1
                    continue
            
            # Commit batch immediately after each page
            try:
                session.commit()
            except Exception as e:
                logger.error(f"Commit error on page {page}: {str(e)}")
                session.rollback()
            
            logger.info(f"Page {page}: Inserted {page_inserted}, Skipped {page_skipped}")
            
            page += 1
        
        except Exception as e:
            logger.error(f"Error on page {page}: {str(e)}")
            session.rollback()
            break
    
    # Final commit to ensure everything is saved
    try:
        session.commit()
    except Exception as e:
        logger.error(f"Final commit error: {str(e)}")
        session.rollback()
    
    # Get final count from database
    final_item_count = session.query(DwItem).count()
    
    # Log final summary
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ FULL DATA WAREHOUSE SYNC COMPLETED SUCCESSFULLY")
    logger.info(f"{'='*80}")
    logger.info(f"Items inserted in this sync: {total_inserted}")
    logger.info(f"Items skipped (already existed): {total_skipped}")
    logger.info(f"TOTAL ITEMS NOW IN DATABASE: {final_item_count}")
    logger.info(f"Sync finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'='*80}\n")
    
    # Update status to completed
    try:
        status_obj = session.get(SyncState, "full_sync_status")
        if status_obj:
            status_obj.value = "COMPLETED"
        session.commit()
    except Exception as e:
        logger.error(f"Error updating final status: {str(e)}")
    finally:
        sync_logger.removeHandler(handler)


# ----------------------
# INCREMENTAL UPDATE
# ----------------------

def _get_last_change_id(session: Session):
    """Get the last synced change ID from SyncState"""
    state = session.get(SyncState, "items_last_change_id")
    if not state:
        return None
    try:
        return int(state.value)
    except ValueError:
        return None


def _set_last_change_id(session: Session, change_id: int):
    """Update the last synced change ID in SyncState"""
    state = session.get(SyncState, "items_last_change_id")
    if not state:
        state = SyncState(key="items_last_change_id", value=str(change_id))
        session.add(state)
    else:
        state.value = str(change_id)
    session.commit()


def incremental_dw_update(session: Session):
    """
    Incremental update for items - only syncs items modified since last sync.
    Uses date filtering to get only changed items from PS365 API.
    Shows which items have actual changes that will be updated.
    """
    logger.info("Starting incremental item update...")
    
    # Get last sync timestamp from database
    last_sync_result = session.query(DwItem).order_by(DwItem.last_sync_at.desc()).limit(1).first()
    last_sync_time = last_sync_result.last_sync_at if last_sync_result else None
    
    if last_sync_time:
        last_modified_from = last_sync_time.strftime("%Y-%m-%d")
        logger.info(f"Syncing items modified since {last_modified_from}")
    else:
        last_modified_from = ""
        logger.info("No previous sync found - syncing all items")
    
    page = 1
    updated_count = 0
    total_found = 0
    now = utc_now_for_db()
    all_changes = []
    
    while True:
        try:
            response = call_ps365("list_items", {
                "filter_define": {
                    "only_counted": "N",
                    "page_number": page,
                    "page_size": PAGE_SIZE,
                    "active_type": "all",
                    "ecommerce_type": "all",
                    "categories_selection": "",
                    "departments_selection": "",
                    "items_supplier_selection": "",
                    "brands_selection": "",
                    "seasons_selection": "",
                    "models_selection": "",
                    "items_selection": "",
                    "colours_selection": "",
                    "sizes_selection": "",
                    "sizes_group_selection": "",
                    "attributes_1_selection": "",
                    "attributes_2_selection": "",
                    "attributes_3_selection": "",
                    "attributes_4_selection": "",
                    "attributes_5_selection": "",
                    "attributes_6_selection": "",
                    "keyword_search_item_code_365": "",
                    "keyword_search_item_name": "",
                    "text_field_value_1_selection": "",
                    "text_field_value_2_selection": "",
                    "text_field_value_3_selection": "",
                    "text_field_value_4_selection": "",
                    "text_field_value_5_selection": "",
                    "number_field_value_1_selection": "",
                    "number_field_value_2_selection": "",
                    "number_field_value_3_selection": "",
                    "number_field_value_4_selection": "",
                    "number_field_value_5_selection": "",
                    "date_field_value_1_selection": "",
                    "date_field_value_2_selection": "",
                    "date_field_value_3_selection": "",
                    "date_field_value_4_selection": "",
                    "date_field_value_5_selection": "",
                    "boolean_field_value_1": "",
                    "boolean_field_value_2": "",
                    "boolean_field_value_3": "",
                    "boolean_field_value_4": "",
                    "boolean_field_value_5": "",
                    "date_field_1_value_from": "",
                    "date_field_1_value_to": "",
                    "date_field_2_value_from": "",
                    "date_field_2_value_to": "",
                    "date_field_3_value_from": "",
                    "date_field_3_value_to": "",
                    "date_field_4_value_from": "",
                    "date_field_4_value_to": "",
                    "date_field_5_value_from": "",
                    "date_field_5_value_to": "",
                    "last_modified_from": last_modified_from,
                    "last_modified_to": "",
                    "creation_date_from": "",
                    "creattion_date_to": "",
                    "display_fields": "item_code_365,item_name,active,category_code_365,brand_code_365,season_code_365,attribute_1_code_365,attribute_2_code_365,attribute_3_code_365,attribute_4_code_365,attribute_5_code_365,attribute_6_code_365,item_length,item_width,item_height,item_weight,number_of_pieces,number_field_1_value",
                },
            })
            
            api_response = response.get("api_response", {})
            if api_response.get("response_code") != "1":
                logger.error(f"API Error on page {page}: {api_response.get('response_msg', 'Unknown')}")
                break
            
            items = response.get("list_items", [])
            if not items:
                break
            
            for i in items:
                total_found += 1
                code = i.get("item_code_365", "").upper()
                if not code:
                    continue
                
                # Helper to convert numeric fields safely
                def to_decimal(val):
                    if val is None or val == "":
                        return None
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return None
                
                def to_int(val):
                    if val is None or val == "":
                        return None
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        return None
                
                core = {
                    "item_code_365": code,
                    "item_name": i.get("item_name", ""),
                    "active": bool(i.get("active", True)),
                    "category_code_365": i.get("category_code_365"),
                    "brand_code_365": i.get("brand_code_365"),
                    "season_code_365": i.get("season_code_365"),
                    "attribute_1_code_365": i.get("attribute_1_code_365"),
                    "attribute_2_code_365": i.get("attribute_2_code_365"),
                    "attribute_3_code_365": i.get("attribute_3_code_365"),
                    "attribute_4_code_365": i.get("attribute_4_code_365"),
                    "attribute_5_code_365": i.get("attribute_5_code_365"),
                    "attribute_6_code_365": i.get("attribute_6_code_365"),
                    "item_length": to_decimal(i.get("item_length")),
                    "item_width": to_decimal(i.get("item_width")),
                    "item_height": to_decimal(i.get("item_height")),
                    "item_weight": to_decimal(i.get("item_weight")),
                    "number_of_pieces": to_int(i.get("number_of_pieces")),
                    "selling_qty": to_decimal(i.get("number_field_1_value")),
                }
                attr_hash = _compute_hash(core)
                
                existing = session.get(DwItem, code)
                
                # Check if there's an actual change
                has_change = False
                change_details = ""
                
                if not existing:
                    has_change = True
                    change_details = "NEW ITEM"
                elif existing.attr_hash != attr_hash:
                    has_change = True
                    changes = []
                    if existing.item_name != core["item_name"]:
                        changes.append(f"name: '{existing.item_name}' → '{core['item_name']}'")
                    if existing.active != core["active"]:
                        changes.append(f"active: {existing.active} → {core['active']}")
                    if existing.category_code_365 != core["category_code_365"]:
                        changes.append(f"category: {existing.category_code_365} → {core['category_code_365']}")
                    if existing.brand_code_365 != core["brand_code_365"]:
                        changes.append(f"brand: {existing.brand_code_365} → {core['brand_code_365']}")
                    if existing.season_code_365 != core["season_code_365"]:
                        changes.append(f"season: {existing.season_code_365} → {core['season_code_365']}")
                    if existing.attribute_1_code_365 != core["attribute_1_code_365"]:
                        changes.append(f"attribute1: {existing.attribute_1_code_365} → {core['attribute_1_code_365']}")
                    if existing.attribute_2_code_365 != core["attribute_2_code_365"]:
                        changes.append(f"attribute2: {existing.attribute_2_code_365} → {core['attribute_2_code_365']}")
                    if existing.attribute_3_code_365 != core["attribute_3_code_365"]:
                        changes.append(f"attribute3: {existing.attribute_3_code_365} → {core['attribute_3_code_365']}")
                    if existing.attribute_4_code_365 != core["attribute_4_code_365"]:
                        changes.append(f"attribute4: {existing.attribute_4_code_365} → {core['attribute_4_code_365']}")
                    if existing.attribute_5_code_365 != core["attribute_5_code_365"]:
                        changes.append(f"attribute5: {existing.attribute_5_code_365} → {core['attribute_5_code_365']}")
                    if existing.attribute_6_code_365 != core["attribute_6_code_365"]:
                        changes.append(f"attribute6: {existing.attribute_6_code_365} → {core['attribute_6_code_365']}")
                    change_details = " | ".join(changes)
                
                if has_change:
                    all_changes.append({
                        "code": code,
                        "name": core["item_name"],
                        "status": "NEW" if not existing else "UPDATED",
                        "details": change_details
                    })
                    
                    if existing:
                        existing.item_name = core["item_name"]
                        existing.active = core["active"]
                        existing.category_code_365 = core["category_code_365"]
                        existing.brand_code_365 = core["brand_code_365"]
                        existing.season_code_365 = core["season_code_365"]
                        existing.attribute_1_code_365 = core["attribute_1_code_365"]
                        existing.attribute_2_code_365 = core["attribute_2_code_365"]
                        existing.attribute_3_code_365 = core["attribute_3_code_365"]
                        existing.attribute_4_code_365 = core["attribute_4_code_365"]
                        existing.attribute_5_code_365 = core["attribute_5_code_365"]
                        existing.attribute_6_code_365 = core["attribute_6_code_365"]
                        existing.item_length = core["item_length"]
                        existing.item_width = core["item_width"]
                        existing.item_height = core["item_height"]
                        existing.item_weight = core["item_weight"]
                        existing.number_of_pieces = core["number_of_pieces"]
                        existing.selling_qty = core["selling_qty"]
                        existing.attr_hash = attr_hash
                        existing.last_sync_at = now
                    else:
                        obj = DwItem(**core, attr_hash=attr_hash, last_sync_at=now)
                        session.add(obj)
                    
                    updated_count += 1
            
            session.commit()
            page += 1
            
        except Exception as e:
            logger.error(f"Error on page {page}: {str(e)}")
            break
    
    # Log summary with all changes
    logger.info(f"\n{'='*80}")
    logger.info(f"INCREMENTAL SYNC SUMMARY")
    logger.info(f"{'='*80}")
    logger.info(f"Items found with PS365 modifications: {total_found}")
    logger.info(f"Items with actual data changes: {updated_count}")
    
    if all_changes:
        logger.info(f"\nItems that will be updated:")
        for i, item in enumerate(all_changes, 1):
            logger.info(f"  {i}. [{item['status']}] {item['code']} - {item['name']}")
            if item['details']:
                logger.info(f"     Changes: {item['details']}")
    else:
        logger.info(f"\nNo items require updating - all data already matches PS365")
    
    logger.info(f"{'='*80}\n")


# ----------------------
# INVOICE SYNC FUNCTIONS
# ----------------------

def sync_invoice_headers_from_date(session: Session, date_from: str, date_to: str = None):
    """
    Sync invoice headers from PS365 for a date range.
    date_from: YYYY-MM-DD format (required)
    date_to: YYYY-MM-DD format (optional, defaults to today)
    """
    page = 1
    all_headers = []
    inserted = 0
    updated = 0
    
    try:
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        if date_to is None:
            to_dt = datetime.now()  # Default to today
        else:
            to_dt = datetime.strptime(date_to, "%Y-%m-%d")
        to_dt = to_dt + timedelta(days=1)  # Add 1 day to include the to_date
        from_date = from_dt.strftime("%Y-%m-%d")
        to_date = to_dt.strftime("%Y-%m-%d")
    except Exception as e:
        logger.error(f"Invalid date format: {date_from} or {date_to}")
        return 0, 0
    
    logger.info(f"Syncing invoice headers from {date_from} to {date_to if date_to else 'today'}")
    logger.info(f"API invoice date filter: {from_date} to {to_date}")
    
    while True:
        try:
            payload = {
                "filter_define": {
                    "only_counted": "N",
                    "page_number": page,
                    "page_size": PAGE_SIZE,
                    "invoice_type": "all",
                    "invoice_number_selection": "",
                    "invoice_customer_code_selection": "",
                    "invoice_customer_name_selection": "",
                    "invoice_customer_email_selection": "",
                    "invoice_customer_phone_selection": "",
                    "from_date": from_date,  # Invoice value date in yyyy-mm-dd format
                    "to_date": to_date,      # Invoice value date in yyyy-mm-dd format
                }
            }
            
            logger.info(f"Fetching invoice headers page {page}...")
            response = call_ps365("list_loyalty_invoices_header", payload)
            logger.info(f"API response received, parsing...")
            api_resp = response.get("api_response", {})
            
            if api_resp.get("response_code") != "1":
                logger.error(f"PS365 API Error: {api_resp}")
                break
            
            invoices = response.get("list_invoices", []) or []
            logger.info(f"Page {page}: received {len(invoices)} invoices")
            if not invoices:
                logger.info(f"No more invoices on page {page}")
                break
            
            for inv in invoices:
                invoice_no = inv.get("invoice_no_365")
                if not invoice_no:
                    continue
                
                # Create hash to detect changes
                hash_data = {
                    "invoice_no_365": invoice_no,
                    "invoice_type": inv.get("invoice_type"),
                    "invoice_date_utc0": inv.get("invoice_date_utc0"),
                    "customer_code_365": inv.get("customer_code_365"),
                    "store_code_365": inv.get("store_code_365"),
                    "user_code_365": inv.get("user_code_365"),
                    "total_sub": str(inv.get("total_sub")),
                    "total_discount": str(inv.get("total_discount")),
                    "total_vat": str(inv.get("total_vat")),
                    "total_grand": str(inv.get("total_grand")),
                    "points_earned": str(inv.get("points_earned")),
                    "points_redeemed": str(inv.get("points_redeemed")),
                }
                attr_hash = _compute_hash(hash_data)
                
                # Check if exists (skip if it does - headers don't change)
                existing = session.query(DwInvoiceHeader).filter_by(invoice_no_365=invoice_no).first()
                
                if existing:
                    # Header already exists - skip it since headers don't change
                    all_headers.append(invoice_no)
                    continue
                
                # Insert new header only
                # Extract date only (no time)
                inv_date_str = inv.get("invoice_date_utc0")
                if inv_date_str:
                    if isinstance(inv_date_str, str):
                        inv_date = datetime.fromisoformat(inv_date_str.replace('Z', '+00:00')).date()
                    else:
                        inv_date = inv_date_str.date() if hasattr(inv_date_str, 'date') else inv_date_str
                else:
                    inv_date = None
                
                # Apply negation for return invoices
                invoice_type = inv.get("invoice_type")
                sign = _get_invoice_type_sign(invoice_type)
                
                header = DwInvoiceHeader(
                    invoice_no_365=invoice_no,
                    invoice_type=invoice_type,
                    invoice_date_utc0=inv_date,
                    customer_code_365=inv.get("customer_code_365"),
                    store_code_365=inv.get("store_code_365"),
                    user_code_365=inv.get("user_code_365"),
                    total_sub=(inv.get("total_sub") or 0) * sign,
                    total_discount=(inv.get("total_discount") or 0) * sign,
                    total_vat=(inv.get("total_vat") or 0) * sign,
                    total_grand=(inv.get("total_grand") or 0) * sign,
                    points_earned=inv.get("points_earned"),
                    points_redeemed=inv.get("points_redeemed"),
                    attr_hash=attr_hash,
                    last_sync_at=datetime.now()
                )
                session.add(header)
                inserted += 1
                
                all_headers.append(invoice_no)
            
            session.commit()
            page += 1
            
        except Exception as e:
            logger.error(f"Error on page {page}: {str(e)}", exc_info=True)
            session.rollback()
            break
    
    logger.info(f"Invoice headers synced: {inserted} inserted, total {len(all_headers)}")
    return inserted, 0


def _get_invoice_type_sign(invoice_type: str) -> int:
    """
    Determine sign multiplier based on invoice type.
    Returns -1 for returns/credits, +1 for sales.
    """
    if not invoice_type:
        return 1
    
    invoice_type_upper = invoice_type.upper().strip()
    # Common return types - make these negative
    return_types = ["RETURN", "CREDIT_NOTE", "SALES_RETURN", "CREDIT", "REFUND"]
    
    if any(rt in invoice_type_upper for rt in return_types):
        return -1
    return 1


def sync_invoice_lines_from_date(session: Session, date_from: str, date_to: str = None):
    """
    Sync invoice lines from PS365 for a date range.
    Lines for returns/credit notes are stored with negative quantities/totals.
    date_from: YYYY-MM-DD format (required)
    date_to: YYYY-MM-DD format (optional, defaults to today)
    """
    page = 1
    all_lines = []
    inserted = 0
    updated = 0
    total_invoices_received = 0
    total_lines_received = 0
    total_lines_skipped = 0
    
    try:
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        if date_to is None:
            to_dt = datetime.now()  # Default to today
        else:
            to_dt = datetime.strptime(date_to, "%Y-%m-%d")
        to_dt = to_dt + timedelta(days=1)  # Add 1 day to include the to_date
        from_date = from_dt.strftime("%Y-%m-%d")
        to_date = to_dt.strftime("%Y-%m-%d")
    except Exception as e:
        logger.error(f"Invalid date format: {date_from} or {date_to}")
        return 0, 0
    
    logger.info(f"Syncing invoice lines from {date_from} to {date_to if date_to else 'today'}")
    logger.info(f"API invoice date filter: {from_date} to {to_date}")
    
    # Pre-load all existing lines and headers into sets for fast lookup (much faster than querying each time)
    existing_lines_set = set()
    for row in session.query(DwInvoiceLine.invoice_no_365, DwInvoiceLine.line_number).all():
        existing_lines_set.add((row[0], row[1]))
    logger.info(f"Pre-loaded {len(existing_lines_set)} existing invoice lines for fast lookup")
    
    existing_headers_set = set()
    invoice_type_map = {}  # Map invoice_no -> type for sign calculation
    for row in session.query(DwInvoiceHeader.invoice_no_365, DwInvoiceHeader.invoice_type).all():
        existing_headers_set.add(row[0])
        invoice_type_map[row[0]] = row[1]
    logger.info(f"Pre-loaded {len(existing_headers_set)} existing invoice headers for fast lookup")
    
    while True:
        try:
            payload = {
                "filter_define": {
                    "only_counted": "N",
                    "page_number": page,
                    "page_size": PAGE_SIZE,
                    "invoice_type": "all",
                    "invoice_number_selection": "",
                    "invoice_customer_code_selection": "",
                    "invoice_customer_name_selection": "",
                    "invoice_customer_email_selection": "",
                    "invoice_customer_phone_selection": "",
                    "from_date": from_date,  # Invoice value date in yyyy-mm-dd format
                    "to_date": to_date,      # Invoice value date in yyyy-mm-dd format
                }
            }
            
            logger.info(f"Fetching invoice lines page {page}...")
            response = call_ps365("list_loyalty_invoices", payload)
            api_resp = response.get("api_response", {})
            
            if api_resp.get("response_code") != "1":
                logger.error(f"PS365 API Error: {api_resp}")
                break
            
            invoices = response.get("list_invoices", []) or []
            logger.info(f"Page {page}: received {len(invoices)} invoices with details")
            if not invoices:
                logger.info(f"No more invoices on page {page}")
                break
            
            total_invoices_received += len(invoices)
            page_lines_inserted = 0
            page_lines_skipped = 0
            
            for inv_idx, inv in enumerate(invoices, 1):
                # Log raw response structure for first few invoices to debug
                if page == 1 and inv_idx <= 2:
                    logger.info(f"DEBUG Page {page} Invoice {inv_idx} response keys: {list(inv.keys())}")
                
                # Try to extract invoice number - it might be at different levels
                invoice_no = None
                if "invoice" in inv:
                    inv_obj = inv.get("invoice", inv)
                    header = inv_obj.get("invoice_header", {})
                    invoice_no = header.get("invoice_no_365")
                elif "invoice_no_365" in inv:
                    invoice_no = inv.get("invoice_no_365")
                elif "invoice_header" in inv:
                    header = inv.get("invoice_header", {})
                    invoice_no = header.get("invoice_no_365")
                
                if not invoice_no:
                    logger.warning(f"Page {page} Invoice {inv_idx}: Could not extract invoice_no_365 from response")
                    total_lines_skipped += 1
                    page_lines_skipped += 1
                    continue
                
                # Try to extract lines - might be at different levels
                lines = []
                if "invoice" in inv:
                    inv_obj = inv.get("invoice", inv)
                    lines = inv_obj.get("list_invoice_details", []) or []
                elif "list_invoice_details" in inv:
                    lines = inv.get("list_invoice_details", []) or []
                
                total_lines_received += len(lines)
                logger.info(f"Page {page} Invoice {inv_idx} ({invoice_no}): received {len(lines)} lines")
                
                for line_idx, line in enumerate(lines, 1):
                    line_number = line.get("line_number")
                    
                    # Check if line already exists (fast in-memory lookup - much faster than DB query)
                    if (invoice_no, line_number) in existing_lines_set:
                        all_lines.append((invoice_no, line_number))
                        page_lines_skipped += 1
                        total_lines_skipped += 1
                        continue
                    
                    # Check if parent invoice header exists (fast in-memory lookup)
                    if invoice_no not in existing_headers_set:
                        logger.warning(f"Page {page} Invoice {invoice_no} Line {line_number}: header not found in DW")
                        page_lines_skipped += 1
                        total_lines_skipped += 1
                        continue
                    
                    # Insert new line only
                    # Get sign multiplier based on invoice type (returns are negative)
                    invoice_type = invoice_type_map.get(invoice_no, "")
                    sign = _get_invoice_type_sign(invoice_type)
                    
                    # Extract raw values
                    raw_quantity = line.get("line_quantity")
                    raw_line_total_excl = line.get("line_total_sub")
                    raw_line_total_discount = line.get("line_total_discount")
                    raw_line_total_vat = line.get("line_total_vat")
                    raw_line_total_incl = line.get("line_total_grand")
                    
                    # Apply sign multiplier (negate for returns)
                    quantity = float(raw_quantity or 0) * sign if raw_quantity else None
                    line_total_excl = float(raw_line_total_excl or 0) * sign if raw_line_total_excl else None
                    line_total_discount = float(raw_line_total_discount or 0) * sign if raw_line_total_discount else None
                    line_total_vat = float(raw_line_total_vat or 0) * sign if raw_line_total_vat else None
                    line_total_incl = float(raw_line_total_incl or 0) * sign if raw_line_total_incl else None
                    
                    hash_data = {
                        "invoice_no_365": invoice_no,
                        "line_number": str(line_number),
                        "item_code_365": line.get("item_code_365"),
                        "quantity": str(quantity),
                        "price_excl": str(line.get("line_price_exclude_vat")),
                        "price_incl": str(line.get("line_price_include_vat")),
                        "vat_percent": str(line.get("line_vat_percentage")),
                        "line_total_excl": str(line_total_excl),
                        "line_total_incl": str(line_total_incl),
                    }
                    attr_hash = _compute_hash(hash_data)
                    
                    invoice_line = DwInvoiceLine(
                        invoice_no_365=invoice_no,
                        line_number=line_number,
                        item_code_365=line.get("item_code_365"),
                        quantity=quantity,
                        price_excl=line.get("line_price_exclude_vat"),
                        price_incl=line.get("line_price_include_vat"),
                        discount_percent=line.get("line_discount_percentage"),
                        vat_code_365=line.get("line_vat_code_365"),
                        vat_percent=line.get("line_vat_percentage"),
                        line_total_excl=line_total_excl,
                        line_total_discount=line_total_discount,
                        line_total_vat=line_total_vat,
                        line_total_incl=line_total_incl,
                        attr_hash=attr_hash,
                        last_sync_at=datetime.now()
                    )
                    session.add(invoice_line)
                    inserted += 1
                    page_lines_inserted += 1
                    all_lines.append((invoice_no, line_number))
                    
                    # Log if this is a return line
                    if sign == -1:
                        logger.debug(f"Page {page} Invoice {invoice_no} Line {line_number}: RETURN (qty={quantity})")
            
            session.commit()
            logger.info(f"Page {page} complete: {page_lines_inserted} lines inserted, {page_lines_skipped} skipped")
            page += 1
            
        except Exception as e:
            logger.error(f"Error on page {page}: {str(e)}", exc_info=True)
            session.rollback()
            break
    
    logger.info(f"\n{'='*80}")
    logger.info(f"INVOICE LINES SYNC SUMMARY")
    logger.info(f"{'='*80}")
    logger.info(f"Total invoices received from API: {total_invoices_received}")
    logger.info(f"Total lines received from API: {total_lines_received}")
    logger.info(f"Total lines inserted: {inserted}")
    logger.info(f"Total lines skipped (already existed): {total_lines_skipped}")
    logger.info(f"Total unique line records: {len(all_lines)}")
    logger.info(f"{'='*80}\n")
    return inserted, 0


def sync_invoice_stores(session: Session):
    """Sync unique stores from existing invoice headers"""
    logger.info("Syncing invoice stores from headers")
    
    # Get unique stores from headers using raw SQL for reliability
    from sqlalchemy import text
    result = session.execute(text("SELECT DISTINCT store_code_365 FROM dw_invoice_header WHERE store_code_365 IS NOT NULL ORDER BY store_code_365"))
    store_codes = [row[0] for row in result]
    logger.info(f"Found {len(store_codes)} unique store codes: {store_codes}")
    
    inserted = 0
    updated = 0
    
    for store_code in store_codes:
        if not store_code:
            logger.debug(f"Skipping empty store code")
            continue
        
        hash_data = {"store_code_365": store_code}
        attr_hash = _compute_hash(hash_data)
        
        existing = session.query(DwStore).filter_by(store_code_365=store_code).first()
        
        if existing:
            if existing.attr_hash != attr_hash:
                existing.attr_hash = attr_hash
                existing.last_sync_at = utc_now_for_db()
                updated += 1
                logger.debug(f"Updated store {store_code}")
        else:
            store = DwStore(
                store_code_365=store_code,
                store_name=store_code,
                attr_hash=attr_hash,
                last_sync_at=utc_now_for_db()
            )
            session.add(store)
            inserted += 1
            logger.debug(f"Inserted new store {store_code}")
    
    session.commit()
    logger.info(f"Stores synced: {inserted} inserted, {updated} updated")
    return inserted, updated


def sync_invoice_cashiers(session: Session):
    """Sync unique cashiers from existing invoice headers"""
    logger.info("Syncing invoice cashiers from headers")
    
    # Get unique cashiers from headers using raw SQL for reliability
    from sqlalchemy import text
    result = session.execute(text("SELECT DISTINCT user_code_365 FROM dw_invoice_header WHERE user_code_365 IS NOT NULL ORDER BY user_code_365"))
    user_codes = [row[0] for row in result]
    logger.info(f"Found {len(user_codes)} unique user codes: {user_codes}")
    
    inserted = 0
    updated = 0
    
    for user_code in user_codes:
        if not user_code:
            logger.debug(f"Skipping empty user code")
            continue
        
        hash_data = {"user_code_365": user_code}
        attr_hash = _compute_hash(hash_data)
        
        existing = session.query(DwCashier).filter_by(user_code_365=user_code).first()
        
        if existing:
            if existing.attr_hash != attr_hash:
                existing.attr_hash = attr_hash
                existing.last_sync_at = utc_now_for_db()
                updated += 1
                logger.debug(f"Updated cashier {user_code}")
        else:
            cashier = DwCashier(
                user_code_365=user_code,
                user_name=user_code,
                attr_hash=attr_hash,
                last_sync_at=utc_now_for_db()
            )
            session.add(cashier)
            inserted += 1
            logger.debug(f"Inserted new cashier {user_code}")
    
    session.commit()
    logger.info(f"Cashiers synced: {inserted} inserted, {updated} updated")
    return inserted, updated


def _update_invoice_sync_status(session: Session, status: str, message: str):
    """Update invoice sync status in SyncState table - non-blocking"""
    try:
        # Import here to avoid any scope issues
        from models import SyncState as SS
        
        status_obj = session.query(SS).filter_by(key="invoice_sync_status").first()
        if status_obj:
            status_obj.value = f"{status}|{message}"
        else:
            status_obj = SS(key="invoice_sync_status", value=f"{status}|{message}")
            session.add(status_obj)
        session.commit()
        logger.info(f"Status updated: {status} - {message}")
    except Exception as e:
        # Don't let status updates crash the sync
        logger.warning(f"Could not update sync status (non-critical): {e}")
        try:
            session.rollback()
        except:
            pass


def sync_invoices_from_date(session: Session, date_from: str, date_to: str = None):
    """
    Complete invoice sync: headers -> lines -> stores -> cashiers (uses existing PSCustomer table)
    date_from: YYYY-MM-DD format (required)
    date_to: YYYY-MM-DD format (optional, defaults to today)
    """
    # Setup file-based logging
    log_file = _setup_file_logging("sync_invoices")
    
    # Set initial status
    _update_invoice_sync_status(session, "RUNNING", "Starting invoice sync...")
    
    date_range_str = f"{date_from} to {date_to if date_to else 'today'}"
    logger.info(f"\n{'='*80}")
    logger.info(f"STARTING INVOICE SYNC FROM {date_range_str}")
    logger.info(f"Log file: {log_file}")
    logger.info(f"{'='*80}\n")
    
    try:
        _update_invoice_sync_status(session, "RUNNING", "Syncing invoice headers...")
        h_ins, h_upd = sync_invoice_headers_from_date(session, date_from, date_to)
        
        _update_invoice_sync_status(session, "RUNNING", f"Headers done ({h_ins} inserted). Syncing lines...")
        l_ins, l_upd = sync_invoice_lines_from_date(session, date_from, date_to)
        
        _update_invoice_sync_status(session, "RUNNING", f"Lines done ({l_ins} inserted). Syncing stores...")
        s_ins, s_upd = sync_invoice_stores(session)
        
        _update_invoice_sync_status(session, "RUNNING", "Syncing cashiers...")
        u_ins, u_upd = sync_invoice_cashiers(session)
        
        # Set completed status with summary
        summary = f"Headers: {h_ins}, Lines: {l_ins}, Stores: {s_ins}, Cashiers: {u_ins}"
        _update_invoice_sync_status(session, "COMPLETE", summary)
        
        logger.info(f"\n{'='*80}")
        logger.info(f"INVOICE SYNC COMPLETE")
        logger.info(f"{'='*80}")
        logger.info(f"Headers:  {h_ins} inserted, {h_upd} updated")
        logger.info(f"Lines:    {l_ins} inserted, {l_upd} updated")
        logger.info(f"Stores:   {s_ins} inserted, {s_upd} updated")
        logger.info(f"Cashiers: {u_ins} inserted, {u_upd} updated")
        logger.info(f"Note: Using existing PSCustomer table for customers (not syncing separately)")
        logger.info(f"{'='*80}\n")
        
        return h_ins, h_upd
        
    except KeyboardInterrupt:
        # Process was interrupted (timeout, SIGTERM, etc.)
        _update_invoice_sync_status(session, "FAILED", "Sync interrupted (timeout or external termination)")
        logger.error(f"\n{'='*80}")
        logger.error(f"❌ INVOICE SYNC FAILED - INTERRUPTED")
        logger.error(f"{'='*80}")
        logger.error(f"The sync process was interrupted (likely by worker timeout)")
        logger.error(f"Please check gunicorn timeout settings and try again")
        logger.error(f"{'='*80}\n")
        raise
        
    except Exception as e:
        # Any other error
        error_msg = str(e)[:200]
        _update_invoice_sync_status(session, "FAILED", error_msg)
        logger.error(f"\n{'='*80}")
        logger.error(f"❌ INVOICE SYNC FAILED - ERROR")
        logger.error(f"{'='*80}")
        logger.error(f"Exception: {error_msg}")
        logger.error(f"Full traceback:", exc_info=True)
        logger.error(f"{'='*80}\n")
        raise
