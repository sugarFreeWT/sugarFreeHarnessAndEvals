from __future__ import annotations

from typing import Protocol

from sugarfree.core.messages import AssistantTurn, ModelRequest


class ModelError(RuntimeError):
    """A provider-neutral model invocation failure."""


class Model(Protocol):
    async def complete(self, request: ModelRequest) -> AssistantTurn: ...

