from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sugarfree.evals.benchmark import build_benchmark


def _write_report(path: Path, *, suite: str = "demo", passed: int = 2, elapsed: float = 2.0) -> None:
    path.write_text(
        json.dumps(
            {
                "suite": suite,
                "started_at": "2026-01-01T00:00:00+00:00",
                "metadata": {"model": "scripted"},
                "passed": passed == 2,
                "summary": {
                    "passed_cases": passed,
                    "total_cases": 2,
                    "pass_rate": passed / 2,
                    "steps": 4,
                    "tool_calls": 2,
                    "tokens": 20,
                    "elapsed_seconds": elapsed,
                },
                "cases": [
                    {"case_id": "one", "passed": True},
                    {"case_id": "two", "passed": passed == 2},
                ],
            }
        ),
        encoding="utf-8",
    )


class BenchmarkTests(unittest.TestCase):
    def test_builds_adjacent_comparisons_and_case_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            _write_report(first, passed=1, elapsed=4.0)
            _write_report(second, passed=2, elapsed=2.0)

            benchmark = build_benchmark([first, second])

            self.assertEqual(benchmark["run_count"], 2)
            self.assertEqual(benchmark["comparisons"][0]["delta"]["pass_rate"], 0.5)
            self.assertEqual(benchmark["comparisons"][0]["case_changes"]["fixed_failures"], ["two"])
            self.assertEqual(benchmark["runs"][0]["average_elapsed_seconds"], 2.0)

    def test_different_suites_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            _write_report(first, suite="one")
            _write_report(second, suite="two")

            with self.assertRaisesRegex(ValueError, "different suites"):
                build_benchmark([first, second])


if __name__ == "__main__":
    unittest.main()

