import os
import sqlite3
import tempfile
import unittest

from nursing_dss.database import Database
from nursing_dss.database.seed import seed_demo
from tests.base import SystemTestCase


class DatabaseTests(SystemTestCase):
    def test_all_tables_exist(self):
        with sqlite3.connect(self.db.path) as c:
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("Units", "Rooms", "Beds", "Patients", "Nurses", "Call_Bells", "Assignments", "Alerts",
                  "Notification_Logs"):
            self.assertIn(t, names)

    def test_only_one_active_assignment_per_room_even_with_raw_sql(self):
        self.db.assign_room(1, 1)
        c = sqlite3.connect(self.db.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO Assignments(nurse_id, room_id, start_time) VALUES (2, 1, 'x')")
        finally:
            c.rollback()
            c.close()

    def test_room_or_bed_check_constraint(self):
        c = sqlite3.connect(self.db.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO Assignments(nurse_id, room_id, bed_id, start_time) VALUES (1, 1, 1, 'x')")
        finally:
            c.rollback()
            c.close()

    def test_acuity_range_enforced(self):
        with self.assertRaises(ValueError):
            self.db.upsert_patient(1, 6)

    def test_patient_upsert_returns_right_id_and_keeps_admission_time(self):
        first = self.db.upsert_patient(1, 2)
        before = {p["patient_id"]: p["admission_timestamp"] for p in self.db.patients_view()}
        again = self.db.upsert_patient(1, 5)
        after = {p["patient_id"]: p["admission_timestamp"] for p in self.db.patients_view()}
        self.assertEqual(first, again)
        self.assertEqual(before[first], after[first])
        self.assertEqual(self.db.get_bell_context(1011).acuity, 5)

    def test_seed_is_idempotent_and_never_resets_patients(self):
        self.db.upsert_patient(1, 5)
        before = self.db.patients_view()
        seed_demo(self.db)
        self.assertEqual(before, self.db.patients_view())
        self.assertEqual(len(self.db.list_units()), 1)

    def test_deleting_a_bed_with_bell_assignment_and_alert_cascades(self):
        self.db.assign_bed(2, 3)
        self.press(1021)
        self.db.delete_bed(3)
        self.assertIsNone(self.db.get_bell_context(1021))
        self.assertEqual(self.db.list_alerts_view(), [])

    def test_old_notebook_database_is_migrated(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE Nurses (nurse_id INTEGER PRIMARY KEY, nurse_name TEXT NOT NULL, contact TEXT,
                qualification_level TEXT NOT NULL DEFAULT 'RN', max_workload INTEGER NOT NULL DEFAULT 12);
            INSERT INTO Nurses (nurse_name, contact) VALUES ('Old Timer', 'EXT-1');
            CREATE TABLE Alerts (alert_id INTEGER PRIMARY KEY, bell_id INTEGER NOT NULL, nurse_id INTEGER,
                timestamp TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE Notification_Logs (notification_id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL,
                channel TEXT NOT NULL, delivery_status TEXT NOT NULL);""")
        c.commit()
        c.close()
        nurse = Database(path).list_nurses()[0]
        self.assertEqual((nurse.name, nurse.available, nurse.mobility_restricted), ("Old Timer", True, False))


if __name__ == "__main__":
    unittest.main()
