#!/usr/bin/env python3
"""
One-time fix: Correct CODInvoiceAllocation #103 for IN10053466
The clear_pending_payment function overwrote the actual received_amount (417.01)
with the theoretical due amount (452.33 - 27.03 = 425.30).
This script restores it to match the CODReceipt's actual received amount.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import CODInvoiceAllocation, CODReceipt
from decimal import Decimal

with app.app_context():
    alloc = db.session.get(CODInvoiceAllocation, 103)
    if not alloc:
        print("Allocation 103 not found")
        sys.exit(1)
    
    receipt = db.session.get(CODReceipt, alloc.cod_receipt_id)
    if not receipt:
        print("Receipt not found")
        sys.exit(1)
    
    print(f"Invoice: {alloc.invoice_no}")
    print(f"Allocation received_amount: {alloc.received_amount}")
    print(f"Receipt received_amount:    {receipt.received_amount}")
    print(f"Allocation deduct_amount:   {alloc.deduct_amount}")
    
    if alloc.received_amount != receipt.received_amount:
        old = alloc.received_amount
        alloc.received_amount = receipt.received_amount
        db.session.commit()
        print(f"\nFIXED: {old} -> {receipt.received_amount}")
    else:
        print("\nNo fix needed - amounts already match.")
