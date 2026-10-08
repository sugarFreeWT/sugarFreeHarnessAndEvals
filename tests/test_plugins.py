from __future__ import annotations

import unittest

from sugarfree.core.messages import AssistantTurn
from sugarfree.core.result import TerminationReason
from sugarfree.models.scripted import ScriptedModel
from sugarfree.plugins import PluginError, PluginManager
from sugarfree.tools.calculator import CalculatorTool


class ModelPlugin:
    name = "model"
    requires = ()

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def setup(self, context: object) -> None:
        self.events.append("model.setup")
        context.provide("model", ScriptedModel([AssistantTurn(content="plugin answer")]))

    def teardown(self, context: object) -> None:
        self.events.append("model.teardown")


class ToolPlugin:
    name = "calculator"
    requires = ("model",)

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def setup(self, context: object) -> None:
        self.events.append("tool.setup")
        context.register_tool(CalculatorTool())

    def teardown(self, context: object) -> None:
        self.events.append("tool.teardown")


class PluginManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependencies_load_in_order_and_cleanup(self) -> None:
        events: list[str] = []
        manager = PluginManager([ToolPlugin(events), ModelPlugin(events)])

        context = await manager.load()
        self.assertEqual(manager.loaded_plugins, ("model", "calculator"))
        self.assertEqual(events, ["model.setup", "tool.setup"])
        self.assertEqual(context.require("model").requests, [])
        self.assertEqual(context.tools.specs()[0].name, "calculator")
        result = await context.create_agent().run("hello", run_id="plugin")
        self.assertEqual(result.answer, "plugin answer")
        self.assertEqual(result.termination_reason, TerminationReason.COMPLETED)

        await manager.close()
        self.assertEqual(events, ["model.setup", "tool.setup", "tool.teardown", "model.teardown"])
        with self.assertRaises(PluginError):
            context.require("model")
        self.assertEqual(context.tools.specs(), ())

    async def test_missing_dependency_is_rejected(self) -> None:
        manager = PluginManager([ToolPlugin([])])

        with self.assertRaisesRegex(PluginError, "dependency is missing"):
            await manager.load()

    async def test_cycle_is_rejected(self) -> None:
        class A:
            name = "a"
            requires = ("b",)
            setup = lambda self, context: None
            teardown = lambda self, context: None

        class B:
            name = "b"
            requires = ("a",)
            setup = lambda self, context: None
            teardown = lambda self, context: None

        with self.assertRaisesRegex(PluginError, "cycle"):
            await PluginManager([A(), B()]).load()


if __name__ == "__main__":
    unittest.main()
