from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sugarfree.core.messages import ToolSpec
from sugarfree.tools.base import Tool, ToolExecutionError


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError(f"unknown tool: {name}") from exc

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(
            ToolSpec(tool.name, tool.description, tool.input_schema)
            for tool in self._tools.values()
        )

    def validate(self, tool: Tool, arguments: Mapping[str, Any]) -> None:
        schema = tool.input_schema
        if schema.get("type") == "object" and not isinstance(arguments, Mapping):
            raise ToolExecutionError("tool arguments must be an object")

        required = schema.get("required", [])
        missing = [name for name in required if name not in arguments]
        if missing:
            raise ToolExecutionError(f"missing required argument(s): {', '.join(missing)}")

        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = [name for name in arguments if name not in properties]
            if extra:
                raise ToolExecutionError(f"unexpected argument(s): {', '.join(extra)}")

        for name, value in arguments.items():
            expected = properties.get(name, {}).get("type")
            if expected and not _matches_type(value, expected):
                raise ToolExecutionError(f"argument '{name}' must be {expected}")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    return True
