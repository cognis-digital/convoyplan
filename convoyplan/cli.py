"""Command-line interface for CONVOYPLAN."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import PlanError, load_plan, plan_convoy, PlanResult


def _format_table(result: PlanResult) -> str:
    lines: List[str] = []
    status = "FEASIBLE" if result.feasible else "INFEASIBLE"
    lines.append(f"Mission: {result.mission}   [{status}]")
    lines.append(
        f"Vehicles: {result.vehicles}   "
        f"Total: {result.total_distance_km:.1f} km   "
        f"Fuel: {result.total_fuel_l:.1f} L"
    )
    lines.append(
        f"Usable/vehicle: {result.usable_fuel_per_vehicle_l:.1f} L   "
        f"Resupplies: {result.resupply_count}   "
        f"Max chokepoint risk: {result.max_chokepoint_risk:.0f}/100"
    )
    lines.append("")
    header = (
        f"{'LEG':<16}{'KM':>8}{'TERRAIN':>10}{'BURN(L)':>10}"
        f"{'REM(L)':>9}{'RSPLY':>7}{'CHOKE':>14}{'RISK':>6}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for lg in result.legs:
        choke = lg.chokepoint or "-"
        risk = f"{lg.chokepoint_risk:.0f}" if lg.chokepoint_risk is not None else "-"
        rsply = "yes" if lg.resupply_here else "-"
        lines.append(
            f"{lg.name[:16]:<16}{lg.distance_km:>8.1f}{lg.terrain:>10}"
            f"{lg.fuel_burn_l:>10.1f}{lg.fuel_remaining_l:>9.1f}"
            f"{rsply:>7}{choke[:14]:>14}{risk:>6}"
        )
    if result.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for w in result.warnings:
            lines.append(f"  * {w}")
    return "\n".join(lines)


def _cmd_plan(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
        result = plan_convoy(plan)
    except FileNotFoundError:
        print(f"error: plan file not found: {args.plan}", file=sys.stderr)
        return 2
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_format_table(result))

    # Exit non-zero when sustainment is infeasible (the tool's failure notion).
    return 0 if result.feasible else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Defense logistics route/sustainment planner: fuel, resupply "
            "windows, and chokepoint risk from a YAML plan (analysis only)."
        ),
    )
    parser.add_argument(
        "--version", action="version",
        version=f"{TOOL_NAME} {TOOL_VERSION}",
    )
    sub = parser.add_subparsers(dest="command")

    p_plan = sub.add_parser(
        "plan", help="Compute sustainment for a convoy plan file."
    )
    p_plan.add_argument("plan", help="Path to the YAML plan file.")
    p_plan.add_argument(
        "--format", choices=("table", "json"), default="table",
        help="Output format (default: table).",
    )
    p_plan.set_defaults(func=_cmd_plan)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
