from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sugarfree.sandbox import DockerProcessRunner, ProcessRequest, SandboxError


PINNED_IMAGE = "python@sha256:" + "a" * 64
CONTAINER_NAME = "sugarfree-" + "b" * 32


class DockerProcessRunnerTests(unittest.TestCase):
    def test_builds_locked_down_docker_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            runner = DockerProcessRunner(
                root=root,
                image=PINNED_IMAGE,
                allowed_executables={"python"},
                docker_executable="docker-test",
            )

            command = runner.build_command(
                ProcessRequest(argv=("python", "-c", "print('ok')"), cwd=child),
                container_name=CONTAINER_NAME,
            )

            self.assertEqual(command[0:2], ("docker-test", "run"))
            self.assertIn("none", command)
            self.assertIn("--read-only", command)
            self.assertIn("ALL", command)
            self.assertIn("no-new-privileges", command)
            self.assertIn("65534:65534", command)
            self.assertIn("/workspace/child", command)
            self.assertIn(PINNED_IMAGE, command)
            mount = command[command.index("--mount") + 1]
            self.assertTrue(mount.endswith(",readonly"))
            self.assertEqual(command[-3:], ("python", "-c", "print('ok')"))

    def test_rejects_unpinned_image_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "pinned"):
                DockerProcessRunner(
                    root=directory,
                    image="python:latest",
                    allowed_executables={"python"},
                )

    def test_rejects_environment_injection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = DockerProcessRunner(
                root=root,
                image=PINNED_IMAGE,
                allowed_executables={"python"},
            )

            with self.assertRaisesRegex(SandboxError, "environment injection"):
                runner.build_command(
                    ProcessRequest(argv=("python",), cwd=root, env={"SECRET": "value"}),
                    container_name=CONTAINER_NAME,
                )

    def test_rejects_non_allowlisted_container_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = DockerProcessRunner(
                root=root,
                image=PINNED_IMAGE,
                allowed_executables={"python"},
            )

            with self.assertRaisesRegex(SandboxError, "not allowed"):
                runner.build_command(
                    ProcessRequest(argv=("sh", "-c", "echo unsafe"), cwd=root),
                    container_name=CONTAINER_NAME,
                )

    def test_writable_workspace_is_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = DockerProcessRunner(
                root=root,
                image=PINNED_IMAGE,
                allowed_executables={"python"},
                workspace_writable=True,
            )

            command = runner.build_command(
                ProcessRequest(argv=("python",), cwd=root),
                container_name=CONTAINER_NAME,
            )
            mount = command[command.index("--mount") + 1]
            self.assertFalse(mount.endswith(",readonly"))


if __name__ == "__main__":
    unittest.main()

