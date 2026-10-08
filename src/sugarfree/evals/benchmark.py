from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


METRICS = (
    "pass_rate",
    "average_steps",
    "average_tool_calls",
    "average_tokens",
    "average_elapsed_seconds",
)


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    path: str
    suite: str
    started_at: str
    metadata: Mapping[str, Any]
    passed_cases: int
    total_cases: int
    pass_rate: float
    average_steps: float
    average_tool_calls: float
    average_tokens: float
    average_elapsed_seconds: float
    failed_cases: tuple[str, ...]

    @classmethod
    def from_report(cls, path: str | Path) -> "BenchmarkRun":
        report_path = Path(path)
        try:
            raw = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load benchmark report {report_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"benchmark report must be an object: {report_path}")
        summary = raw.get("summary")
        cases = raw.get("cases", [])
        if not isinstance(summary, Mapping) or not isinstance(cases, list):
            raise ValueError(f"benchmark report has invalid summary or cases: {report_path}")
        total = _number(summary, "total_cases", report_path)
        passed = _number(summary, "passed_cases", report_path)
        if total <= 0:
            raise ValueError(f"benchmark report has no cases: {report_path}")
        failed = tuple(
            str(case.get("case_id"))
            for case in cases
            if isinstance(case, Mapping) and not bool(case.get("passed"))
        )
        return cls(
            path=str(report_path),
            suite=str(raw.get("suite", report_path.stem)),
            started_at=str(raw.get("started_at", "")),
            metadata=dict(raw.get("metadata", {})) if isinstance(raw.get("metadata", {}), Mapping) else {},
            passed_cases=passed,
            total_cases=total,
            pass_rate=float(summary.get("pass_rate", passed / total)),
            average_steps=float(summary.get("steps", 0)) / total,
            average_tool_calls=float(summary.get("tool_calls", 0)) / total,
            average_tokens=float(summary.get("tokens", 0)) / total,
            average_elapsed_seconds=float(summary.get("elapsed_seconds", 0)) / total,
            failed_cases=failed,
        )

    def metrics(self) -> dict[str, float]:
        return {
            "pass_rate": self.pass_rate,
            "average_steps": self.average_steps,
            "average_tool_calls": self.average_tool_calls,
            "average_tokens": self.average_tokens,
            "average_elapsed_seconds": self.average_elapsed_seconds,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "suite": self.suite,
            "started_at": self.started_at,
            "metadata": dict(self.metadata),
            "passed_cases": self.passed_cases,
            "total_cases": self.total_cases,
            "pass_rate": self.pass_rate,
            "average_steps": self.average_steps,
            "average_tool_calls": self.average_tool_calls,
            "average_tokens": self.average_tokens,
            "average_elapsed_seconds": self.average_elapsed_seconds,
            "failed_cases": list(self.failed_cases),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkComparison:
    baseline: BenchmarkRun
    current: BenchmarkRun

    def to_dict(self) -> dict[str, Any]:
        baseline_metrics = self.baseline.metrics()
        current_metrics = self.current.metrics()
        deltas = {
            metric: current_metrics[metric] - baseline_metrics[metric]
            for metric in METRICS
        }
        baseline_failed = set(self.baseline.failed_cases)
        current_failed = set(self.current.failed_cases)
        return {
            "baseline": self.baseline.to_dict(),
            "current": self.current.to_dict(),
            "delta": deltas,
            "case_changes": {
                "new_failures": sorted(current_failed - baseline_failed),
                "fixed_failures": sorted(baseline_failed - current_failed),
                "still_failing": sorted(baseline_failed & current_failed),
            },
        }


def build_benchmark(reports: list[str | Path]) -> dict[str, Any]:
    if len(reports) < 1:
        raise ValueError("at least one report is required")
    runs = [BenchmarkRun.from_report(path) for path in reports]
    suites = {run.suite for run in runs}
    if len(suites) != 1:
        raise ValueError(f"cannot compare different suites: {sorted(suites)}")
    comparisons = [
        BenchmarkComparison(previous, current).to_dict()
        for previous, current in zip(runs, runs[1:])
    ]
    return {
        "suite": runs[-1].suite,
        "run_count": len(runs),
        "runs": [run.to_dict() for run in runs],
        "comparisons": comparisons,
    }


def _number(summary: Mapping[str, Any], key: str, path: Path) -> int:
    value = summary.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"benchmark report field '{key}' is invalid: {path}")
    return int(value)

