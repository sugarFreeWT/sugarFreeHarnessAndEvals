from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sugarfree.core.events import AgentEvent, EventSink
from sugarfree.core.messages import Message, ModelRequest, Usage
from sugarfree.core.result import AgentResult, TerminationReason
from sugarfree.fs.changes import WorkspaceSnapshot
from sugarfree.fs.workspace import WorkspaceFileStore
from sugarfree.models.base import Model
from sugarfree.security.policy import (
    ApprovalHandler,
    PolicyDecisionKind,
    ToolPolicy,
    resolve_approval,
)
from sugarfree.tools.base import ToolExecutionError, ToolResult
from sugarfree.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class AgentConfig:
    max_steps: int = 8
    stop_on_tool_error: bool = False
    system_prompt: str | None = None
    model_timeout_seconds: float | None = None
    tool_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        for name, value in (
            ("model_timeout_seconds", self.model_timeout_seconds),
            ("tool_timeout_seconds", self.tool_timeout_seconds),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero")


class Agent:
    def __init__(
        self,
        *,
        model: Model,
        tools: ToolRegistry | None = None,
        config: AgentConfig | None = None,
        event_sink: EventSink | None = None,
        tool_policy: ToolPolicy | None = None,
        approval_handler: ApprovalHandler | None = None,
        workspace: WorkspaceFileStore | None = None,
        workspace_max_files: int = 1000,
        workspace_max_total_bytes: int = 8 * 1024 * 1024,
        workspace_max_diff_lines: int = 200,
    ) -> None:
        self.model = model
        self.tools = tools or ToolRegistry()
        self.config = config or AgentConfig()
        self.event_sink = event_sink
        self.tool_policy = tool_policy
        self.approval_handler = approval_handler
        self.workspace = workspace
        self.workspace_max_files = workspace_max_files
        self.workspace_max_total_bytes = workspace_max_total_bytes
        self.workspace_max_diff_lines = workspace_max_diff_lines

    async def run(self, prompt: str, *, run_id: str | None = None) -> AgentResult:
        current_run_id = run_id or uuid.uuid4().hex
        started = time.perf_counter()
        events: list[AgentEvent] = []
        messages: list[Message] = []
        if self.config.system_prompt:
            messages.append(Message(role="system", content=self.config.system_prompt))
        messages.append(Message(role="user", content=prompt))

        steps = 0
        tool_calls = 0
        input_tokens = 0
        output_tokens = 0
        answer = ""
        error: str | None = None

        async def emit(kind: str, payload: dict[str, Any] | None = None) -> None:
            event = AgentEvent.create(
                run_id=current_run_id,
                sequence=len(events) + 1,
                kind=kind,
                payload=payload,
            )
            events.append(event)
            if self.event_sink is None:
                return
            try:
                outcome = self.event_sink.emit(event)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception as exc:  # Observability must not break the agent.
                events.append(
                    AgentEvent.create(
                        run_id=current_run_id,
                        sequence=len(events) + 1,
                        kind="observer.failed",
                        payload={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )

        await emit("run.started", {"prompt": prompt, "max_steps": self.config.max_steps})
        workspace_before: WorkspaceSnapshot | None = None
        if self.workspace is not None:
            try:
                workspace_before = WorkspaceSnapshot.capture(
                    self.workspace,
                    max_files=self.workspace_max_files,
                    max_total_bytes=self.workspace_max_total_bytes,
                )
            except Exception as exc:
                await emit(
                    "workspace.snapshot_failed",
                    {"phase": "before", "error": f"{type(exc).__name__}: {exc}"},
                )
        termination = TerminationReason.MAX_STEPS

        try:
            for step in range(1, self.config.max_steps + 1):
                steps = step
                request = ModelRequest(messages=tuple(messages), tools=self.tools.specs())
                await emit("model.requested", {"step": step, "request": request.to_dict()})
                try:
                    turn = await _with_timeout(
                        self.model.complete(request),
                        self.config.model_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    termination = TerminationReason.CANCELLED
                    error = "agent run was cancelled"
                    raise
                except asyncio.TimeoutError:
                    termination = TerminationReason.MODEL_TIMEOUT
                    error = f"model timed out after {self.config.model_timeout_seconds}s"
                    await emit(
                        "model.timeout",
                        {"step": step, "timeout_seconds": self.config.model_timeout_seconds},
                    )
                    break
                except Exception as exc:
                    termination = TerminationReason.MODEL_ERROR
                    error = f"{type(exc).__name__}: {exc}"
                    await emit("model.failed", {"step": step, "error": error})
                    break

                input_tokens += turn.usage.input_tokens
                output_tokens += turn.usage.output_tokens
                await emit("model.responded", {"step": step, "response": turn.to_dict()})
                messages.append(
                    Message(
                        role="assistant",
                        content=turn.content,
                        tool_calls=turn.tool_calls,
                    )
                )

                if not turn.tool_calls:
                    answer = turn.content
                    termination = TerminationReason.COMPLETED
                    break

                stop_after_error = False
                for call in turn.tool_calls:
                    tool_calls += 1
                    await emit("tool.requested", {"step": step, "call": call.to_dict()})
                    try:
                        tool = self.tools.get(call.name)
                        self.tools.validate(tool, call.arguments)
                        if self.tool_policy is not None:
                            decision = await _with_timeout(
                                self.tool_policy.check(call, tool),
                                self.config.tool_timeout_seconds,
                            )
                            await emit(
                                "tool.policy_checked",
                                {
                                    "step": step,
                                    "call": call.to_dict(),
                                    "decision": decision.kind.value,
                                    "reason": decision.reason,
                                },
                            )
                            if decision.kind is PolicyDecisionKind.APPROVAL_REQUIRED:
                                await emit(
                                    "tool.approval_requested",
                                    {"step": step, "call": call.to_dict(), "reason": decision.reason},
                                )
                                approved = await _with_timeout(
                                    resolve_approval(self.approval_handler, call, tool),
                                    self.config.tool_timeout_seconds,
                                )
                                if not approved:
                                    decision = type(decision).deny("approval was unavailable or rejected")
                                else:
                                    await emit(
                                        "tool.approved",
                                        {"step": step, "call": call.to_dict()},
                                    )
                            if decision.kind is PolicyDecisionKind.DENY:
                                result = ToolResult(
                                    content=f"tool denied: {decision.reason}",
                                    is_error=True,
                                )
                                await emit(
                                    "tool.denied",
                                    {
                                        "step": step,
                                        "call": call.to_dict(),
                                        "reason": decision.reason,
                                    },
                                )
                                if self.config.stop_on_tool_error:
                                    termination = TerminationReason.TOOL_DENIED
                                    error = result.content
                                    stop_after_error = True
                                raise _PolicyDenied(result)
                        await emit("tool.started", {"step": step, "call": call.to_dict()})
                        outcome = tool.execute(call.arguments)
                        result = await _with_timeout(
                            outcome,
                            self.config.tool_timeout_seconds,
                        )
                        if not isinstance(result, ToolResult):
                            raise TypeError("tool must return ToolResult")
                        await emit(
                            "tool.completed",
                            {"step": step, "call": call.to_dict(), "content": result.content},
                        )
                    except _PolicyDenied as exc:
                        result = exc.result
                    except asyncio.TimeoutError:
                        result = ToolResult(
                            content=f"tool timed out after {self.config.tool_timeout_seconds}s",
                            is_error=True,
                        )
                        await emit(
                            "tool.timeout",
                            {
                                "step": step,
                                "call": call.to_dict(),
                                "timeout_seconds": self.config.tool_timeout_seconds,
                            },
                        )
                        if self.config.stop_on_tool_error:
                            termination = TerminationReason.TOOL_TIMEOUT
                            error = result.content
                            stop_after_error = True
                    except Exception as exc:
                        safe_error = exc if isinstance(exc, ToolExecutionError) else ToolExecutionError(
                            f"{type(exc).__name__}: {exc}"
                        )
                        result = ToolResult(content=str(safe_error), is_error=True)
                        await emit(
                            "tool.failed",
                            {"step": step, "call": call.to_dict(), "error": result.content},
                        )
                        if self.config.stop_on_tool_error:
                            termination = TerminationReason.TOOL_ERROR
                            error = result.content
                            stop_after_error = True

                    messages.append(
                        Message(
                            role="tool",
                            content=result.content,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            is_error=result.is_error,
                        )
                    )
                    if stop_after_error:
                        break
                if stop_after_error:
                    break
        except asyncio.CancelledError:
            await emit("run.failed", {"reason": termination.value, "error": error})
            raise

        elapsed = time.perf_counter() - started
        if self.workspace is not None and workspace_before is not None:
            try:
                workspace_after = WorkspaceSnapshot.capture(
                    self.workspace,
                    max_files=self.workspace_max_files,
                    max_total_bytes=self.workspace_max_total_bytes,
                )
                changes = workspace_before.diff(
                    workspace_after,
                    max_diff_lines=self.workspace_max_diff_lines,
                )
                if changes:
                    await emit(
                        "workspace.changed",
                        {
                            "changed_files": len(changes),
                            "before_files": len(workspace_before.files),
                            "after_files": len(workspace_after.files),
                            "changes": changes,
                        },
                    )
            except Exception as exc:
                await emit(
                    "workspace.snapshot_failed",
                    {"phase": "after", "error": f"{type(exc).__name__}: {exc}"},
                )
        usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
        final_kind = "run.completed" if termination is TerminationReason.COMPLETED else "run.failed"
        await emit(
            final_kind,
            {
                "reason": termination.value,
                "steps": steps,
                "tool_calls": tool_calls,
                "usage": usage.to_dict(),
                "error": error,
            },
        )
        return AgentResult(
            run_id=current_run_id,
            answer=answer,
            termination_reason=termination,
            messages=tuple(messages),
            events=tuple(events),
            steps=steps,
            tool_calls=tool_calls,
            usage=usage,
            elapsed_seconds=elapsed,
            error=error,
        )


async def _with_timeout(awaitable: Any, timeout_seconds: float | None) -> Any:
    if not inspect.isawaitable(awaitable):
        return awaitable
    if timeout_seconds is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


class _PolicyDenied(Exception):
    def __init__(self, result: ToolResult) -> None:
        self.result = result
