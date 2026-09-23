"""Typed HTTP client for the local NeMo Agent Toolkit API."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin
import uuid

import httpx


@dataclass(frozen=True)
class AgentReply:
    content: str
    status_code: int
    duration_ms: int
    request_id: str = ""


class AgentClientError(RuntimeError):
    def __init__(self, status_code: int, detail: str, duration_ms: int = 0, request_id: str = "", category: str = "upstream_error", validation_fields: tuple = ()) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.duration_ms = duration_ms
        self.request_id = request_id
        self.category = category
        self.validation_fields = validation_fields


def safe_validation_fields(details: Any) -> tuple:
    if not isinstance(details, list):
        return ()
    allowed_fields = {'body', 'query', 'path', 'header', 'messages', 'model', 'role', 'content', 'type', 'text', 'input_message', 'stream'}
    allowed_types = {'missing', 'string_type', 'list_type', 'dict_type', 'model_type', 'literal_error', 'extra_forbidden', 'json_invalid', 'value_error'}
    result = []
    for item in details[:10]:
        if not isinstance(item, dict) or not isinstance(item.get('loc'), list):
            continue
        location = [part if type(part) is int and 0 <= part < 10000 else part if isinstance(part, str) and part in allowed_fields else '<field>' for part in item['loc'][:8]]
        error_type = item.get('type')
        result.append({'location': location, 'type': error_type if isinstance(error_type, str) and error_type in allowed_types else 'validation_error'})
    return tuple(result)


class AgentClient:
    def __init__(self, chat_url: str) -> None:
        self.chat_url = chat_url
        self.health_url = urljoin(chat_url, "/health")

    async def health(self) -> dict[str, Any]:
        started_at = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(self.health_url)
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            return {
                "status": "online" if response.is_success else "degraded",
                "httpStatus": response.status_code,
                "durationMs": duration_ms,
            }
        except httpx.RequestError as error:
            return {
                "status": "offline",
                "httpStatus": None,
                "durationMs": round((time.perf_counter() - started_at) * 1000),
                "error": str(error),
            }

    async def chat(self, access_token: str, prompt: str, *, read_only: bool = False) -> AgentReply:
        started_at = time.perf_counter()
        request_id = uuid.uuid4().hex
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    self.chat_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "X-Request-ID": request_id,
                        **({"X-LearningNeMo-Read-Only": "true"} if read_only else {}),
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
        except httpx.RequestError as error:
            timed_out = isinstance(error, httpx.TimeoutException)
            raise AgentClientError(504 if timed_out else 502, "Agent connection failed; completion is unknown",
                round((time.perf_counter() - started_at) * 1000), request_id,
                "transport_timeout" if timed_out else "transport_error") from None
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        if not response.is_success:
            try:
                document = response.json()
            except ValueError:
                document = {}
            validation = document.get("detail") if isinstance(document, dict) else None
            category = "request_validation" if response.status_code == 422 and isinstance(validation, list) else "workflow_rejected" if response.status_code == 422 else "upstream_error"
            raise AgentClientError(response.status_code, "The agent did not complete the request", duration_ms, request_id, category, safe_validation_fields(validation))
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise AgentClientError(502, "The agent returned an unexpected response", duration_ms, request_id, "invalid_response") from None
        if not isinstance(content, str):
            raise AgentClientError(502, "The agent response did not contain text", duration_ms, request_id, "invalid_response")
        return AgentReply(
            content=content,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )