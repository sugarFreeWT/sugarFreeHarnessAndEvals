from __future__ import annotations

import os
import tempfile
from pathlib import Path


class WorkspacePathError(ValueError):
    """A path that escapes the configured workspace or violates its policy."""


class WorkspaceFileStore:
    """Constrained UTF-8 file access rooted at one workspace directory."""

    def __init__(self, root: str | Path, *, max_read_bytes: int = 256 * 1024) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"workspace root does not exist: {self.root}")
        if max_read_bytes < 1:
            raise ValueError("max_read_bytes must be at least 1")
        self.max_read_bytes = max_read_bytes

    def resolve(self, relative_path: str) -> Path:
        if not relative_path or "\x00" in relative_path:
            raise WorkspacePathError("path must be a non-empty string without NUL bytes")
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePathError(f"path escapes workspace: {relative_path}") from exc
        return candidate

    def read_text(self, relative_path: str) -> str:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise WorkspacePathError(f"file does not exist: {relative_path}")
        size = path.stat().st_size
        if size > self.max_read_bytes:
            raise WorkspacePathError(
                f"file is too large: {size} bytes exceeds {self.max_read_bytes} byte limit"
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspacePathError(f"file is not valid UTF-8: {relative_path}") from exc

    def write_text(self, relative_path: str, content: str) -> int:
        if not isinstance(content, str):
            raise WorkspacePathError("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_read_bytes:
            raise WorkspacePathError(
                f"content is too large: {len(encoded)} bytes exceeds {self.max_read_bytes} byte limit"
            )
        path = self.resolve(relative_path)
        if path.exists() and not path.is_file():
            raise WorkspacePathError(f"target is not a regular file: {relative_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return len(encoded)

