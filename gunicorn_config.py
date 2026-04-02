# Production-Ready Gunicorn Configuration
import os

# Worker configuration
bind = "0.0.0.0:5000"
workers = 2                 # 2 workers — enough headroom for scheduler + user traffic
threads = 6                 # 6 threads per worker for better concurrency
worker_class = "gthread"    # Threaded worker class
max_requests = 2000         # Recycle worker after 2000 requests to prevent memory leaks
max_requests_jitter = 100   # Add randomness to recycling

# Timeout settings - extended for large PO downloads with many items
timeout = 120               # 2 minutes — tighter than before; keeps workers healthier
keepalive = 5               # Keep connections alive longer for efficiency
graceful_timeout = 30       # Allow graceful shutdown

# Logging - enable for production debugging
loglevel = "info"           # Log info and above for debugging
accesslog = "-"             # Log to stdout for Replit logs
errorlog = "-"              # Log errors to stdout
capture_output = True       # Capture print statements
enable_stdio_inheritance = True

# Performance optimizations
preload_app = False         # Disabled: app loads inside worker AFTER port bind (required for Cloud Run health checks)
worker_tmp_dir = "/dev/shm"
sendfile = True             # Use sendfile for static files
tcp_nodelay = True          # Disable Nagle's algorithm for faster responses

def post_fork(server, worker):
    if worker.age == 1:
        os.environ["SCHEDULER_WORKER"] = "1"
    else:
        os.environ["SCHEDULER_WORKER"] = "0"

print(f"Production config: {workers} workers, {threads} threads/worker, {timeout}s timeout, preload disabled")
