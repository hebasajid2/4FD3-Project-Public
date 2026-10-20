"""Which nurse is responsible for a bed right now?"""
from dataclasses import dataclass
from typing import Optional

from ..database import Database
from ..models import Nurse


@dataclass(frozen=True)
class Resolution:
    nurse: Optional[Nurse]
    level: Optional[str]  # "BED" (override) | "ROOM" | None (nobody assigned)


def resolve_responsible_nurse(db: Database, bed_id: int) -> Resolution:
    nurse_id, level = db.resolve_assignment(bed_id)
    if nurse_id is None:
        return Resolution(None, None)
    return Resolution(db.get_nurse(nurse_id), level)
