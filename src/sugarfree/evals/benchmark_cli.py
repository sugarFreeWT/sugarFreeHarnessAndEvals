from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sugarfree.evals.benchmark import build_benchmark


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare one or more sugarfree eval report.json files."
    )
    parser.add_argument(
        "reports",
        type=Path,
        nargs="+",
        help="Reports ordered from oldest to newest",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the benchmark JSON to this path",
    )
    return parser


def run_cli(args: argparse.Namespace) -> int:
    benchmark = build_benchmark(args.reports)
    payload = json.dumps(benchmark, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")

    print(f"Suite: {benchmark['suite']}")
    print(f"Runs: {benchmark['run_count']}")
    for run in benchmark["runs"]:
        print(
            f"- {Path(run['path']).parent.name}: "
            f"{run['passed_cases']}/{run['total_cases']} passed, "
            f"steps={run['average_steps']:.2f}, "
            f"tools={run['average_tool_calls']:.2f}, "
            f"tokens={run['average_tokens']:.2f}, "
            f"elapsed={run['average_elapsed_seconds']:.3f}s"
        )
    for index, comparison in enumerate(benchmark["comparisons"], start=1):
        delta = comparison["delta"]
        changes = comparison["case_changes"]
        print(
            f"Delta {index}: pass_rate={delta['pass_rate']:+.3f}, "
            f"steps={delta['average_steps']:+.3f}, "
            f"tools={delta['average_tool_calls']:+.3f}, "
            f"tokens={delta['average_tokens']:+.3f}, "
            f"elapsed={delta['average_elapsed_seconds']:+.3f}s"
        )
        if changes["new_failures"]:
            print(f"  New failures: {', '.join(changes['new_failures'])}")
        if changes["fixed_failures"]:
            print(f"  Fixed failures: {', '.join(changes['fixed_failures'])}")
    if args.output:
        print(f"Benchmark: {args.output}")
    return 0


def main() -> None:
    try:
        raise SystemExit(run_cli(build_parser().parse_args()))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

