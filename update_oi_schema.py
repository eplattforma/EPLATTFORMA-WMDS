"""
Schema update script for Operational Intelligence tables and columns.

Adds:
- wms_* classification columns to ps_items_dw table
- wms_category_defaults table
- wms_item_overrides table
- wms_classification_runs table
"""

import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def update_oi_schema():
    """Add OI-related columns and tables to the database."""
    
    wms_columns = [
        ("wms_zone", "VARCHAR(50)"),
        ("wms_unit_type", "VARCHAR(50)"),
        ("wms_fragility", "VARCHAR(20)"),
        ("wms_stackability", "VARCHAR(20)"),
        ("wms_temperature_sensitivity", "VARCHAR(30)"),
        ("wms_pressure_sensitivity", "VARCHAR(20)"),
        ("wms_shape_type", "VARCHAR(30)"),
        ("wms_spill_risk", "BOOLEAN"),
        ("wms_pick_difficulty", "INTEGER"),
        ("wms_shelf_height", "VARCHAR(20)"),
        ("wms_box_fit_rule", "VARCHAR(30)"),
        ("wms_class_confidence", "INTEGER"),
        ("wms_class_source", "VARCHAR(30)"),
        ("wms_class_notes", "TEXT"),
        ("wms_classified_at", "TIMESTAMP"),
        ("wms_class_evidence", "TEXT"),
    ]
    
    for col_name, col_type in wms_columns:
        try:
            db.session.execute(text(f"""
                ALTER TABLE ps_items_dw 
                ADD COLUMN IF NOT EXISTS {col_name} {col_type}
            """))
            logger.debug(f"Added/verified column: {col_name}")
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.warning(f"Could not add column {col_name}: {e}")
    
    db.session.commit()
    logger.info("WMS classification columns updated on ps_items_dw")
    
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS wms_category_defaults (
                category_code_365 VARCHAR(64) PRIMARY KEY,
                default_zone VARCHAR(50),
                default_fragility VARCHAR(20),
                default_stackability VARCHAR(20),
                default_temperature_sensitivity VARCHAR(30),
                default_pressure_sensitivity VARCHAR(20),
                default_shape_type VARCHAR(30),
                default_spill_risk BOOLEAN,
                default_pick_difficulty INTEGER,
                default_shelf_height VARCHAR(20),
                default_box_fit_rule VARCHAR(30),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                notes TEXT,
                updated_by VARCHAR(100),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.session.commit()
        logger.info("Created wms_category_defaults table")
    except Exception as e:
        if "already exists" not in str(e).lower():
            logger.warning(f"Could not create wms_category_defaults: {e}")
        db.session.rollback()
    
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS wms_item_overrides (
                item_code_365 VARCHAR(64) PRIMARY KEY,
                zone_override VARCHAR(50),
                unit_type_override VARCHAR(50),
                fragility_override VARCHAR(20),
                stackability_override VARCHAR(20),
                temperature_sensitivity_override VARCHAR(30),
                pressure_sensitivity_override VARCHAR(20),
                shape_type_override VARCHAR(30),
                spill_risk_override BOOLEAN,
                pick_difficulty_override INTEGER,
                shelf_height_override VARCHAR(20),
                box_fit_rule_override VARCHAR(30),
                override_reason TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                updated_by VARCHAR(100),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.session.commit()
        logger.info("Created wms_item_overrides table")
    except Exception as e:
        if "already exists" not in str(e).lower():
            logger.warning(f"Could not create wms_item_overrides: {e}")
        db.session.rollback()
    
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS wms_classification_runs (
                id SERIAL PRIMARY KEY,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                run_by VARCHAR(100),
                mode VARCHAR(30) DEFAULT 'moderate_60',
                active_items_scanned INTEGER,
                items_updated INTEGER,
                items_needing_review INTEGER,
                notes TEXT
            )
        """))
        db.session.commit()
        logger.info("Created wms_classification_runs table")
    except Exception as e:
        if "already exists" not in str(e).lower():
            logger.warning(f"Could not create wms_classification_runs: {e}")
        db.session.rollback()
    
    logger.info("OI schema update complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from app import app
    with app.app_context():
        update_oi_schema()
