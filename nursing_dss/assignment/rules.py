"""Single source of truth for assignment constraints.

Both the optimizer (to stay feasible) and the validator (to warn the charge
nurse about manual edits) call `nurse_violations`, so they can never disagree.
"""
import math
from typing import List, Sequence, Tuple

from .. import config
from ..models import Nurse, Violation

Point = Tuple[float, float]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def spread(points: Sequence[Point]) -> float:
    """Largest pairwise distance between the given points (0 for <2 points)."""
    best = 0.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            best = max(best, dist(points[i], points[j]))
    return best


def max_acuity_for(qualification: str) -> int:
    return config.MAX_ACUITY_BY_QUALIFICATION.get(qualification, 0)


def can_take_acuity(nurse: Nurse, acuity: int) -> bool:
    return (acuity or 0) <= max_acuity_for(nurse.qualification)


def nurse_violations(nurse: Nurse, load: int, max_acuity: int, room_points: Sequence[Point],
                     station: Point) -> List[Violation]:
    """Constraint check for ONE nurse holding rooms at `room_points`.

    load        total acuity points assigned to the nurse
    max_acuity  highest single-patient acuity among those patients
    """
    out: List[Violation] = []
    if not room_points and load == 0:
        return out
    if not nurse.available:
        out.append(Violation("ERROR", "AVAILABILITY",
                             f"{nurse.name} is marked unavailable but has patients assigned.", nurse.nurse_id))
    if load > nurse.max_workload:
        out.append(Violation("ERROR", "CAPACITY",
                             f"{nurse.name}: workload {load} exceeds capacity {nurse.max_workload}.",
                             nurse.nurse_id))
    if max_acuity > max_acuity_for(nurse.qualification):
        out.append(Violation("ERROR", "QUALIFICATION",
                             f"{nurse.name} ({nurse.qualification}) cannot take acuity-{max_acuity} patients "
                             f"(limit {max_acuity_for(nurse.qualification)}).", nurse.nurse_id))
    if nurse.mobility_restricted:
        far = [p for p in room_points if dist(p, station) > config.MOBILITY_MAX_FROM_STATION]
        if far:
            out.append(Violation("ERROR", "MOBILITY",
                                 f"{nurse.name} has a mobility restriction but has rooms more than "
                                 f"{config.MOBILITY_MAX_FROM_STATION:g} m from the station.", nurse.nurse_id))
        if spread(room_points) > config.MOBILITY_MAX_SPREAD:
            out.append(Violation("ERROR", "MOBILITY",
                                 f"{nurse.name} has a mobility restriction but rooms are spread over "
                                 f"{spread(room_points):.0f} m (limit {config.MOBILITY_MAX_SPREAD:g} m).",
                                 nurse.nurse_id))
    return out
