from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sugarfree.tools.base import ToolExecutionError, ToolResult


class LookupTool:
    name = "lookup"
    description = "Look up a value by key in the configured knowledge table."
    input_schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
        "additionalProperties": False,
    }

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        key = str(arguments["key"])
        try:
            return ToolResult(content=self._values[key])
        except KeyError as exc:
            raise ToolExecutionError(f"key not found: {key}") from exc

