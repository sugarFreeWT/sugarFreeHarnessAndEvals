from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)(\s*(?:=|:)\s*)([^\s,;]+)"
)
_MASKED_API_KEY = re.compile(
    r"(?i)(\bapi\s+key\s*(?:=|:)\s*)\*+[A-Za-z0-9_-]{2,}"
)


def redact_sensitive_text(value: str) -> str:
    """Remove common credentials and provider-rendered API key identifiers."""

    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda match: "Bearer " + REDACTED
            if match.group(0).lower().startswith("bearer ")
            else REDACTED,
            redacted,
        )
    redacted = _ASSIGNED_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        redacted,
    )
    return _MASKED_API_KEY.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        redacted,
    )
