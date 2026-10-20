"""Walking-route estimation: nearest-neighbour construction + 2-opt improvement.

A route starts at the nurses' station, visits every assigned room once and
does not return (open path). Distances are straight-line (Euclidean)."""
from typing import Dict, List, Sequence, Tuple

from ..models import Plan, UnitState
from .rules import Point, dist, spread


def path_length(start: Point, order: Sequence[int], items: Dict[int, Point]) -> float:
    total, here = 0.0, start
    for key in order:
        total += dist(here, items[key])
        here = items[key]
    return total


def _nearest_neighbour(start: Point, items: Dict[int, Point]) -> List[int]:
    remaining = sorted(items)
    order: List[int] = []
    here = start
    while remaining:
        nxt = min(remaining, key=lambda k: (dist(here, items[k]), k))
        order.append(nxt)
        remaining.remove(nxt)
        here = items[nxt]
    return order


def _two_opt(start: Point, order: List[int], items: Dict[int, Point]) -> List[int]:
    best = list(order)
    best_len = path_length(start, best, items)
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                trial = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                trial_len = path_length(start, trial, items)
                if trial_len + 1e-9 < best_len:
                    best, best_len, improved = trial, trial_len, True
    return best


def best_route(items: Dict[int, Point], start: Point) -> Tuple[List[int], float]:
    """(visit order of room ids, total walking distance) for the given rooms."""
    if not items:
        return [], 0.0
    order = _two_opt(start, _nearest_neighbour(start, items), items)
    return order, path_length(start, order, items)


def nurse_routes(state: UnitState, plan: Plan) -> Dict[int, Dict]:
    """Per nurse: ordered room ids, walking distance, and walking spread."""
    rooms_by_nurse: Dict[int, set] = {}
    for bed_id, nurse_id in plan.items():
        bed = state.beds.get(bed_id)
        if bed is not None and bed.room_id in state.rooms:
            rooms_by_nurse.setdefault(nurse_id, set()).add(bed.room_id)
    out: Dict[int, Dict] = {}
    for nurse_id, room_ids in rooms_by_nurse.items():
        items = {rid: state.rooms[rid].point for rid in room_ids}
        order, length = best_route(items, state.station)
        out[nurse_id] = {"rooms": order, "distance": length, "spread": spread(list(items.values()))}
    return out
