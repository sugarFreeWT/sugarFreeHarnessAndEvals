from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sugarfree.core.messages import AssistantTurn, ModelRequest, ToolCall, Usage
from sugarfree.models.base import ModelError
from sugarfree.security.redaction import redact_sensitive_text


@dataclass(slots=True)
class OpenAICompatibleModel:
    api_key: str
    model: str
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: float = 60.0
    temperature: float = 0.0

    async def complete(self, request: ModelRequest) -> AssistantTurn:
        return await asyncio.to_thread(self._complete_sync, request)

    def _complete_sync(self, request: ModelRequest) -> AssistantTurn:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message(message) for message in request.messages],
            "temperature": self.temperature,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        http_request = Request(
            url,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelError(
                f"model HTTP {exc.code}: {redact_sensitive_text(detail)}"
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelError(f"model request failed: {exc}") from exc

        try:
            message = body["choices"][0]["message"]
            calls = tuple(self._tool_call(item) for item in message.get("tool_calls", []))
            raw_usage = body.get("usage", {})
            usage = Usage(
                input_tokens=int(raw_usage.get("prompt_tokens", 0)),
                output_tokens=int(raw_usage.get("completion_tokens", 0)),
            )
            return AssistantTurn(
                content=message.get("content") or "",
                tool_calls=calls,
                usage=usage,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError("model returned an invalid chat completion response") from exc

    @staticmethod
    def _message(message: Any) -> dict[str, Any]:
        value: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            value["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(dict(call.arguments)),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            value["tool_call_id"] = message.tool_call_id
        if message.tool_name:
            value["name"] = message.tool_name
        return value

    @staticmethod
    def _tool_call(value: dict[str, Any]) -> ToolCall:
        function = value["function"]
        raw_arguments = function.get("arguments") or "{}"
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        return ToolCall(
            id=str(value["id"]),
            name=str(function["name"]),
            arguments=arguments,
        )
