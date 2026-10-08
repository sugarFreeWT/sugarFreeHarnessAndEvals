from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sugarfree.core.agent import Agent, AgentConfig
from sugarfree.core.events import EventSink
from sugarfree.evals.cases import EvalCase, load_suite
from sugarfree.evals.runner import EvalRunner
from sugarfree.models.scripted import ScriptedModel
from sugarfree.evals.scorers import EventScorer, RequiredToolCallsScorer, WorkspaceChangesScorer
from sugarfree.evals.runtime import default_tools
from sugarfree.fs.workspace import WorkspaceFileStore
from sugarfree.tools.calculator import CalculatorTool
from sugarfree.tools.lookup import LookupTool
from sugarfree.tools.registry import ToolRegistry


class EvalTests(unittest.IsolatedAsyncioTestCase):
    async def test_architecture_suite_passes_and_writes_trajectories(self) -> None:
        root = Path(__file__).resolve().parents[1]
        suite = load_suite(root / "evals" / "architecture-smoke.json")

        def agent_factory(
            case: EvalCase,
            sink: EventSink,
            workspace: WorkspaceFileStore | None,
        ) -> Agent:
            return Agent(
                model=ScriptedModel(case.model_script),
                tools=default_tools(workspace),
                config=AgentConfig(max_steps=case.agent_max_steps),
                event_sink=sink,
                workspace=workspace,
            )

        with tempfile.TemporaryDirectory() as directory:
            report = await EvalRunner(agent_factory=agent_factory).run(suite, directory)

            self.assertTrue(report.passed)
            self.assertEqual(report.passed_cases, 10)
            self.assertTrue((Path(directory) / "report.json").is_file())
            trajectories = list((Path(directory) / "trajectories").glob("*.jsonl"))
            self.assertEqual(len(trajectories), 10)
            self.assertTrue(all(path.read_text(encoding="utf-8") for path in trajectories))
            self.assertEqual(
                (Path(directory) / "workspaces" / "workspace_modify" / "note.txt").read_text(
                    encoding="utf-8"
                ),
                "new value\n",
            )

    async def test_regression_suite_passes_and_reports_tag_coverage(self) -> None:
        root = Path(__file__).resolve().parents[1]
        suite = load_suite(root / "evals" / "agent-regression.json")

        def agent_factory(
            case: EvalCase,
            sink: EventSink,
            workspace: WorkspaceFileStore | None,
        ) -> Agent:
            return Agent(
                model=ScriptedModel(case.model_script),
                tools=default_tools(workspace),
                config=AgentConfig(max_steps=case.agent_max_steps),
                event_sink=sink,
                workspace=workspace,
            )

        with tempfile.TemporaryDirectory() as directory:
            report = await EvalRunner(agent_factory=agent_factory).run(suite, directory)

            self.assertTrue(report.passed)
            self.assertEqual(report.passed_cases, 23)
            self.assertEqual(report.tag_summary["security"]["total_cases"], 3)
            self.assertEqual(report.tag_summary["security"]["pass_rate"], 1.0)
            self.assertEqual(report.tag_summary["workspace"]["total_cases"], 8)
            payload = json.loads((Path(directory) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["tag_summary"]["recovery"]["passed_cases"], 7)

    def test_duplicate_case_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(
                '{"cases": ['
                '{"id":"same","prompt":"a","model_script":[]},'
                '{"id":"same","prompt":"b","model_script":[]}'
                ']}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_suite(path)

    def test_invalid_or_duplicate_tags_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tags.json"
            path.write_text(
                '{"cases":[{"id":"bad-tag","prompt":"x","tags":["Bad Tag"],'
                '"model_script":[{"content":"x"}]}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid tag"):
                load_suite(path)

            path.write_text(
                '{"cases":[{"id":"duplicate-tag","prompt":"x","tags":["core","core"],'
                '"model_script":[{"content":"x"}]}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "tags must be unique"):
                load_suite(path)

    def test_live_suite_does_not_require_model_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.json"
            path.write_text(
                '{"mode":"live","cases":['
                '{"id":"live-case","prompt":"answer","expect":{"answer_contains":["ok"]}}'
                ']}',
                encoding="utf-8",
            )

            suite = load_suite(path)

            self.assertEqual(suite.mode, "live")
            self.assertEqual(suite.cases[0].model_script, ())

    def test_scripted_suite_requires_model_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scripted.json"
            path.write_text(
                '{"mode":"scripted","cases":['
                '{"id":"missing-script","prompt":"answer"}'
                ']}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "model_script"):
                load_suite(path)

    async def test_required_tool_calls_checks_order_and_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strict.json"
            path.write_text(
                '{"cases":[{"id":"strict","prompt":"answer",'
                '"model_script":[{"tool_calls":[{"id":"c1","name":"calculator",'
                '"arguments":{"expression":"7 * 13"}}]},{"content":"91"}],'
                '"expect":{"required_tool_calls":[{"name":"calculator",'
                '"arguments":{"expression":"7 * 13"}}]}}]}',
                encoding="utf-8",
            )
            suite = load_suite(path)
            case = suite.cases[0]
            result = await Agent(
                model=ScriptedModel(case.model_script),
                tools=ToolRegistry([CalculatorTool()]),
            ).run(case.prompt)

            score = RequiredToolCallsScorer().score(case, result)

            self.assertIsNotNone(score)
            self.assertTrue(score.passed)

    async def test_event_scorer_checks_required_and_forbidden_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(
                '{"cases":[{"id":"events","prompt":"answer",'
                '"model_script":[{"content":"done"}],'
                '"expect":{"required_events":["run.completed"],'
                '"forbidden_events":["tool.requested"]}}]}',
                encoding="utf-8",
            )
            case = load_suite(path).cases[0]
            result = await Agent(model=ScriptedModel(case.model_script)).run(case.prompt)

            score = EventScorer().score(case, result)

            self.assertIsNotNone(score)
            self.assertTrue(score.passed)

    async def test_workspace_scorer_rejects_wrong_content_and_unexpected_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace.json"
            path.write_text(
                '{"cases":[{"id":"workspace-score","prompt":"write files",'
                '"workspace":{"files":{}},'
                '"model_script":[{"tool_calls":['
                '{"id":"w1","name":"write_file","arguments":{"path":"result.txt","content":"wrong"}},'
                '{"id":"w2","name":"write_file","arguments":{"path":"extra.txt","content":"extra"}}]},'
                '{"content":"done"}],'
                '"expect":{"workspace_changes":['
                '{"path":"result.txt","status":"added","content":"expected"}]}}]}',
                encoding="utf-8",
            )
            case = load_suite(path).cases[0]
            workspace_root = Path(directory) / "workspace"
            workspace_root.mkdir()
            workspace = WorkspaceFileStore(workspace_root)
            result = await Agent(
                model=ScriptedModel(case.model_script),
                tools=default_tools(workspace),
                workspace=workspace,
            ).run(case.prompt)

            score = WorkspaceChangesScorer().score(case, result)

            self.assertIsNotNone(score)
            self.assertFalse(score.passed)
            self.assertIn("unexpected changes: ['extra.txt']", score.detail)
            self.assertIn("final content hash did not match", score.detail)

    def test_workspace_expectation_requires_safe_explicit_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(
                '{"cases":[{"id":"unsafe","prompt":"write",'
                '"workspace":{"files":{"../outside.txt":"bad"}},'
                '"model_script":[],"expect":{"workspace_changes":[]}}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "stay inside"):
                load_suite(path)

            path.write_text(
                '{"cases":[{"id":"dot-path","prompt":"write",'
                '"workspace":{"files":{".":"bad"}},"model_script":[]}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "stay inside"):
                load_suite(path)

            path.write_text(
                '{"cases":[{"id":"implicit","prompt":"write","model_script":[],'
                '"expect":{"workspace_changes":[]}}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "requires a workspace"):
                load_suite(path)


if __name__ == "__main__":
    unittest.main()
