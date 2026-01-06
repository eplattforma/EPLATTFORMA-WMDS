#!/usr/bin/env python3
"""
Test script to verify corridor extraction during import
"""
import sys
sys.path.append('.')

from import_handler import extract_corridor_from_location
from models import InvoiceItem
from app import app, db

def test_corridor_extraction():
    """Test the corridor extraction function"""
    print("Testing corridor extraction function...")
    
    test_cases = [
        "40-02-A01",
        "11-01-B01", 
        "10-05-C05",
        "14-05-C04",
        "9-01-A01",
        "5-03-B02"
    ]
    
    for location in test_cases:
        corridor = extract_corridor_from_location(location)
        print(f"Location: {location} -> Corridor: {corridor}")

def test_database_assignment():
    """Test database assignment of corridor data"""
    print("\nTesting database assignment...")
    
    with app.app_context():
        # Create a test item
        test_item = InvoiceItem()
        test_item.invoice_no = "TEST_CORRIDOR"
        test_item.item_code = "TEST_ITEM"
        test_item.location = "40-02-A01"
        test_item.corridor = extract_corridor_from_location(test_item.location)
        
        print(f"Created item: location={test_item.location}, corridor={test_item.corridor}")
        
        # Add to database
        db.session.add(test_item)
        db.session.commit()
        
        # Query back from database
        saved_item = db.session.query(InvoiceItem).filter_by(invoice_no="TEST_CORRIDOR").first()
        if saved_item:
            print(f"Saved to DB: location={saved_item.location}, corridor={saved_item.corridor}")
            
            # Clean up
            db.session.delete(saved_item)
            db.session.commit()
            print("Test item cleaned up")
        else:
            print("ERROR: Item not found in database")

if __name__ == "__main__":
    test_corridor_extraction()
    test_database_assignment()