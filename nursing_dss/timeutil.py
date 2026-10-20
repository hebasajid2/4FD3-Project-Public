"""UTC time helpers. All timestamps are stored as fixed-width ISO-8601 strings
so that plain string comparison in SQL orders them correctly."""
from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Fixed-width ISO string in UTC, e.g. 2026-10-19T20:15:03.123456+00:00."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)
