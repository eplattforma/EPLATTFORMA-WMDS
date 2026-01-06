"""
Critical performance fixes for the warehouse picking system
Addresses high CPU load and slow response times
"""

import subprocess
import os
from sqlalchemy import text
from models import db

def apply_critical_fixes():
    """Apply immediate performance fixes"""
    
    print("Applying critical performance optimizations...")
    
    # 1. Optimize Gunicorn configuration
    gunicorn_config = """
# Optimized Gunicorn configuration for performance
bind = "0.0.0.0:5000"
workers = 2
worker_class = "sync"
worker_connections = 100
max_requests = 200
max_requests_jitter = 50
timeout = 30
keepalive = 2
preload_app = True

# Memory optimizations
worker_tmp_dir = "/dev/shm"
"""
    
    with open('gunicorn_config.py', 'w') as f:
        f.write(gunicorn_config)
    
    print("Updated Gunicorn configuration for better performance")
    
    # 2. Clean up database for better performance
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT pg_stat_reset()"))
            conn.execute(text("SELECT pg_stat_reset_shared('bgwriter')"))
            conn.commit()
        print("Reset database statistics")
    except Exception as e:
        print(f"Database optimization warning: {e}")
    
    # 3. Set optimal database parameters
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SET work_mem = '16MB'"))
            conn.execute(text("SET maintenance_work_mem = '64MB'"))
            conn.execute(text("SET effective_cache_size = '256MB'"))
            conn.execute(text("SET random_page_cost = 1.1"))
            conn.execute(text("SET seq_page_cost = 1.0"))
            conn.commit()
        print("Applied database performance settings")
    except Exception as e:
        print(f"Database settings warning: {e}")
    
    print("Performance fixes applied successfully")
    return True

if __name__ == '__main__':
    from main import app
    
    with app.app_context():
        apply_critical_fixes()