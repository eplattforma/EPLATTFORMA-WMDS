#!/usr/bin/env python3
"""
Batch Payment Terms Update Script
Updates customer payment terms in batches of 300 from an Excel file
"""
import sys
import pandas as pd
from app import app, db
from routes_payment_terms import process_import

def batch_update(file_path, batch_size=300, start_row=0, dry_run=False):
    """
    Process payment terms updates in batches
    
    Args:
        file_path: Path to Excel file
        batch_size: Number of rows to process per batch (default 300)
        start_row: Starting row number (0-indexed, default 0)
        dry_run: If True, only preview changes without applying
    """
    with app.app_context():
        # Read the entire Excel file
        df = pd.read_excel(file_path)
        total_rows = len(df)
        
        print(f"Total rows in file: {total_rows}")
        print(f"Batch size: {batch_size}")
        print(f"Starting from row: {start_row}")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE UPDATE'}")
        print("-" * 60)
        
        # Calculate batches
        batch_num = (start_row // batch_size) + 1
        end_row = min(start_row + batch_size, total_rows)
        
        # Process the current batch
        batch_df = df.iloc[start_row:end_row]
        
        print(f"\nProcessing Batch {batch_num}:")
        print(f"  Rows {start_row + 1} to {end_row} (of {total_rows})")
        print(f"  Customers in this batch: {len(batch_df)}")
        
        # Convert DataFrame back to Excel in memory
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            batch_df.to_excel(writer, index=False, sheet_name="CreditTerms")
        buf.seek(0)
        
        # Process the batch
        result = process_import(buf, dry_run=dry_run)
        
        print("\nResults:")
        print(f"  Status: {result['status']}")
        print(f"  Rows processed: {result['rows_processed']}")
        print(f"  Rows updated: {result['rows_updated']}")
        print(f"  Rows skipped: {result['rows_skipped']}")
        print(f"  New terms created: {result['created_terms']}")
        print(f"  Versions closed: {result['closed_versions']}")
        
        if result.get('skipped_codes'):
            print(f"  Skipped codes: {', '.join(result['skipped_codes'][:10])}")
        
        # Show progress
        print(f"\nProgress: {end_row}/{total_rows} rows processed ({end_row*100//total_rows}%)")
        
        if end_row < total_rows:
            next_batch = (end_row // batch_size) + 1
            print(f"\nNext batch command:")
            print(f"  python batch_update_customers.py {file_path} --batch {batch_size} --start {end_row} {'--dry-run' if dry_run else ''}")
        else:
            print("\n✅ All batches completed!")
        
        return result

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Batch update customer payment terms')
    parser.add_argument('file', help='Path to Excel file')
    parser.add_argument('--batch', type=int, default=300, help='Batch size (default: 300)')
    parser.add_argument('--start', type=int, default=0, help='Starting row number (default: 0)')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying')
    
    args = parser.parse_args()
    
    try:
        batch_update(args.file, batch_size=args.batch, start_row=args.start, dry_run=args.dry_run)
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)
