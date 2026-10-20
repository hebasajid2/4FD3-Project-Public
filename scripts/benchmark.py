"""Baseline vs optimizer over many random units (the evaluation objective).

    python scripts/benchmark.py --runs 200 --rooms 15 --nurses 6
"""
import argparse
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nursing_dss.assignment import metrics  # noqa: E402
from nursing_dss.assignment.baseline import baseline_plan  # noqa: E402
from nursing_dss.assignment.optimizer import optimize  # noqa: E402
from nursing_dss.assignment.scenarios import make_scenario  # noqa: E402

COLUMNS = [
    ("utilization_std_pct", "Utilization std-dev (pct. points)"),
    ("workload_std", "Workload std-dev (acuity points)"),
    ("max_utilization_pct", "Max nurse utilization (%)"),
    ("total_travel_m", "Total walking distance (m)"),
    ("max_walk_spread_m", "Max walking spread (m)"),
    ("rule_errors", "Rule violations per unit"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--rooms", type=int, default=15)
    ap.add_argument("--beds-per-room", type=int, default=2)
    ap.add_argument("--nurses", type=int, default=6)
    args = ap.parse_args()

    base, opt, secs, unplaced = [], [], [], 0
    for seed in range(args.runs):
        state = make_scenario(seed, args.rooms, args.beds_per_room, args.nurses)
        base.append(metrics.compute_metrics(state, baseline_plan(state))["summary"])
        result = optimize(state)
        opt.append(metrics.compute_metrics(state, result.plan)["summary"])
        secs.append(result.elapsed_s)
        unplaced += len(result.unassigned_rooms)

    print(f"{args.runs} random units: {args.rooms} rooms x {args.beds_per_room} beds, {args.nurses} nurses\n")
    print(f"{'Metric':38s} {'Baseline mean±sd':>20s} {'Optimized mean±sd':>20s}")
    for key, label in COLUMNS:
        b, o = [r[key] for r in base], [r[key] for r in opt]
        print(f"{label:38s} {st.mean(b):>12.2f} ±{st.pstdev(b):6.2f} {st.mean(o):>12.2f} ±{st.pstdev(o):6.2f}")
    print(f"\nRooms the optimizer could not place: {unplaced}")
    print(f"Optimizer time per unit: mean {1000 * st.mean(secs):.1f} ms, max {1000 * max(secs):.1f} ms")


if __name__ == "__main__":
    main()
