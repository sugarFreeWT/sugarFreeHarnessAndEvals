from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Mapping, Protocol


class EventSink(Protocol):
    def emit(self, event: "AgentEvent") -> Awaitable[None] | None: ...


@dataclass(frozen=True, slots=True)
class AgentEvent:
    run_id: str
    sequence: int
    kind: str
    timestamp: str
    payload: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        kind: str,
        payload: Mapping[str, Any] | None = None,
    ) -> "AgentEvent":
        return cls(
            run_id=run_id,
            sequence=sequence,
            kind=kind,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }

