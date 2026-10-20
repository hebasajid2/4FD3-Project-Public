"""The predefined manual/baseline method the optimizer is compared against.

Models what a busy charge nurse does with a paper sheet: walk the corridor in
room-number order and hand each available nurse the next block of rooms, with
the same number of rooms each. It ignores acuity, capacity and qualifications.
"""
from ..models import Plan, UnitState


def baseline_plan(state: UnitState) -> Plan:
    nurses = state.available_nurses()
    rooms = [r for r in state.sorted_rooms() if state.beds_in_room(r.room_id)]
    plan: Plan = {}
    if not nurses or not rooms:
        return plan
    base, extra = divmod(len(rooms), len(nurses))
    index = 0
    for k, nurse in enumerate(nurses):
        size = base + (1 if k < extra else 0)
        for room in rooms[index:index + size]:
            for bed in state.beds_in_room(room.room_id):
                plan[bed.bed_id] = nurse.nurse_id
        index += size
    return plan
