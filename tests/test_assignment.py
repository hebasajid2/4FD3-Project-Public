import unittest

from nursing_dss.assignment.baseline import baseline_plan
from nursing_dss.assignment.optimizer import optimize
from nursing_dss.assignment.scenarios import make_scenario
from nursing_dss.assignment.travel import best_route, path_length
from nursing_dss.assignment.validator import validate_plan
from nursing_dss.input import InvalidSignal, parse_signal
from nursing_dss.models import Bed, Nurse, Room, UnitState


def tiny_state(**nurse_overrides):
    rooms = {i: Room(i, str(100 + i), 1, 10.0 * i, 0.0) for i in range(1, 5)}
    beds = {i: Bed(i, "A", i, a) for i, a in zip(range(1, 5), (5, 3, 3, 1))}
    nurses = {1: Nurse(1, "Rn One", "E1", "RN", 12), 2: Nurse(2, "Rpn Two", "E2", "RPN", 12),
              3: Nurse(3, "Rn Three", "E3", "RN", 12)}
    for k, v in nurse_overrides.items():
        nurses[int(k[1:])] = v
    return UnitState(1, "T", rooms, beds, nurses, station=(0.0, 0.0))


class OptimizerRules(unittest.TestCase):
    def test_acuity_5_never_goes_to_an_rpn(self):
        s = tiny_state()
        plan = optimize(s).plan
        self.assertNotEqual(plan[1], 2)
        self.assertEqual(validate_plan(s, plan), [])

    def test_capacity_is_a_hard_limit_and_overflow_is_reported(self):
        s = tiny_state(n1=Nurse(1, "Rn One", "E1", "RN", 3), n2=Nurse(2, "Rpn Two", "E2", "RPN", 1),
                       n3=Nurse(3, "Rn Three", "E3", "RN", 3))
        res = optimize(s)
        self.assertTrue(res.unassigned_rooms)  # total load 12 > total capacity 7
        for nid in (1, 2, 3):
            load = sum(s.beds[b].acuity for b, n in res.plan.items() if n == nid)
            self.assertLessEqual(load, s.nurses[nid].max_workload)

    def test_unavailable_nurses_get_nothing(self):
        s = tiny_state(n3=Nurse(3, "Rn Three", "E3", "RN", 12, available=False))
        self.assertNotIn(3, set(optimize(s).plan.values()))

    def test_mobility_restricted_nurse_only_gets_nearby_rooms(self):
        s = tiny_state(n1=Nurse(1, "Rn One", "E1", "RN", 20, mobility_restricted=True))
        plan = optimize(s).plan
        for bed_id, nid in plan.items():
            if nid == 1:
                self.assertLessEqual(s.rooms[s.beds[bed_id].room_id].x, 30.0)
        self.assertEqual([v for v in validate_plan(s, plan) if v.severity == "ERROR"], [])

    def test_deterministic(self):
        s = make_scenario(7)
        self.assertEqual(optimize(s).plan, optimize(s).plan)

    def test_continuity_keeps_rooms_with_previous_nurse(self):
        s = tiny_state()
        base = optimize(s).plan
        self.assertEqual(base, optimize(s, previous_plan=base, w_continuity=100.0).plan)

    def test_no_nurses_means_nothing_assigned_not_a_crash(self):
        s = tiny_state()
        s.nurses.clear()
        res = optimize(s)
        self.assertEqual(res.plan, {})
        self.assertEqual(len(res.unassigned_rooms), 4)


class ValidatorAndBaseline(unittest.TestCase):
    def test_baseline_ignores_acuity_and_can_break_rules(self):
        # The baseline hands the first block of rooms (acuity 5) to the first nurse, an RPN here.
        s = tiny_state(n1=Nurse(1, "Rpn One", "E1", "RPN", 12))
        errors = [v for v in validate_plan(s, baseline_plan(s)) if v.severity == "ERROR"]
        self.assertIn("QUALIFICATION", {v.code for v in errors})

    def test_validator_flags_each_rule(self):
        s = tiny_state(n1=Nurse(1, "Rn One", "E1", "RN", 4, mobility_restricted=True),
                       n3=Nurse(3, "Rn Three", "E3", "RN", 12, available=False))
        plan = {1: 1, 2: 1, 3: 3, 4: 1}  # nurse 1: over capacity + room 4 is 40 m out; nurse 3 unavailable
        codes = {v.code for v in validate_plan(s, plan)}
        self.assertTrue({"CAPACITY", "MOBILITY", "AVAILABILITY"} <= codes)
        self.assertIn("UNASSIGNED", {v.code for v in validate_plan(s, {1: 1})})

    def test_split_room_is_a_warning(self):
        s = tiny_state()
        s.beds[10] = Bed(10, "B", 1, 2)
        plan = {1: 1, 10: 3, 2: 1, 3: 1, 4: 3}
        found = [x for x in validate_plan(s, plan) if x.code == "SPLIT_ROOM"]
        self.assertEqual(found[0].severity, "WARNING")


class Travel(unittest.TestCase):
    def test_route_visits_everything_and_is_no_longer_than_naive_order(self):
        items = {1: (30.0, 0.0), 2: (10.0, 0.0), 3: (20.0, 5.0), 4: (5.0, 0.0)}
        order, length = best_route(items, (0.0, 0.0))
        self.assertEqual(sorted(order), [1, 2, 3, 4])
        self.assertLessEqual(length, path_length((0.0, 0.0), [1, 2, 3, 4], items))

    def test_empty_route(self):
        self.assertEqual(best_route({}, (0, 0)), ([], 0.0))


class InputParsing(unittest.TestCase):
    def test_accepts_int_string_and_dict(self):
        for raw in (1011, "1011", {"bell_id": 1011}, {"bell_id": " 1011 "}):
            self.assertEqual(parse_signal(raw).bell_id, 1011)

    def test_rejects_garbage(self):
        for raw in (None, "", "abc", {}, {"bell_id": None}, -5, 0, True):
            with self.assertRaises(InvalidSignal):
                parse_signal(raw)


if __name__ == "__main__":
    unittest.main()
