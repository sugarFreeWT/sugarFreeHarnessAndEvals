from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sugarfree.fs import WorkspaceFileStore, WorkspacePathError, WorkspaceSnapshot
from sugarfree.tools.read_file import ReadFileTool
from sugarfree.tools.write_file import WriteFileTool
from sugarfree.tools.base import ToolExecutionError


class WorkspaceFileStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_and_atomically_writes_utf8_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFileStore(root)
            writer = WriteFileTool(store)
            reader = ReadFileTool(store)

            written = await writer.execute({"path": "src/note.txt", "content": "你好\nworld"})
            read = await reader.execute({"path": "src/note.txt"})

            self.assertFalse(written.is_error)
            self.assertIn("wrote", written.content)
            self.assertEqual(read.content, "你好\nworld")
            self.assertFalse(list((root / "src").glob(".*.tmp")))

    async def test_rejects_path_traversal_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceFileStore(directory)

            for path in ("../outside.txt", str(Path(directory).parent / "outside.txt")):
                with self.assertRaises(WorkspacePathError):
                    store.resolve(path)

    async def test_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            try:
                (root / "link").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is unavailable on this Windows environment")
            store = WorkspaceFileStore(root)

            with self.assertRaises(WorkspacePathError):
                store.resolve("link/secret.txt")

    async def test_enforces_size_and_utf8_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFileStore(root, max_read_bytes=4)
            (root / "large.txt").write_text("12345", encoding="utf-8")
            (root / "binary.txt").write_bytes(b"\xff\xfe")

            with self.assertRaises(WorkspacePathError):
                store.read_text("large.txt")
            with self.assertRaises(WorkspacePathError):
                store.read_text("binary.txt")
            with self.assertRaises(WorkspacePathError):
                store.write_text("new.txt", "12345")

    async def test_read_and_write_tools_return_safe_tool_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceFileStore(directory)
            with self.assertRaises(ToolExecutionError):
                await ReadFileTool(store).execute({"path": "missing.txt"})
            with self.assertRaises(ToolExecutionError):
                await WriteFileTool(store).execute({"path": "../bad", "content": "x"})


class WorkspaceSnapshotTests(unittest.TestCase):
    def test_reports_added_modified_and_deleted_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFileStore(root)
            (root / "modified.txt").write_text("old\n", encoding="utf-8")
            (root / "deleted.txt").write_text("gone\n", encoding="utf-8")
            before = WorkspaceSnapshot.capture(store)

            (root / "modified.txt").write_text("new\n", encoding="utf-8")
            (root / "deleted.txt").unlink()
            (root / "added.txt").write_text("hello\n", encoding="utf-8")
            changes = WorkspaceSnapshot.capture(store)
            by_path = {item["path"]: item for item in before.diff(changes)}

            self.assertEqual(by_path["added.txt"]["status"], "added")
            self.assertIn("+hello", by_path["added.txt"]["unified_diff"])
            self.assertEqual(by_path["modified.txt"]["status"], "modified")
            self.assertIn("-old", by_path["modified.txt"]["unified_diff"])
            self.assertIn("+new", by_path["modified.txt"]["unified_diff"])
            self.assertEqual(by_path["deleted.txt"]["status"], "deleted")
            self.assertIn("-gone", by_path["deleted.txt"]["unified_diff"])
            self.assertNotEqual(
                by_path["modified.txt"]["before_sha256"],
                by_path["modified.txt"]["after_sha256"],
            )

    def test_binary_change_has_metadata_without_text_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFileStore(root)
            path = root / "data.bin"
            path.write_bytes(b"\xff\x00")
            before = WorkspaceSnapshot.capture(store)
            path.write_bytes(b"\xfe\x01\x02")

            change = before.diff(WorkspaceSnapshot.capture(store))[0]

            self.assertEqual(change["status"], "modified")
            self.assertEqual(change["before_size"], 2)
            self.assertEqual(change["after_size"], 3)
            self.assertNotIn("unified_diff", change)

    def test_excludes_internal_hidden_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            for folder in (".git", "node_modules", "__pycache__", ".hidden"):
                (root / folder).mkdir()
                (root / folder / "ignored.txt").write_text("ignored", encoding="utf-8")
            (root / ".ignored.txt").write_text("ignored", encoding="utf-8")
            (root / "kept.txt").write_text("kept", encoding="utf-8")
            try:
                (root / "outside-link.txt").symlink_to(Path(outside) / "outside.txt")
                (Path(outside) / "outside.txt").write_text("outside", encoding="utf-8")
            except OSError:
                pass

            snapshot = WorkspaceSnapshot.capture(WorkspaceFileStore(root))

            self.assertEqual(set(snapshot.files), {"kept.txt"})

    def test_enforces_file_and_total_byte_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFileStore(root)
            (root / "a.txt").write_text("12", encoding="utf-8")
            (root / "b.txt").write_text("34", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "more than 1 files"):
                WorkspaceSnapshot.capture(store, max_files=1)
            with self.assertRaisesRegex(ValueError, "exceeds 3 snapshot bytes"):
                WorkspaceSnapshot.capture(store, max_total_bytes=3)

    def test_limits_diff_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFileStore(root)
            path = root / "many.txt"
            path.write_text("\n".join(f"old-{index}" for index in range(10)), encoding="utf-8")
            before = WorkspaceSnapshot.capture(store)
            path.write_text("\n".join(f"new-{index}" for index in range(10)), encoding="utf-8")

            change = before.diff(WorkspaceSnapshot.capture(store), max_diff_lines=5)[0]

            self.assertEqual(len(change["unified_diff"]), 5)
            self.assertTrue(change["diff_truncated"])
            with self.assertRaisesRegex(ValueError, "max_diff_lines must be positive"):
                before.diff(WorkspaceSnapshot.capture(store), max_diff_lines=0)


if __name__ == "__main__":
    unittest.main()
