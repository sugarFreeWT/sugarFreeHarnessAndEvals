from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class EvalSnapshot:
    path: str
    suite: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    average_steps: float
    average_tool_calls: float
    case_results: Mapping[str, bool]
    tag_pass_rates: Mapping[str, float]

    @classmethod
    def load(cls, path: str | Path) -> "EvalSnapshot":
        source = Path(path)
        raw = _load_object(source, "evaluation report")
        summary = raw.get("summary")
        if not isinstance(summary, Mapping):
            raise ValueError(f"evaluation report has no summary object: {source}")
        total = _integer(summary, "total_cases", source)
        passed = _integer(summary, "passed_cases", source)
        if total < 1 or passed < 0 or passed > total:
            raise ValueError(f"evaluation report has invalid case counts: {source}")
        case_results = _case_results(raw, source)
        if len(case_results) != total:
            raise ValueError(
                f"evaluation report case count mismatch: expected {total}, got {len(case_results)}"
            )
        return cls(
            path=str(source),
            suite=str(raw.get("suite", "")),
            total_cases=total,
            passed_cases=passed,
            pass_rate=_optional_number(summary, "pass_rate", passed / total, source),
            average_steps=_average(summary, "steps", "average_steps", total, source),
            average_tool_calls=_average(
                summary,
                "tool_calls",
                "average_tool_calls",
                total,
                source,
            ),
            case_results=case_results,
            tag_pass_rates=_tag_pass_rates(raw, source),
        )


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    passed: bool
    expected: object
    actual: object
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ReleaseGateResult:
    name: str
    evaluated_at: str
    current_report: str
    replay_report: str | None
    baseline_report: str | None
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        failed = [check.name for check in self.checks if not check.passed]
        return {
            "name": self.name,
            "evaluated_at": self.evaluated_at,
            "passed": self.passed,
            "current_report": self.current_report,
            "replay_report": self.replay_report,
            "baseline_report": self.baseline_report,
            "summary": {
                "passed_checks": len(self.checks) - len(failed),
                "total_checks": len(self.checks),
                "failed_checks": failed,
            },
            "checks": [check.to_dict() for check in self.checks],
        }


def evaluate_release_gate(
    *,
    config_path: str | Path,
    current_report: str | Path,
    replay_report: str | Path | None = None,
    baseline_report: str | Path | None = None,
) -> ReleaseGateResult:
    config_source = Path(config_path)
    config = _load_object(config_source, "release gate config")
    current = EvalSnapshot.load(current_report)
    replay = EvalSnapshot.load(replay_report) if replay_report is not None else None
    baseline = EvalSnapshot.load(baseline_report) if baseline_report is not None else None
    checks: list[GateCheck] = []

    expected_suite = str(config.get("suite", ""))
    if expected_suite:
        _add_check(
            checks,
            "suite",
            current.suite == expected_suite,
            expected_suite,
            current.suite,
            "current report suite must match gate config",
        )

    min_total_cases = _config_integer(config, "min_total_cases", 1, config_source)
    _add_check(
        checks,
        "minimum_cases",
        current.total_cases >= min_total_cases,
        f">= {min_total_cases}",
        current.total_cases,
        "current report must retain the required evaluation coverage",
    )

    min_pass_rate = _config_rate(config, "min_pass_rate", 1.0, config_source)
    _add_check(
        checks,
        "pass_rate",
        current.pass_rate >= min_pass_rate,
        f">= {min_pass_rate}",
        current.pass_rate,
        "current evaluation pass rate must meet the release threshold",
    )

    required_cases = _config_string_list(config, "required_cases", config_source)
    missing_required = sorted(case for case in required_cases if case not in current.case_results)
    failing_required = sorted(
        case for case in required_cases if case in current.case_results and not current.case_results[case]
    )
    _add_check(
        checks,
        "required_cases",
        not missing_required and not failing_required,
        list(required_cases),
        {"missing": missing_required, "failing": failing_required},
        "all release-critical cases must exist and pass",
    )

    tag_thresholds = config.get("tag_min_pass_rates", {})
    if not isinstance(tag_thresholds, Mapping):
        raise ValueError(f"tag_min_pass_rates must be an object: {config_source}")
    for tag, raw_threshold in sorted(tag_thresholds.items()):
        threshold = _rate(raw_threshold, f"tag_min_pass_rates.{tag}", config_source)
        actual = current.tag_pass_rates.get(str(tag))
        _add_check(
            checks,
            f"tag:{tag}",
            actual is not None and actual >= threshold,
            f">= {threshold}",
            actual,
            f"tag '{tag}' must exist and meet its pass-rate threshold",
        )

    require_replay = bool(config.get("require_replay", False))
    _add_check(
        checks,
        "replay_present",
        replay is not None or not require_replay,
        require_replay,
        replay is not None,
        "a replay report is required when replay gating is enabled",
    )
    if replay is not None:
        replay_min = _config_rate(config, "replay_min_pass_rate", min_pass_rate, config_source)
        _add_check(
            checks,
            "replay_suite",
            replay.suite == current.suite,
            current.suite,
            replay.suite,
            "current and replay reports must describe the same suite",
        )
        _add_check(
            checks,
            "replay_pass_rate",
            replay.pass_rate >= replay_min,
            f">= {replay_min}",
            replay.pass_rate,
            "strict replay must meet its pass-rate threshold",
        )
        if bool(config.get("require_replay_case_parity", True)):
            current_cases = dict(current.case_results)
            replay_cases = dict(replay.case_results)
            _add_check(
                checks,
                "replay_case_parity",
                current_cases == replay_cases,
                current_cases,
                replay_cases,
                "strict replay must reproduce the same case outcomes",
            )

    require_baseline = bool(config.get("require_baseline", False))
    _add_check(
        checks,
        "baseline_present",
        baseline is not None or not require_baseline,
        require_baseline,
        baseline is not None,
        "a versioned baseline is required when baseline gating is enabled",
    )
    if baseline is not None:
        _add_check(
            checks,
            "baseline_suite",
            baseline.suite == current.suite,
            current.suite,
            baseline.suite,
            "current and baseline reports must describe the same suite",
        )
        removed = sorted(set(baseline.case_results) - set(current.case_results))
        max_removed = _config_integer(config, "max_removed_cases", 0, config_source)
        _add_check(
            checks,
            "removed_cases",
            len(removed) <= max_removed,
            f"<= {max_removed}",
            removed,
            "baseline cases cannot disappear without an explicit policy change",
        )
        baseline_failures = {case for case, passed in baseline.case_results.items() if not passed}
        current_failures = {case for case, passed in current.case_results.items() if not passed}
        new_failures = sorted(current_failures - baseline_failures)
        max_new_failures = _config_integer(config, "max_new_failures", 0, config_source)
        _add_check(
            checks,
            "new_failures",
            len(new_failures) <= max_new_failures,
            f"<= {max_new_failures}",
            new_failures,
            "newly failing cases block release",
        )
        _metric_delta_check(
            checks,
            config,
            config_source,
            "average_steps",
            current.average_steps,
            baseline.average_steps,
            "max_average_steps_increase",
        )
        _metric_delta_check(
            checks,
            config,
            config_source,
            "average_tool_calls",
            current.average_tool_calls,
            baseline.average_tool_calls,
            "max_average_tool_calls_increase",
        )

    return ReleaseGateResult(
        name=str(config.get("name", config_source.stem)),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        current_report=current.path,
        replay_report=replay.path if replay else None,
        baseline_report=baseline.path if baseline else None,
        checks=tuple(checks),
    )


def _metric_delta_check(
    checks: list[GateCheck],
    config: Mapping[str, Any],
    config_source: Path,
    metric: str,
    current: float,
    baseline: float,
    config_key: str,
) -> None:
    if config_key not in config:
        return
    limit = _number(config[config_key], config_key, config_source)
    delta = current - baseline
    _add_check(
        checks,
        metric,
        delta <= limit,
        f"increase <= {limit}",
        {"baseline": baseline, "current": current, "increase": delta},
        f"{metric} regression must remain within the configured tolerance",
    )


def _add_check(
    checks: list[GateCheck],
    name: str,
    passed: bool,
    expected: object,
    actual: object,
    detail: str,
) -> None:
    checks.append(GateCheck(name, passed, expected, actual, detail))


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object: {path}")
    return raw


def _case_results(raw: Mapping[str, Any], path: Path) -> dict[str, bool]:
    compact = raw.get("case_results")
    if isinstance(compact, Mapping):
        if any(not isinstance(value, bool) for value in compact.values()):
            raise ValueError(f"baseline case_results must contain booleans: {path}")
        return {str(case): value for case, value in compact.items()}
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"evaluation report has no cases array: {path}")
    results: dict[str, bool] = {}
    for case in cases:
        if not isinstance(case, Mapping) or not isinstance(case.get("passed"), bool):
            raise ValueError(f"evaluation report has an invalid case result: {path}")
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in results:
            raise ValueError(f"evaluation report has an invalid or duplicate case id: {path}")
        results[case_id] = bool(case["passed"])
    return results


def _tag_pass_rates(raw: Mapping[str, Any], path: Path) -> dict[str, float]:
    tag_summary = raw.get("tag_summary", {})
    if not isinstance(tag_summary, Mapping):
        raise ValueError(f"tag_summary must be an object: {path}")
    rates: dict[str, float] = {}
    for tag, value in tag_summary.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid tag summary for {tag}: {path}")
        rates[str(tag)] = _rate(value.get("pass_rate"), f"tag_summary.{tag}", path)
    return rates


def _average(
    summary: Mapping[str, Any],
    total_key: str,
    average_key: str,
    total_cases: int,
    path: Path,
) -> float:
    if average_key in summary:
        return _number(summary[average_key], average_key, path)
    return _number(summary.get(total_key), total_key, path) / total_cases


def _integer(value: Mapping[str, Any], key: str, path: Path) -> int:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"field '{key}' must be an integer: {path}")
    return raw


def _optional_number(
    value: Mapping[str, Any],
    key: str,
    default: float,
    path: Path,
) -> float:
    return default if key not in value else _number(value[key], key, path)


def _number(value: Any, key: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"field '{key}' must be numeric: {path}")
    return float(value)


def _rate(value: Any, key: str, path: Path) -> float:
    rate = _number(value, key, path)
    if not 0 <= rate <= 1:
        raise ValueError(f"field '{key}' must be between 0 and 1: {path}")
    return rate


def _config_rate(config: Mapping[str, Any], key: str, default: float, path: Path) -> float:
    return default if key not in config else _rate(config[key], key, path)


def _config_integer(config: Mapping[str, Any], key: str, default: int, path: Path) -> int:
    if key not in config:
        return default
    raw = config[key]
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"field '{key}' must be a non-negative integer: {path}")
    return raw


def _config_string_list(
    config: Mapping[str, Any],
    key: str,
    path: Path,
) -> tuple[str, ...]:
    raw = config.get(key, [])
    if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
        raise ValueError(f"field '{key}' must be an array of non-empty strings: {path}")
    return tuple(raw)
