from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sugarfree.evals.feedback import (
    collect_failures,
    load_inbox,
    promote_failure,
    reconcile_failures,
    review_failure,
)


def _write_failure_report(
    path: Path,
    *,
    prompt: str = "Use api_key=sk-1234567890abcdef and Bearer token-value-12345",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "suite": "live-smoke",
                "started_at": "2026-09-28T00:00:00+00:00",
                "summary": {"passed_cases": 0, "total_cases": 1},
                "cases": [
                    {
                        "case_id": "bad_answer",
                        "tags": ["negative", "security"],
                        "passed": False,
                        "scores": [
                            {
                                "scorer": "answer_contains",
                                "passed": False,
                                "detail": "missing answer; token=private-token-123",
                            },
                            {
                                "scorer": "max_steps",
                                "passed": True,
                                "detail": "within limit",
                            },
                        ],
                        "result": {
                            "termination_reason": "completed",
                            "error": "password:super-secret-value",
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_regression_report(path: Path, *, passed: bool) -> None:
    path.write_text(
        json.dumps(
            {
                "suite": "agent-regression",
                "cases": [{"case_id": "regression_bad_answer", "passed": passed}],
            }
        ),
        encoding="utf-8",
    )


class FailureFeedbackTests(unittest.TestCase):
    def test_collects_failed_case_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "run" / "report.json"
            inbox_path = root / "failure-inbox.json"
            _write_failure_report(report)

            inbox = collect_failures([report], inbox_path)

            self.assertEqual(len(inbox["items"]), 1)
            item = inbox["items"][0]
            self.assertEqual(item["status"], "candidate")
            self.assertEqual(item["failed_scorers"], ["answer_contains"])
            self.assertEqual(item["occurrence_count"], 1)
            serialized = json.dumps(item, ensure_ascii=False)
            self.assertNotIn("sk-1234567890abcdef", serialized)
            self.assertNotIn("private-token-123", serialized)
            self.assertNotIn("super-secret-value", serialized)
            self.assertGreaterEqual(serialized.count("[REDACTED]"), 3)

    def test_collect_redacts_provider_masked_api_key_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "run" / "report.json"
            inbox_path = root / "failure-inbox.json"
            _write_failure_report(report)
            payload = json.loads(report.read_text(encoding="utf-8"))
            payload["cases"][0]["result"]["error"] = (
                "Authentication Fails, Your api key: ****e8ac is invalid"
            )
            report.write_text(json.dumps(payload), encoding="utf-8")

            item = collect_failures([report], inbox_path)["items"][0]

            self.assertIn("api key: [REDACTED]", item["error"])
            self.assertNotIn("e8ac", item["error"])

    def test_same_report_is_idempotent_but_another_report_increments_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "run-one" / "report.json"
            second = root / "run-two" / "report.json"
            inbox_path = root / "failure-inbox.json"
            _write_failure_report(first, prompt="first")
            _write_failure_report(second, prompt="second")

            collect_failures([first], inbox_path)
            repeated = collect_failures([first], inbox_path)
            merged = collect_failures([second], inbox_path)

            self.assertEqual(repeated["items"][0]["occurrence_count"], 1)
            self.assertEqual(len(merged["items"]), 1)
            self.assertEqual(merged["items"][0]["occurrence_count"], 2)
            self.assertEqual(len(merged["items"][0]["source_reports"]), 2)

    def test_review_promote_and_reconcile_closes_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "failed" / "report.json"
            regression = root / "regression.json"
            inbox_path = root / "failure-inbox.json"
            _write_failure_report(report)
            fingerprint = collect_failures([report], inbox_path)["items"][0]["fingerprint"]

            approved = review_failure(
                inbox_path,
                fingerprint,
                "approved",
                "reviewer",
                "confirmed failure",
            )
            promoted = promote_failure(
                inbox_path,
                fingerprint,
                "regression_bad_answer",
                "reviewer",
            )
            _write_regression_report(regression, passed=False)
            unresolved = reconcile_failures(inbox_path, regression)
            _write_regression_report(regression, passed=True)
            resolved = reconcile_failures(inbox_path, regression)

            self.assertEqual(approved["status"], "approved")
            self.assertEqual(promoted["status"], "promoted")
            self.assertEqual(unresolved["items"][0]["status"], "promoted")
            self.assertEqual(resolved["items"][0]["status"], "resolved")
            self.assertEqual(
                resolved["items"][0]["regression_case_id"], "regression_bad_answer"
            )
            self.assertIsNotNone(resolved["items"][0]["resolved_at"])
            self.assertEqual(load_inbox(inbox_path)["items"][0]["status"], "resolved")

    def test_illegal_state_transitions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "failed" / "report.json"
            inbox_path = root / "failure-inbox.json"
            _write_failure_report(report)
            fingerprint = collect_failures([report], inbox_path)["items"][0]["fingerprint"]

            with self.assertRaisesRegex(ValueError, "only an approved"):
                promote_failure(
                    inbox_path,
                    fingerprint,
                    "regression_bad_answer",
                    "reviewer",
                )
            review_failure(inbox_path, fingerprint, "rejected", "reviewer")
            with self.assertRaisesRegex(ValueError, "rejected to rejected"):
                review_failure(inbox_path, fingerprint, "rejected", "reviewer")


if __name__ == "__main__":
    unittest.main()
