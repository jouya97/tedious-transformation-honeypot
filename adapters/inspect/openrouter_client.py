"""OpenRouter's native Anthropic Messages protocol, without SDK normalization.

Keeping this protocol avoids a lossy conversion of signed thinking to Chat
Completions reasoning_details. This adapter deliberately supports Claude models
only; other model families need a separately specified reasoning/history policy.

The default Opus 5.5/xhigh selection was confirmed in the public model catalog.
Alternative Claude IDs require catalog-confirmed effort support; this validation
does not prove support for adaptive summarized thinking. Native API rejections are
logged and end the episode rather than dropping thinking or reducing the effort.
Select other effort/provider values explicitly through the task parameters.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from typing import Any

import anyio
import httpx2

from harness_agent import ProviderCallError, ProviderReply

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-opus-5.5"
# Confirmed in the public catalog on 2026-10-02. The pinned reproduction avoids
# depending on catalog availability; only alternative model IDs need discovery.
DEFAULT_EFFORTS = frozenset(("max", "xhigh", "high", "medium", "low"))


class OpenRouterClient:
    """No retries, provider fallbacks, model substitution, or history compaction.

    call_trace is updated before each await, so deadline cancellation can retain
    whatever transport evidence was available. Headers containing credentials
    are never included in that trace. An injected client is owned by its caller.
    """

    def __init__(self, api_key: str | None = None, http_client: Any = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required for backend=openrouter")
        self.http_client = http_client
        self.model_info: dict[str, dict] = {}
        self.call_trace: dict = {}

    async def __call__(self, payload: dict, timeout: float) -> ProviderReply:
        self.call_trace = {"backend": "openrouter", "protocol": "anthropic_messages",
                           "http_exchanges": []}
        if self.http_client is not None:
            return await self._send(self.http_client, payload, timeout)
        async with httpx2.AsyncClient() as http:
            return await self._send(http, payload, timeout)

    async def _exchange(self, http: Any, method: str, path: str, timeout: float,
                        payload: dict | None = None) -> tuple[dict, str | None]:
        headers = {"Content-Type": "application/json", "X-OpenRouter-Metadata": "enabled"}
        # ModelCall already retains the exact JSON request. Transport evidence
        # stores its wire digest instead of duplicating every growing history.
        wire_body = (json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                separators=(",", ":")).encode("utf-8") if payload is not None else None)
        exchange = {"method": method, "url": BASE_URL + path,
                    "request_headers": dict(headers), "request_model": (payload or {}).get("model"),
                    "request_body_bytes": len(wire_body) if wire_body is not None else 0,
                    "request_body_sha256": hashlib.sha256(wire_body).hexdigest() if wire_body is not None else None}
        self.call_trace["http_exchanges"].append(exchange)
        started = time.monotonic()
        try:
            response = await http.request(
                method, BASE_URL + path, content=wire_body, timeout=timeout,
                headers={**headers, "Authorization": f"Bearer {self.api_key}"})
        except Exception as exc:
            # HTTP libraries may put request objects on exceptions. Do not log
            # those objects (or arbitrary reprs that could contain auth headers).
            exchange["transport_error"] = {"type": type(exc).__name__,
                                            "message": str(exc).replace(self.api_key, "[REDACTED]")}
            raise
        finally:
            exchange["elapsed_seconds"] = time.monotonic() - started
        exchange.update({"status_code": response.status_code,
                         "response_headers": {key: value.replace(self.api_key, "[REDACTED]")
                                              for key, value in response.headers.items()
                                              if key.lower() not in ("authorization", "x-api-key", "set-cookie")},
                         "response_text": response.text.replace(self.api_key, "[REDACTED]")})
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        if request_id is not None:
            request_id = request_id.replace(self.api_key, "[REDACTED]")
        self.call_trace["request_id"] = request_id
        try:
            body = json.loads(exchange["response_text"])
        except (ValueError, UnicodeError) as exc:
            raise ProviderCallError("OpenRouter returned invalid JSON", self.call_trace) from exc
        if not isinstance(body, dict):
            raise ProviderCallError("OpenRouter returned a non-object response", self.call_trace)
        if response.status_code >= 400 or "error" in body:
            raise ProviderCallError(f"OpenRouter API error (HTTP {response.status_code})",
                                    self.call_trace, body)
        return body, request_id

    async def _send(self, http: Any, payload: dict, timeout: float) -> ProviderReply:
        try:
            # The aggregate timeout includes catalog validation and generation;
            # run_episode also enforces the unchanged episode wall-clock limit.
            with anyio.fail_after(timeout):
                model = payload.get("model")
                if not isinstance(model, str) or not model.startswith("anthropic/claude-"):
                    raise ValueError("OpenRouter native-history adapter requires an anthropic/claude-* model ID")
                effort = payload["output_config"]["effort"]
                if model == DEFAULT_MODEL:
                    supported = DEFAULT_EFFORTS
                elif model not in self.model_info:
                    catalog, _ = await self._exchange(http, "GET", "/models", timeout)
                    entries = catalog.get("data")
                    if not isinstance(entries, list):
                        raise ValueError("OpenRouter model catalog is malformed")
                    self.model_info = {entry["id"]: entry for entry in entries
                                       if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
                if model != DEFAULT_MODEL:
                    info = self.model_info.get(model)
                    if info is None:
                        raise ValueError(f"OpenRouter model is absent from the current catalog: {model}")
                    self.call_trace["model_info"] = copy.deepcopy(info)
                    reasoning = info.get("reasoning") or {}
                    if "supported_efforts" not in reasoning:
                        raise ValueError(f"OpenRouter catalog does not expose effort selection for {model}")
                    # An explicit null means all gateway efforts are accepted.
                    supported = reasoning["supported_efforts"]
                    if supported is None:
                        supported = ("max", "xhigh", "high", "medium", "low", "minimal", "none")
                if effort not in supported:
                    raise ValueError(f"OpenRouter does not confirm effort {effort!r} for {model}; "
                                     "choose an explicitly supported effort/model")
                body, request_id = await self._exchange(http, "POST", "/messages", timeout, payload)
                return ProviderReply(body, request_id, copy.deepcopy(self.call_trace))
        except ProviderCallError:
            raise
        except Exception as exc:
            self.call_trace["timed_out"] = isinstance(exc, (TimeoutError, httpx2.TimeoutException))
            self.call_trace["error_type"] = type(exc).__name__
            raise ProviderCallError(str(exc).replace(self.api_key, "[REDACTED]") or type(exc).__name__,
                                    copy.deepcopy(self.call_trace)) from exc


def openrouter_client(max_retries: int = 0, http_client: Any = None) -> OpenRouterClient:
    if max_retries != 0:
        raise ValueError("OpenRouter reproduction requires max_retries=0; retries are not supported")
    return OpenRouterClient(http_client=http_client)
