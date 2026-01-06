# Production-Ready Gunicorn Configuration
import os

# Worker configuration - 2 workers prevents single-worker blocking
bind = "0.0.0.0:5000"
workers = 2                 # Multiple workers prevent blocking during long operations
worker_class = "sync"
worker_connections = 100    # Allow more concurrent connections per worker
max_requests = 500          # Recycle workers after 500 requests to prevent memory leaks
max_requests_jitter = 50    # Add randomness to prevent all workers recycling at once

# Timeout settings - increased for database clone operations
timeout = 900               # 15 minutes - allows for full database clone operations
keepalive = 5               # Keep connections alive longer for efficiency
graceful_timeout = 30       # Allow graceful shutdown

# Logging - enable for production debugging
loglevel = "warning"        # Log warnings and errors
accesslog = "-"             # Log to stdout for Replit logs
errorlog = "-"              # Log errors to stdout
capture_output = True       # Capture print statements
enable_stdio_inheritance = True

# Performance optimizations
preload_app = False         # Load app per worker for faster deployment
reuse_port = True
worker_tmp_dir = "/dev/shm"
sendfile = True             # Use sendfile for static files
tcp_nodelay = True          # Disable Nagle's algorithm for faster responses

print("Production config: 2 workers, 900s timeout, logging enabled")
