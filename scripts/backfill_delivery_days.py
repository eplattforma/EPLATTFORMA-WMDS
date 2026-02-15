from app import app, db
from models import PSCustomer, CustomerDeliverySlot
from services.delivery_days import parse_delivery_days_strict
from datetime import datetime
import json

def backfill_delivery_days():
    with app.app_context():
        customers = PSCustomer.query.all()
        print(f"Starting backfill for {len(customers)} customers...")
        
        count = 0
        for customer in customers:
            parsed = parse_delivery_days_strict(customer.delivery_days)
            
            customer.delivery_days_status = parsed["status"]
            customer.delivery_days_invalid_tokens = json.dumps(parsed["invalid"]) if parsed["invalid"] else None
            customer.delivery_days_parsed_at = datetime.utcnow()
            
            # Clear old slots
            CustomerDeliverySlot.query.filter_by(customer_code_365=customer.customer_code_365).delete()
            
            if parsed["status"] == "OK":
                for dow, wk in parsed["slots"]:
                    db.session.add(CustomerDeliverySlot(
                        customer_code_365=customer.customer_code_365,
                        dow=dow,
                        week_code=wk
                    ))
            
            count += 1
            if count % 100 == 0:
                db.session.commit()
                print(f"Processed {count} customers...")
        
        db.session.commit()
        print("Backfill completed successfully.")

if __name__ == "__main__":
    backfill_delivery_days()
