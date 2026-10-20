"""Plain data classes shared by every layer (no database or UI imports)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import config

# A plan maps bed_id -> nurse_id. Bed level is the finest grain, so room-level
# and bed-level assignments are both expressed the same way.
Plan = Dict[int, int]


def natural_key(text: str):
    """Sort '9' before '10' and numbers before words."""
    try:
        return (0, int(text), "")
    except (TypeError, ValueError):
        return (1, 0, str(text))


@dataclass(frozen=True)
class Nurse:
    nurse_id: int
    name: str
    contact: Optional[str]
    qualification: str
    max_workload: int
    available: bool = True
    mobility_restricted: bool = False

    @property
    def has_device(self) -> bool:
        return bool(self.contact and self.contact.strip())


@dataclass(frozen=True)
class Room:
    room_id: int
    room_number: str
    unit_id: int
    x: float
    y: float

    @property
    def point(self) -> Tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True)
class Bed:
    bed_id: int
    bed_number: str
    room_id: int
    acuity: Optional[int] = None  # None = no patient in the bed


@dataclass(frozen=True)
class BellContext:
    """Everything the routing system needs to know about one call bell."""
    bell_id: int
    bed_id: int
    bed_number: str
    room_id: int
    room_number: str
    unit_id: int
    x: float
    y: float
    acuity: Optional[int]


@dataclass(frozen=True)
class Violation:
    severity: str  # "ERROR" | "WARNING"
    code: str      # CAPACITY, QUALIFICATION, MOBILITY, AVAILABILITY, UNASSIGNED, SPLIT_ROOM, UNKNOWN
    message: str
    nurse_id: Optional[int] = None
    bed_id: Optional[int] = None


@dataclass
class UnitState:
    unit_id: int
    unit_name: str
    rooms: Dict[int, Room]
    beds: Dict[int, Bed]
    nurses: Dict[int, Nurse]
    station: Tuple[float, float] = config.STATION_COORD

    def sorted_rooms(self) -> List[Room]:
        return sorted(self.rooms.values(), key=lambda r: (natural_key(r.room_number), r.room_id))

    def beds_in_room(self, room_id: int) -> List[Bed]:
        return sorted((b for b in self.beds.values() if b.room_id == room_id),
                      key=lambda b: (b.bed_number, b.bed_id))

    def room_load(self, room_id: int) -> int:
        return sum(b.acuity or 0 for b in self.beds_in_room(room_id))

    def room_max_acuity(self, room_id: int) -> int:
        return max((b.acuity or 0 for b in self.beds_in_room(room_id)), default=0)

    def available_nurses(self) -> List[Nurse]:
        return sorted((n for n in self.nurses.values() if n.available), key=lambda n: n.nurse_id)
