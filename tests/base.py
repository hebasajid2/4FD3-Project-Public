"""Shared fixtures: a fresh seeded database + routing system per test, and a
controllable clock so escalation timing can be tested without sleeping."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from nursing_dss.database import Database
from nursing_dss.database.seed import seed_demo
from nursing_dss.output.notifier import SimulatedNotifier
from nursing_dss.routing import RoutingSystem


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += timedelta(seconds=seconds)


class SystemTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db = Database(os.path.join(self._tmp, "test.db"))
        self.unit_id = seed_demo(self.db)
        self.clock = FakeClock()
        self.notifier = SimulatedNotifier()
        self.system = RoutingSystem(self.db, self.notifier, ack_timeout_s=60, clock=self.clock)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # helpers -------------------------------------------------------------
    def nurse_id(self, name: str) -> int:
        return next(n.nurse_id for n in self.db.list_nurses() if n.name == name)

    def room_id(self, number: str) -> int:
        return next(r.room_id for r in self.db.load_unit_state(self.unit_id).rooms.values()
                    if r.room_number == number)

    def press(self, bell_id: int):
        from nursing_dss.input import parse_signal
        return self.system.handle_signal(parse_signal(bell_id, received_at=self.clock()))

    def save_optimized(self):
        rec = self.system.assignments.recommend(self.unit_id)
        ok, _ = self.system.assignments.commit(self.unit_id, rec.plan)
        self.assertTrue(ok)
        return rec.plan
