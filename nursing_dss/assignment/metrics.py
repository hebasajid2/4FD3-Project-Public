"""Operational indicators for the dashboard and for baseline-vs-optimizer evaluation."""
from statistics import pstdev
from typing import Dict, List

from ..models import Plan, UnitState
from .travel import nurse_routes
from .validator import validate_plan


def compute_metrics(state: UnitState, plan: Plan) -> Dict:
    """{'per_nurse': [...], 'summary': {...}} for the nurses on duty (available)."""
    routes = nurse_routes(state, plan)
    per_nurse: List[Dict] = []
    for nurse in state.available_nurses():
        bed_ids = [b for b, n in plan.items() if n == nurse.nurse_id and b in state.beds]
        load = sum(state.beds[b].acuity or 0 for b in bed_ids)
        route = routes.get(nurse.nurse_id, {"rooms": [], "distance": 0.0, "spread": 0.0})
        per_nurse.append({
            "nurse_id": nurse.nurse_id,
            "name": nurse.name,
            "qualification": nurse.qualification,
            "rooms": [state.rooms[r].room_number for r in route["rooms"]],
            "room_ids": list(route["rooms"]),
            "beds": len(bed_ids),
            "load": load,
            "capacity": nurse.max_workload,
            "utilization_pct": 100.0 * load / nurse.max_workload,
            "travel_m": route["distance"],
            "spread_m": route["spread"],
        })

    loads = [p["load"] for p in per_nurse]
    utils = [p["utilization_pct"] for p in per_nurse]
    violations = validate_plan(state, plan)
    summary = {
        "nurses_on_duty": len(per_nurse),
        "total_load": sum(loads),
        "workload_std": pstdev(loads) if loads else 0.0,
        "utilization_std_pct": pstdev(utils) if utils else 0.0,
        "max_utilization_pct": max(utils, default=0.0),
        "total_travel_m": sum(p["travel_m"] for p in per_nurse),
        "max_walk_spread_m": max((p["spread_m"] for p in per_nurse), default=0.0),
        "overloaded_nurses": sum(1 for p in per_nurse if p["load"] > p["capacity"]),
        "unassigned_beds": sum(1 for v in violations if v.code == "UNASSIGNED" and v.severity == "ERROR"),
        "rule_errors": sum(1 for v in violations if v.severity == "ERROR"),
    }
    return {"per_nurse": per_nurse, "summary": summary}


METRIC_LABELS = [
    ("workload_std", "Workload std-dev (acuity points)"),
    ("utilization_std_pct", "Utilization std-dev (pct. points)"),
    ("max_utilization_pct", "Max nurse utilization (%)"),
    ("total_travel_m", "Total walking distance (m)"),
    ("max_walk_spread_m", "Max walking spread (m)"),
    ("overloaded_nurses", "Nurses over capacity"),
    ("unassigned_beds", "Unassigned occupied beds"),
    ("rule_errors", "Rule violations (errors)"),
]


def compare(state: UnitState, baseline: Plan, candidate: Plan) -> List[Dict]:
    """Side-by-side rows. For every metric a LOWER value is better."""
    b = compute_metrics(state, baseline)["summary"]
    c = compute_metrics(state, candidate)["summary"]
    rows = []
    for key, label in METRIC_LABELS:
        base, cand = b[key], c[key]
        change = "" if base == 0 else f"{100.0 * (cand - base) / base:+.0f}%"
        rows.append({"metric": label, "baseline": round(base, 2),
                     "candidate": round(cand, 2), "change": change})
    return rows
