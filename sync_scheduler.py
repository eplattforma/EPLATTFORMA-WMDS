#!/usr/bin/env python3
"""
Data Warehouse Sync Scheduler
Run this as a scheduled job to automatically sync data from PS365
Usage: python sync_scheduler.py [full|incremental]
"""

import sys
import os
import logging
from datetime import datetime
from pathlib import Path

# Setup logging
os.makedirs('logs', exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = f'logs/sync_{timestamp}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()  # Also print to console for monitoring
    ]
)

logger = logging.getLogger(__name__)

def run_full_sync():
    """Run full data warehouse sync"""
    logger.info("="*80)
    logger.info("STARTING FULL DATA WAREHOUSE SYNC")
    logger.info(f"Timestamp: {datetime.now().isoformat()}")
    logger.info("="*80)
    
    try:
        from app import app, db
        from datawarehouse_sync import full_dw_sync
        
        with app.app_context():
            result = full_dw_sync(db.session)
            logger.info("="*80)
            logger.info("✅ FULL SYNC COMPLETED SUCCESSFULLY")
            logger.info(f"Timestamp: {datetime.now().isoformat()}")
            logger.info("="*80)
            return True
            
    except Exception as e:
        logger.error("="*80)
        logger.error(f"❌ FULL SYNC FAILED")
        logger.error(f"Error: {str(e)}")
        logger.error("="*80, exc_info=True)
        return False

def run_incremental_sync():
    """Run incremental data warehouse sync"""
    logger.info("="*80)
    logger.info("STARTING INCREMENTAL DATA WAREHOUSE SYNC")
    logger.info(f"Timestamp: {datetime.now().isoformat()}")
    logger.info("="*80)
    
    try:
        from app import app, db
        from datawarehouse_sync import incremental_dw_update
        
        with app.app_context():
            incremental_dw_update(db.session)
            logger.info("="*80)
            logger.info("✅ INCREMENTAL SYNC COMPLETED SUCCESSFULLY")
            logger.info(f"Timestamp: {datetime.now().isoformat()}")
            logger.info("="*80)
            return True
            
    except Exception as e:
        logger.error("="*80)
        logger.error(f"❌ INCREMENTAL SYNC FAILED")
        logger.error(f"Error: {str(e)}")
        logger.error("="*80, exc_info=True)
        return False

if __name__ == '__main__':
    sync_type = sys.argv[1].lower() if len(sys.argv) > 1 else 'incremental'
    
    if sync_type == 'full':
        success = run_full_sync()
    elif sync_type == 'incremental':
        success = run_incremental_sync()
    else:
        logger.error(f"Unknown sync type: {sync_type}. Use 'full' or 'incremental'")
        sys.exit(1)
    
    sys.exit(0 if success else 1)
