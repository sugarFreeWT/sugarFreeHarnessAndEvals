from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sugarfree.core.agent import Agent, AgentConfig
from sugarfree.core.events import EventSink
from sugarfree.evals.cases import EvalCase, load_suite
from sugarfree.evals.runner import EvalRunner
from sugarfree.evals.runtime import default_tools
from sugarfree.models.scripted import ScriptedModel
from sugarfree.fs.workspace import WorkspaceFileStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic sugarfree eval suite.")
    parser.add_argument("suite", type=Path, help="Path to an eval suite JSON file")
    parser.add_argument("--output-dir", type=Path, help="Directory for report and trajectories")
    return parser


async def run_cli(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or Path("runs") / f"{suite.name}-{stamp}"

    def agent_factory(
        case: EvalCase,
        sink: EventSink,
        workspace: WorkspaceFileStore | None,
    ) -> Agent:
        return Agent(
            model=ScriptedModel(case.model_script),
            tools=default_tools(workspace),
            config=AgentConfig(max_steps=case.agent_max_steps),
            event_sink=sink,
            workspace=workspace,
        )

    if suite.mode != "scripted":
        raise ValueError("the deterministic runner only accepts suites with mode=scripted")
    report = await EvalRunner(agent_factory=agent_factory).run(
        suite,
        output_dir,
        metadata={"provider": "scripted", "model": "scripted"},
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
    raise SystemExit(asyncio.run(run_cli(build_parser().parse_args())))


if __name__ == "__main__":
    main()
