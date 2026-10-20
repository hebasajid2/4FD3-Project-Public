"""JSON API. Every handler takes a dict and returns a dict, and talks ONLY to the
RoutingSystem (and, through it, to the assignment engine and the database)."""
import sqlite3
from dataclasses import asdict
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from ..database.seed import seed_demo
from ..input.signals import InvalidSignal, parse_signal
from ..models import Plan, Violation
from ..routing import RoutingSystem

USER_ERRORS = (ValueError, KeyError, TypeError, sqlite3.Error, InvalidSignal)


def _plan_in(raw: Optional[Dict]) -> Plan:
    """JSON gives {"12": 3}; the engine wants {12: 3}."""
    return {int(b): int(n) for b, n in (raw or {}).items() if n is not None}


def _plan_out(plan: Plan) -> Dict[str, int]:
    return {str(b): n for b, n in plan.items()}


def _violations(items: List[Violation]) -> List[Dict]:
    return [asdict(v) for v in items]


class Api:
    def __init__(self, system: RoutingSystem):
        self.system = system
        self.db = system.db
        self.assign = system.assignments

    # ------------------------------------------------------------------ reads
    def health(self, _):
        return {"status": "ok", "units": len(self.db.list_units())}

    def snapshot(self, p: Dict) -> Dict:
        """Everything the UI needs to draw one unit."""
        units = self.db.list_units()
        if not units:
            return {"units": [], "unit": None}
        unit_id = int(p.get("unit_id") or units[0]["unit_id"])
        state = self.db.load_unit_state(unit_id)
        bells = {b["bed_id"]: b["bell_id"] for b in self.db.list_bells(unit_id)}
        rooms = [{
            "room_id": r.room_id, "room_number": r.room_number, "x": r.x, "y": r.y,
            "beds": [{"bed_id": b.bed_id, "bed_number": b.bed_number, "acuity": b.acuity,
                      "bell_id": bells.get(b.bed_id)} for b in state.beds_in_room(r.room_id)],
        } for r in state.sorted_rooms()]
        nurses = [{"nurse_id": n.nurse_id, "name": n.name, "contact": n.contact,
                   "qualification": n.qualification, "max_workload": n.max_workload,
                   "available": n.available, "mobility_restricted": n.mobility_restricted}
                  for n in state.nurses.values()]
        plan = self.db.current_plan(unit_id)
        out = {"units": units, "unit": {"unit_id": unit_id, "unit_name": state.unit_name},
               "station": list(state.station), "rooms": rooms, "nurses": nurses, "plan": _plan_out(plan)}
        out.update(self._evaluation(unit_id, plan))
        return out

    def _evaluation(self, unit_id: int, plan: Plan) -> Dict:
        state = self.assign.load_state(unit_id)
        return {"violations": _violations(self.assign.validate(state, plan)),
                "metrics": self.assign.evaluate(state, plan),
                "comparison": self.assign.compare_with_baseline(state, plan)}

    def assignment_history(self, _):
        return {"assignments": self.db.list_assignments()}

    def alerts(self, _):
        return {"alerts": self.db.list_alerts_view(50), "notifications": self.db.list_notifications_view(50),
                "inbox": self.db.inbox_view(50)}

    # ------------------------------------------------------------------ assignments
    def evaluate(self, p: Dict) -> Dict:
        return self._evaluation(int(p["unit_id"]), _plan_in(p.get("plan")))

    def recommend(self, p: Dict) -> Dict:
        unit_id = int(p["unit_id"])
        rec = self.assign.recommend(unit_id, bool(p.get("prefer_continuity", True)))
        out = {"plan": _plan_out(rec.plan), "elapsed_ms": round(rec.elapsed_s * 1000, 1),
               "unassigned_rooms": [{"room_id": r, "reason": why} for r, why in rec.unassigned_rooms]}
        out.update(self._evaluation(unit_id, rec.plan))
        return out

    def baseline(self, p: Dict) -> Dict:
        unit_id = int(p["unit_id"])
        plan = self.assign.baseline(unit_id)
        return {"plan": _plan_out(plan), **self._evaluation(unit_id, plan)}

    def commit(self, p: Dict) -> Dict:
        unit_id, plan = int(p["unit_id"]), _plan_in(p.get("plan"))
        ok, violations = self.assign.commit(unit_id, plan, override=bool(p.get("override")))
        return {"saved": ok, "violations": _violations(violations),
                "message": "Assignment saved. New call bells now route to these nurses." if ok else
                           "Not saved: fix the errors, or tick the charge-nurse override."}

    def manual_assign(self, p: Dict) -> Dict:
        nurse_id, target_id = int(p["nurse_id"]), int(p["target_id"])
        if p.get("level") == "Bed":
            self.db.assign_bed(nurse_id, target_id)
        else:
            self.db.assign_room(nurse_id, target_id)
        return {"saved": True}

    # ------------------------------------------------------------------ call bells
    def press_bell(self, p: Dict) -> Dict:
        signal = parse_signal(p.get("bell_id"), source="SIMULATED", received_at=self.system.clock())
        result = self.system.handle_signal(signal)
        return result.to_dict()

    def acknowledge(self, p: Dict) -> Dict:
        seconds = self.system.acknowledge(int(p["alert_id"]))
        return {"response_seconds": round(seconds, 2)}

    def escalate(self, p: Dict) -> Dict:
        now = self.system.clock() + timedelta(seconds=float(p.get("elapsed_seconds") or 0))
        return {"results": [r.to_dict() for r in self.system.escalate_pending(now)]}

    # ------------------------------------------------------------------ setup
    def add_unit(self, p): return {"unit_id": self.db.add_unit(p["name"])}

    def add_room(self, p):
        return {"room_id": self.db.add_room(p["room_number"], int(p["unit_id"]), float(p["x"]), float(p["y"]))}

    def add_bed(self, p): return {"bed_id": self.db.add_bed(p["bed_number"], int(p["room_id"]))}

    def save_patient(self, p):
        return {"patient_id": self.db.upsert_patient(int(p["bed_id"]), int(p["acuity"]))}

    def discharge(self, p):
        self.db.discharge_patient(int(p["bed_id"]))
        return {"ok": True}

    def add_nurse(self, p):
        return {"nurse_id": self.db.add_nurse(p["name"], p.get("contact"), p.get("qualification", "RN"),
                                              int(p.get("max_workload", 12)))}

    def update_nurse(self, p):
        nid = int(p["nurse_id"])
        self.db.set_nurse_status(nid, bool(p.get("available", True)), bool(p.get("mobility_restricted")))
        if "contact" in p:
            self.db.set_nurse_contact(nid, p.get("contact"))
        return {"ok": True}

    def add_bell(self, p):
        self.db.add_bell(int(p["bell_id"]), int(p["bed_id"]))
        return {"ok": True}

    def delete(self, p):
        {"room": self.db.delete_room, "bed": self.db.delete_bed}[p["kind"]](int(p["id"]))
        return {"ok": True}

    def load_demo(self, _):
        return {"unit_id": seed_demo(self.db)}

    # ------------------------------------------------------------------ routing table
    def routes(self) -> Dict[tuple, Callable[[Dict], Dict]]:
        return {
            ("GET", "/api/health"): self.health, ("GET", "/api/snapshot"): self.snapshot,
            ("GET", "/api/assignments/history"): self.assignment_history, ("GET", "/api/alerts"): self.alerts,
            ("POST", "/api/assignments/evaluate"): self.evaluate,
            ("POST", "/api/assignments/recommend"): self.recommend,
            ("POST", "/api/assignments/baseline"): self.baseline,
            ("POST", "/api/assignments/commit"): self.commit,
            ("POST", "/api/assignments/manual"): self.manual_assign,
            ("POST", "/api/bell"): self.press_bell, ("POST", "/api/alerts/ack"): self.acknowledge,
            ("POST", "/api/alerts/escalate"): self.escalate,
            ("POST", "/api/setup/unit"): self.add_unit, ("POST", "/api/setup/room"): self.add_room,
            ("POST", "/api/setup/bed"): self.add_bed, ("POST", "/api/setup/patient"): self.save_patient,
            ("POST", "/api/setup/discharge"): self.discharge, ("POST", "/api/setup/nurse"): self.add_nurse,
            ("POST", "/api/setup/nurse/update"): self.update_nurse, ("POST", "/api/setup/bell"): self.add_bell,
            ("POST", "/api/setup/delete"): self.delete, ("POST", "/api/setup/demo"): self.load_demo,
        }
