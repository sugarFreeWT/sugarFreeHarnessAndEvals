from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from sugarfree.core.messages import AssistantTurn, ToolCall, Usage
from sugarfree.core.result import TerminationReason


@dataclass(frozen=True, slots=True)
class ToolExpectation:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkspaceChangeExpectation:
    path: str
    status: str
    content: str | None = None


@dataclass(frozen=True, slots=True)
class Expectations:
    termination_reason: TerminationReason = TerminationReason.COMPLETED
    answer_contains: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    required_tool_calls: tuple[ToolExpectation, ...] = ()
    max_tool_calls: int | None = None
    max_steps: int | None = None
    workspace_changes: tuple[WorkspaceChangeExpectation, ...] | None = None
    required_events: tuple[str, ...] = ()
    forbidden_events: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    prompt: str
    model_script: tuple[AssistantTurn, ...] = ()
    expectations: Expectations = field(default_factory=Expectations)
    agent_max_steps: int = 8
    workspace_files: Mapping[str, str] | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalSuite:
    name: str
    cases: tuple[EvalCase, ...]
    mode: str = "scripted"


def load_suite(path: str | Path) -> EvalSuite:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load eval suite {source}: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError("eval suite must be an object with a cases array")

    mode = str(raw.get("mode", "scripted"))
    if mode not in {"scripted", "live"}:
        raise ValueError("eval suite mode must be 'scripted' or 'live'")

    cases = tuple(_parse_case(value, require_script=mode == "scripted") for value in raw["cases"])
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("eval case ids must be unique")
    if not cases:
        raise ValueError("eval suite must contain at least one case")
    return EvalSuite(name=str(raw.get("name") or source.stem), cases=cases, mode=mode)


def _parse_case(value: Any, *, require_script: bool = True) -> EvalCase:
    if not isinstance(value, Mapping):
        raise ValueError("each eval case must be an object")
    try:
        case_id = str(value["id"])
        prompt = str(value["prompt"])
        script_values = value.get("model_script", [])
    except KeyError as exc:
        raise ValueError(f"eval case is missing field: {exc.args[0]}") from exc
    if not case_id or not prompt:
        raise ValueError("eval case id and prompt cannot be empty")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", case_id) is None:
        raise ValueError(f"eval case id contains unsafe characters: {case_id}")
    if require_script and "model_script" not in value:
        raise ValueError(f"eval case is missing field: model_script")
    if not isinstance(script_values, list):
        raise ValueError(f"model_script must be an array in case {case_id}")

    expected = value.get("expect", {})
    if not isinstance(expected, Mapping):
        raise ValueError(f"expect must be an object in case {case_id}")
    try:
        reason = TerminationReason(str(expected.get("termination_reason", "completed")))
    except ValueError as exc:
        raise ValueError(f"unknown termination reason in case {case_id}") from exc

    workspace_files = _parse_workspace(value.get("workspace"), case_id)
    workspace_changes = _parse_workspace_changes(expected, case_id)
    if workspace_changes is not None and workspace_files is None:
        raise ValueError(
            f"workspace_changes requires a workspace object in case {case_id}"
        )

    tags = _string_list(value, "tags", case_id)
    for tag in tags:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", tag) is None:
            raise ValueError(f"invalid tag in case {case_id}: {tag}")
    if len(tags) != len(set(tags)):
        raise ValueError(f"tags must be unique in case {case_id}")

    return EvalCase(
        id=case_id,
        prompt=prompt,
        model_script=tuple(_parse_turn(turn, case_id) for turn in script_values),
        expectations=Expectations(
            termination_reason=reason,
            answer_contains=tuple(str(item) for item in expected.get("answer_contains", [])),
            required_tools=tuple(str(item) for item in expected.get("required_tools", [])),
            required_tool_calls=tuple(
                _parse_tool_expectation(item, case_id)
                for item in expected.get("required_tool_calls", [])
            ),
            max_tool_calls=_optional_int(expected, "max_tool_calls", case_id),
            max_steps=_optional_int(expected, "max_steps", case_id),
            workspace_changes=workspace_changes,
            required_events=_string_list(expected, "required_events", case_id),
            forbidden_events=_string_list(expected, "forbidden_events", case_id),
        ),
        agent_max_steps=int(value.get("agent_max_steps", 8)),
        workspace_files=workspace_files,
        tags=tags,
    )


def _parse_turn(value: Any, case_id: str) -> AssistantTurn:
    if not isinstance(value, Mapping):
        raise ValueError(f"model script turns must be objects in case {case_id}")
    raw_calls = value.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raise ValueError(f"tool_calls must be an array in case {case_id}")
    calls = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping) or not isinstance(raw_call.get("arguments", {}), Mapping):
            raise ValueError(f"invalid tool call in case {case_id}")
        calls.append(
            ToolCall(
                id=str(raw_call["id"]),
                name=str(raw_call["name"]),
                arguments=dict(raw_call.get("arguments", {})),
            )
        )
    raw_usage = value.get("usage", {})
    return AssistantTurn(
        content=str(value.get("content", "")),
        tool_calls=tuple(calls),
        usage=Usage(
            input_tokens=int(raw_usage.get("input_tokens", 0)),
            output_tokens=int(raw_usage.get("output_tokens", 0)),
        ),
    )


def _optional_int(value: Mapping[str, Any], key: str, case_id: str) -> int | None:
    raw = value.get(key)
    if raw is None:
        return None
    parsed = int(raw)
    if parsed < 0:
        raise ValueError(f"{key} cannot be negative in case {case_id}")
    return parsed


def _string_list(value: Mapping[str, Any], key: str, case_id: str) -> tuple[str, ...]:
    raw = value.get(key, [])
    if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
        raise ValueError(f"{key} must be an array of non-empty strings in case {case_id}")
    return tuple(raw)


def _parse_tool_expectation(value: Any, case_id: str) -> ToolExpectation:
    if not isinstance(value, Mapping) or not value.get("name"):
        raise ValueError(f"invalid required_tool_calls entry in case {case_id}")
    arguments = value.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise ValueError(f"tool expectation arguments must be an object in case {case_id}")
    return ToolExpectation(name=str(value["name"]), arguments=dict(arguments))


def _parse_workspace(value: Any, case_id: str) -> Mapping[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"workspace must be an object in case {case_id}")
    files = value.get("files", {})
    if not isinstance(files, Mapping):
        raise ValueError(f"workspace.files must be an object in case {case_id}")
    parsed: dict[str, str] = {}
    for path, content in files.items():
        safe_path = _workspace_path(path, case_id)
        if not isinstance(content, str):
            raise ValueError(f"workspace file content must be text in case {case_id}: {safe_path}")
        parsed[safe_path] = content
    return parsed


def _parse_workspace_changes(
    expected: Mapping[str, Any],
    case_id: str,
) -> tuple[WorkspaceChangeExpectation, ...] | None:
    if "workspace_changes" not in expected:
        return None
    values = expected["workspace_changes"]
    if not isinstance(values, list):
        raise ValueError(f"workspace_changes must be an array in case {case_id}")
    parsed: list[WorkspaceChangeExpectation] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid workspace_changes entry in case {case_id}")
        path = _workspace_path(value.get("path"), case_id)
        status = str(value.get("status", ""))
        if status not in {"added", "modified", "deleted"}:
            raise ValueError(f"invalid workspace change status in case {case_id}: {status}")
        content = value.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError(f"workspace change content must be text in case {case_id}: {path}")
        if status == "deleted" and content is not None:
            raise ValueError(f"deleted workspace change cannot expect final content in case {case_id}")
        parsed.append(WorkspaceChangeExpectation(path=path, status=status, content=content))
    paths = [item.path for item in parsed]
    if len(paths) != len(set(paths)):
        raise ValueError(f"workspace change paths must be unique in case {case_id}")
    return tuple(parsed)


def _workspace_path(value: Any, case_id: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"workspace path must be a safe POSIX relative path in case {case_id}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not posix.parts
        or posix.as_posix() == "."
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
    ):
        raise ValueError(f"workspace path must stay inside the case workspace in case {case_id}: {value}")
    return posix.as_posix()
