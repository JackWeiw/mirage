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


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    fake: Any = types.ModuleType("anthropic")

    class _Anthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.messages = self  # messages.create on same object

        def create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(text='{"ok": 1}')],
            )

    fake.Anthropic = _Anthropic
    fake.RateLimitError = type("RateLimitError", (Exception,), {})
    fake.InternalServerError = type("InternalServerError", (Exception,), {})
    fake.APIConnectionError = type("APIConnectionError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    fake: Any = types.ModuleType("openai")

    class _OpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": 2}'),
                        finish_reason="length",
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
