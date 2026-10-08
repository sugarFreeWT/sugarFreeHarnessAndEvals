from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath

from sugarfree.sandbox.types import ProcessRequest, ProcessResult, SandboxError


class DockerProcessRunner:
    """Runs allowlisted commands in a locked-down Docker container."""

    def __init__(
        self,
        *,
        root: str | Path,
        image: str,
        allowed_executables: set[str] | frozenset[str],
        docker_executable: str | None = None,
        workspace_writable: bool = False,
        memory: str = "256m",
        cpus: float = 1.0,
        pids_limit: int = 64,
        max_output_bytes: int = 64 * 1024,
        require_image_digest: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.image = image
        self.allowed_executables = frozenset(allowed_executables)
        self.docker_executable = docker_executable or shutil.which("docker") or "docker"
        self.workspace_writable = workspace_writable
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.max_output_bytes = max_output_bytes
        if not self.root.is_dir():
            raise ValueError(f"sandbox root does not exist: {self.root}")
        if not image or (require_image_digest and not _PINNED_IMAGE.fullmatch(image)):
            raise ValueError("Docker image must be pinned by sha256 digest")
        if not self.allowed_executables:
            raise ValueError("allowed_executables cannot be empty")
        if cpus <= 0 or pids_limit < 1 or max_output_bytes < 1:
            raise ValueError("Docker resource limits must be positive")
        if "," in str(self.root):
            raise ValueError("sandbox root cannot contain a comma")

    async def run(self, request: ProcessRequest) -> ProcessResult:
        container_name = f"sugarfree-{uuid.uuid4().hex}"
        command = self.build_command(request, container_name=container_name)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SandboxError(f"could not start Docker: {exc}") from exc

        try:
            stdout, stderr = await _communicate(process, request.timeout_seconds)
        except asyncio.CancelledError:
            await asyncio.shield(self._stop_container(container_name, process))
            raise
        except asyncio.TimeoutError:
            stdout, stderr = await self._stop_container(container_name, process)
            stdout_text, stdout_truncated = _decode(stdout, self.max_output_bytes)
            stderr_text, stderr_truncated = _decode(stderr, self.max_output_bytes)
            return ProcessResult(
                returncode=-1,
                stdout=stdout_text,
                stderr=stderr_text,
                timed_out=True,
                output_truncated=stdout_truncated or stderr_truncated,
            )

        stdout_text, stdout_truncated = _decode(stdout, self.max_output_bytes)
        stderr_text, stderr_truncated = _decode(stderr, self.max_output_bytes)
        return ProcessResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout_text,
            stderr=stderr_text,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    def build_command(self, request: ProcessRequest, *, container_name: str) -> tuple[str, ...]:
        if not _VALID_CONTAINER_NAME.fullmatch(container_name):
            raise SandboxError(f"invalid container name: {container_name}")
        relative_cwd = self._validate(request)
        container_cwd = str(PurePosixPath("/workspace", *relative_cwd.parts))
        mount = f"type=bind,source={self.root},target=/workspace"
        if not self.workspace_writable:
            mount += ",readonly"
        return (
            self.docker_executable,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            str(self.cpus),
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            mount,
            "--workdir",
            container_cwd,
            self.image,
            *request.argv,
        )

    def _validate(self, request: ProcessRequest) -> Path:
        if not request.argv or not all(isinstance(value, str) and value for value in request.argv):
            raise SandboxError("argv must contain at least one non-empty string")
        if any("\x00" in value for value in request.argv):
            raise SandboxError("argv cannot contain NUL bytes")
        if request.argv[0] not in self.allowed_executables:
            raise SandboxError(f"container executable is not allowed: {request.argv[0]}")
        if request.env:
            raise SandboxError("Docker environment injection is disabled")
        cwd = request.cwd.resolve()
        try:
            relative = cwd.relative_to(self.root)
        except ValueError as exc:
            raise SandboxError(f"working directory is outside sandbox root: {cwd}") from exc
        if not cwd.is_dir():
            raise SandboxError(f"working directory does not exist: {cwd}")
        if request.timeout_seconds is not None and request.timeout_seconds <= 0:
            raise SandboxError("timeout_seconds must be greater than zero")
        return relative

    async def _stop_container(
        self,
        container_name: str,
        process: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes]:
        if not _VALID_CONTAINER_NAME.fullmatch(container_name):
            process.kill()
            return await process.communicate()
        try:
            cleanup = await asyncio.create_subprocess_exec(
                self.docker_executable,
                "rm",
                "--force",
                container_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(cleanup.wait(), timeout=10)
        except (OSError, asyncio.TimeoutError):
            pass
        if process.returncode is None:
            process.kill()
        return await process.communicate()


_VALID_CONTAINER_NAME = re.compile(r"sugarfree-[a-f0-9]{32}")
_PINNED_IMAGE = re.compile(r"[^\s]+@sha256:[a-fA-F0-9]{64}")


async def _communicate(
    process: asyncio.subprocess.Process,
    timeout_seconds: float | None,
) -> tuple[bytes, bytes]:
    communication = process.communicate()
    if timeout_seconds is None:
        return await communication
    return await asyncio.wait_for(communication, timeout=timeout_seconds)


def _decode(value: bytes, limit: int) -> tuple[str, bool]:
    return value[:limit].decode("utf-8", errors="replace"), len(value) > limit
