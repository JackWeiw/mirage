"""Tests for the LLMClient abstraction (anthropic + openai shapes).

No real network: the SDK modules are replaced with fakes via monkeypatch so the
constructor args and call shape are asserted directly. The api_key must travel
only to the SDK constructor (header transport), never into response text.
"""

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from agent.llm_client import AnthropicClient, OpenAIClient, make_client
from config.framework_config import AgentConfig


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    text: str | None = '{"ok": 1}',
) -> None:
    fake: Any = types.ModuleType("anthropic")

    class _Anthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.messages = self  # messages.create on same object

        def create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(text=text)],
            )

    fake.Anthropic = _Anthropic
    fake.RateLimitError = type("RateLimitError", (Exception,), {})
    fake.InternalServerError = type("InternalServerError", (Exception,), {})
    fake.APIConnectionError = type("APIConnectionError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    finish_reason: str = "length",
    content: str | None = '{"ok": 2}',
    reasoning_content: str | None = None,
) -> None:
    fake: Any = types.ModuleType("openai")

    class _OpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs: Any) -> Any:
            captured["call_kwargs"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=content,
                            reasoning_content=reasoning_content,
                        ),
                        finish_reason=finish_reason,
                    )
                ],
            )

    fake.OpenAI = _OpenAI
    fake.RateLimitError = type("RateLimitError", (Exception,), {})
    fake.InternalServerError = type("InternalServerError", (Exception,), {})
    fake.APIConnectionError = type("APIConnectionError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "openai", fake)


def test_anthropic_client_constructs_with_base_url_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _install_fake_anthropic(monkeypatch, captured)
    cfg = AgentConfig(
        api_key="secret-key", base_url="https://gw/v1", provider="anthropic", model="m"
    )
    client = AnthropicClient(cfg)
    text, stop = client.complete("prompt")
    assert text == '{"ok": 1}'
    assert stop == "end_turn"
    # base_url AND api_key reach the SDK constructor.
    assert captured["api_key"] == "secret-key"
    assert captured["base_url"] == "https://gw/v1"
    assert client.is_available()


def test_openai_client_constructs_with_base_url_and_maps_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _install_fake_openai(monkeypatch, captured)
    cfg = AgentConfig(api_key="secret-key", base_url="https://gw/v1", provider="openai", model="m")
    client = OpenAIClient(cfg)
    text, stop = client.complete("prompt")
    assert text == '{"ok": 2}'
    assert stop == "max_tokens"  # OpenAI finish_reason "length" -> max_tokens
    assert captured["api_key"] == "secret-key"
    assert captured["base_url"] == "https://gw/v1"


def test_no_official_endpoint_injected_when_base_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # base_url None -> we do NOT pass base_url to the constructor at all, so no
    # official host is ever injected by this module (only the SDK's own default
    # could apply, and only because the operator left it unset).
    captured: dict[str, Any] = {}
    _install_fake_anthropic(monkeypatch, captured)
    cfg = AgentConfig(api_key="k", provider="anthropic")  # base_url None
    AnthropicClient(cfg)
    assert "base_url" not in captured


def test_anthropic_api_key_not_in_return_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch, {})
    cfg = AgentConfig(api_key="secret-key", provider="anthropic")
    text, _ = AnthropicClient(cfg).complete("prompt")
    assert "secret-key" not in text


def test_openai_api_key_not_in_return_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch, {})
    cfg = AgentConfig(api_key="secret-key", provider="openai")
    text, _ = OpenAIClient(cfg).complete("prompt")
    assert "secret-key" not in text


def test_client_unavailable_when_sdk_missing() -> None:
    # api_key set but the SDK import fails -> is_available() False (degraded).
    cfg = AgentConfig(api_key="k", provider="anthropic")

    class _BrokenAnthropic(AnthropicClient):
        def _build_sdk_client(self) -> Any:
            raise ImportError("anthropic not installed")

    client = _BrokenAnthropic(cfg)
    assert not client.is_available()


def test_make_client_dispatches_on_provider() -> None:
    assert isinstance(make_client(AgentConfig(provider="anthropic", api_key="k")), AnthropicClient)
    assert isinstance(make_client(AgentConfig(provider="openai", api_key="k")), OpenAIClient)


def test_transient_exceptions_non_empty_when_sdk_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch, {})
    client = AnthropicClient(AgentConfig(api_key="k", provider="anthropic"))
    assert len(client.transient_exceptions()) == 3


def test_openai_finish_stop_maps_to_end_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    # The common happy path: finish_reason "stop" -> "end_turn".
    _install_fake_openai(monkeypatch, {}, finish_reason="stop", content='{"ok": 9}')
    client = OpenAIClient(AgentConfig(api_key="k", provider="openai"))
    text, stop = client.complete("prompt")
    assert text == '{"ok": 9}'
    assert stop == "end_turn"


def test_openai_content_filter_maps_to_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A censored/incomplete response is a forced stop -> treat as truncation so
    # the partial text is not trusted downstream.
    _install_fake_openai(monkeypatch, {}, finish_reason="content_filter", content="partial")
    client = OpenAIClient(AgentConfig(api_key="k", provider="openai"))
    _text, stop = client.complete("prompt")
    assert stop == "max_tokens"


def test_anthropic_complete_raises_on_none_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A tool-use-only response (no text block) must raise, not return "None".
    _install_fake_anthropic(monkeypatch, {}, text=None)
    client = AnthropicClient(AgentConfig(api_key="k", provider="anthropic"))
    with pytest.raises(RuntimeError, match="no text content"):
        client.complete("prompt")


def test_openai_complete_raises_on_none_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A tool-call / content-filter refusal (message.content is None AND no
    # reasoning_content) must raise, not return "None".
    _install_fake_openai(
        monkeypatch, {}, finish_reason="stop", content=None, reasoning_content=None
    )
    client = OpenAIClient(AgentConfig(api_key="k", provider="openai"))
    with pytest.raises(RuntimeError, match="no text content"):
        client.complete("prompt")


def test_openai_reasoning_model_falls_back_to_reasoning_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reasoning models (GLM-4.x / deepseek-r1 / o1-style) return content=None and
    # put the answer in reasoning_content. The client must fall back to it.
    _install_fake_openai(
        monkeypatch,
        {},
        finish_reason="stop",
        content=None,
        reasoning_content='{"revised": "config"}',
    )
    client = OpenAIClient(AgentConfig(api_key="k", provider="openai"))
    text, stop = client.complete("prompt")
    assert text == '{"revised": "config"}'
    assert stop == "end_turn"


def test_openai_uses_max_tokens_not_max_completion_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OpenAI-compatible gateways (vLLM/GLM) honor max_tokens and silently ignore
    # max_completion_tokens, so the call must send max_tokens (not the newer
    # max_completion_tokens), or the model runs at the gateway's default budget.
    captured: dict[str, Any] = {}
    _install_fake_openai(monkeypatch, captured, finish_reason="stop", content="ok")
    cfg = AgentConfig(api_key="k", provider="openai", max_tokens=4096)
    OpenAIClient(cfg).complete("prompt")
    call = captured.get("call_kwargs", {})
    assert call.get("max_tokens") == 4096
    assert "max_completion_tokens" not in call


def test_openai_transient_exceptions_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch, {}, finish_reason="stop")
    client = OpenAIClient(AgentConfig(api_key="k", provider="openai"))
    assert len(client.transient_exceptions()) == 3
