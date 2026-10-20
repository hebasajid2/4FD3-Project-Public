"""ROUTING SYSTEM - the hub every other part of the application connects to.

        INPUT                    ROUTING SYSTEM                       OUTPUT
   call bell signal  ->  resolve nurse (bed > room)         ->  Notifier.send()
   (input.signals)       fall back / escalate on failure         (output.notifier)
                         write Alerts + Notification_Logs
                               ^            ^           ^
                  Frontend ----+            |           +---- Database
                                 AssignmentService (self.assignments)

The frontend never talks to the database or the optimizer directly for
operational work; it calls this object.
"""
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from .. import config
from ..assignment.service import AssignmentService
from ..database import Database
from ..input.signals import CallBellSignal
from ..models import BellContext, Nurse
from ..output.notifier import DeliveryResult, Notifier, SimulatedNotifier
from ..timeutil import iso, now_utc, parse_iso
from .escalation import rank_backup_nurses
from .resolver import resolve_responsible_nurse


@dataclass
class RoutingResult:
    ok: bool                       # delivered to somebody's device
    status: str                    # final alert status (or UNKNOWN_BELL)
    bell_id: int
    alert_id: Optional[int] = None
    nurse_id: Optional[int] = None
    nurse_name: Optional[str] = None
    channel: Optional[str] = None
    room_number: Optional[str] = None
    bed_number: Optional[str] = None
    assignment_level: Optional[str] = None  # BED | ROOM | None
    flags: List[str] = field(default_factory=list)  # problems the user must see
    attempts: List[Dict] = field(default_factory=list)
    routing_latency_ms: float = 0.0
    notify_latency_ms: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


class RoutingSystem:
    def __init__(self, db: Database, notifier: Optional[Notifier] = None,
                 ack_timeout_s: int = config.ACK_TIMEOUT_SECONDS,
                 clock: Callable[[], datetime] = now_utc):
        self.db = db
        self.notifier: Notifier = notifier or SimulatedNotifier()
        self.ack_timeout_s = ack_timeout_s
        self.clock = clock
        self.assignments = AssignmentService(db)

    # ------------------------------------------------------------------ INPUT -> OUTPUT
    def handle_signal(self, signal: CallBellSignal) -> RoutingResult:
        t0 = time.perf_counter()
        ctx = self.db.get_bell_context(signal.bell_id)
        if ctx is None:
            return RoutingResult(False, "UNKNOWN_BELL", signal.bell_id,
                                 flags=[f"Call bell {signal.bell_id} is not registered. No alert was created."])

        stamp = iso(signal.received_at)
        res = resolve_responsible_nurse(self.db, ctx.bed_id)
        result = RoutingResult(False, "DELIVERY_FAILED", signal.bell_id, room_number=ctx.room_number,
                               bed_number=ctx.bed_number, assignment_level=res.level)

        if res.nurse is None:
            # Nobody is assigned: flag it, still try to get a human to respond.
            alert_id = self.db.create_alert(signal.bell_id, None, "ESCALATED_UNASSIGNED", stamp)
            result.status, result.alert_id = "ESCALATED_UNASSIGNED", alert_id
            result.flags.append(f"No nurse is assigned to Room {ctx.room_number} Bed {ctx.bed_number}. "
                                "Falling back to the charge nurse / closest available nurse.")
            for nurse in rank_backup_nurses(self.db, ctx, set(), charge_first=True):
                if self._deliver(alert_id, nurse, ctx, result, signal.received_at):
                    result.ok = True
                    break
            if not result.ok:
                result.flags.append("No fallback nurse could be reached. MANUAL RESPONSE REQUIRED.")
            return self._finish(result, t0)

        primary = res.nurse
        alert_id = self.db.create_alert(signal.bell_id, primary.nurse_id, "DISPATCHED", stamp)
        result.status, result.alert_id = "DISPATCHED", alert_id
        if self._deliver(alert_id, primary, ctx, result, signal.received_at):
            result.ok = True
            return self._finish(result, t0)

        # Primary could not be reached (no device / unavailable / delivery failure): reroute.
        self.db.set_alert_status(alert_id, "DELIVERY_FAILED")
        result.status = "DELIVERY_FAILED"
        parent, tried = alert_id, {primary.nurse_id}
        for nurse in rank_backup_nurses(self.db, ctx, tried):
            child = self.db.create_alert(signal.bell_id, nurse.nurse_id, "DISPATCHED", stamp, parent)
            result.alert_id = child
            if self._deliver(child, nurse, ctx, result, signal.received_at):
                result.ok, result.status = True, "DISPATCHED"
                result.flags.append(f"Rerouted to {nurse.name}.")
                return self._finish(result, t0)
            self.db.set_alert_status(child, "DELIVERY_FAILED")
            parent = child
        result.flags.append("No nurse could be reached. MANUAL RESPONSE REQUIRED.")
        return self._finish(result, t0)

    # ------------------------------------------------------------------ acknowledgement
    def acknowledge(self, alert_id: int, when: Optional[datetime] = None) -> float:
        """Record that the nurse acknowledged. Returns response time in seconds."""
        when = when or self.clock()
        alert = self.db.get_alert(alert_id)
        if alert is None:
            raise ValueError(f"Alert {alert_id} does not exist.")
        if alert["acknowledged_at"]:
            raise ValueError(f"Alert {alert_id} was already acknowledged.")
        self.db.acknowledge_alert(alert_id, iso(when))
        return (when - parse_iso(alert["timestamp"])).total_seconds()

    # ------------------------------------------------------------------ escalation
    def escalate_pending(self, now: Optional[datetime] = None) -> List[RoutingResult]:
        """Pass every alert that has gone un-acknowledged for `ack_timeout_s` to the
        closest available nurse. Call this periodically (or from the UI demo button)."""
        now = now or self.clock()
        cutoff = iso(now - timedelta(seconds=self.ack_timeout_s))
        return [self._escalate(alert, now) for alert in self.db.find_unacknowledged(cutoff)]

    def _escalate(self, alert: Dict, now: datetime) -> RoutingResult:
        t0 = time.perf_counter()
        ctx = self.db.get_bell_context(alert["bell_id"])
        result = RoutingResult(False, "ESCALATION_FAILED", alert["bell_id"], alert_id=alert["alert_id"],
                               room_number=ctx.room_number if ctx else None,
                               bed_number=ctx.bed_number if ctx else None)
        result.flags.append(f"Alert {alert['alert_id']} was not acknowledged within {self.ack_timeout_s}s.")
        tried = set(self.db.alert_chain_nurse_ids(alert["alert_id"]))
        if ctx is None or len(tried) >= config.MAX_ESCALATION_LEVELS:
            self.db.set_alert_status(alert["alert_id"], "ESCALATION_FAILED")
            result.flags.append("Escalation limit reached. MANUAL RESPONSE REQUIRED.")
            return self._finish(result, t0)

        parent = alert["alert_id"]
        for nurse in rank_backup_nurses(self.db, ctx, tried):
            child = self.db.create_alert(alert["bell_id"], nurse.nurse_id, "DISPATCHED", iso(now), parent)
            if self._deliver(child, nurse, ctx, result, now):
                self.db.set_alert_status(alert["alert_id"], "ESCALATED")
                result.ok, result.status, result.alert_id = True, "DISPATCHED", child
                result.flags.append(f"Escalated to {nurse.name}.")
                return self._finish(result, t0)
            self.db.set_alert_status(child, "DELIVERY_FAILED")
            parent = child
        self.db.set_alert_status(alert["alert_id"], "ESCALATION_FAILED")
        result.flags.append("No backup nurse could be reached. MANUAL RESPONSE REQUIRED.")
        return self._finish(result, t0)

    # ------------------------------------------------------------------ internals
    def _deliver(self, alert_id: int, nurse: Nurse, ctx: BellContext, result: RoutingResult,
                 when: datetime) -> bool:
        message = f"Call bell: Room {ctx.room_number} - Bed {ctx.bed_number}"
        if not nurse.available:
            outcome = DeliveryResult(False, "UNAVAILABLE", f"{nurse.name} is marked unavailable.", 0.0)
        elif not nurse.has_device:
            outcome = DeliveryResult(False, "NO_DEVICE", f"{nurse.name} has no linked device.", 0.0)
        else:
            try:
                outcome = self.notifier.send(nurse, message)
            except Exception as exc:  # a broken notifier must never crash or hide an alert
                outcome = DeliveryResult(False, f"Device: {nurse.contact}", f"Notifier error: {exc}", 0.0)

        self.db.log_notification(alert_id, outcome.channel, outcome.ok, iso(when),
                                 outcome.latency_ms, outcome.detail)
        result.attempts.append({"alert_id": alert_id, "nurse": nurse.name, "channel": outcome.channel,
                                "delivered": outcome.ok, "detail": outcome.detail})
        if outcome.ok:
            result.nurse_id, result.nurse_name = nurse.nurse_id, nurse.name
            result.channel, result.notify_latency_ms = outcome.channel, outcome.latency_ms
            result.alert_id = alert_id
        else:
            result.flags.append(f"Could not reach {nurse.name}: {outcome.detail}")
        return outcome.ok

    @staticmethod
    def _finish(result: RoutingResult, t0: float) -> RoutingResult:
        result.routing_latency_ms = (time.perf_counter() - t0) * 1000.0
        return result
