"""SQLite connection helper: foreign keys always on, commit/rollback handled."""
import sqlite3
from contextlib import contextmanager


@contextmanager
def connect(path: str):
    conn = sqlite3.connect(path, timeout=15.0)  # timeout avoids "database is locked"
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
