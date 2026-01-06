"""
Update Settings table schema to support larger content
Changes the 'value' column from VARCHAR(500) to TEXT
"""
import logging
from app import app, db
from models import Setting

def update_settings_schema():
    """Update the settings table to support larger content"""
    with app.app_context():
        try:
            # Check if the column needs updating by trying to create a long value
            test_setting = Setting.query.filter_by(key='schema_test').first()
            if test_setting:
                db.session.delete(test_setting)
                db.session.commit()
            
            # Create a test setting with long content
            long_content = "x" * 1000  # 1000 characters
            test_setting = Setting(key='schema_test', value=long_content)
            db.session.add(test_setting)
            db.session.commit()
            
            # Clean up test setting
            db.session.delete(test_setting)
            db.session.commit()
            
            logging.info("✅ Settings schema is already updated to support TEXT")
            
        except Exception as e:
            db.session.rollback()
            logging.info(f"Settings schema needs updating: {e}")
            
            try:
                # Update the column type to TEXT using newer SQLAlchemy syntax
                with db.engine.connect() as connection:
                    connection.execute(db.text('ALTER TABLE settings ALTER COLUMN value TYPE TEXT;'))
                    connection.commit()
                logging.info("✅ Settings schema updated successfully - value column is now TEXT")
                
            except Exception as alter_error:
                logging.error(f"❌ Failed to update settings schema: {alter_error}")
                raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_settings_schema()