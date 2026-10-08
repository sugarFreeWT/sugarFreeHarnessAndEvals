from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sugarfree.evals.gate import evaluate_release_gate


def _write_report(
    path: Path,
    *,
    case_results: dict[str, bool] | None = None,
    security_rate: float = 1.0,
    steps: int = 6,
    tool_calls: int = 3,
) -> None:
    results = case_results or {"core_case": True, "security_case": True, "workspace_case": True}
    passed = sum(results.values())
    total = len(results)
    path.write_text(
        json.dumps(
            {
                "suite": "demo",
                "summary": {
                    "passed_cases": passed,
                    "total_cases": total,
                    "pass_rate": passed / total,
                    "steps": steps,
                    "tool_calls": tool_calls,
                },
                "tag_summary": {
                    "security": {
                        "passed_cases": round(security_rate),
                        "total_cases": 1,
                        "pass_rate": security_rate,
                    }
                },
                "cases": [
                    {"case_id": case_id, "passed": case_passed}
                    for case_id, case_passed in results.items()
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_baseline(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "suite": "demo",
                "summary": {
                    "passed_cases": 3,
                    "total_cases": 3,
                    "pass_rate": 1.0,
                    "average_steps": 2.0,
                    "average_tool_calls": 1.0,
                },
                "case_results": {
                    "core_case": True,
                    "security_case": True,
                    "workspace_case": True,
                },
            }
        ),
        encoding="utf-8",
    )


def _write_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "name": "test-gate",
                "suite": "demo",
                "min_total_cases": 3,
                "min_pass_rate": 1.0,
                "replay_min_pass_rate": 1.0,
                "require_replay": True,
                "require_replay_case_parity": True,
                "require_baseline": True,
                "max_removed_cases": 0,
                "max_new_failures": 0,
                "max_average_steps_increase": 0.1,
                "max_average_tool_calls_increase": 0.1,
                "required_cases": ["core_case", "security_case"],
                "tag_min_pass_rates": {"security": 1.0},
            }
        ),
        encoding="utf-8",
    )


class ReleaseGateTests(unittest.TestCase):
    def test_gate_passes_for_matching_reports_and_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            current = root / "current.json"
            replay = root / "replay.json"
            baseline = root / "baseline.json"
            _write_config(config)
            _write_report(current)
            _write_report(replay)
            _write_baseline(baseline)

            result = evaluate_release_gate(
                config_path=config,
                current_report=current,
                replay_report=replay,
                baseline_report=baseline,
            )

            self.assertTrue(result.passed)
            self.assertTrue(all(check.passed for check in result.checks))

    def test_gate_reports_new_failure_tag_and_replay_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            current = root / "current.json"
            replay = root / "replay.json"
            baseline = root / "baseline.json"
            _write_config(config)
            _write_report(
                current,
                case_results={
                    "core_case": True,
                    "security_case": False,
                    "workspace_case": True,
                },
                security_rate=0.0,
            )
            _write_report(replay)
            _write_baseline(baseline)

            result = evaluate_release_gate(
                config_path=config,
                current_report=current,
                replay_report=replay,
                baseline_report=baseline,
            )

            failed = {check.name for check in result.checks if not check.passed}
            self.assertFalse(result.passed)
            self.assertIn("pass_rate", failed)
            self.assertIn("required_cases", failed)
            self.assertIn("tag:security", failed)
            self.assertIn("replay_case_parity", failed)
            self.assertIn("new_failures", failed)

    def test_gate_detects_removed_case_and_metric_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            current = root / "current.json"
            baseline = root / "baseline.json"
            _write_config(config)
            config_data = json.loads(config.read_text(encoding="utf-8"))
            config_data["require_replay"] = False
            config_data["min_total_cases"] = 2
            config_data["required_cases"] = ["core_case"]
            config.write_text(json.dumps(config_data), encoding="utf-8")
            _write_report(
                current,
                case_results={"core_case": True, "security_case": True},
                steps=6,
                tool_calls=4,
            )
            _write_baseline(baseline)

            result = evaluate_release_gate(
                config_path=config,
                current_report=current,
                baseline_report=baseline,
            )

            failed = {check.name for check in result.checks if not check.passed}
            self.assertIn("removed_cases", failed)
            self.assertIn("average_steps", failed)
            self.assertIn("average_tool_calls", failed)


if __name__ == "__main__":
    unittest.main()
