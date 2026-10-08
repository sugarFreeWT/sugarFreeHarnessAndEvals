from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from sugarfree.sandbox import LocalRestrictedProcessRunner, ProcessRequest, SandboxError
from sugarfree.tools.run_command import RunCommandTool


class RestrictedProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_allowed_executable_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = LocalRestrictedProcessRunner(
                root=root,
                allowed_executables={sys.executable},
            )

            result = await runner.run(
                ProcessRequest(
                    argv=(sys.executable, "-c", "print('safe')"),
                    cwd=root,
                    env={},
                )
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "safe")

    async def test_rejects_executable_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = LocalRestrictedProcessRunner(root=root, allowed_executables=set())

            with self.assertRaisesRegex(SandboxError, "not allowed"):
                await runner.run(ProcessRequest(argv=(sys.executable,), cwd=root))

    async def test_rejects_working_directory_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            runner = LocalRestrictedProcessRunner(
                root=directory,
                allowed_executables={sys.executable},
            )

            with self.assertRaisesRegex(SandboxError, "outside sandbox root"):
                await runner.run(
                    ProcessRequest(argv=(sys.executable, "-c", "pass"), cwd=Path(outside))
                )

    async def test_timeout_kills_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = LocalRestrictedProcessRunner(
                root=root,
                allowed_executables={sys.executable},
            )

            result = await runner.run(
                ProcessRequest(
                    argv=(sys.executable, "-c", "import time; time.sleep(10)"),
                    cwd=root,
                    timeout_seconds=0.05,
                )
            )

            self.assertTrue(result.timed_out)
            self.assertEqual(result.returncode, -1)

    async def test_output_is_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = LocalRestrictedProcessRunner(
                root=root,
                allowed_executables={sys.executable},
                max_output_bytes=8,
            )

            result = await runner.run(
                ProcessRequest(
                    argv=(sys.executable, "-c", "print('abcdefghijklmnop')"),
                    cwd=root,
                )
            )

            self.assertTrue(result.output_truncated)
            self.assertLessEqual(len(result.stdout.encode("utf-8")), 8)

    async def test_run_command_tool_uses_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = LocalRestrictedProcessRunner(
                root=root,
                allowed_executables={sys.executable},
            )
            tool = RunCommandTool(runner, str(root))

            result = await tool.execute(
                {"argv": [sys.executable, "-c", "print('through tool')"]}
            )

            self.assertFalse(result.is_error)
            self.assertIn("through tool", result.content)


if __name__ == "__main__":
    unittest.main()

