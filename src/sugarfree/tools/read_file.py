from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sugarfree.fs.workspace import WorkspaceFileStore
from sugarfree.tools.base import ToolExecutionError, ToolResult


class ReadFileTool:
    name = "read_file"
    description = "Read a UTF-8 text file relative to the workspace root."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, store: WorkspaceFileStore) -> None:
        self.store = store

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            return ToolResult(content=self.store.read_text(str(arguments["path"])))
        except Exception as exc:
            raise ToolExecutionError(str(exc)) from exc

