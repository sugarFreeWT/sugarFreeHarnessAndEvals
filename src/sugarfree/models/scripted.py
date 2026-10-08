from __future__ import annotations

from collections.abc import Iterable

from sugarfree.core.messages import AssistantTurn, ModelRequest
from sugarfree.models.base import ModelError


class ScriptedModel:
    """Returns predefined turns in order for deterministic tests and evals."""

    def __init__(self, turns: Iterable[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.requests: list[ModelRequest] = []
        self._index = 0

    async def complete(self, request: ModelRequest) -> AssistantTurn:
        self.requests.append(request)
        if self._index >= len(self._turns):
            raise ModelError("scripted model has no response left")
        turn = self._turns[self._index]
        self._index += 1
        return turn

