from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol

from sugarfree.core.messages import ToolCall
from sugarfree.tools.base import Tool


class PolicyDecisionKind(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    kind: PolicyDecisionKind
    reason: str

    @classmethod
    def allow(cls, reason: str = "allowed") -> "PolicyDecision":
        return cls(PolicyDecisionKind.ALLOW, reason)

    @classmethod
    def deny(cls, reason: str) -> "PolicyDecision":
        return cls(PolicyDecisionKind.DENY, reason)

    @classmethod
    def approval_required(cls, reason: str = "approval required") -> "PolicyDecision":
        return cls(PolicyDecisionKind.APPROVAL_REQUIRED, reason)


class ToolPolicy(Protocol):
    def check(
        self,
        call: ToolCall,
        tool: Tool,
    ) -> Awaitable[PolicyDecision] | PolicyDecision: ...


class AllowlistPolicy:
    def __init__(self, allowed_tools: set[str] | frozenset[str]) -> None:
        self.allowed_tools = frozenset(allowed_tools)

    def check(self, call: ToolCall, tool: Tool) -> PolicyDecision:
        if call.name in self.allowed_tools:
            return PolicyDecision.allow("tool is in allowlist")
        return PolicyDecision.deny(f"tool is not in allowlist: {call.name}")


class ApprovalPolicy:
    def __init__(self, tools_requiring_approval: set[str] | frozenset[str]) -> None:
        self.tools_requiring_approval = frozenset(tools_requiring_approval)

    def check(self, call: ToolCall, tool: Tool) -> PolicyDecision:
        if call.name in self.tools_requiring_approval:
            return PolicyDecision.approval_required(f"approval required for tool: {call.name}")
        return PolicyDecision.allow("tool does not require approval")


ApprovalHandler = Callable[[ToolCall, Tool], Awaitable[bool] | bool]


async def resolve_approval(
    handler: ApprovalHandler | None,
    call: ToolCall,
    tool: Tool,
) -> bool:
    if handler is None:
        return False
    result = handler(call, tool)
    return bool(await result if inspect.isawaitable(result) else result)

