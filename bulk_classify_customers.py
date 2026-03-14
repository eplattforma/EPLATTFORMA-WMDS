#!/usr/bin/env python3
"""Bulk classify customers from a tab-separated file."""

import sys
from datetime import datetime, timezone
from app import app, db
from models import CrmCustomerProfile

def bulk_classify_from_file(filename):
    """Import classifications from file (tab or comma separated)."""
    updated = 0
    errors = []
    
    with app.app_context():
        with open(filename, 'r') as f:
            lines = f.readlines()
        
        for line_no, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#') or 'Code' in line:
                continue
            
            # Try tab first, then comma
            parts = [p.strip() for p in line.split('\t') if p.strip()]
            if len(parts) < 2:
                parts = [p.strip() for p in line.split(',') if p.strip()]
            
            if len(parts) < 2:
                errors.append(f"Line {line_no}: Expected 2 columns, got {len(parts)}")
                continue
            
            customer_code = parts[0]
            classification = parts[1]
            
            try:
                prof = CrmCustomerProfile.query.get(customer_code)
                if not prof:
                    prof = CrmCustomerProfile(customer_code_365=customer_code)
                    db.session.add(prof)
                
                prof.classification = classification if classification else None
                prof.updated_by = "bulk_import_script"
                prof.updated_at = datetime.now(timezone.utc)
                updated += 1
                
                if updated % 100 == 0:
                    print(f"  Processed {updated} records...")
            except Exception as e:
                errors.append(f"Line {line_no} ({customer_code}): {str(e)}")
        
        try:
            db.session.commit()
            print(f"\n✓ Successfully updated {updated} customer classifications")
            if errors:
                print(f"⚠ {len(errors)} errors encountered:")
                for err in errors[:10]:
                    print(f"  - {err}")
                if len(errors) > 10:
                    print(f"  ... and {len(errors) - 10} more")
        except Exception as e:
            print(f"✗ Commit failed: {e}")
            db.session.rollback()
            return False
    
    return True

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python bulk_classify_customers.py <filename>")
        sys.exit(1)
    
    filename = sys.argv[1]
    print(f"Importing classifications from {filename}...")
    success = bulk_classify_from_file(filename)
    sys.exit(0 if success else 1)
