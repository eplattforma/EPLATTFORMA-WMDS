#!/bin/bash
# Production-ready server start script
# Uses optimized gunicorn config with single worker
exec gunicorn -c gunicorn_config.py main:app
