"""AssignmentService: what the routing system calls to create, check and save
assignments. Wraps the pure algorithms with the database."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


from ..database import Database
from ..models import Plan, UnitState, Violation
from . import metrics
from .baseline import baseline_plan
from .optimizer import OptimizationResult, optimize
from .validator import has_errors, validate_plan


@dataclass
class Recommendation:
    state: UnitState
    plan: Plan
    unassigned_rooms: List[Tuple[int, str]] = field(default_factory=list)
    violations: List[Violation] = field(default_factory=list)
    elapsed_s: float = 0.0


class AssignmentService:
    def __init__(self, db: Database):
        self.db = db

    # ----------------------------------------------------------- generate
    def load_state(self, unit_id: int) -> UnitState:
        return self.db.load_unit_state(unit_id)

    def baseline(self, unit_id: int) -> Plan:
        return baseline_plan(self.load_state(unit_id))

    def recommend(self, unit_id: int, prefer_continuity: bool = True) -> Recommendation:
        state = self.load_state(unit_id)
        previous = self.db.current_plan(unit_id) if prefer_continuity else None
        result: OptimizationResult = optimize(state, previous_plan=previous)
        return Recommendation(state, result.plan, result.unassigned_rooms,
                              validate_plan(state, result.plan), result.elapsed_s)

    # ----------------------------------------------------------- review / edit
    @staticmethod
    def apply_edit(state: UnitState, plan: Plan, target_type: str, target_id: int, nurse_id: int) -> Plan:
        """Return a copy of `plan` with a room (all its beds) or one bed given to `nurse_id`."""
        new = dict(plan)
        if target_type == "Room":
            for bed in state.beds_in_room(int(target_id)):
                new[bed.bed_id] = int(nurse_id)
        elif target_type == "Bed":
            if int(target_id) not in state.beds:
                raise ValueError(f"Bed {target_id} is not in this unit.")
            new[int(target_id)] = int(nurse_id)
        else:
            raise ValueError("Target type must be 'Room' or 'Bed'.")
        return new

    @staticmethod
    def validate(state: UnitState, plan: Plan) -> List[Violation]:
        return validate_plan(state, plan)

    @staticmethod
    def evaluate(state: UnitState, plan: Plan) -> Dict:
        return metrics.compute_metrics(state, plan)

    @staticmethod
    def compare_with_baseline(state: UnitState, plan: Plan) -> List[Dict]:
        return metrics.compare(state, baseline_plan(state), plan)

    # ----------------------------------------------------------- save
    def commit(self, unit_id: int, plan: Plan, override: bool = False) -> Tuple[bool, List[Violation]]:
        """Save `plan` as the live assignment. Blocks on rule ERRORs unless the
        charge nurse explicitly overrides (they keep final authority)."""
        state = self.load_state(unit_id)
        violations = validate_plan(state, plan)
        if has_errors(violations) and not override:
            return False, violations
        self.db.commit_plan(unit_id, plan)
        return True, violations
