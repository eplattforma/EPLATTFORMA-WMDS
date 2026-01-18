"""
Schema update script for WmsPackingProfile pack_mode columns.
Adds pack_mode, loss_risk, carton_type_hint, max_carton_weight_kg.
"""
import logging
from app import app, db
from sqlalchemy import text, inspect

def update_packing_profile_schema():
    """
    Add pack_mode related columns to wms_packing_profile table.
    Safe for both SQLite and PostgreSQL.
    """
    try:
        with app.app_context():
            inspector = inspect(db.engine)
            
            if 'wms_packing_profile' not in inspector.get_table_names():
                logging.info("wms_packing_profile table does not exist yet, will be created by db.create_all()")
                return True, "Table will be created by ORM"
            
            columns = [col['name'] for col in inspector.get_columns('wms_packing_profile')]
            
            new_columns = [
                ("pack_mode", "VARCHAR(20)"),
                ("loss_risk", "BOOLEAN"),
                ("carton_type_hint", "VARCHAR(10)"),
                ("max_carton_weight_kg", "NUMERIC(10,2)")
            ]
            
            for col_name, col_type in new_columns:
                if col_name not in columns:
                    try:
                        db.session.execute(text(f'ALTER TABLE wms_packing_profile ADD COLUMN {col_name} {col_type}'))
                        db.session.commit()
                        logging.info(f"Added {col_name} column to wms_packing_profile table")
                    except Exception as e:
                        db.session.rollback()
                        logging.warning(f"Could not add {col_name}: {e}")
            
            return True, "wms_packing_profile schema updated successfully"
    except Exception as e:
        logging.error(f"Error updating wms_packing_profile schema: {str(e)}")
        return False, f"Error: {str(e)}"

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success, message = update_packing_profile_schema()
    print(message)
