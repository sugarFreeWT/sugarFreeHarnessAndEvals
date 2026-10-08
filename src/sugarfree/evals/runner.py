from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from sugarfree.core.agent import Agent
from sugarfree.core.events import EventSink
from sugarfree.core.result import AgentResult
from sugarfree.evals.cases import EvalCase, EvalSuite
from sugarfree.evals.scorers import Score, Scorer, default_scorers
from sugarfree.fs.workspace import WorkspaceFileStore
from sugarfree.tracing.jsonl import JsonlEventSink

AgentFactory = Callable[[EvalCase, EventSink, WorkspaceFileStore | None], Agent]


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    tags: tuple[str, ...]
    passed: bool
    scores: tuple[Score, ...]
    result: AgentResult

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "tags": list(self.tags),
            "passed": self.passed,
            "scores": [score.to_dict() for score in self.scores],
            "result": self.result.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    suite_name: str
    started_at: str
    cases: tuple[CaseResult, ...]
    metadata: Mapping[str, object] | None = None

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def passed_cases(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def tag_summary(self) -> dict[str, dict[str, int | float]]:
        return _tag_summary(self.cases)

    def to_dict(self) -> dict[str, object]:
        total = len(self.cases)
        return {
            "suite": self.suite_name,
            "started_at": self.started_at,
            "metadata": dict(self.metadata or {}),
            "passed": self.passed,
            "summary": {
                "passed_cases": self.passed_cases,
                "total_cases": total,
                "pass_rate": self.passed_cases / total if total else 0.0,
                "steps": sum(case.result.steps for case in self.cases),
                "tool_calls": sum(case.result.tool_calls for case in self.cases),
                "tokens": sum(case.result.usage.total_tokens for case in self.cases),
                "elapsed_seconds": sum(case.result.elapsed_seconds for case in self.cases),
            },
            "tag_summary": self.tag_summary,
            "cases": [case.to_dict() for case in self.cases],
        }


class EvalRunner:
    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        scorers: Iterable[Scorer] | None = None,
    ) -> None:
        self.agent_factory = agent_factory
        self.scorers = tuple(scorers or default_scorers())

    async def run(
        self,
        suite: EvalSuite,
        output_dir: str | Path,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> EvalReport:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(timezone.utc).isoformat()
        case_results: list[CaseResult] = []

        for case in suite.cases:
            trajectory_path = destination / "trajectories" / f"{case.id}.jsonl"
            if trajectory_path.exists():
                trajectory_path.unlink()
            sink = JsonlEventSink(trajectory_path)
            workspace = _prepare_workspace(destination, case)
            agent = self.agent_factory(case, sink, workspace)
            result = await agent.run(case.prompt, run_id=case.id)
            scores = tuple(
                score
                for scorer in self.scorers
                if (score := scorer.score(case, result)) is not None
            )
            case_results.append(
                CaseResult(
                    case_id=case.id,
                    tags=case.tags,
                    passed=all(score.passed for score in scores),
                    scores=scores,
                    result=result,
                )
            )

        report = EvalReport(suite.name, started_at, tuple(case_results), metadata)
        (destination / "report.json").write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return report


def _prepare_workspace(destination: Path, case: EvalCase) -> WorkspaceFileStore | None:
    if case.workspace_files is None:
        return None
    workspaces_root = (destination / "workspaces").resolve()
    workspace_root = (workspaces_root / case.id).resolve()
    if not workspace_root.is_relative_to(workspaces_root):
        raise ValueError(f"case workspace escapes output directory: {case.id}")
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    workspace_root.mkdir(parents=True)
    store = WorkspaceFileStore(workspace_root)
    for path, content in case.workspace_files.items():
        store.write_text(path, content)
    return store


def _tag_summary(cases: tuple[CaseResult, ...]) -> dict[str, dict[str, int | float]]:
    tags = sorted({tag for case in cases for tag in case.tags})
    summary: dict[str, dict[str, int | float]] = {}
    for tag in tags:
        tagged = [case for case in cases if tag in case.tags]
        passed = sum(case.passed for case in tagged)
        summary[tag] = {
            "passed_cases": passed,
            "total_cases": len(tagged),
            "pass_rate": passed / len(tagged),
        }
    return summary
