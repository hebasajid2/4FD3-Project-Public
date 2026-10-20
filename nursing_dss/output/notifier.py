"""OUTPUT stage: push an alert to a nurse's device.

`Notifier` is the interface. Only `SimulatedNotifier` exists in this prototype;
a real SMS / push / pager integration would be another subclass with the same
`send` method, and nothing else in the system would change.
"""
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, List, Set

from ..models import Nurse
from ..timeutil import iso, now_utc


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    channel: str       # e.g. "Device: EXT-401"
    detail: str
    latency_ms: float


class Notifier(ABC):
    @abstractmethod
    def send(self, nurse: Nurse, message: str) -> DeliveryResult:
        """Deliver `message` to the nurse's device; never raise for delivery problems."""


class SimulatedNotifier(Notifier):
    """In-memory 'phones'. Keeps an outbox per device and can simulate failures."""

    def __init__(self, delay_s: float = 0.0, fail_contacts: Iterable[str] = ()):
        self.delay_s = delay_s
        self.fail_contacts: Set[str] = set(fail_contacts)
        self._outbox: List[Dict] = []
        self._lock = threading.Lock()

    def send(self, nurse: Nurse, message: str) -> DeliveryResult:
        start = time.perf_counter()
        channel = f"Device: {nurse.contact}"
        if self.delay_s:
            time.sleep(self.delay_s)
        if nurse.contact in self.fail_contacts:
            return DeliveryResult(False, channel, "Simulated device failure.",
                                  (time.perf_counter() - start) * 1000)
        with self._lock:
            self._outbox.append({"time": iso(now_utc()), "device": nurse.contact,
                                 "nurse": nurse.name, "message": message})
        return DeliveryResult(True, channel, "Delivered to simulated device.",
                              (time.perf_counter() - start) * 1000)

    @property
    def outbox(self) -> List[Dict]:
        with self._lock:
            return list(self._outbox)

    def clear(self) -> None:
        with self._lock:
            self._outbox.clear()
