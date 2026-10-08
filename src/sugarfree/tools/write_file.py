from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sugarfree.fs.workspace import WorkspaceFileStore
from sugarfree.tools.base import ToolExecutionError, ToolResult


class WriteFileTool:
    name = "write_file"
    description = "Atomically write UTF-8 text to a workspace-relative file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, store: WorkspaceFileStore) -> None:
        self.store = store

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            size = self.store.write_text(str(arguments["path"]), str(arguments["content"]))
            return ToolResult(content=f"wrote {size} UTF-8 bytes")
        except Exception as exc:
            raise ToolExecutionError(str(exc)) from exc

