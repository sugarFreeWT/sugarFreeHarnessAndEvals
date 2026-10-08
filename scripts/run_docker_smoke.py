from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sugarfree.sandbox import DockerProcessRunner, ProcessRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the Docker ProcessRunner boundary.")
    parser.add_argument("--image", required=True, help="Digest-pinned image reference")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--executable", default="python")
    return parser


async def run(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    runner = DockerProcessRunner(
        root=workspace,
        image=args.image,
        allowed_executables={args.executable},
    )
    result = await runner.run(
        ProcessRequest(
            argv=(args.executable, "-c", "print('docker sandbox ready')"),
            cwd=workspace,
            timeout_seconds=30,
        )
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return 0 if result.returncode == 0 and not result.timed_out else 1


def main() -> None:
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))


if __name__ == "__main__":
    main()

