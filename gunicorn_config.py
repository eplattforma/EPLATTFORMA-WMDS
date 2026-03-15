# Production-Ready Gunicorn Configuration
import os

# Worker configuration - single worker to reduce memory usage in Replit
bind = "0.0.0.0:5000"
workers = 1                 # Single worker to stay within memory limits
threads = 4                 # Use threads for concurrency instead of extra workers
worker_class = "gthread"    # Threaded worker class
max_requests = 1000         # Recycle worker after 1000 requests to prevent memory leaks
max_requests_jitter = 50    # Add randomness to recycling

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
preload_app = False         # Disabled: app loads inside worker AFTER port bind (required for Cloud Run health checks)
worker_tmp_dir = "/dev/shm"
sendfile = True             # Use sendfile for static files
tcp_nodelay = True          # Disable Nagle's algorithm for faster responses

print("Production config: 1 worker, 4 threads, 300s timeout, preload disabled (fast port bind)")
