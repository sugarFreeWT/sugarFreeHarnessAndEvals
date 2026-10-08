from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Mapping, Protocol


class ToolExecutionError(RuntimeError):
    """A safe error that can be returned to the model as a tool result."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool = False


class Tool(Protocol):
    name: str
    description: str
    input_schema: Mapping[str, Any]

    def execute(self, arguments: Mapping[str, Any]) -> Awaitable[ToolResult] | ToolResult: ...

