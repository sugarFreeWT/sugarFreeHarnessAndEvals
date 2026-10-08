from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol


class SandboxError(RuntimeError):
    """A process request rejected by the configured execution boundary."""


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float | None = None
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False


class ProcessRunner(Protocol):
    async def run(self, request: ProcessRequest) -> ProcessResult: ...

