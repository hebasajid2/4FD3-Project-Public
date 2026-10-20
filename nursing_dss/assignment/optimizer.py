"""Workload-balancing, distance-aware assignment optimizer.

Unit of work = a ROOM (all beds of a room go to the same nurse: continuity and
less walking). Method = greedy construction followed by local search
(move / swap) minimizing

    cost = W_BALANCE * imbalance + W_DISTANCE * total_walk + W_CONTINUITY * rooms_changed

subject to the hard rules in `rules.nurse_violations` (capacity, qualification,
availability, mobility). Deterministic: same input -> same plan.
"""
import time
from dataclasses import dataclass, field
from itertools import combinations
from statistics import pstdev
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from .. import config
from ..models import Plan, UnitState
from .rules import nurse_violations
from .travel import best_route


@dataclass
class OptimizationResult:
    plan: Plan
    unassigned_rooms: List[Tuple[int, str]] = field(default_factory=list)  # (room_id, reason)
    objective: float = 0.0
    passes: int = 0
    elapsed_s: float = 0.0


def optimize(state: UnitState, previous_plan: Optional[Plan] = None,
             w_balance: float = config.W_BALANCE, w_distance: float = config.W_DISTANCE,
             w_continuity: float = config.W_CONTINUITY,
             max_passes: int = config.MAX_LOCAL_SEARCH_PASSES) -> OptimizationResult:
    t0 = time.perf_counter()
    nurses = state.available_nurses()
    rooms = [r for r in state.sorted_rooms() if state.beds_in_room(r.room_id)]
    if not nurses or not rooms:
        reason = "No available nurses." if not nurses else "No beds."
        return OptimizationResult({}, [(r.room_id, reason) for r in rooms],
                                  elapsed_s=time.perf_counter() - t0)

    nurse_by_id = {n.nurse_id: n for n in nurses}
    load = {r.room_id: state.room_load(r.room_id) for r in rooms}
    top = {r.room_id: state.room_max_acuity(r.room_id) for r in rooms}
    point = {r.room_id: r.point for r in rooms}

    # Who looked after each room before (majority of its beds), for continuity.
    previous_owner: Dict[int, int] = {}
    if previous_plan:
        for r in rooms:
            owners = [previous_plan[b.bed_id] for b in state.beds_in_room(r.room_id) if b.bed_id in previous_plan]
            if owners:
                previous_owner[r.room_id] = max(set(owners), key=owners.count)

    route_cache: Dict[FrozenSet[int], float] = {}

    def walk(room_ids: FrozenSet[int]) -> float:
        if room_ids not in route_cache:
            route_cache[room_ids] = best_route({rid: point[rid] for rid in room_ids}, state.station)[1]
        return route_cache[room_ids]

    def feasible(nurse_id: int, room_ids: Set[int]) -> bool:
        nurse = nurse_by_id[nurse_id]
        return not nurse_violations(
            nurse, sum(load[r] for r in room_ids), max((top[r] for r in room_ids), default=0),
            [point[r] for r in room_ids], state.station)

    assign: Dict[int, Set[int]] = {n.nurse_id: set() for n in nurses}
    owner: Dict[int, int] = {}

    def cost() -> float:
        if config.BALANCE_MODE == "utilization":
            values = [100.0 * sum(load[r] for r in assign[n.nurse_id]) / n.max_workload for n in nurses]
            balance = pstdev(values)
        else:
            balance = 10.0 * pstdev([sum(load[r] for r in assign[n.nurse_id]) for n in nurses])
        distance = sum(walk(frozenset(assign[n.nurse_id])) for n in nurses)
        changed = sum(1 for rid, nid in owner.items()
                      if rid in previous_owner and previous_owner[rid] in nurse_by_id
                      and previous_owner[rid] != nid)
        return w_balance * balance + w_distance * distance + w_continuity * changed

    def place(room_id: int, nurse_id: int) -> None:
        assign[nurse_id].add(room_id)
        owner[room_id] = nurse_id

    def unplace(room_id: int) -> int:
        nurse_id = owner.pop(room_id)
        assign[nurse_id].discard(room_id)
        return nurse_id

    def construct(pending: List[int]) -> List[int]:
        """Greedily place rooms (heaviest first); return the rooms that fit nobody."""
        left: List[int] = []
        for rid in sorted(pending, key=lambda r: (-load[r], -top[r], r)):
            best_nurse, best_cost = None, None
            for n in nurses:
                if not feasible(n.nurse_id, assign[n.nurse_id] | {rid}):
                    continue
                place(rid, n.nurse_id)
                c = cost()
                unplace(rid)
                if best_cost is None or c < best_cost - 1e-12:
                    best_nurse, best_cost = n.nurse_id, c
            if best_nurse is None:
                left.append(rid)
            else:
                place(rid, best_nurse)
        return left

    def local_search() -> int:
        passes, improved = 0, True
        current = cost()
        while improved and passes < max_passes:
            improved, passes = False, passes + 1
            # moves
            for rid in sorted(owner):
                src = owner[rid]
                for n in nurses:
                    dst = n.nurse_id
                    if dst == src or not feasible(dst, assign[dst] | {rid}):
                        continue
                    unplace(rid)
                    place(rid, dst)
                    c = cost()
                    if c < current - 1e-9:
                        current, improved = c, True
                        break
                    unplace(rid)
                    place(rid, src)
            # swaps
            for r1, r2 in combinations(sorted(owner), 2):
                a, b = owner[r1], owner[r2]
                if a == b:
                    continue
                if not (feasible(a, (assign[a] - {r1}) | {r2}) and feasible(b, (assign[b] - {r2}) | {r1})):
                    continue
                unplace(r1)
                unplace(r2)
                place(r1, b)
                place(r2, a)
                c = cost()
                if c < current - 1e-9:
                    current, improved = c, True
                else:
                    unplace(r1)
                    unplace(r2)
                    place(r1, a)
                    place(r2, b)
        return passes

    unplaced = construct([r.room_id for r in rooms])
    total_passes = local_search()
    for _ in range(3):  # loads changed during search: retry rooms that did not fit
        if not unplaced:
            break
        still = construct(unplaced)
        if len(still) == len(unplaced):
            break
        unplaced = still
        total_passes += local_search()

    plan: Plan = {}
    for rid, nid in owner.items():
        for bed in state.beds_in_room(rid):
            plan[bed.bed_id] = nid
    reason = ("No available nurse can take this room without breaking a capacity, "
              "qualification or mobility rule.")
    return OptimizationResult(plan, [(rid, reason) for rid in sorted(unplaced)], cost(),
                              total_passes, time.perf_counter() - t0)
