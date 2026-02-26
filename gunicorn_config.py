# Production-Ready Gunicorn Configuration
import os

# Worker configuration - 2 workers prevents single-worker blocking
bind = "0.0.0.0:5000"
workers = 2                 # Multiple workers prevent blocking during long operations
worker_class = "sync"
worker_connections = 100    # Allow more concurrent connections per worker
max_requests = 500          # Recycle workers after 500 requests to prevent memory leaks
max_requests_jitter = 50    # Add randomness to prevent all workers recycling at once

# Timeout settings - extended for large PO downloads with many items
timeout = 300               # 5 minutes - allows for large PO downloads with many items
keepalive = 5               # Keep connections alive longer for efficiency
graceful_timeout = 30       # Allow graceful shutdown

# Logging - enable for production debugging
loglevel = "warning"        # Log warnings and errors
accesslog = "-"             # Log to stdout for Replit logs
errorlog = "-"              # Log errors to stdout
capture_output = True       # Capture print statements
enable_stdio_inheritance = True

# Performance optimizations
preload_app = True          # Load app once before forking (faster startup)
reuse_port = True
worker_tmp_dir = "/dev/shm"
sendfile = True             # Use sendfile for static files
tcp_nodelay = True          # Disable Nagle's algorithm for faster responses

import os

_scheduler_started = False

def post_fork(server, worker):
    global _scheduler_started
    os.environ["GUNICORN_WORKER"] = "1"
    if not _scheduler_started:
        _scheduler_started = True
        try:
            from scheduler import setup_scheduler
            from app import app
            setup_scheduler(app)
            server.log.info("Background scheduler started in worker %s", worker.pid)
        except Exception as e:
            server.log.warning("Could not start scheduler: %s", e)

print("Production config: 2 workers, 120s timeout, logging enabled")
