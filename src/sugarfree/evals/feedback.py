from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from sugarfree.security.redaction import redact_sensitive_text

INBOX_VERSION = 1


def collect_failures(
    reports: Iterable[str | Path],
    inbox_path: str | Path,
) -> dict[str, Any]:
    """Collect failed eval cases into a review inbox.

    A fingerprint identifies the failure shape, while source report paths identify
    observations. Re-collecting the same report is therefore idempotent.
    """

    inbox = load_inbox(inbox_path)
    items = {str(item["fingerprint"]): item for item in inbox["items"]}

    for report_value in reports:
        report_path = Path(report_value).resolve()
        report = _load_object(report_path, "evaluation report")
        suite = _required_string(report, "suite", report_path)
        cases = report.get("cases")
        if not isinstance(cases, list):
            raise ValueError(f"evaluation report has no cases array: {report_path}")

        for case in cases:
            if not isinstance(case, Mapping):
                raise ValueError(f"evaluation report has an invalid case: {report_path}")
            if bool(case.get("passed", False)):
                continue

            candidate = _candidate_from_case(suite, case, report_path)
            fingerprint = str(candidate["fingerprint"])
            existing = items.get(fingerprint)
            if existing is None:
                inbox["items"].append(candidate)
                items[fingerprint] = candidate
                continue

            source = str(report_path)
            source_reports = existing.setdefault("source_reports", [])
            if source not in source_reports:
                source_reports.append(source)
                existing["occurrence_count"] = int(existing["occurrence_count"]) + 1
                existing["last_seen"] = candidate["last_seen"]
            for trajectory in candidate["trajectory_paths"]:
                if trajectory not in existing.setdefault("trajectory_paths", []):
                    existing["trajectory_paths"].append(trajectory)

    inbox["items"].sort(
        key=lambda item: (str(item.get("first_seen", "")), str(item["fingerprint"]))
    )
    _save_inbox(inbox_path, inbox)
    return inbox


def review_failure(
    inbox_path: str | Path,
    fingerprint: str,
    status: str,
    reviewer: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Approve or reject a candidate using explicit state transitions."""

    if status not in {"approved", "rejected"}:
        raise ValueError("review status must be 'approved' or 'rejected'")
    if not reviewer.strip():
        raise ValueError("reviewer cannot be empty")

    inbox = load_inbox(inbox_path)
    item = _find_item(inbox, fingerprint)
    current = str(item["status"])
    allowed = {
        "candidate": {"approved", "rejected"},
        "approved": {"rejected"},
        "rejected": {"approved"},
    }
    if status not in allowed.get(current, set()):
        raise ValueError(f"cannot transition failure from {current} to {status}")

    item["status"] = status
    item["reviewer"] = redact_text(reviewer.strip())
    item["review_note"] = redact_text(note) if note is not None else None
    item["reviewed_at"] = _utc_now()
    _save_inbox(inbox_path, inbox)
    return item


def promote_failure(
    inbox_path: str | Path,
    fingerprint: str,
    regression_case_id: str,
    reviewer: str,
) -> dict[str, Any]:
    """Link an approved failure to a manually authored regression case."""

    if not regression_case_id.strip():
        raise ValueError("regression case id cannot be empty")
    if not reviewer.strip():
        raise ValueError("reviewer cannot be empty")

    inbox = load_inbox(inbox_path)
    item = _find_item(inbox, fingerprint)
    if item["status"] != "approved":
        raise ValueError("only an approved failure can be promoted")

    item["status"] = "promoted"
    item["regression_case_id"] = regression_case_id.strip()
    item["reviewer"] = redact_text(reviewer.strip())
    item["promoted_at"] = _utc_now()
    _save_inbox(inbox_path, inbox)
    return item


def reconcile_failures(
    inbox_path: str | Path,
    report_path: str | Path,
) -> dict[str, Any]:
    """Resolve promoted failures whose linked regression case now passes."""

    report_source = Path(report_path).resolve()
    report = _load_object(report_source, "evaluation report")
    raw_cases = report.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(f"evaluation report has no cases array: {report_source}")
    case_results: dict[str, bool] = {}
    for case in raw_cases:
        if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
            raise ValueError(f"evaluation report has an invalid case: {report_source}")
        case_results[str(case["case_id"])] = bool(case.get("passed", False))

    inbox = load_inbox(inbox_path)
    changed = False
    for item in inbox["items"]:
        if item.get("status") != "promoted":
            continue
        case_id = item.get("regression_case_id")
        if isinstance(case_id, str) and case_results.get(case_id) is True:
            item["status"] = "resolved"
            item["resolved_at"] = _utc_now()
            item["resolution_report"] = str(report_source)
            changed = True

    if changed:
        _save_inbox(inbox_path, inbox)
    return inbox


def load_inbox(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        return {"version": INBOX_VERSION, "updated_at": None, "items": []}
    raw = _load_object(source, "failure inbox")
    if raw.get("version") != INBOX_VERSION or not isinstance(raw.get("items"), list):
        raise ValueError(f"unsupported or invalid failure inbox: {source}")
    for item in raw["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("fingerprint"), str):
            raise ValueError(f"failure inbox contains an invalid item: {source}")
    return raw


def redact_text(value: str) -> str:
    return redact_sensitive_text(value)


def _candidate_from_case(
    suite: str,
    case: Mapping[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    case_id = _required_string(case, "case_id", report_path)
    scores = case.get("scores", [])
    if not isinstance(scores, list):
        raise ValueError(f"case scores must be an array: {report_path}#{case_id}")
    failed_scorers: list[str] = []
    failure_details: list[str] = []
    for score in scores:
        if not isinstance(score, Mapping) or bool(score.get("passed", False)):
            continue
        failed_scorers.append(str(score.get("scorer", "unknown")))
        failure_details.append(redact_text(str(score.get("detail", ""))))
    if not failed_scorers:
        failed_scorers.append("case")

    result = case.get("result", {})
    if not isinstance(result, Mapping):
        result = {}
    termination_reason = str(result.get("termination_reason", "unknown"))
    fingerprint = _fingerprint(suite, case_id, failed_scorers, termination_reason)
    now = _utc_now()
    trajectory = report_path.parent / "trajectories" / f"{case_id}.jsonl"
    raw_tags = case.get("tags", [])
    tags = [redact_text(str(tag)) for tag in raw_tags] if isinstance(raw_tags, list) else []
    error = result.get("error")

    return {
        "fingerprint": fingerprint,
        "suite": redact_text(suite),
        "source_case_id": redact_text(case_id),
        "status": "candidate",
        "first_seen": now,
        "last_seen": now,
        "occurrence_count": 1,
        "failed_scorers": sorted(set(failed_scorers)),
        "failure_details": failure_details,
        "termination_reason": redact_text(termination_reason),
        "error": redact_text(str(error)) if error is not None else None,
        "prompt": redact_text(_extract_prompt(result)),
        "tags": tags,
        "source_reports": [str(report_path)],
        "trajectory_paths": [str(trajectory.resolve())] if trajectory.is_file() else [],
        "reviewer": None,
        "review_note": None,
        "reviewed_at": None,
        "regression_case_id": None,
        "promoted_at": None,
        "resolved_at": None,
        "resolution_report": None,
    }


def _extract_prompt(result: Mapping[str, Any]) -> str:
    messages = result.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            if (
                isinstance(message, Mapping)
                and message.get("role") == "user"
                and isinstance(message.get("content"), str)
            ):
                return str(message["content"])
    events = result.get("events", [])
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, Mapping) or event.get("kind") != "run.started":
                continue
            payload = event.get("payload", {})
            if isinstance(payload, Mapping) and isinstance(payload.get("prompt"), str):
                return str(payload["prompt"])
    return ""


def _fingerprint(
    suite: str,
    case_id: str,
    failed_scorers: Iterable[str],
    termination_reason: str,
) -> str:
    payload = {
        "suite": suite,
        "source_case_id": case_id,
        "failed_scorers": sorted(set(failed_scorers)),
        "termination_reason": termination_reason,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _find_item(inbox: Mapping[str, Any], fingerprint: str) -> dict[str, Any]:
    for item in inbox["items"]:
        if item.get("fingerprint") == fingerprint:
            return item
    raise ValueError(f"failure fingerprint not found: {fingerprint}")


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object: {path}")
    return raw


def _required_string(value: Mapping[str, Any], key: str, path: Path) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{key} must be a non-empty string: {path}")
    return raw


def _save_inbox(path: str | Path, inbox: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    inbox["updated_at"] = _utc_now()
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(inbox, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
