"""Error prevention: check a (possibly hand-edited) plan against every rule."""
from typing import Dict, List, Set

from ..models import Plan, UnitState, Violation
from .rules import nurse_violations


def validate_plan(state: UnitState, plan: Plan) -> List[Violation]:
    out: List[Violation] = []

    for bed_id, nurse_id in plan.items():
        if bed_id not in state.beds:
            out.append(Violation("ERROR", "UNKNOWN", f"Bed {bed_id} is not part of this unit.", bed_id=bed_id))
        if nurse_id not in state.nurses:
            out.append(Violation("ERROR", "UNKNOWN", f"Nurse {nurse_id} does not exist.", nurse_id=nurse_id))

    for bed in sorted(state.beds.values(), key=lambda b: b.bed_id):
        if bed.bed_id not in plan:
            room = state.rooms[bed.room_id]
            occupied = bed.acuity is not None
            out.append(Violation(
                "ERROR" if occupied else "WARNING", "UNASSIGNED",
                f"Room {room.room_number} Bed {bed.bed_number} has no nurse"
                f"{'' if occupied else ' (empty bed, but call bells there would not route)'}.",
                bed_id=bed.bed_id))

    beds_by_nurse: Dict[int, List[int]] = {}
    for bed_id, nurse_id in plan.items():
        if bed_id in state.beds and nurse_id in state.nurses:
            beds_by_nurse.setdefault(nurse_id, []).append(bed_id)

    for nurse_id, bed_ids in sorted(beds_by_nurse.items()):
        nurse = state.nurses[nurse_id]
        load = sum(state.beds[b].acuity or 0 for b in bed_ids)
        max_acuity = max((state.beds[b].acuity or 0 for b in bed_ids), default=0)
        points = list({state.rooms[state.beds[b].room_id].point for b in bed_ids})
        out.extend(nurse_violations(nurse, load, max_acuity, points, state.station))

    for room in state.sorted_rooms():
        owners: Set[int] = {plan[b.bed_id] for b in state.beds_in_room(room.room_id) if b.bed_id in plan}
        if len(owners) > 1:
            names = ", ".join(sorted(state.nurses[o].name for o in owners if o in state.nurses))
            out.append(Violation("WARNING", "SPLIT_ROOM",
                                 f"Room {room.room_number} is split between {names} (extra walking)."))
    return out


def has_errors(violations: List[Violation]) -> bool:
    return any(v.severity == "ERROR" for v in violations)
