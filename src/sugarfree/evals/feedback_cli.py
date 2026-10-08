from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sugarfree.evals.feedback import (
    collect_failures,
    promote_failure,
    reconcile_failures,
    review_failure,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the negative-case feedback inbox.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="Collect failed cases from reports.")
    collect.add_argument("reports", nargs="+", type=Path)
    collect.add_argument("--inbox", type=Path, required=True)

    review = subparsers.add_parser("review", help="Approve or reject a candidate.")
    review.add_argument("--inbox", type=Path, required=True)
    review.add_argument("--fingerprint", required=True)
    review.add_argument("--status", choices=("approved", "rejected"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note")

    promote = subparsers.add_parser("promote", help="Link an approved failure to regression.")
    promote.add_argument("--inbox", type=Path, required=True)
    promote.add_argument("--fingerprint", required=True)
    promote.add_argument("--case-id", required=True)
    promote.add_argument("--reviewer", required=True)

    reconcile = subparsers.add_parser(
        "reconcile", help="Resolve promoted failures from a passing regression report."
    )
    reconcile.add_argument("--inbox", type=Path, required=True)
    reconcile.add_argument("--report", type=Path, required=True)
    return parser


def run_cli(args: argparse.Namespace) -> int:
    if args.command == "collect":
        inbox = collect_failures(args.reports, args.inbox)
        candidates = sum(item["status"] == "candidate" for item in inbox["items"])
        print(f"Failure inbox: {args.inbox}")
        print(f"Items: {len(inbox['items'])}; candidates: {candidates}")
        return 0
    if args.command == "review":
        item = review_failure(
            args.inbox,
            args.fingerprint,
            args.status,
            args.reviewer,
            args.note,
        )
        print(f"Failure {item['fingerprint']}: {item['status']}")
        return 0
    if args.command == "promote":
        item = promote_failure(
            args.inbox,
            args.fingerprint,
            args.case_id,
            args.reviewer,
        )
        print(f"Failure {item['fingerprint']} linked to {item['regression_case_id']}")
        return 0
    if args.command == "reconcile":
        inbox = reconcile_failures(args.inbox, args.report)
        resolved = sum(item["status"] == "resolved" for item in inbox["items"])
        print(f"Failure inbox: {args.inbox}")
        print(f"Resolved items: {resolved}")
        return 0
    raise ValueError(f"unknown command: {args.command}")


def main() -> None:
    try:
        raise SystemExit(run_cli(build_parser().parse_args()))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
