from datetime import datetime, timezone
from sqlalchemy.types import TypeDecorator, DateTime

class UTCDateTime(TypeDecorator):
    """
    Stores UTC timestamps.
    - Postgres: stores timezone-aware UTC datetimes
    - SQLite: stores naive UTC datetimes (no tz support), returns aware UTC on read
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None

        # Treat naive as UTC
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        value = value.astimezone(timezone.utc)

        # SQLite can't store tz-aware reliably; store as naive UTC
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)

        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None

        # SQLite returns naive; interpret as UTC
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)
