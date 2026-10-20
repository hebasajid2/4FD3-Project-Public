"""Choosing a backup nurse when the responsible one cannot be reached or does
not acknowledge in time."""
from typing import Dict, List, Set

from ..assignment.rules import can_take_acuity, dist
from ..database import Database
from ..models import BellContext, Nurse


def rank_backup_nurses(db: Database, ctx: BellContext, exclude: Set[int],
                       charge_first: bool = False) -> List[Nurse]:
    """Eligible backup nurses, closest first.

    Eligible = available, has a device, not already tried, and qualified for the
    patient's acuity. 'Closest' = distance from the bell's room to the nearest
    room that nurse currently covers (or to the station if they cover none).
    """
    state = db.load_unit_state(ctx.unit_id)
    plan = db.current_plan(ctx.unit_id)
    rooms_of: Dict[int, set] = {}
    for bed_id, nurse_id in plan.items():
        if bed_id in state.beds:
            rooms_of.setdefault(nurse_id, set()).add(state.beds[bed_id].room_id)

    here = (ctx.x, ctx.y)
    ranked = []
    for nurse in state.nurses.values():
        if nurse.nurse_id in exclude or not nurse.available or not nurse.has_device:
            continue
        if not can_take_acuity(nurse, ctx.acuity or 0):
            continue
        points = [state.rooms[r].point for r in rooms_of.get(nurse.nurse_id, ())] or [state.station]
        distance = min(dist(p, here) for p in points)
        priority = 0 if (charge_first and nurse.qualification == "CHARGE") else 1
        ranked.append(((priority, distance, nurse.nurse_id), nurse))
    return [n for _, n in sorted(ranked, key=lambda t: t[0])]
