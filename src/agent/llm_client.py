"""LLM client abstraction: Anthropic-Messages and OpenAI-Chat-Completions shapes.

Mirrors crucible PR #13's brain.Provider pattern: one interface, two endpoint
shapes, the operator's gateway chosen via ``AgentConfig.provider``. The
``api_key`` is passed to the SDK constructor (header-only transport, managed by
the SDK) and is never placed in a request body, response text, or log line.
``base_url`` None means the SDK's own default host is used -- this module never
injects a vendor official host itself, so a None ``base_url`` is the only path
that can reach a vendor endpoint, and only because the operator left it unset.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from config.framework_config import AgentConfig

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """Normalized LLM call surface for AgentCore.

    ``complete`` returns ``(text, stop_reason)`` where ``stop_reason ==
    "max_tokens"`` means the response was truncated by the model's token limit.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.model = config.model
        self.max_tokens = config.max_tokens
        self._sdk: Any = None
        try:
            self._sdk = self._build_sdk_client()
        except ImportError:
            logger.warning("%s_sdk_not_installed", self.__class__.__name__.lower())

    @abstractmethod
    def _build_sdk_client(self) -> Any:
        """Construct the SDK client with api_key (+ base_url when set).

        Raise ImportError if the SDK is not installed so the agent degrades to
        offline mode instead of crashing at startup.
        """

    @abstractmethod
    def complete(self, prompt: str) -> tuple[str, str]:
        """Call the model; return (text, stop_reason)."""

    def is_available(self) -> bool:
        return self._sdk is not None

    @abstractmethod
    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        """SDK exception classes worth retrying (rate limit, 5xx, connection)."""


class AnthropicClient(LLMClient):
    """Anthropic Messages-API shape."""

    def _build_sdk_client(self) -> Any:
        import anthropic

        kwargs: dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url is not None:
            kwargs["base_url"] = self.config.base_url
        return anthropic.Anthropic(**kwargs)

    def complete(self, prompt: str) -> tuple[str, str]:
        response = self._sdk.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text
        if content is None:
            raise RuntimeError("Anthropic response had no text content block")
        stop = response.stop_reason
        if stop is None:
            raise RuntimeError("Anthropic response had no stop_reason")
        return str(content), str(stop)

    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        import anthropic

        return (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        )


class OpenAIClient(LLMClient):
    """OpenAI Chat Completions shape (also covers OpenAI-compatible gateways)."""

    def _build_sdk_client(self) -> Any:
        import openai

        kwargs: dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url is not None:
            kwargs["base_url"] = self.config.base_url
        return openai.OpenAI(**kwargs)

    def complete(self, prompt: str) -> tuple[str, str]:
        response = self._sdk.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("OpenAI response had no text content")
        text = str(content)
        # Map OpenAI finish_reason onto the normalized stop_reason AgentCore uses.
        # "length" (token limit) and "content_filter" (censored/incomplete) both
        # mean the response was cut short -> treat as truncation so the caller
        # raises LLMTruncationError instead of trusting partial text.
        finish = response.choices[0].finish_reason
        stop = "max_tokens" if finish in ("length", "content_filter") else "end_turn"
        return text, stop

    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        import openai

        return (
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
        )


def make_client(config: AgentConfig) -> LLMClient:
    """Construct the LLMClient for ``config.provider``."""
    if config.provider == "openai":
        return OpenAIClient(config)
    return AnthropicClient(config)
