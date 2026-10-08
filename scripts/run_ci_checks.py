from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the complete offline validation pipeline used by CI."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs") / "ci",
        help="Directory for eval, replay, and benchmark artifacts",
    )
    return parser


def run_checks(output_dir: Path) -> int:
    destination = output_dir if output_dir.is_absolute() else ROOT / output_dir
    architecture = destination / "architecture-smoke"
    replay = destination / "architecture-smoke-replay"
    benchmark = destination / "architecture-benchmark.json"
    regression = destination / "agent-regression"
    regression_replay = destination / "agent-regression-replay"
    regression_benchmark = destination / "agent-regression-benchmark.json"
    gate_report = destination / "release-gate-report.json"
    commands = (
        [sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests"],
        [sys.executable, "scripts/run_tests.py"],
        [
            sys.executable,
            "scripts/run_eval.py",
            "evals/architecture-smoke.json",
            "--output-dir",
            str(architecture),
        ],
        [
            sys.executable,
            "scripts/run_replay.py",
            "evals/architecture-smoke.json",
            "--trajectories",
            str(architecture / "trajectories"),
            "--output-dir",
            str(replay),
        ],
        [
            sys.executable,
            "scripts/run_benchmark.py",
            str(architecture / "report.json"),
            str(replay / "report.json"),
            "--output",
            str(benchmark),
        ],
        [
            sys.executable,
            "scripts/run_eval.py",
            "evals/agent-regression.json",
            "--output-dir",
            str(regression),
        ],
        [
            sys.executable,
            "scripts/run_replay.py",
            "evals/agent-regression.json",
            "--trajectories",
            str(regression / "trajectories"),
            "--output-dir",
            str(regression_replay),
        ],
        [
            sys.executable,
            "scripts/run_benchmark.py",
            str(regression / "report.json"),
            str(regression_replay / "report.json"),
            "--output",
            str(regression_benchmark),
        ],
        [
            sys.executable,
            "scripts/run_release_gate.py",
            "--config",
            "gates/offline-release.json",
            "--current",
            str(regression / "report.json"),
            "--replay",
            str(regression_replay / "report.json"),
            "--baseline",
            "evals/baselines/agent-regression.json",
            "--output",
            str(gate_report),
        ],
    )

    for command in commands:
        print(f"\n> {shlex.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            print(f"CI checks failed with exit code {completed.returncode}", file=sys.stderr)
            return completed.returncode

    print(f"\nAll CI checks passed. Artifacts: {destination}")
    return 0


def main() -> None:
    raise SystemExit(run_checks(build_parser().parse_args().output_dir))


if __name__ == "__main__":
    main()
