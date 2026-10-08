from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sugarfree.sandbox.types import ProcessRequest, ProcessResult, SandboxError


class LocalRestrictedProcessRunner:
    """A policy-gated local runner; this is not an OS-level security sandbox."""

    def __init__(
        self,
        *,
        root: str | Path,
        allowed_executables: set[str] | frozenset[str],
        max_output_bytes: int = 64 * 1024,
    ) -> None:
        self.root = Path(root).resolve()
        self.allowed_executables = frozenset(
            _normalize_executable(value) for value in allowed_executables
        )
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be at least 1")
        self.max_output_bytes = max_output_bytes

    async def run(self, request: ProcessRequest) -> ProcessResult:
        self._validate(request)
        try:
            process = await asyncio.create_subprocess_exec(
                *request.argv,
                cwd=str(request.cwd),
                env=dict(request.env),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await _communicate(process, request.timeout_seconds)
            except asyncio.CancelledError:
                process.kill()
                await process.communicate()
                raise
            except asyncio.TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
                stdout_text, stdout_truncated = _decode(stdout, self.max_output_bytes)
                stderr_text, stderr_truncated = _decode(stderr, self.max_output_bytes)
                return ProcessResult(
                    returncode=-1,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    timed_out=True,
                    output_truncated=stdout_truncated or stderr_truncated,
                )
        except OSError as exc:
            raise SandboxError(f"could not start process: {exc}") from exc

        stdout_text, stdout_truncated = _decode(stdout, self.max_output_bytes)
        stderr_text, stderr_truncated = _decode(stderr, self.max_output_bytes)
        return ProcessResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout_text,
            stderr=stderr_text,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    def _validate(self, request: ProcessRequest) -> None:
        if not request.argv or not all(isinstance(value, str) and value for value in request.argv):
            raise SandboxError("argv must contain at least one non-empty string")
        if not Path(request.argv[0]).is_absolute():
            raise SandboxError("executable path must be absolute")
        executable = _normalize_executable(request.argv[0])
        if executable not in self.allowed_executables:
            raise SandboxError(f"executable is not allowed: {request.argv[0]}")
        cwd = request.cwd.resolve()
        if not _is_within(cwd, self.root):
            raise SandboxError(f"working directory is outside sandbox root: {cwd}")
        if not cwd.is_dir():
            raise SandboxError(f"working directory does not exist: {cwd}")
        if request.timeout_seconds is not None and request.timeout_seconds <= 0:
            raise SandboxError("timeout_seconds must be greater than zero")


async def _communicate(
    process: asyncio.subprocess.Process,
    timeout_seconds: float | None,
) -> tuple[bytes, bytes]:
    communicate = process.communicate()
    if timeout_seconds is None:
        return await communicate
    return await asyncio.wait_for(communicate, timeout=timeout_seconds)


def _decode(value: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(value) > limit
    return value[:limit].decode("utf-8", errors="replace"), truncated


def _normalize_executable(value: str) -> str:
    return (
        os.path.normcase(os.path.abspath(value))
        if ("/" in value or "\\" in value)
        else os.path.normcase(value)
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
