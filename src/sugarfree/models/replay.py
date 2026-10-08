from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from sugarfree.core.messages import AssistantTurn, ModelRequest, ToolCall, Usage
from sugarfree.models.base import ModelError


class ReplayModel:
    """Replays model responses captured in an Agent JSONL trajectory."""

    def __init__(self, trajectory_path: str | Path, *, validate_requests: bool = True) -> None:
        self.trajectory_path = Path(trajectory_path)
        self.validate_requests = validate_requests
        self._requests, self._outcomes = _load_trajectory(self.trajectory_path)
        self._index = 0

    async def complete(self, request: ModelRequest) -> AssistantTurn:
        if self._index >= len(self._outcomes):
            raise ModelError(f"replay has no model outcome left: {self.trajectory_path}")
        if self.validate_requests:
            expected = self._requests[self._index]
            actual = request.to_dict()
            if expected != actual:
                raise ModelError(
                    f"replay request mismatch at step {self._index + 1}: "
                    f"expected {expected!r}, got {actual!r}"
                )
        outcome = self._outcomes[self._index]
        self._index += 1
        if isinstance(outcome, _ReplayFailure):
            raise ModelError(outcome.message)
        return outcome


@dataclass(frozen=True, slots=True)
class _ReplayFailure:
    message: str


def _load_trajectory(
    path: Path,
) -> tuple[list[dict[str, Any]], list[AssistantTurn | _ReplayFailure]]:
    if not path.is_file():
        raise ValueError(f"trajectory file does not exist: {path}")
    requests: list[dict[str, Any]] = []
    outcomes: list[AssistantTurn | _ReplayFailure] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("kind") == "model.requested":
                if len(requests) != len(outcomes):
                    raise ValueError("new model request appeared before the previous outcome")
                request = event.get("payload", {}).get("request")
                if not isinstance(request, dict):
                    raise ValueError("model.requested has no request object")
                requests.append(request)
            elif event.get("kind") == "model.responded":
                if len(requests) != len(outcomes) + 1:
                    raise ValueError("model response has no matching request")
                response = event.get("payload", {}).get("response")
                outcomes.append(_parse_turn(response))
            elif event.get("kind") == "model.failed":
                if len(requests) != len(outcomes) + 1:
                    raise ValueError("model failure has no matching request")
                error = event.get("payload", {}).get("error")
                if not isinstance(error, str) or not error:
                    raise ValueError("model.failed has no error string")
                prefix = "ModelError: "
                outcomes.append(
                    _ReplayFailure(error[len(prefix):] if error.startswith(prefix) else error)
                )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid replay trajectory {path}: {exc}") from exc
    if len(requests) != len(outcomes):
        raise ValueError(
            f"invalid replay trajectory {path}: "
            f"{len(requests)} requests but {len(outcomes)} outcomes"
        )
    if not outcomes:
        raise ValueError(f"invalid replay trajectory {path}: no model outcomes")
    return requests, outcomes


def _parse_turn(value: Any) -> AssistantTurn:
    if not isinstance(value, Mapping):
        raise ValueError("invalid replay response")
    raw_calls = value.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raise ValueError("invalid replay tool_calls")
    calls: list[ToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping) or not isinstance(raw_call.get("arguments"), Mapping):
            raise ValueError("invalid replay tool call")
        calls.append(
            ToolCall(
                id=str(raw_call.get("id", "")),
                name=str(raw_call.get("name", "")),
                arguments=dict(raw_call["arguments"]),
            )
        )
    raw_usage = value.get("usage", {})
    if not isinstance(raw_usage, Mapping):
        raw_usage = {}
    return AssistantTurn(
        content=str(value.get("content", "")),
        tool_calls=tuple(calls),
        usage=Usage(
            input_tokens=int(raw_usage.get("input_tokens", 0)),
            output_tokens=int(raw_usage.get("output_tokens", 0)),
        ),
    )
