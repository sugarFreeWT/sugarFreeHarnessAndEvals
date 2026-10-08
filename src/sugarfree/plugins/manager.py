from __future__ import annotations

import inspect
from collections.abc import Awaitable, Iterable
from typing import Any, Protocol

from sugarfree.tools.base import Tool
from sugarfree.tools.registry import ToolRegistry


class PluginError(RuntimeError):
    """Raised when a plugin cannot be composed or its lifecycle fails."""


class Plugin(Protocol):
    name: str
    requires: tuple[str, ...]

    def setup(self, context: "PluginContext") -> Awaitable[None] | None: ...

    def teardown(self, context: "PluginContext") -> Awaitable[None] | None: ...


class PluginContext:
    def __init__(self) -> None:
        self.tools = ToolRegistry()
        self._services: dict[str, Any] = {}
        self._owners: dict[str, str] = {}
        self._owned_tools: dict[str, set[str]] = {}
        self._active_plugin: str | None = None

    def provide(self, name: str, service: Any) -> None:
        if name in self._services:
            raise PluginError(f"service already provided: {name}")
        self._services[name] = service
        if self._active_plugin:
            self._owners[f"service:{name}"] = self._active_plugin

    def require(self, name: str) -> Any:
        try:
            return self._services[name]
        except KeyError as exc:
            raise PluginError(f"required service is unavailable: {name}") from exc

    def register_tool(self, tool: Tool) -> None:
        self.tools.register(tool)
        if self._active_plugin:
            self._owned_tools.setdefault(self._active_plugin, set()).add(tool.name)

    def create_agent(self, *, config: Any = None, event_sink: Any = None) -> Any:
        from sugarfree.core.agent import Agent

        return Agent(
            model=self.require("model"),
            tools=self.tools,
            config=config,
            event_sink=event_sink,
        )

    def _begin(self, plugin_name: str) -> None:
        self._active_plugin = plugin_name

    def _end(self) -> None:
        self._active_plugin = None

    def _cleanup(self, plugin_name: str) -> None:
        for tool_name in self._owned_tools.pop(plugin_name, set()):
            self.tools.unregister(tool_name)
        for key, owner in tuple(self._owners.items()):
            if owner != plugin_name:
                continue
            self._services.pop(key.removeprefix("service:"), None)
            del self._owners[key]


class PluginManager:
    def __init__(self, plugins: Iterable[Plugin] = ()) -> None:
        self.context = PluginContext()
        plugin_list = list(plugins)
        self._plugins = {plugin.name: plugin for plugin in plugin_list}
        if len(self._plugins) != len(plugin_list):
            raise PluginError("plugin names must be unique")
        self._loaded: list[str] = []

    @property
    def loaded_plugins(self) -> tuple[str, ...]:
        return tuple(self._loaded)

    async def load(self) -> PluginContext:
        if self._loaded:
            raise PluginError("plugin manager is already loaded")
        order = self._topological_order()
        try:
            for name in order:
                plugin = self._plugins[name]
                self.context._begin(name)
                try:
                    outcome = plugin.setup(self.context)
                    if inspect.isawaitable(outcome):
                        await outcome
                except Exception as exc:
                    self.context._cleanup(name)
                    raise PluginError(f"plugin setup failed: {name}: {exc}") from exc
                finally:
                    self.context._end()
                self._loaded.append(name)
        except Exception:
            await self.close()
            raise
        return self.context

    async def close(self) -> None:
        for name in reversed(self._loaded):
            plugin = self._plugins[name]
            self.context._begin(name)
            try:
                outcome = plugin.teardown(self.context)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception as exc:
                raise PluginError(f"plugin teardown failed: {name}: {exc}") from exc
            finally:
                self.context._end()
                self.context._cleanup(name)
        self._loaded.clear()

    def _topological_order(self) -> list[str]:
        state: dict[str, int] = {}
        order: list[str] = []

        def visit(name: str) -> None:
            if state.get(name) == 1:
                raise PluginError(f"plugin dependency cycle includes: {name}")
            if state.get(name) == 2:
                return
            if name not in self._plugins:
                raise PluginError(f"plugin dependency is missing: {name}")
            state[name] = 1
            for dependency in self._plugins[name].requires:
                visit(dependency)
            state[name] = 2
            order.append(name)

        for name in self._plugins:
            visit(name)
        return order
