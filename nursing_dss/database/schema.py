"""Database schema (the 8 tables from the design document) plus a light
migration so a database created by the original Colab notebook can be reused.

Differences from the original notebook schema (all additive, see README):
  * Nurses: available, mobility_restricted
  * Alerts: acknowledged_at, parent_alert_id, CHECK on status values
  * Notification_Logs: sent_at, latency_ms, detail
  * UNIQUE(unit_id, room_number), UNIQUE(room_id, bed_number), UNIQUE(unit_name)
  * Partial unique indexes: only one *active* assignment per room / per bed
  * ON DELETE CASCADE on every child table so deleting a bed/room works
"""
import sqlite3

from .. import config

_STATUSES = ", ".join(f"'{s}'" for s in config.ALERT_STATUSES)
_QUALS = ", ".join(f"'{q}'" for q in config.QUALIFICATIONS)

TABLES_SQL = f"""
CREATE TABLE IF NOT EXISTS Units (
    unit_id   INTEGER PRIMARY KEY,
    unit_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS Rooms (
    room_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT NOT NULL,
    unit_id     INTEGER NOT NULL,
    x_coord     REAL NOT NULL DEFAULT 0.0,
    y_coord     REAL NOT NULL DEFAULT 0.0,
    UNIQUE (unit_id, room_number),
    FOREIGN KEY (unit_id) REFERENCES Units(unit_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS Beds (
    bed_id     INTEGER PRIMARY KEY,
    bed_number TEXT NOT NULL,
    room_id    INTEGER NOT NULL,
    UNIQUE (room_id, bed_number),
    FOREIGN KEY (room_id) REFERENCES Rooms(room_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS Patients (
    patient_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bed_id              INTEGER UNIQUE NOT NULL,
    acuity_score        INTEGER NOT NULL CHECK (acuity_score BETWEEN 1 AND 5),
    admission_timestamp TEXT NOT NULL,
    FOREIGN KEY (bed_id) REFERENCES Beds(bed_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS Nurses (
    nurse_id            INTEGER PRIMARY KEY,
    nurse_name          TEXT NOT NULL,
    contact             TEXT,
    qualification_level TEXT NOT NULL DEFAULT 'RN' CHECK (qualification_level IN ({_QUALS})),
    max_workload        INTEGER NOT NULL DEFAULT 12 CHECK (max_workload > 0),
    available           INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0, 1)),
    mobility_restricted INTEGER NOT NULL DEFAULT 0 CHECK (mobility_restricted IN (0, 1))
);

CREATE TABLE IF NOT EXISTS Call_Bells (
    bell_id INTEGER PRIMARY KEY,
    bed_id  INTEGER NOT NULL,
    FOREIGN KEY (bed_id) REFERENCES Beds(bed_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS Assignments (
    assignment_id INTEGER PRIMARY KEY,
    nurse_id      INTEGER NOT NULL,
    room_id       INTEGER,
    bed_id        INTEGER,
    start_time    TEXT NOT NULL,
    end_time      TEXT,
    FOREIGN KEY (nurse_id) REFERENCES Nurses(nurse_id),
    FOREIGN KEY (room_id)  REFERENCES Rooms(room_id) ON DELETE CASCADE,
    FOREIGN KEY (bed_id)   REFERENCES Beds(bed_id)   ON DELETE CASCADE,
    CHECK ((room_id IS NOT NULL AND bed_id IS NULL)
        OR (room_id IS NULL AND bed_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS Alerts (
    alert_id        INTEGER PRIMARY KEY,
    bell_id         INTEGER NOT NULL,
    nurse_id        INTEGER,
    timestamp       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ({_STATUSES})),
    acknowledged_at TEXT,
    parent_alert_id INTEGER,
    FOREIGN KEY (bell_id)  REFERENCES Call_Bells(bell_id) ON DELETE CASCADE,
    FOREIGN KEY (nurse_id) REFERENCES Nurses(nurse_id),
    FOREIGN KEY (parent_alert_id) REFERENCES Alerts(alert_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS Notification_Logs (
    notification_id INTEGER PRIMARY KEY,
    alert_id        INTEGER NOT NULL,
    channel         TEXT NOT NULL,
    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('DELIVERED', 'FAILED')),
    sent_at         TEXT,
    latency_ms      REAL,
    detail          TEXT,
    FOREIGN KEY (alert_id) REFERENCES Alerts(alert_id) ON DELETE CASCADE
);
"""

# Columns added after the first notebook version: (table, column, DDL type).
_ADDED_COLUMNS = [
    ("Nurses", "available", "INTEGER NOT NULL DEFAULT 1"),
    ("Nurses", "mobility_restricted", "INTEGER NOT NULL DEFAULT 0"),
    ("Alerts", "acknowledged_at", "TEXT"),
    ("Alerts", "parent_alert_id", "INTEGER REFERENCES Alerts(alert_id)"),
    ("Notification_Logs", "sent_at", "TEXT"),
    ("Notification_Logs", "latency_ms", "REAL"),
    ("Notification_Logs", "detail", "TEXT"),
]

INDEXES_SQL = """
-- Only one ACTIVE (end_time IS NULL) assignment per room and per bed.
CREATE UNIQUE INDEX IF NOT EXISTS ux_assign_active_room
    ON Assignments(room_id) WHERE end_time IS NULL AND room_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_assign_active_bed
    ON Assignments(bed_id)  WHERE end_time IS NULL AND bed_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_alerts_status ON Alerts(status, timestamp);
CREATE INDEX IF NOT EXISTS ix_alerts_parent ON Alerts(parent_alert_id);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLES_SQL)
    _migrate(conn)
    conn.executescript(INDEXES_SQL)
