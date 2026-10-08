from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from sugarfree.core.result import AgentResult
from sugarfree.evals.cases import EvalCase


@dataclass(frozen=True, slots=True)
class Score:
    scorer: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, str | bool]:
        return {"scorer": self.scorer, "passed": self.passed, "detail": self.detail}


class Scorer(Protocol):
    name: str

    def score(self, case: EvalCase, result: AgentResult) -> Score | None: ...


class TerminationScorer:
    name = "termination"

    def score(self, case: EvalCase, result: AgentResult) -> Score:
        expected = case.expectations.termination_reason
        passed = result.termination_reason is expected
        return Score(self.name, passed, f"expected {expected.value}, got {result.termination_reason.value}")


class AnswerContainsScorer:
    name = "answer_contains"

    def score(self, case: EvalCase, result: AgentResult) -> Score | None:
        expected = case.expectations.answer_contains
        if not expected:
            return None
        missing = [fragment for fragment in expected if fragment not in result.answer]
        return Score(
            self.name,
            not missing,
            "all expected fragments found" if not missing else f"missing: {missing}",
        )


class RequiredToolsScorer:
    name = "required_tools"

    def score(self, case: EvalCase, result: AgentResult) -> Score | None:
        expected = case.expectations.required_tools
        if not expected:
            return None
        used = [
            str(event.payload["call"]["name"])
            for event in result.events
            if event.kind == "tool.requested"
        ]
        missing = [name for name in expected if name not in used]
        return Score(
            self.name,
            not missing,
            f"used tools: {used}" if not missing else f"missing {missing}; used {used}",
        )


class RequiredToolCallsScorer:
    name = "required_tool_calls"

    def score(self, case: EvalCase, result: AgentResult) -> Score | None:
        expected = case.expectations.required_tool_calls
        if not expected:
            return None
        actual = [
            event.payload["call"]
            for event in result.events
            if event.kind == "tool.requested"
        ]
        mismatches: list[str] = []
        if len(actual) < len(expected):
            mismatches.append(f"expected at least {len(expected)} calls, got {len(actual)}")
        for index, wanted in enumerate(expected):
            if index >= len(actual):
                break
            got = actual[index]
            if got.get("name") != wanted.name or got.get("arguments") != dict(wanted.arguments):
                mismatches.append(
                    f"call {index + 1}: expected {wanted.name}({dict(wanted.arguments)!r}), "
                    f"got {got.get('name')}({got.get('arguments')!r})"
                )
        return Score(
            self.name,
            not mismatches,
            "tool calls matched" if not mismatches else "; ".join(mismatches),
        )


class MaxToolCallsScorer:
    name = "max_tool_calls"

    def score(self, case: EvalCase, result: AgentResult) -> Score | None:
        limit = case.expectations.max_tool_calls
        if limit is None:
            return None
        passed = result.tool_calls <= limit
        return Score(self.name, passed, f"limit {limit}, got {result.tool_calls}")


class MaxStepsScorer:
    name = "max_steps"

    def score(self, case: EvalCase, result: AgentResult) -> Score | None:
        limit = case.expectations.max_steps
        if limit is None:
            return None
        passed = result.steps <= limit
        return Score(self.name, passed, f"limit {limit}, got {result.steps}")


class WorkspaceChangesScorer:
    name = "workspace_changes"

    def score(self, case: EvalCase, result: AgentResult) -> Score | None:
        expected = case.expectations.workspace_changes
        if expected is None:
            return None
        failures = [
            event for event in result.events if event.kind == "workspace.snapshot_failed"
        ]
        if failures:
            phases = [str(event.payload.get("phase", "unknown")) for event in failures]
            return Score(self.name, False, f"workspace snapshot failed: {phases}")

        actual = [
            change
            for event in result.events
            if event.kind == "workspace.changed"
            for change in event.payload.get("changes", [])
        ]
        actual_by_path = {str(change.get("path")): change for change in actual}
        expected_by_path = {item.path: item for item in expected}
        problems: list[str] = []

        missing = sorted(set(expected_by_path) - set(actual_by_path))
        unexpected = sorted(set(actual_by_path) - set(expected_by_path))
        if missing:
            problems.append(f"missing changes: {missing}")
        if unexpected:
            problems.append(f"unexpected changes: {unexpected}")

        for path in sorted(set(expected_by_path) & set(actual_by_path)):
            wanted = expected_by_path[path]
            got = actual_by_path[path]
            if got.get("status") != wanted.status:
                problems.append(
                    f"{path}: expected status {wanted.status}, got {got.get('status')}"
                )
            if wanted.content is not None:
                expected_hash = hashlib.sha256(wanted.content.encode("utf-8")).hexdigest()
                if got.get("after_sha256") != expected_hash:
                    problems.append(f"{path}: final content hash did not match")

        detail = "workspace changes matched" if not problems else "; ".join(problems)
        return Score(self.name, not problems, detail)


class EventScorer:
    name = "events"

    def score(self, case: EvalCase, result: AgentResult) -> Score | None:
        required = case.expectations.required_events
        forbidden = case.expectations.forbidden_events
        if not required and not forbidden:
            return None
        actual = [event.kind for event in result.events]
        missing = [kind for kind in required if kind not in actual]
        present = [kind for kind in forbidden if kind in actual]
        problems: list[str] = []
        if missing:
            problems.append(f"missing required events: {missing}")
        if present:
            problems.append(f"forbidden events present: {present}")
        return Score(
            self.name,
            not problems,
            "event expectations matched" if not problems else "; ".join(problems),
        )


def default_scorers() -> tuple[Scorer, ...]:
    return (
        TerminationScorer(),
        AnswerContainsScorer(),
        RequiredToolsScorer(),
        RequiredToolCallsScorer(),
        MaxToolCallsScorer(),
        MaxStepsScorer(),
        WorkspaceChangesScorer(),
        EventScorer(),
    )
