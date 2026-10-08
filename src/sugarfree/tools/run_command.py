from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sugarfree.sandbox.types import ProcessRequest, ProcessRunner, SandboxError
from sugarfree.tools.base import ToolResult


class RunCommandTool:
    """Runs an explicitly allowlisted argv through a ProcessRunner."""

    name = "run_command"
    description = "Run an allowlisted command with argv arguments in the restricted workspace."
    input_schema = {
        "type": "object",
        "properties": {
            "argv": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["argv"],
        "additionalProperties": False,
    }

    def __init__(self, runner: ProcessRunner, cwd: str) -> None:
        self.runner = runner
        self.cwd = Path(cwd)

    async def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        argv = arguments["argv"]
        if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
            raise SandboxError("argv must be an array of strings")
        result = await self.runner.run(ProcessRequest(argv=tuple(argv), cwd=self.cwd))
        content = f"exit_code={result.returncode}\nstdout:\n{result.stdout}"
        if result.stderr:
            content += f"\nstderr:\n{result.stderr}"
        if result.timed_out:
            content = "timed_out=true\n" + content
        if result.output_truncated:
            content = "output_truncated=true\n" + content
        return ToolResult(content=content, is_error=result.timed_out or result.returncode != 0)
