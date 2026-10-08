from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sugarfree.core.events import AgentEvent
from sugarfree.core.messages import Message, Usage


class TerminationReason(StrEnum):
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    MODEL_ERROR = "model_error"
    MODEL_TIMEOUT = "model_timeout"
    TOOL_ERROR = "tool_error"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_DENIED = "tool_denied"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentResult:
    run_id: str
    answer: str
    termination_reason: TerminationReason
    messages: tuple[Message, ...]
    events: tuple[AgentEvent, ...]
    steps: int
    tool_calls: int
    usage: Usage
    elapsed_seconds: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "answer": self.answer,
            "termination_reason": self.termination_reason.value,
            "messages": [message.to_dict() for message in self.messages],
            "events": [event.to_dict() for event in self.events],
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "usage": self.usage.to_dict(),
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }
