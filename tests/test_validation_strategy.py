"""The five tests from the design document's 'Validation Strategy', automated."""
import statistics
import unittest

from nursing_dss.assignment import metrics
from nursing_dss.assignment.baseline import baseline_plan
from nursing_dss.assignment.optimizer import optimize
from nursing_dss.assignment.scenarios import make_scenario

from tests.base import SystemTestCase


class RoutingAccuracy(SystemTestCase):
    def test_every_bell_reaches_the_assigned_nurse(self):
        plan = self.save_optimized()
        state = self.db.load_unit_state(self.unit_id)
        for bell in self.db.list_bells(self.unit_id):
            expected = state.nurses[plan[bell["bed_id"]]]
            result = self.press(bell["bell_id"])
            self.assertTrue(result.ok, result.flags)
            self.assertEqual(result.nurse_id, expected.nurse_id, f"bell {bell['bell_id']}")
            self.assertEqual(result.channel, f"Device: {expected.contact}")
            self.assertEqual(result.status, "DISPATCHED")
        self.assertEqual(len(self.notifier.outbox), 12)

    def test_alert_and_notification_are_logged(self):
        self.save_optimized()
        r = self.press(1011)
        alerts = self.db.list_alerts_view()
        self.assertEqual(alerts[0]["alert_id"], r.alert_id)
        logs = self.db.list_notifications_view()
        self.assertEqual(logs[0]["delivery_status"], "DELIVERED")


class ReassignmentHandling(SystemTestCase):
    def test_new_signals_follow_a_mid_shift_reassignment(self):
        bob, carol = self.nurse_id("Bob Jones"), self.nurse_id("Carol Vance")
        room = self.room_id("101")
        self.db.assign_room(bob, room)
        self.assertEqual(self.press(1011).nurse_id, bob)
        self.db.assign_room(carol, room)  # charge nurse reassigns mid-shift
        self.assertEqual(self.press(1011).nurse_id, carol)
        self.assertEqual(self.press(1012).nurse_id, carol)
        self.assertEqual(len(self.db.list_assignments(active_only=True)), 1)

    def test_bed_level_override_beats_room_level(self):
        bob, carol = self.nurse_id("Bob Jones"), self.nurse_id("Carol Vance")
        self.db.assign_room(bob, self.room_id("101"))
        bed_a = self.db.get_bell_context(1011).bed_id
        self.db.assign_bed(carol, bed_a)
        r_a, r_b = self.press(1011), self.press(1012)
        self.assertEqual((r_a.nurse_id, r_a.assignment_level), (carol, "BED"))
        self.assertEqual((r_b.nurse_id, r_b.assignment_level), (bob, "ROOM"))

    def test_assigning_a_room_clears_bed_overrides_inside_it(self):
        bob, carol = self.nurse_id("Bob Jones"), self.nurse_id("Carol Vance")
        self.db.assign_bed(carol, self.db.get_bell_context(1011).bed_id)
        self.db.assign_room(bob, self.room_id("101"))
        self.assertEqual(self.press(1011).nurse_id, bob)


class ResponseTime(SystemTestCase):
    def test_routing_and_notification_are_fast(self):
        self.save_optimized()
        times = [self.press(b["bell_id"]) for b in self.db.list_bells(self.unit_id)]
        self.assertLess(max(r.routing_latency_ms for r in times), 500, "end-to-end routing > 500 ms")
        self.assertLess(max(r.notify_latency_ms for r in times), 100, "notification > 100 ms")
        logged = [n["latency_ms"] for n in self.db.list_notifications_view()]
        self.assertTrue(all(v is not None for v in logged))

    def test_acknowledgement_response_time_is_measured(self):
        self.save_optimized()
        r = self.press(1011)
        self.clock.advance(12)
        self.assertAlmostEqual(self.system.acknowledge(r.alert_id), 12.0, places=3)
        self.assertEqual(self.db.get_alert(r.alert_id)["status"], "ACKNOWLEDGED")
        with self.assertRaises(ValueError):
            self.system.acknowledge(r.alert_id)


class WorkloadFairnessAndBalance(unittest.TestCase):
    def test_optimizer_beats_manual_baseline_over_many_scenarios(self):
        base_util, opt_util, base_err, opt_err = [], [], [], []
        base_travel, opt_travel = [], []
        for seed in range(30):
            state = make_scenario(seed, n_rooms=15, n_nurses=6)
            b = metrics.compute_metrics(state, baseline_plan(state))["summary"]
            result = optimize(state)
            self.assertEqual(result.unassigned_rooms, [], f"seed {seed}")
            o = metrics.compute_metrics(state, result.plan)["summary"]
            base_util.append(b["utilization_std_pct"]); opt_util.append(o["utilization_std_pct"])
            base_err.append(b["rule_errors"]); opt_err.append(o["rule_errors"])
            base_travel.append(b["total_travel_m"]); opt_travel.append(o["total_travel_m"])
        self.assertLess(statistics.mean(opt_util), 0.5 * statistics.mean(base_util))
        self.assertEqual(sum(opt_err), 0, "optimizer must never break a hard rule")
        self.assertGreater(sum(base_err), 0)
        self.assertLessEqual(statistics.mean(opt_travel), statistics.mean(base_travel))


class EdgeCases(SystemTestCase):
    def test_room_with_no_assigned_nurse_is_flagged_and_falls_back_to_charge(self):
        r = self.press(1011)  # nothing assigned yet
        self.assertEqual(r.status, "ESCALATED_UNASSIGNED")
        self.assertTrue(any("No nurse is assigned" in f for f in r.flags))
        self.assertTrue(r.ok)
        self.assertEqual(r.nurse_name, "Alice Smith")  # the CHARGE nurse
        self.assertEqual(self.db.list_alerts_view()[0]["nurse"], "(unassigned)")

    def test_unassigned_and_no_one_reachable_is_loudly_flagged(self):
        for n in self.db.list_nurses():
            self.db.set_nurse_status(n.nurse_id, available=False, mobility_restricted=False)
        r = self.press(1011)
        self.assertFalse(r.ok)
        self.assertTrue(any("MANUAL RESPONSE REQUIRED" in f for f in r.flags))
        self.assertEqual(self.db.list_alerts_view()[0]["status"], "ESCALATED_UNASSIGNED")

    def test_nurse_without_phone_is_flagged_and_call_is_rerouted(self):
        bob = self.nurse_id("Bob Jones")
        self.db.assign_room(bob, self.room_id("101"))
        self.db.set_nurse_contact(bob, None)
        r = self.press(1011)
        self.assertTrue(any("no linked device" in f for f in r.flags))
        self.assertTrue(r.ok)
        self.assertNotEqual(r.nurse_id, bob)
        logs = self.db.list_notifications_view()
        self.assertIn("FAILED", [l["delivery_status"] for l in logs])
        statuses = {a["alert_id"]: a["status"] for a in self.db.list_alerts_view()}
        self.assertEqual(statuses[min(statuses)], "DELIVERY_FAILED")

    def test_device_failure_is_flagged_and_rerouted(self):
        bob = self.nurse_id("Bob Jones")
        self.db.assign_room(bob, self.room_id("101"))
        self.notifier.fail_contacts.add("EXT-402")
        r = self.press(1011)
        self.assertTrue(r.ok)
        self.assertNotEqual(r.nurse_id, bob)
        self.assertTrue(any("Could not reach Bob Jones" in f for f in r.flags))

    def test_unknown_bell_creates_no_alert(self):
        r = self.press(424242)
        self.assertEqual((r.ok, r.status), (False, "UNKNOWN_BELL"))
        self.assertEqual(self.db.list_alerts_view(), [])

    def test_unavailable_assigned_nurse_is_rerouted(self):
        bob = self.nurse_id("Bob Jones")
        self.db.assign_room(bob, self.room_id("101"))
        self.db.set_nurse_status(bob, available=False, mobility_restricted=False)
        r = self.press(1011)
        self.assertTrue(r.ok)
        self.assertNotEqual(r.nurse_id, bob)

    def test_broken_notifier_never_crashes_routing(self):
        class Broken:
            def send(self, nurse, message):
                raise RuntimeError("boom")
        self.system.notifier = Broken()
        self.db.assign_room(self.nurse_id("Bob Jones"), self.room_id("101"))
        r = self.press(1011)
        self.assertFalse(r.ok)
        self.assertTrue(any("Notifier error" in f for f in r.flags))


class Escalation(SystemTestCase):
    def test_unacknowledged_alert_escalates_to_closest_available_nurse(self):
        bob, carol = self.nurse_id("Bob Jones"), self.nurse_id("Carol Vance")
        self.db.assign_room(bob, self.room_id("101"))
        self.db.assign_room(carol, self.room_id("104"))   # closest to room 101 (10 m)
        self.db.assign_room(self.nurse_id("Frank Miller"), self.room_id("106"))  # far away
        first = self.press(1011)
        self.assertEqual(first.nurse_id, bob)
        self.clock.advance(30)
        self.assertEqual(self.system.escalate_pending(), [])  # not yet due
        self.clock.advance(31)
        results = self.system.escalate_pending()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].nurse_id, carol)
        self.assertEqual(self.db.get_alert(first.alert_id)["status"], "ESCALATED")
        self.assertEqual(self.system.escalate_pending(), [], "must not escalate the same alert twice")

    def test_acknowledged_alert_does_not_escalate(self):
        self.db.assign_room(self.nurse_id("Bob Jones"), self.room_id("101"))
        r = self.press(1011)
        self.clock.advance(10)
        self.system.acknowledge(r.alert_id)
        self.clock.advance(120)
        self.assertEqual(self.system.escalate_pending(), [])

    def test_backup_must_be_qualified_for_the_patient(self):
        # Room 103 Bed A is acuity 5: an RPN may never be the backup.
        self.db.assign_room(self.nurse_id("Bob Jones"), self.room_id("103"))
        self.db.set_nurse_status(self.nurse_id("Alice Smith"), False, False)
        for n in ("Carol Vance", "David Lee"):
            self.db.set_nurse_status(self.nurse_id(n), False, False)
        first = self.press(1031)
        self.clock.advance(61)
        out = self.system.escalate_pending()
        self.assertFalse(out[0].ok)
        self.assertEqual(self.db.get_alert(first.alert_id)["status"], "ESCALATION_FAILED")
        self.assertTrue(any("MANUAL RESPONSE REQUIRED" in f for f in out[0].flags))


if __name__ == "__main__":
    unittest.main()
