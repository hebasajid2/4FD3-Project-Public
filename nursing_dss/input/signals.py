"""INPUT stage: turn anything a call bell can send into one validated object."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..timeutil import now_utc


class InvalidSignal(ValueError):
    """The incoming payload is not a usable call bell signal."""


@dataclass(frozen=True)
class CallBellSignal:
    bell_id: int
    received_at: datetime
    source: str = "SIMULATED"  # SIMULATED | HTTP | HARDWARE


def parse_signal(raw: Any, source: str = "SIMULATED", received_at: Optional[datetime] = None) -> CallBellSignal:
    """Accepts 1011, "1011", or {"bell_id": 1011}. Raises InvalidSignal otherwise."""
    value = raw.get("bell_id") if isinstance(raw, dict) else raw
    if isinstance(value, bool) or value is None:
        raise InvalidSignal("Missing bell_id.")
    try:
        bell_id = int(str(value).strip())
    except (TypeError, ValueError):
        raise InvalidSignal(f"bell_id must be an integer, got {value!r}.") from None
    if bell_id <= 0:
        raise InvalidSignal("bell_id must be positive.")
    return CallBellSignal(bell_id, received_at or now_utc(), source)
