from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sugarfree.core.agent import Agent, AgentConfig
from sugarfree.core.events import EventSink
from sugarfree.evals.cases import EvalCase, load_suite
from sugarfree.evals.runner import EvalRunner
from sugarfree.evals.runtime import default_tools
from sugarfree.models.replay import ReplayModel
from sugarfree.fs.workspace import WorkspaceFileStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay an Agent evaluation from JSONL trajectories.")
    parser.add_argument("suite", type=Path, help="Original eval suite used for the run")
    parser.add_argument("--trajectories", type=Path, required=True, help="Directory containing case JSONL files")
    parser.add_argument("--output-dir", type=Path, help="Directory for replay report and trajectories")
    parser.add_argument("--no-validate-requests", action="store_true")
    return parser


async def run_cli(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    if suite.mode not in {"live", "scripted"}:
        raise ValueError(f"unsupported suite mode: {suite.mode}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("runs") / f"{suite.name}-replay-{stamp}"

    def agent_factory(
        case: EvalCase,
        sink: EventSink,
        workspace: WorkspaceFileStore | None,
    ) -> Agent:
        trajectory = args.trajectories / f"{case.id}.jsonl"
        return Agent(
            model=ReplayModel(trajectory, validate_requests=not args.no_validate_requests),
            tools=default_tools(workspace),
            config=AgentConfig(max_steps=case.agent_max_steps),
            event_sink=sink,
            workspace=workspace,
        )

    report = await EvalRunner(agent_factory=agent_factory).run(
        suite,
        output_dir,
        metadata={
            "provider": "replay",
            "source_trajectories": str(args.trajectories),
            "request_validation": not args.no_validate_requests,
        },
    )
    print(f"Suite: {report.suite_name}")
    for case in report.cases:
        marker = "PASS" if case.passed else "FAIL"
        print(
            f"[{marker}] {case.case_id}: {case.result.termination_reason.value}, "
            f"steps={case.result.steps}, tools={case.result.tool_calls}"
        )
        for score in case.scores:
            if not score.passed:
                print(f"       {score.scorer}: {score.detail}")
    print(f"Result: {report.passed_cases}/{len(report.cases)} cases passed")
    print(f"Report: {output_dir / 'report.json'}")
    return 0 if report.passed else 1


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run_cli(build_parser().parse_args())))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
