# Production-Ready Gunicorn Configuration
import os

# Worker configuration
bind = "0.0.0.0:5000"
reuse_port = True           # Required by autoscale (Cloud Run) for fast worker handoff
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
preload_app = True          # Load app once in master before workers fork — workers are
                            # immediately ready to serve (health probe passes right away).
                            # Without this, each worker runs ~90s of schema migrations
                            # before accepting requests, causing Cloud Run health checks
                            # to time out and the deployment to fail.
worker_tmp_dir = "/dev/shm"
sendfile = True             # Use sendfile for static files
tcp_nodelay = True          # Disable Nagle's algorithm for faster responses

def post_fork(server, worker):
    """Re-establish DB connections per worker after fork.

    With preload_app=True the SQLAlchemy engine is created in the master
    process.  Forked workers must dispose the inherited pool so they get
    their own fresh connections instead of sharing the master's file
    descriptors (which leads to silent corruption or connection errors).
    """
    os.environ["GUNICORN_WORKER_AGE"] = str(worker.age)
    try:
        from app import db
        db.engine.dispose()
    except Exception:
        pass  # If app hasn't initialised yet, nothing to dispose

print(f"Production config: {workers} workers, {threads} threads/worker, {timeout}s timeout, preload enabled")
