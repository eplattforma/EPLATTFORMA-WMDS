"""
Fix route status case inconsistency - standardize to UPPERCASE
"""
from app import app, db
from models import Shipment

def fix_route_status_case():
    """Update all lowercase 'completed' statuses to uppercase 'COMPLETED'"""
    with app.app_context():
        # Find all routes with lowercase 'completed'
        routes_to_fix = Shipment.query.filter_by(status='completed').all()
        
        count = len(routes_to_fix)
        print(f"Found {count} routes with lowercase 'completed' status")
        
        if count > 0:
            # Update to uppercase
            for route in routes_to_fix:
                route.status = 'COMPLETED'
            
            db.session.commit()
            print(f"✅ Updated {count} routes to uppercase 'COMPLETED'")
        else:
            print("No routes to update")
        
        # Verify final status distribution
        print("\nFinal status distribution:")
        from sqlalchemy import func
        status_counts = db.session.query(
            Shipment.status,
            func.count(Shipment.id)
        ).group_by(Shipment.status).all()
        
        for status, count in status_counts:
            print(f"  {status}: {count}")

if __name__ == '__main__':
    fix_route_status_case()
