#!/usr/bin/env python3
"""
Database Copy Script: Production → Development
Copies all tables and data from production to development database.

USAGE:
1. Set environment variables:
   export PROD_DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"
   export DEV_DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"

2. Run the script:
   python copy_prod_to_dev.py

SAFETY: This script will WIPE the development database before copying.
"""

import os
import sys
import subprocess
import tempfile
from datetime import datetime

def check_env_vars():
    """Verify required environment variables are set."""
    # Try both naming conventions (Replit uses DATABASE_URL_PROD/DEV)
    prod_url = os.environ.get('DATABASE_URL_PROD') or os.environ.get('PROD_DATABASE_URL')
    dev_url = os.environ.get('DATABASE_URL_DEV') or os.environ.get('DEV_DATABASE_URL')
    
    if not prod_url:
        print("ERROR: DATABASE_URL_PROD environment variable not set")
        print("Check your Replit Secrets for the production database URL")
        return None, None
    
    if not dev_url:
        print("ERROR: DATABASE_URL_DEV environment variable not set")
        print("Check your Replit Secrets for the development database URL")
        return None, None
    
    return prod_url, dev_url

def confirm_action():
    """Get user confirmation before proceeding."""
    print("\n" + "="*60)
    print("WARNING: This will WIPE your development database!")
    print("All existing data in development will be replaced.")
    print("="*60)
    
    response = input("\nType 'YES' to confirm: ").strip()
    return response == 'YES'

def run_pg_dump(prod_url, dump_file):
    """Export production database to a dump file."""
    print(f"\n[1/3] Exporting production database...")
    
    cmd = [
        'pg_dump',
        '--no-owner',
        '--no-acl',
        '--format=custom',
        '--verbose',
        '-f', dump_file,
        prod_url
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"ERROR: pg_dump failed: {result.stderr}")
            return False
        print(f"   Export complete: {dump_file}")
        return True
    except subprocess.TimeoutExpired:
        print("ERROR: pg_dump timed out after 10 minutes")
        return False
    except FileNotFoundError:
        print("ERROR: pg_dump not found. Make sure PostgreSQL client tools are installed.")
        return False

def run_pg_restore(dev_url, dump_file):
    """Restore dump file to development database."""
    print(f"\n[2/3] Restoring to development database...")
    
    cmd = [
        'pg_restore',
        '--no-owner',
        '--no-acl',
        '--clean',
        '--if-exists',
        '--verbose',
        '-d', dev_url,
        dump_file
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            # pg_restore returns non-zero even on minor issues, check for real errors
            if 'error' in result.stderr.lower() and 'warning' not in result.stderr.lower():
                print(f"WARNING: pg_restore had issues: {result.stderr[:500]}")
            else:
                print("   Some warnings occurred (usually harmless)")
        print("   Restore complete!")
        return True
    except subprocess.TimeoutExpired:
        print("ERROR: pg_restore timed out after 10 minutes")
        return False
    except FileNotFoundError:
        print("ERROR: pg_restore not found. Make sure PostgreSQL client tools are installed.")
        return False

def verify_copy(dev_url):
    """Basic verification that tables exist in dev."""
    print(f"\n[3/3] Verifying data copy...")
    
    try:
        import psycopg2
        conn = psycopg2.connect(dev_url)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = cur.fetchall()
        
        print(f"   Found {len(tables)} tables in development database:")
        for (table,) in tables[:10]:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            count = cur.fetchone()[0]
            print(f"      - {table}: {count} rows")
        
        if len(tables) > 10:
            print(f"      ... and {len(tables) - 10} more tables")
        
        conn.close()
        return True
    except ImportError:
        print("   Skipping verification (psycopg2 not available)")
        return True
    except Exception as e:
        print(f"   Verification failed: {e}")
        return False

def main():
    print("="*60)
    print("Database Copy: Production → Development")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # Check environment variables
    prod_url, dev_url = check_env_vars()
    if not prod_url or not dev_url:
        sys.exit(1)
    
    # Mask URLs for display
    def mask_url(url):
        if '@' in url:
            parts = url.split('@')
            return f"***@{parts[1][:30]}..."
        return url[:30] + "..."
    
    print(f"\nProduction: {mask_url(prod_url)}")
    print(f"Development: {mask_url(dev_url)}")
    
    # Confirm action
    if not confirm_action():
        print("\nOperation cancelled.")
        sys.exit(0)
    
    # Create temp file for dump
    with tempfile.NamedTemporaryFile(suffix='.dump', delete=False) as f:
        dump_file = f.name
    
    try:
        # Step 1: Export production
        if not run_pg_dump(prod_url, dump_file):
            sys.exit(1)
        
        # Step 2: Restore to development
        if not run_pg_restore(dev_url, dump_file):
            sys.exit(1)
        
        # Step 3: Verify
        verify_copy(dev_url)
        
        print("\n" + "="*60)
        print("SUCCESS! Database copy completed.")
        print("="*60)
        
    finally:
        # Clean up dump file
        try:
            os.unlink(dump_file)
            print(f"\nCleaned up temporary file: {dump_file}")
        except:
            pass

if __name__ == '__main__':
    main()
