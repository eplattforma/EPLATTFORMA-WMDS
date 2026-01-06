#!/usr/bin/env python3
"""
Database migration script to add corridor column and populate it from location data
"""

import sys
import os
import re
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the current directory to the path so we can import our models
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def extract_corridor_from_location(location):
    """
    Extract corridor from location string (e.g., "10-05-A01" -> "10")
    Returns corridor with leading zeros if needed (e.g., "9" -> "09")
    """
    if not location:
        return None
    
    # Split by dash and take the first part as corridor
    parts = location.strip().split('-')
    if len(parts) >= 1:
        corridor = parts[0].strip()
        
        # Add leading zero if single digit
        if corridor.isdigit() and len(corridor) == 1:
            corridor = "0" + corridor
            
        return corridor
    
    return None

def update_corridor_schema():
    """Add corridor column and populate it from existing location data"""
    
    # Get database URL from environment
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("❌ DATABASE_URL environment variable not found")
        return False
    
    session = None
    try:
        # Create engine and session
        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        print("🔄 Starting corridor column migration...")
        
        # Check if corridor column already exists
        result = session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'invoice_items' AND column_name = 'corridor'
        """))
        
        if result.fetchone():
            print("✅ Corridor column already exists")
        else:
            # Add corridor column
            print("➕ Adding corridor column...")
            session.execute(text("""
                ALTER TABLE invoice_items 
                ADD COLUMN corridor VARCHAR(10)
            """))
            session.commit()
            print("✅ Corridor column added successfully")
        
        # Get all items that need corridor data
        result = session.execute(text("""
            SELECT invoice_no, item_code, location 
            FROM invoice_items 
            WHERE location IS NOT NULL AND location != ''
        """))
        
        items = result.fetchall()
        print(f"🔄 Processing {len(items)} items to extract corridor data...")
        
        updated_count = 0
        
        for item in items:
            invoice_no, item_code, location = item
            corridor = extract_corridor_from_location(location)
            
            if corridor:
                session.execute(text("""
                    UPDATE invoice_items 
                    SET corridor = :corridor 
                    WHERE invoice_no = :invoice_no AND item_code = :item_code
                """), {
                    'corridor': corridor,
                    'invoice_no': invoice_no,
                    'item_code': item_code
                })
                updated_count += 1
        
        session.commit()
        print(f"✅ Updated corridor data for {updated_count} items")
        
        # Show sample of corridor data
        result = session.execute(text("""
            SELECT DISTINCT corridor, COUNT(*) as count 
            FROM invoice_items 
            WHERE corridor IS NOT NULL 
            GROUP BY corridor 
            ORDER BY corridor
        """))
        
        corridors = result.fetchall()
        corridor_dict = {row[0]: row[1] for row in corridors}
        print(f"📊 Found corridors: {corridor_dict}")
        
        session.close()
        print("✅ Corridor schema migration completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error during corridor migration: {e}")
        if session is not None:
            session.rollback()
            session.close()
        return False

if __name__ == "__main__":
    update_corridor_schema()