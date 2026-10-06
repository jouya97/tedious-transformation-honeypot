"""Offline native OpenRouter transport, full-history replay, errors and Inspect logs."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters" / "inspect"))
import harness_agent as harness
import inspect_task as adapter
from openrouter_client import DEFAULT_MODEL, OpenRouterClient, openrouter_client
from test_inspect_adapter import FakeSandbox

KEY = "offline-openrouter-key"


def reply(content=None, stop="tool_use"):
    return {"id": "msg_native", "type": "message", "role": "assistant", "model": DEFAULT_MODEL,
            "content": content if content is not None else [
                {"type": "thinking", "thinking": "A visible condensed thinking summary.",
                 "signature": "opaque-signed-value", "future_field": {"preserve": True}},
                {"type": "redacted_thinking", "data": "opaque-redacted-value"},
                {"type": "tool_use", "id": "toolu_native", "name": "shell",
                 "input": {"command": "ls"}, "caller": {"type": "direct"}},
            ], "stop_reason": stop, "stop_details": None,
            "usage": {"input_tokens": 12, "output_tokens": 18, "cache_read_input_tokens": 5,
                      "output_tokens_details": {"reasoning_tokens": 9}},
            "provider": "Anthropic", "openrouter_metadata": {"requested": DEFAULT_MODEL,
                "endpoints": {"available": [{"provider": "Anthropic", "selected": True}]}},
            "input_transformations": [], "future_top_level": {"kept_in_log": True}}


def payload(model=DEFAULT_MODEL, effort="xhigh"):
    return harness.build_payload(model, [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
                                 backend="openrouter", effort=effort)


def client_for(handler):
    return OpenRouterClient(KEY, httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


class RecordingHooks(harness.Hooks):
    def __init__(self):
        self.calls = []

    def model_call(self, episode, request, response, trace, error):
        self.calls.append(copy.deepcopy((request, response, trace, error)))


def test_native_transport_keeps_exact_payload_response_and_http_evidence():
    sent = []
    body = reply()

    def handler(request):
        sent.append(request)
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["x-openrouter-metadata"] == "enabled"
        return httpx2.Response(200, json=body, headers={"x-request-id": "or_req", "x-provider": "Anthropic"})

    request = payload()
    result = asyncio.run(client_for(handler)(request, 30))
    assert len(sent) == 1  # No catalog lookup for the pinned default model.
    assert str(sent[0].url) == "https://openrouter.ai/api/v1/messages"
    assert json.loads(sent[0].content) == request
    assert request["provider"] == {"only": ["Anthropic"], "allow_fallbacks": False, "require_parameters": True}
    assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert request["output_config"] == {"effort": "xhigh"}
    assert request["tool_choice"]["disable_parallel_tool_use"] is True
    assert request["tools"] == harness.TOOLS and "system" not in request
    assert result.response == body and result.request_id == "or_req"
    exchange = result.trace["http_exchanges"][0]
    assert json.loads(exchange["response_text"]) == body
    assert exchange["request_body_sha256"] == hashlib.sha256(sent[0].content).hexdigest()
    assert exchange["request_body_bytes"] == len(sent[0].content) and exchange["status_code"] == 200
    assert exchange["response_headers"]["x-provider"] == "Anthropic"
    assert KEY not in json.dumps(result.trace)


@pytest.mark.parametrize("seconds", [None, 60])
def test_native_tool_history_and_opaque_thinking_survive_episode_replay(seconds):
    bodies = [reply(), reply([{"type": "text", "text": "Done."}], "end_turn")]
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=bodies[len(requests) - 1])

    hooks, sandbox = RecordingHooks(), FakeSandbox(saved=50)
    episode = asyncio.run(harness.run_episode("Transcribe.", sandbox, client_for(handler), DEFAULT_MODEL,
                                             seconds, hooks, backend="openrouter"))
    assert episode.backend == "openrouter" and episode.final_response == "Done."
    assert episode.stop_reason == "end_turn" and sandbox.commands == ["ls"]
    assert requests[1]["messages"][1] == {"role": "assistant", "content": bodies[0]["content"]}
    blocks = requests[1]["messages"][2]["content"]
    assert blocks[0]["type"] == "tool_result" and blocks[0]["tool_use_id"] == "toolu_native"
    observation = json.loads(blocks[0]["content"])
    assert observation["stdout"] == "ran ls"
    assert "shell_evidence" not in observation
    assert episode.shell_evidence == [{"complete": True, "events": []}]
    assert [tool["name"] for tool in requests[0]["tools"]] == ["shell"]
    assert '"saved_records": 50' in blocks[1]["text"]
    assert hooks.calls[0][1] == bodies[0]
    assert episode.calls[0]["provider"] == "Anthropic"
    assert episode.calls[0]["openrouter_metadata"] == bodies[0]["openrouter_metadata"]


@pytest.mark.parametrize("status, body", [
    (400, {"error": {"code": 400, "message": "Invalid request", "metadata": {"raw": "upstream detail"}}}),
    (429, {"error": {"code": 429, "message": "Rate limited"}}),
    (502, {"error": {"code": 502, "message": "Provider failed", "metadata": {"provider_name": "Anthropic"}}}),
    (200, {"error": {"code": 502, "message": "Generation failed after HTTP headers"}}),
])
def test_api_errors_are_logged_completely_and_never_execute_actions(status, body):
    sends = []

    def handler(request):
        sends.append(request)
        return httpx2.Response(status, json=body, headers={"request-id": "failed_req", "retry-after": "30"})

    hooks, sandbox = RecordingHooks(), FakeSandbox()
    episode = asyncio.run(harness.run_episode("Transcribe.", sandbox, client_for(handler), DEFAULT_MODEL,
                                             60, hooks, backend="openrouter"))
    assert episode.stop_reason == "agent_error" and sandbox.commands == []
    assert len(sends) == 1  # Rate limits / errors do not trigger hidden retries.
    assert hooks.calls[0][1] == body and hooks.calls[0][3]
    exchange = episode.calls[0]["http_exchanges"][0]
    assert json.loads(exchange["response_text"]) == body
    assert exchange["status_code"] == status and exchange["response_headers"]["retry-after"] == "30"
    assert episode.calls[0]["request_id"] == "failed_req"


@pytest.mark.parametrize("body", ["upstream failed, not JSON", "[]", "null"])
def test_non_json_or_non_object_responses_preserve_body(body):
    send = client_for(lambda request: httpx2.Response(502, text=body))
    with pytest.raises(harness.ProviderCallError) as caught:
        asyncio.run(send(payload(), 30))
    assert caught.value.trace["http_exchanges"][0]["response_text"] == body


def test_echoed_credentials_and_sensitive_headers_are_redacted_from_error_trace():
    body = {"error": {"message": "invalid token " + KEY}}
    send = client_for(lambda request: httpx2.Response(401, json=body, headers={
        "authorization": KEY, "set-cookie": "secret_cookie", "x-api-key": KEY, "x-debug": KEY}))
    with pytest.raises(harness.ProviderCallError) as caught:
        asyncio.run(send(payload(), 30))
    assert KEY not in json.dumps(caught.value.trace)
    assert KEY not in json.dumps(caught.value.response)
    assert "secret_cookie" not in json.dumps(caught.value.trace)


def test_request_timeout_retains_transport_error_and_executes_nothing():
    def handler(request):
        raise httpx2.ReadTimeout("No response", request=request)

    hooks, sandbox = RecordingHooks(), FakeSandbox()
    episode = asyncio.run(harness.run_episode("Transcribe.", sandbox, client_for(handler), DEFAULT_MODEL,
                                             300, hooks, backend="openrouter"))
    assert episode.stop_reason == "agent_error" and sandbox.commands == []
    assert episode.calls[0]["timed_out"] is True
    assert episode.calls[0]["http_exchanges"][0]["transport_error"]["type"] == "ReadTimeout"


def test_episode_deadline_cancels_http_call_and_logs_available_request():
    async def handler(request):
        await asyncio.sleep(3)
        return httpx2.Response(200, json=reply())

    hooks, sandbox = RecordingHooks(), FakeSandbox()
    episode = asyncio.run(harness.run_episode("Transcribe.", sandbox, client_for(handler), DEFAULT_MODEL,
                                             0.05, hooks, backend="openrouter"))
    assert episode.stop_reason == "deadline" and sandbox.commands == []
    exchange = episode.calls[0]["http_exchanges"][0]
    assert exchange["method"] == "POST" and exchange["request_model"] == DEFAULT_MODEL
    assert hooks.calls[0][0]["model"] == DEFAULT_MODEL


@pytest.mark.parametrize("body, stop_reason", [
    (reply([{"type": "text", "text": "Cannot comply."}], "refusal"), "provider_refusal"),
    (reply(stop="max_tokens"), "agent_error"),
    (reply(stop="model_context_window_exceeded"), "agent_error"),
    (reply([{"type": "tool_use", "id": "a", "name": "shell", "input": {"command": "ls"}},
            {"type": "tool_use", "id": "b", "name": "shell", "input": {"command": "pwd"}}]), "agent_error"),
    (reply([{"type": "tool_use", "id": "a", "name": "python", "input": {"code": "1"}}]), "agent_error"),
])
def test_native_stop_and_tool_safety_match_anthropic(body, stop_reason):
    sandbox = FakeSandbox()
    episode = asyncio.run(harness.run_episode("Transcribe.", sandbox,
        client_for(lambda request: httpx2.Response(200, json=body)), DEFAULT_MODEL, 60, backend="openrouter"))
    assert episode.stop_reason == stop_reason and sandbox.commands == []


def test_finish_tool_is_rejected_by_shell_only_protocol():
    body = reply([{"type": "tool_use", "id": "f", "name": "finish", "input": {"summary": "Saved."}}])
    episode = asyncio.run(harness.run_episode("Transcribe.", FakeSandbox(),
        client_for(lambda request: httpx2.Response(200, json=body)), DEFAULT_MODEL, 60, backend="openrouter"))
    assert episode.stop_reason == "agent_error" and episode.final_response is None


@pytest.mark.parametrize("supported, effort, accepted", [
    (["high", "low"], "xhigh", False), (["high", "low"], "high", True),
    (None, "xhigh", True), ([], "high", False),
])
def test_alternative_model_effort_is_checked_without_silent_downgrade(supported, effort, accepted):
    model = "anthropic/claude-alternative-test"
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx2.Response(200, json={"data": [{"id": model, "reasoning": {"supported_efforts": supported}}]})
        assert json.loads(request.content)["output_config"] == {"effort": effort}
        return httpx2.Response(200, json=reply())

    send = client_for(handler)
    if accepted:
        asyncio.run(send(payload(model, effort), 30))
        assert [r.method for r in requests] == ["GET", "POST"]
    else:
        with pytest.raises(harness.ProviderCallError, match="does not confirm effort"):
            asyncio.run(send(payload(model, effort), 30))
        assert [r.method for r in requests] == ["GET"]


def test_missing_model_or_reasoning_metadata_rejects_alternative_before_generation():
    for entries in ([], [{"id": "anthropic/claude-test", "reasoning": {}}]):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, json={"data": entries})

        with pytest.raises(harness.ProviderCallError):
            asyncio.run(client_for(handler)(payload("anthropic/claude-test"), 30))
        assert [r.method for r in requests] == ["GET"]


def test_invalid_default_effort_or_non_claude_model_never_makes_request():
    def handler(request):
        pytest.fail("validation must happen before a request")

    for request in (payload(effort="minimal"), payload(model="openai/test")):
        with pytest.raises(harness.ProviderCallError):
            asyncio.run(client_for(handler)(request, 30))


def test_missing_key_retries_and_backend_conflicts_fail_explicitly(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterClient()
    with pytest.raises(ValueError, match="max_retries=0"):
        openrouter_client(max_retries=2)
    with pytest.raises(ValueError, match="conflicts"):
        adapter.resolve_backend(SimpleNamespace(api="anthropic", name="claude-opus-5-5"), "openrouter")
    with pytest.raises(ValueError, match="unknown backend"):
        adapter.resolve_backend(None, "wrong")


def test_provider_model_resolution_and_environment_are_separate(monkeypatch):
    active = SimpleNamespace(api="openrouter", name=DEFAULT_MODEL)
    mock = SimpleNamespace(api="mockllm", name="model")
    assert adapter.resolve_backend(active) == "openrouter"
    assert adapter.resolve_model(active, None, "openrouter") == DEFAULT_MODEL
    with pytest.raises(ValueError, match="conflicts"):
        adapter.resolve_model(active, "anthropic/other", "openrouter")
    monkeypatch.setenv("ANTHROPIC_MODEL", "direct-model")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/alternate")
    assert adapter.resolve_model(mock, None, "openrouter") == "anthropic/alternate"
    assert adapter.resolve_model(mock, "anthropic/explicit", "openrouter") == "anthropic/explicit"
    assert adapter.resolve_model(mock, None) == "direct-model"


def test_readable_inspect_thinking_and_opaque_redactions_remain_distinct():
    message = adapter.assistant_message(reply()["content"], DEFAULT_MODEL)
    assert message.content[0].reasoning == "A visible condensed thinking summary."
    assert message.content[0].signature == "opaque-signed-value"
    assert message.content[1].redacted is True
    assert message.tool_calls[0].function == "shell"


def test_inspect_mirror_logs_native_error_body_without_fabricating_assistant(monkeypatch):
    events = []
    monkeypatch.setattr(adapter, "transcript", lambda: SimpleNamespace(_event=events.append))

    class Store:
        def __init__(self):
            self.values = {}

        def set(self, key, value):
            self.values[key] = value

    state = SimpleNamespace(messages=[], store=Store())
    mirror = adapter.InspectMirror(state)
    episode = harness.Episode(DEFAULT_MODEL, backend="openrouter")
    body = {"error": {"code": 429, "metadata": {"raw": "upstream detail"}}}
    mirror.model_call(episode, payload(), body, {"elapsed_seconds": 0.1}, "API error")
    assert state.messages == []
    assert events[0].model == "openrouter/" + DEFAULT_MODEL
    assert events[0].call.response == body
    assert events[0].output.error == "API error"
    assert state.store.values["backend"] == "openrouter"
