from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sugarfree.evals.gate import evaluate_release_gate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate release gates from eval reports.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    result = evaluate_release_gate(
        config_path=args.config,
        current_report=args.current,
        replay_report=args.replay,
        baseline_report=args.baseline,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    marker = "PASS" if result.passed else "FAIL"
    print(f"Release gate: {result.name} [{marker}]")
    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.actual}")
    print(f"Gate report: {args.output}")
    return 0 if result.passed else 1


def main() -> None:
    try:
        raise SystemExit(run_cli(build_parser().parse_args()))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
