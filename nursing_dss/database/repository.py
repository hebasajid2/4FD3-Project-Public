"""The only module that talks SQL. Every other layer goes through `Database`.

Assignment precedence rule (used everywhere): an active BED-level assignment
overrides an active ROOM-level assignment for that bed.
"""
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import config
from ..models import BellContext, Bed, Nurse, Plan, Room, UnitState
from ..timeutil import iso, now_utc
from .connection import connect
from .schema import create_schema


def _rows(rows: Sequence[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def _nurse(row: sqlite3.Row) -> Nurse:
    return Nurse(
        nurse_id=row["nurse_id"],
        name=row["nurse_name"],
        contact=row["contact"],
        qualification=row["qualification_level"],
        max_workload=row["max_workload"],
        available=bool(row["available"]),
        mobility_restricted=bool(row["mobility_restricted"]),
    )


class Database:
    def __init__(self, path: str = config.DEFAULT_DB_PATH):
        self.path = str(path)
        self.init_schema()

    # ------------------------------------------------------------------ plumbing
    def init_schema(self) -> None:
        with connect(self.path) as conn:
            create_schema(conn)

    def _fetch(self, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        with connect(self.path) as conn:
            return _rows(conn.execute(sql, params).fetchall())

    def _fetch_one(self, sql: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self._fetch(sql, params)
        return rows[0] if rows else None

    def _exec(self, sql: str, params: Tuple = ()) -> int:
        with connect(self.path) as conn:
            return conn.execute(sql, params).lastrowid

    # ------------------------------------------------------------------ units
    def add_unit(self, name: str) -> int:
        if not name or not name.strip():
            raise ValueError("Unit name is required.")
        return self._exec("INSERT INTO Units (unit_name) VALUES (?)", (name.strip(),))

    def get_or_create_unit(self, name: str) -> int:
        row = self._fetch_one("SELECT unit_id FROM Units WHERE unit_name = ?", (name.strip(),))
        return row["unit_id"] if row else self.add_unit(name)

    def list_units(self) -> List[Dict[str, Any]]:
        return self._fetch("SELECT unit_id, unit_name FROM Units ORDER BY unit_id")

    # ------------------------------------------------------------------ rooms / beds
    def add_room(self, number: str, unit_id: int, x: float, y: float) -> int:
        number = str(number).strip()
        if not number:
            raise ValueError("Room number is required.")
        return self._exec(
            "INSERT INTO Rooms (room_number, unit_id, x_coord, y_coord) VALUES (?, ?, ?, ?)",
            (number, int(unit_id), float(x), float(y)),
        )

    def upsert_room(self, number: str, unit_id: int, x: float, y: float) -> int:
        row = self._fetch_one(
            "SELECT room_id FROM Rooms WHERE unit_id = ? AND room_number = ?", (int(unit_id), str(number)))
        if row:
            self._exec("UPDATE Rooms SET x_coord = ?, y_coord = ? WHERE room_id = ?",
                       (float(x), float(y), row["room_id"]))
            return row["room_id"]
        return self.add_room(number, unit_id, x, y)

    def add_bed(self, number: str, room_id: int) -> int:
        number = str(number).strip()
        if not number:
            raise ValueError("Bed number is required.")
        return self._exec("INSERT INTO Beds (bed_number, room_id) VALUES (?, ?)", (number, int(room_id)))

    def get_or_create_bed(self, number: str, room_id: int) -> int:
        row = self._fetch_one(
            "SELECT bed_id FROM Beds WHERE room_id = ? AND bed_number = ?", (int(room_id), str(number)))
        return row["bed_id"] if row else self.add_bed(number, room_id)

    def delete_bed(self, bed_id: int) -> None:
        self._exec("DELETE FROM Beds WHERE bed_id = ?", (int(bed_id),))

    def delete_room(self, room_id: int) -> None:
        self._exec("DELETE FROM Rooms WHERE room_id = ?", (int(room_id),))

    # ------------------------------------------------------------------ patients
    def upsert_patient(self, bed_id: int, acuity: int, when: Optional[datetime] = None) -> int:
        """Admit a patient to a bed, or update the acuity of the patient already there.

        Updating the acuity does NOT change the admission timestamp.
        Returns the patient_id (re-selected, so it is always correct).
        """
        acuity = int(acuity)
        if not 1 <= acuity <= 5:
            raise ValueError("Acuity must be between 1 and 5.")
        stamp = iso(when or now_utc())
        self._exec(
            """INSERT INTO Patients (bed_id, acuity_score, admission_timestamp) VALUES (?, ?, ?)
               ON CONFLICT(bed_id) DO UPDATE SET acuity_score = excluded.acuity_score""",
            (int(bed_id), acuity, stamp),
        )
        return self._fetch_one("SELECT patient_id FROM Patients WHERE bed_id = ?", (int(bed_id),))["patient_id"]

    def ensure_patient(self, bed_id: int, acuity: int) -> None:
        """Seed helper: create the patient only if the bed is empty (never resets data)."""
        self._exec(
            "INSERT OR IGNORE INTO Patients (bed_id, acuity_score, admission_timestamp) VALUES (?, ?, ?)",
            (int(bed_id), int(acuity), iso(now_utc())),
        )

    def discharge_patient(self, bed_id: int) -> None:
        self._exec("DELETE FROM Patients WHERE bed_id = ?", (int(bed_id),))

    # ------------------------------------------------------------------ nurses
    def add_nurse(self, name: str, contact: Optional[str], qualification: str, max_workload: int,
                  available: bool = True, mobility_restricted: bool = False) -> int:
        if not name or not name.strip():
            raise ValueError("Nurse name is required.")
        if qualification not in config.QUALIFICATIONS:
            raise ValueError(f"Qualification must be one of {config.QUALIFICATIONS}.")
        return self._exec(
            """INSERT INTO Nurses (nurse_name, contact, qualification_level, max_workload,
                                   available, mobility_restricted) VALUES (?, ?, ?, ?, ?, ?)""",
            (name.strip(), (contact or "").strip() or None, qualification, int(max_workload),
             int(bool(available)), int(bool(mobility_restricted))),
        )

    def upsert_nurse_by_name(self, name: str, contact: Optional[str], qualification: str,
                             max_workload: int) -> int:
        row = self._fetch_one("SELECT nurse_id FROM Nurses WHERE nurse_name = ?", (name,))
        if row:
            self._exec(
                "UPDATE Nurses SET contact = ?, qualification_level = ?, max_workload = ? WHERE nurse_id = ?",
                (contact, qualification, int(max_workload), row["nurse_id"]))
            return row["nurse_id"]
        return self.add_nurse(name, contact, qualification, max_workload)

    def set_nurse_status(self, nurse_id: int, available: bool, mobility_restricted: bool) -> None:
        self._exec("UPDATE Nurses SET available = ?, mobility_restricted = ? WHERE nurse_id = ?",
                   (int(bool(available)), int(bool(mobility_restricted)), int(nurse_id)))

    def set_nurse_contact(self, nurse_id: int, contact: Optional[str]) -> None:
        self._exec("UPDATE Nurses SET contact = ? WHERE nurse_id = ?",
                   ((contact or "").strip() or None, int(nurse_id)))

    def list_nurses(self) -> List[Nurse]:
        return [_nurse(r) for r in self._fetch_raw("SELECT * FROM Nurses ORDER BY nurse_id")]

    def get_nurse(self, nurse_id: int) -> Optional[Nurse]:
        rows = self._fetch_raw("SELECT * FROM Nurses WHERE nurse_id = ?", (int(nurse_id),))
        return _nurse(rows[0]) if rows else None

    def _fetch_raw(self, sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
        with connect(self.path) as conn:
            return conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ call bells
    def add_bell(self, bell_id: int, bed_id: int) -> None:
        self._exec("INSERT INTO Call_Bells (bell_id, bed_id) VALUES (?, ?)", (int(bell_id), int(bed_id)))

    def ensure_bell(self, bell_id: int, bed_id: int) -> None:
        self._exec("INSERT OR IGNORE INTO Call_Bells (bell_id, bed_id) VALUES (?, ?)",
                   (int(bell_id), int(bed_id)))

    def get_bell_context(self, bell_id: int) -> Optional[BellContext]:
        row = self._fetch_one(
            """SELECT c.bell_id, b.bed_id, b.bed_number, r.room_id, r.room_number, r.unit_id,
                      r.x_coord, r.y_coord, p.acuity_score
               FROM Call_Bells c
               JOIN Beds b ON c.bed_id = b.bed_id
               JOIN Rooms r ON b.room_id = r.room_id
               LEFT JOIN Patients p ON p.bed_id = b.bed_id
               WHERE c.bell_id = ?""", (int(bell_id),))
        if not row:
            return None
        return BellContext(row["bell_id"], row["bed_id"], row["bed_number"], row["room_id"],
                           row["room_number"], row["unit_id"], row["x_coord"], row["y_coord"],
                           row["acuity_score"])

    def list_bells(self, unit_id: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = """SELECT c.bell_id, u.unit_name, r.room_number, b.bed_number, b.bed_id
                 FROM Call_Bells c JOIN Beds b ON c.bed_id = b.bed_id
                 JOIN Rooms r ON b.room_id = r.room_id JOIN Units u ON r.unit_id = u.unit_id"""
        params: Tuple = ()
        if unit_id is not None:
            sql += " WHERE r.unit_id = ?"
            params = (int(unit_id),)
        rows = self._fetch(sql, params)
        from ..models import natural_key
        return sorted(rows, key=lambda r: (natural_key(r["room_number"]), r["bed_number"]))

    # ------------------------------------------------------------------ unit state
    def load_unit_state(self, unit_id: int) -> UnitState:
        unit = self._fetch_one("SELECT unit_name FROM Units WHERE unit_id = ?", (int(unit_id),))
        if unit is None:
            raise ValueError(f"Unit {unit_id} does not exist.")
        rooms = {r["room_id"]: Room(r["room_id"], r["room_number"], r["unit_id"], r["x_coord"], r["y_coord"])
                 for r in self._fetch("SELECT * FROM Rooms WHERE unit_id = ?", (int(unit_id),))}
        beds = {r["bed_id"]: Bed(r["bed_id"], r["bed_number"], r["room_id"], r["acuity_score"])
                for r in self._fetch(
                    """SELECT b.bed_id, b.bed_number, b.room_id, p.acuity_score FROM Beds b
                       JOIN Rooms r ON b.room_id = r.room_id
                       LEFT JOIN Patients p ON p.bed_id = b.bed_id WHERE r.unit_id = ?""", (int(unit_id),))}
        nurses = {n.nurse_id: n for n in self.list_nurses()}
        return UnitState(int(unit_id), unit["unit_name"], rooms, beds, nurses)

    # ------------------------------------------------------------------ assignments
    def resolve_assignment(self, bed_id: int) -> Tuple[Optional[int], Optional[str]]:
        """(nurse_id, "BED" | "ROOM") for the active assignment covering this bed.
        A bed-level assignment overrides a room-level one."""
        row = self._fetch_one(
            "SELECT nurse_id FROM Assignments WHERE bed_id = ? AND end_time IS NULL", (int(bed_id),))
        if row:
            return row["nurse_id"], "BED"
        row = self._fetch_one(
            """SELECT a.nurse_id FROM Assignments a JOIN Beds b ON a.room_id = b.room_id
               WHERE b.bed_id = ? AND a.end_time IS NULL""", (int(bed_id),))
        if row:
            return row["nurse_id"], "ROOM"
        return None, None

    def current_plan(self, unit_id: int) -> Plan:
        """Effective bed -> nurse mapping for a unit (bed overrides room)."""
        rows = self._fetch(
            """SELECT b.bed_id,
                      COALESCE(
                        (SELECT a.nurse_id FROM Assignments a WHERE a.bed_id = b.bed_id AND a.end_time IS NULL),
                        (SELECT a.nurse_id FROM Assignments a WHERE a.room_id = b.room_id AND a.end_time IS NULL)
                      ) AS nurse_id
               FROM Beds b JOIN Rooms r ON b.room_id = r.room_id WHERE r.unit_id = ?""", (int(unit_id),))
        return {r["bed_id"]: r["nurse_id"] for r in rows if r["nurse_id"] is not None}

    def assign_room(self, nurse_id: int, room_id: int, when: Optional[datetime] = None) -> None:
        """Assign a whole room. Clears any bed-level overrides inside that room."""
        stamp = iso(when or now_utc())
        with connect(self.path) as conn:
            conn.execute("UPDATE Assignments SET end_time = ? WHERE room_id = ? AND end_time IS NULL",
                         (stamp, int(room_id)))
            conn.execute(
                """UPDATE Assignments SET end_time = ? WHERE end_time IS NULL AND bed_id IN
                   (SELECT bed_id FROM Beds WHERE room_id = ?)""", (stamp, int(room_id)))
            conn.execute("INSERT INTO Assignments (nurse_id, room_id, start_time) VALUES (?, ?, ?)",
                         (int(nurse_id), int(room_id), stamp))

    def assign_bed(self, nurse_id: int, bed_id: int, when: Optional[datetime] = None) -> None:
        """Assign one bed (overrides the room-level assignment for that bed)."""
        stamp = iso(when or now_utc())
        with connect(self.path) as conn:
            conn.execute("UPDATE Assignments SET end_time = ? WHERE bed_id = ? AND end_time IS NULL",
                         (stamp, int(bed_id)))
            conn.execute("INSERT INTO Assignments (nurse_id, bed_id, start_time) VALUES (?, ?, ?)",
                         (int(nurse_id), int(bed_id), stamp))

    def commit_plan(self, unit_id: int, plan: Plan, when: Optional[datetime] = None) -> int:
        """Replace all active assignments of a unit with `plan` in ONE transaction.

        A room whose beds all map to one nurse is stored as a room-level row;
        otherwise each bed gets its own bed-level row. Returns rows written.
        """
        state = self.load_unit_state(unit_id)
        stamp = iso(when or now_utc())
        written = 0
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE Assignments SET end_time = ? WHERE end_time IS NULL AND (
                       room_id IN (SELECT room_id FROM Rooms WHERE unit_id = ?)
                    OR bed_id IN (SELECT b.bed_id FROM Beds b JOIN Rooms r ON b.room_id = r.room_id
                                  WHERE r.unit_id = ?))""", (stamp, int(unit_id), int(unit_id)))
            for room in state.sorted_rooms():
                beds = state.beds_in_room(room.room_id)
                nurse_ids = {plan.get(b.bed_id) for b in beds}
                if len(nurse_ids) == 1 and None not in nurse_ids and beds:
                    conn.execute("INSERT INTO Assignments (nurse_id, room_id, start_time) VALUES (?, ?, ?)",
                                 (int(next(iter(nurse_ids))), room.room_id, stamp))
                    written += 1
                else:
                    for b in beds:
                        if plan.get(b.bed_id) is not None:
                            conn.execute(
                                "INSERT INTO Assignments (nurse_id, bed_id, start_time) VALUES (?, ?, ?)",
                                (int(plan[b.bed_id]), b.bed_id, stamp))
                            written += 1
        return written

    def list_assignments(self, active_only: bool = False) -> List[Dict[str, Any]]:
        return self._fetch(
            f"""SELECT a.assignment_id, n.nurse_name, n.qualification_level,
                       COALESCE(r.room_number, r2.room_number) AS room_number, b.bed_number,
                       CASE WHEN a.bed_id IS NOT NULL THEN 'BED' ELSE 'ROOM' END AS level,
                       a.start_time, a.end_time
                FROM Assignments a
                JOIN Nurses n ON a.nurse_id = n.nurse_id
                LEFT JOIN Rooms r ON a.room_id = r.room_id
                LEFT JOIN Beds b ON a.bed_id = b.bed_id
                LEFT JOIN Rooms r2 ON b.room_id = r2.room_id
                {"WHERE a.end_time IS NULL" if active_only else ""}
                ORDER BY a.assignment_id DESC""")

    # ------------------------------------------------------------------ alerts
    def create_alert(self, bell_id: int, nurse_id: Optional[int], status: str, timestamp: str,
                     parent_alert_id: Optional[int] = None) -> int:
        return self._exec(
            "INSERT INTO Alerts (bell_id, nurse_id, timestamp, status, parent_alert_id) VALUES (?, ?, ?, ?, ?)",
            (int(bell_id), nurse_id, timestamp, status, parent_alert_id))

    def set_alert_status(self, alert_id: int, status: str) -> None:
        self._exec("UPDATE Alerts SET status = ? WHERE alert_id = ?", (status, int(alert_id)))

    def acknowledge_alert(self, alert_id: int, when_iso: str) -> None:
        self._exec("UPDATE Alerts SET status = 'ACKNOWLEDGED', acknowledged_at = ? WHERE alert_id = ?",
                   (when_iso, int(alert_id)))

    def get_alert(self, alert_id: int) -> Optional[Dict[str, Any]]:
        return self._fetch_one("SELECT * FROM Alerts WHERE alert_id = ?", (int(alert_id),))

    def alert_chain_nurse_ids(self, alert_id: int) -> List[int]:
        """Nurses already tried for this call (this alert and all its ancestors)."""
        ids: List[int] = []
        seen = set()
        current: Optional[int] = int(alert_id)
        while current is not None and current not in seen:
            seen.add(current)
            row = self.get_alert(current)
            if not row:
                break
            if row["nurse_id"] is not None:
                ids.append(row["nurse_id"])
            current = row["parent_alert_id"]
        return ids

    def find_unacknowledged(self, cutoff_iso: str) -> List[Dict[str, Any]]:
        """DISPATCHED, un-acknowledged alerts at/older than cutoff that have not escalated yet."""
        return self._fetch(
            """SELECT a.* FROM Alerts a
               WHERE a.status = 'DISPATCHED' AND a.acknowledged_at IS NULL AND a.timestamp <= ?
                 AND NOT EXISTS (SELECT 1 FROM Alerts c WHERE c.parent_alert_id = a.alert_id)
               ORDER BY a.alert_id""", (cutoff_iso,))

    def list_alerts_view(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._fetch(
            """SELECT a.alert_id, a.bell_id, r.room_number, b.bed_number,
                      COALESCE(n.nurse_name, '(unassigned)') AS nurse, a.status,
                      a.timestamp, a.acknowledged_at, a.parent_alert_id
               FROM Alerts a
               JOIN Call_Bells c ON a.bell_id = c.bell_id
               JOIN Beds b ON c.bed_id = b.bed_id JOIN Rooms r ON b.room_id = r.room_id
               LEFT JOIN Nurses n ON a.nurse_id = n.nurse_id
               ORDER BY a.alert_id DESC LIMIT ?""", (int(limit),))

    # ------------------------------------------------------------------ notification logs
    def log_notification(self, alert_id: int, channel: str, delivered: bool, sent_at: str,
                         latency_ms: float, detail: str) -> int:
        return self._exec(
            """INSERT INTO Notification_Logs (alert_id, channel, delivery_status, sent_at, latency_ms, detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (int(alert_id), channel, "DELIVERED" if delivered else "FAILED", sent_at, latency_ms, detail))

    def list_notifications_view(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._fetch(
            """SELECT notification_id, alert_id, channel, delivery_status, sent_at,
                      ROUND(latency_ms, 3) AS latency_ms, detail
               FROM Notification_Logs ORDER BY notification_id DESC LIMIT ?""", (int(limit),))

    def inbox_view(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Messages that reached a (simulated) nurse device, newest first."""
        return self._fetch(
            """SELECT nl.sent_at, nl.channel, nu.nurse_name AS nurse, r.room_number, b.bed_number,
                      a.status AS alert_status, a.alert_id
               FROM Notification_Logs nl
               JOIN Alerts a ON nl.alert_id = a.alert_id
               JOIN Call_Bells c ON a.bell_id = c.bell_id
               JOIN Beds b ON c.bed_id = b.bed_id JOIN Rooms r ON b.room_id = r.room_id
               LEFT JOIN Nurses nu ON nl.channel = 'Device: ' || nu.contact
               WHERE nl.delivery_status = 'DELIVERED'
               ORDER BY nl.notification_id DESC LIMIT ?""", (int(limit),))

    # ------------------------------------------------------------------ reporting views
    def layout_view(self, unit_id: int) -> List[Dict[str, Any]]:
        return self._fetch(
            """SELECT r.room_number AS Room, r.x_coord AS X, r.y_coord AS Y, b.bed_number AS Bed,
                      b.bed_id AS Bed_ID, p.patient_id AS Patient_ID, p.acuity_score AS Acuity,
                      c.bell_id AS Call_Bell_ID
               FROM Rooms r JOIN Beds b ON r.room_id = b.room_id
               LEFT JOIN Patients p ON p.bed_id = b.bed_id
               LEFT JOIN Call_Bells c ON c.bed_id = b.bed_id
               WHERE r.unit_id = ?""", (int(unit_id),))

    def patients_view(self) -> List[Dict[str, Any]]:
        return self._fetch(
            """SELECT p.patient_id, u.unit_name, r.room_number, b.bed_number, p.acuity_score,
                      p.admission_timestamp
               FROM Patients p JOIN Beds b ON p.bed_id = b.bed_id JOIN Rooms r ON b.room_id = r.room_id
               JOIN Units u ON r.unit_id = u.unit_id ORDER BY p.patient_id DESC""")

    def nurses_view(self) -> List[Dict[str, Any]]:
        return self._fetch(
            """SELECT nurse_id, nurse_name, qualification_level, max_workload, contact,
                      CASE available WHEN 1 THEN 'yes' ELSE 'no' END AS available,
                      CASE mobility_restricted WHEN 1 THEN 'yes' ELSE 'no' END AS mobility_restricted
               FROM Nurses ORDER BY nurse_id""")
