from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path

from sugarfree.fs.workspace import WorkspaceFileStore


@dataclass(frozen=True, slots=True)
class FileState:
    path: str
    size: int
    sha256: str
    text: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    files: dict[str, FileState]
    total_bytes: int

    @classmethod
    def capture(
        cls,
        store: WorkspaceFileStore,
        *,
        max_files: int = 1000,
        max_total_bytes: int = 8 * 1024 * 1024,
    ) -> "WorkspaceSnapshot":
        if max_files < 1 or max_total_bytes < 1:
            raise ValueError("snapshot limits must be positive")
        files: dict[str, FileState] = {}
        total_bytes = 0
        for path in _iter_files(store.root):
            relative = path.relative_to(store.root).as_posix()
            size = path.stat().st_size
            if len(files) >= max_files:
                raise ValueError(f"workspace contains more than {max_files} files")
            if total_bytes + size > max_total_bytes:
                raise ValueError(f"workspace exceeds {max_total_bytes} snapshot bytes")
            raw = path.read_bytes()
            text = _decode_small_text(raw, store.max_read_bytes)
            files[relative] = FileState(
                path=relative,
                size=size,
                sha256=hashlib.sha256(raw).hexdigest(),
                text=text,
            )
            total_bytes += size
        return cls(files=files, total_bytes=total_bytes)

    def diff(
        self,
        after: "WorkspaceSnapshot",
        *,
        max_diff_lines: int = 200,
    ) -> list[dict[str, object]]:
        if max_diff_lines < 1:
            raise ValueError("max_diff_lines must be positive")
        changes: list[dict[str, object]] = []
        for path in sorted(set(self.files) | set(after.files)):
            before = self.files.get(path)
            current = after.files.get(path)
            if before and current and before.sha256 == current.sha256:
                continue
            if before is None:
                status = "added"
            elif current is None:
                status = "deleted"
            else:
                status = "modified"
            item: dict[str, object] = {
                "path": path,
                "status": status,
                "before_size": before.size if before else None,
                "after_size": current.size if current else None,
                "before_sha256": before.sha256 if before else None,
                "after_sha256": current.sha256 if current else None,
            }
            diff = _text_diff(before, current)
            if diff:
                item["unified_diff"] = diff[:max_diff_lines]
                item["diff_truncated"] = len(diff) > max_diff_lines
            changes.append(item)
        return changes


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in {".git", "__pycache__", "node_modules"} for part in relative_parts):
            continue
        if any(part.startswith(".") for part in relative_parts):
            continue
        yield path


def _decode_small_text(raw: bytes, limit: int) -> str | None:
    if len(raw) > limit:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _text_diff(before: FileState | None, after: FileState | None) -> list[str]:
    if before is not None and before.text is None:
        return []
    if after is not None and after.text is None:
        return []
    before_text = before.text if before else ""
    after_text = after.text if after else ""
    return list(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
