# High Performance Gunicorn Configuration
import os
import multiprocessing

# Server socket
bind = "0.0.0.0:5000"
backlog = 512

# Worker processes - optimized for current load
workers = 2  # Increased from 1 for better performance
worker_class = "sync"
worker_connections = 20  # Increased capacity
max_requests = 100  # Increased from 15
max_requests_jitter = 10

# Optimized timeouts
timeout = 15  # Increased from 6
keepalive = 2
graceful_timeout = 10

# Optimized logging
loglevel = "warning"  # Reduced logging overhead
accesslog = None
errorlog = "/tmp/gunicorn_error.log"
capture_output = False
enable_stdio_inheritance = False

# Performance optimizations
preload_app = True
reuse_port = True
worker_tmp_dir = "/dev/shm"

# Memory and process optimizations
max_requests_jitter = max_requests // 10
worker_max_requests = max_requests

print(f"Performance config: {workers} workers, {worker_connections} connections")
