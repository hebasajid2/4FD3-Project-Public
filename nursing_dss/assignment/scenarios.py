"""Synthetic units for benchmarking baseline vs optimizer (no database needed)."""
import random
from typing import Optional

from ..models import Bed, Nurse, Room, UnitState


def make_scenario(seed: int, n_rooms: int = 15, beds_per_room: int = 2, n_nurses: int = 6,
                  corridor_columns: int = 5, spacing: float = 15.0,
                  occupancy: float = 0.9) -> UnitState:
    """A grid-shaped unit with random acuities and a plausible mix of nurses.

    Nurse capacities are sized so that the unit is feasible but not trivial
    (total capacity about 1.4x the total load)."""
    rng = random.Random(seed)
    rooms, beds = {}, {}
    bed_id = 1
    for i in range(n_rooms):
        col, row = i % corridor_columns, i // corridor_columns
        room_id = i + 1
        rooms[room_id] = Room(room_id, str(101 + i), 1, col * spacing + 10.0, row * 10.0)
        for k in range(beds_per_room):
            acuity = rng.choice([1, 1, 2, 2, 3, 3, 4, 5]) if rng.random() < occupancy else None
            beds[bed_id] = Bed(bed_id, chr(ord("A") + k), room_id, acuity)
            bed_id += 1
    total = sum(b.acuity or 0 for b in beds.values())
    per_nurse = max(6, round(1.4 * total / n_nurses))
    nurses = {}
    for j in range(n_nurses):
        qual = "RPN" if j >= n_nurses - max(1, n_nurses // 3) else ("CHARGE" if j == 0 else "RN")
        cap = per_nurse + rng.choice([-2, 0, 0, 2]) - (2 if qual == "RPN" else 0)
        nurses[j + 1] = Nurse(j + 1, f"Nurse {j + 1}", f"EXT-{400 + j}", qual, max(6, cap))
    return UnitState(1, f"Synthetic-{seed}", rooms, beds, nurses)
