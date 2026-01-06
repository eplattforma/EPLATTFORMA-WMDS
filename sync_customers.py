#!/usr/bin/env python3
"""
Standalone script to sync customer data from PS365
Can be run from command line without starting the web server

Usage:
    python sync_customers.py

Requirements:
    - PS365_BASE_URL must be set in environment/secrets
    - PS365_TOKEN must be set in environment/secrets
"""
import os
import sys

# Set timezone before importing app
os.environ['TZ'] = 'Europe/Athens'

from app import app, db
from services_powersoft import sync_active_customers

def main():
    """Run the customer sync process"""
    print("=" * 60)
    print("PS365 Customer Sync - Starting...")
    print("=" * 60)
    
    # Check environment variables
    if not os.getenv('PS365_BASE_URL'):
        print("ERROR: PS365_BASE_URL not set in environment")
        sys.exit(1)
    
    if not os.getenv('PS365_TOKEN'):
        print("ERROR: PS365_TOKEN not set in environment")
        sys.exit(1)
    
    print(f"API URL: {os.getenv('PS365_BASE_URL')}")
    print("")
    
    with app.app_context():
        try:
            # Run the sync
            result = sync_active_customers()
            
            # Display results
            print("")
            print("=" * 60)
            print("Sync Completed Successfully!")
            print("=" * 60)
            print(f"Total Customers: {result['total_customers']}")
            print(f"Total Pages: {result['total_pages']}")
            print(f"Records Updated: {result['updated_count']}")
            print("")
            
            # Verify database
            from models import PSCustomer
            db_count = PSCustomer.query.count()
            active_count = PSCustomer.query.filter_by(active=True).count()
            
            print("Database Status:")
            print(f"  Total customers in DB: {db_count}")
            print(f"  Active customers: {active_count}")
            print(f"  Inactive customers: {db_count - active_count}")
            print("")
            
            return 0
            
        except Exception as e:
            print("")
            print("=" * 60)
            print("ERROR: Sync Failed")
            print("=" * 60)
            print(f"Error: {str(e)}")
            print("")
            import traceback
            traceback.print_exc()
            return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
