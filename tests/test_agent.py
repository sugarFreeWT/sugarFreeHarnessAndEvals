from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from sugarfree.core.agent import Agent, AgentConfig
from sugarfree.core.messages import AssistantTurn, ToolCall
from sugarfree.core.result import TerminationReason
from sugarfree.models.scripted import ScriptedModel
from sugarfree.models.replay import ReplayModel
from sugarfree.fs import WorkspaceFileStore
from sugarfree.tools.calculator import CalculatorTool
from sugarfree.tools.base import ToolResult
from sugarfree.tools.registry import ToolRegistry
from sugarfree.tools.write_file import WriteFileTool
from sugarfree.security.policy import AllowlistPolicy, ApprovalPolicy


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_answer_completes(self) -> None:
        agent = Agent(model=ScriptedModel([AssistantTurn(content="done")]))

        result = await agent.run("start", run_id="direct")

        self.assertEqual(result.answer, "done")
        self.assertEqual(result.termination_reason, TerminationReason.COMPLETED)
        self.assertEqual([event.kind for event in result.events], [
            "run.started",
            "model.requested",
            "model.responded",
            "run.completed",
        ])

    async def test_tool_result_is_returned_to_model(self) -> None:
        model = ScriptedModel(
            [
                AssistantTurn(
                    tool_calls=(
                        ToolCall("call-1", "calculator", {"expression": "6 * 7"}),
                    )
                ),
                AssistantTurn(content="42"),
            ]
        )
        agent = Agent(model=model, tools=ToolRegistry([CalculatorTool()]))

        result = await agent.run("calculate", run_id="tool")

        self.assertEqual(result.answer, "42")
        self.assertEqual(result.tool_calls, 1)
        second_request = model.requests[1]
        self.assertEqual(second_request.messages[-1].role, "tool")
        self.assertEqual(second_request.messages[-1].content, "42")

    async def test_unknown_tool_is_recoverable(self) -> None:
        model = ScriptedModel(
            [
                AssistantTurn(tool_calls=(ToolCall("call-1", "missing", {}),)),
                AssistantTurn(content="recovered"),
            ]
        )
        agent = Agent(model=model)

        result = await agent.run("start", run_id="recover")

        self.assertEqual(result.termination_reason, TerminationReason.COMPLETED)
        self.assertTrue(model.requests[1].messages[-1].is_error)
        self.assertIn("unknown tool", model.requests[1].messages[-1].content)
        self.assertIn("tool.failed", [event.kind for event in result.events])

    async def test_max_steps_is_explicit(self) -> None:
        call = AssistantTurn(
            tool_calls=(ToolCall("call-1", "calculator", {"expression": "1 + 1"}),)
        )
        agent = Agent(
            model=ScriptedModel([call, call, AssistantTurn(content="unreachable")]),
            tools=ToolRegistry([CalculatorTool()]),
            config=AgentConfig(max_steps=2),
        )

        result = await agent.run("loop", run_id="bounded")

        self.assertEqual(result.termination_reason, TerminationReason.MAX_STEPS)
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.tool_calls, 2)

    async def test_observer_failure_does_not_break_run(self) -> None:
        class BrokenSink:
            def emit(self, event: object) -> None:
                raise RuntimeError("sink unavailable")

        agent = Agent(
            model=ScriptedModel([AssistantTurn(content="done")]),
            event_sink=BrokenSink(),
        )

        result = await agent.run("start", run_id="observer")

        self.assertEqual(result.termination_reason, TerminationReason.COMPLETED)
        self.assertIn("observer.failed", [event.kind for event in result.events])

    async def test_recorded_trajectory_can_be_replayed(self) -> None:
        import tempfile
        from pathlib import Path

        from sugarfree.tracing.jsonl import JsonlEventSink

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            original = Agent(
                model=ScriptedModel([AssistantTurn(content="done")]),
                event_sink=JsonlEventSink(path),
            )
            first = await original.run("start", run_id="replay")
            replayed = Agent(
                model=ReplayModel(path),
                event_sink=JsonlEventSink(Path(directory) / "replayed.jsonl"),
            )

            second = await replayed.run("start", run_id="replay")

            self.assertEqual(second.answer, first.answer)
            self.assertEqual(second.termination_reason, first.termination_reason)
            self.assertEqual(second.steps, first.steps)
            self.assertEqual(second.tool_calls, first.tool_calls)

    async def test_recorded_model_failure_can_be_replayed(self) -> None:
        import tempfile
        from pathlib import Path

        from sugarfree.tracing.jsonl import JsonlEventSink

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failed.jsonl"
            original = Agent(
                model=ScriptedModel([]),
                event_sink=JsonlEventSink(path),
            )
            first = await original.run("fail", run_id="failed-replay")
            replayed = Agent(model=ReplayModel(path))

            second = await replayed.run("fail", run_id="failed-replay")

            self.assertEqual(first.termination_reason, TerminationReason.MODEL_ERROR)
            self.assertEqual(second.termination_reason, TerminationReason.MODEL_ERROR)
            self.assertIn("model.failed", [event.kind for event in second.events])

    async def test_stop_on_tool_error_skips_remaining_calls(self) -> None:
        turn = AssistantTurn(
            tool_calls=(
                ToolCall("missing-1", "missing", {}),
                ToolCall("calc-1", "calculator", {"expression": "2 + 2"}),
            )
        )
        agent = Agent(
            model=ScriptedModel([turn]),
            tools=ToolRegistry([CalculatorTool()]),
            config=AgentConfig(stop_on_tool_error=True),
        )

        result = await agent.run("start", run_id="stop-on-error")

        self.assertEqual(result.termination_reason, TerminationReason.TOOL_ERROR)
        self.assertEqual(result.tool_calls, 1)
        requested = [event for event in result.events if event.kind == "tool.requested"]
        self.assertEqual(len(requested), 1)

    async def test_model_timeout_has_explicit_reason(self) -> None:
        class SlowModel:
            async def complete(self, request: object) -> AssistantTurn:
                await asyncio.sleep(0.05)
                return AssistantTurn(content="too late")

        agent = Agent(
            model=SlowModel(),
            config=AgentConfig(model_timeout_seconds=0.001),
        )

        result = await agent.run("start", run_id="model-timeout")

        self.assertEqual(result.termination_reason, TerminationReason.MODEL_TIMEOUT)
        self.assertIn("model.timeout", [event.kind for event in result.events])
        self.assertEqual(result.answer, "")

    async def test_tool_timeout_can_stop_the_run(self) -> None:
        class SlowTool:
            name = "slow"
            description = "A deliberately slow test tool."
            input_schema = {"type": "object", "properties": {}}

            async def execute(self, arguments: object) -> ToolResult:
                await asyncio.sleep(0.05)
                return ToolResult(content="too late")

        agent = Agent(
            model=ScriptedModel([
                AssistantTurn(tool_calls=(ToolCall("slow-1", "slow", {}),)),
            ]),
            tools=ToolRegistry([SlowTool()]),
            config=AgentConfig(tool_timeout_seconds=0.001, stop_on_tool_error=True),
        )

        result = await agent.run("start", run_id="tool-timeout")

        self.assertEqual(result.termination_reason, TerminationReason.TOOL_TIMEOUT)
        self.assertIn("tool.timeout", [event.kind for event in result.events])
        self.assertTrue(result.messages[-1].is_error)

    async def test_cancellation_propagates(self) -> None:
        class SlowModel:
            async def complete(self, request: object) -> AssistantTurn:
                await asyncio.sleep(10)
                return AssistantTurn(content="never")

        task = asyncio.create_task(Agent(model=SlowModel()).run("cancel", run_id="cancel"))
        await asyncio.sleep(0.001)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_allowlist_denies_tool_before_execution(self) -> None:
        model = ScriptedModel([
            AssistantTurn(tool_calls=(ToolCall("calc-1", "calculator", {"expression": "2 + 2"}),)),
            AssistantTurn(content="recovered"),
        ])
        agent = Agent(
            model=model,
            tools=ToolRegistry([CalculatorTool()]),
            tool_policy=AllowlistPolicy(set()),
        )

        result = await agent.run("start", run_id="policy-deny")

        self.assertEqual(result.termination_reason, TerminationReason.COMPLETED)
        self.assertIn("tool.denied", [event.kind for event in result.events])
        self.assertTrue(model.requests[1].messages[-1].is_error)
        self.assertIn("tool denied", model.requests[1].messages[-1].content)

    async def test_approval_handler_can_allow_tool(self) -> None:
        model = ScriptedModel([
            AssistantTurn(tool_calls=(ToolCall("calc-1", "calculator", {"expression": "2 + 2"}),)),
            AssistantTurn(content="4"),
        ])
        approvals: list[str] = []

        async def approve(call: ToolCall, tool: object) -> bool:
            approvals.append(call.name)
            return True

        agent = Agent(
            model=model,
            tools=ToolRegistry([CalculatorTool()]),
            tool_policy=ApprovalPolicy({"calculator"}),
            approval_handler=approve,
        )

        result = await agent.run("start", run_id="policy-approve")

        self.assertEqual(result.answer, "4")
        self.assertEqual(approvals, ["calculator"])
        self.assertIn("tool.approved", [event.kind for event in result.events])

    async def test_required_approval_without_handler_denies(self) -> None:
        agent = Agent(
            model=ScriptedModel([
                AssistantTurn(tool_calls=(ToolCall("calc-1", "calculator", {"expression": "2 + 2"}),)),
            ]),
            tools=ToolRegistry([CalculatorTool()]),
            tool_policy=ApprovalPolicy({"calculator"}),
            config=AgentConfig(stop_on_tool_error=True),
        )

        result = await agent.run("start", run_id="policy-no-handler")

        self.assertEqual(result.termination_reason, TerminationReason.TOOL_DENIED)
        self.assertIn("tool.approval_requested", [event.kind for event in result.events])

    async def test_workspace_change_is_recorded_after_tool_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceFileStore(directory)
            model = ScriptedModel([
                AssistantTurn(tool_calls=(ToolCall(
                    "write-1",
                    "write_file",
                    {"path": "result.txt", "content": "created by agent\n"},
                ),)),
                AssistantTurn(content="done"),
            ])
            agent = Agent(
                model=model,
                tools=ToolRegistry([WriteFileTool(store)]),
                workspace=store,
            )

            result = await agent.run("write a result", run_id="workspace-change")

            event = next(event for event in result.events if event.kind == "workspace.changed")
            self.assertEqual(event.payload["changed_files"], 1)
            self.assertEqual(event.payload["changes"][0]["path"], "result.txt")
            self.assertEqual(event.payload["changes"][0]["status"], "added")
            self.assertIn("+created by agent", event.payload["changes"][0]["unified_diff"])
            self.assertLess(
                [item.kind for item in result.events].index("workspace.changed"),
                [item.kind for item in result.events].index("run.completed"),
            )

    async def test_unchanged_workspace_does_not_emit_change_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkspaceFileStore(directory)
            agent = Agent(
                model=ScriptedModel([AssistantTurn(content="done")]),
                workspace=store,
            )

            result = await agent.run("inspect", run_id="workspace-unchanged")

            self.assertNotIn("workspace.changed", [event.kind for event in result.events])

    async def test_workspace_snapshot_failure_does_not_break_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            agent = Agent(
                model=ScriptedModel([AssistantTurn(content="done")]),
                workspace=WorkspaceFileStore(root),
                workspace_max_files=1,
            )

            result = await agent.run("inspect", run_id="workspace-snapshot-failure")

            self.assertEqual(result.termination_reason, TerminationReason.COMPLETED)
            failures = [event for event in result.events if event.kind == "workspace.snapshot_failed"]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].payload["phase"], "before")


if __name__ == "__main__":
    unittest.main()
