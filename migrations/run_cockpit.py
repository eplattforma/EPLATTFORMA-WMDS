"""CLI entry point for cockpit migrations (cockpit-brief Section 10.2).

Delegates to ``migrations.cockpit_schema.ensure_cockpit_schema``. Idempotent.
"""
import logging

from migrations.cockpit_schema import ensure_cockpit_schema


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from app import app
    with app.app_context():
        ensure_cockpit_schema()
