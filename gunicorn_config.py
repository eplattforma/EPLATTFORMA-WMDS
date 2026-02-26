# Production-Ready Gunicorn Configuration
import os

is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"

# Worker configuration
bind = "0.0.0.0:5000"
workers = 2 if is_production else 1
worker_class = "sync"
worker_connections = 100
max_requests = 500
max_requests_jitter = 50

# Timeout settings - extended for large PO downloads with many items
timeout = 300
keepalive = 5
graceful_timeout = 30

# Logging
loglevel = "warning"
accesslog = "-"
errorlog = "-"
capture_output = True
enable_stdio_inheritance = True

# Performance optimizations
preload_app = True
reuse_port = True
worker_tmp_dir = "/dev/shm"
sendfile = True
tcp_nodelay = True

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

env_label = "Production" if is_production else "Development"
print(f"{env_label} config: {workers} worker(s), {timeout}s timeout")
