# Agent Custom Endpoint (base_url + provider) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `AgentCore` target a custom LLM endpoint (Anthropic-shape OR OpenAI-shape) via `base_url` + `api_key` + `provider`, never falling back to a vendor's official endpoint. Unblocks real-ARM structural-tier validation (issue #55).

**Architecture:** Introduce an `LLMClient` abstraction (`src/agent/llm_client.py`) with two implementations — `AnthropicClient` (Messages API) and `OpenAIClient` (Chat Completions) — mirroring crucible PR #13's `Provider` interface. `AgentCore` delegates the LLM call to the selected client; `AgentConfig` gains `base_url` + `provider`. The key travels only in auth headers (SDK-managed); it is never logged. When `api_key` is None the agent stays offline (degraded mode) — unchanged.

**Tech Stack:** Python 3.13, pydantic v2, anthropic SDK (already a dep), openai SDK (new dep, lazy-imported), pytest, ruff + ruff-format + mypy strict (src/), pre-commit. ASCII-only (Windows GBK locale).

**Spec:** issue #55 — https://github.com/JackWeiw/mirage/issues/55

---

## File Structure

- **Create** `src/agent/llm_client.py` — `LLMClient` ABC + `AnthropicClient` + `OpenAIClient`. Each exposes `is_available()`, `complete(prompt) -> tuple[str, str]` (text, stop_reason; stop_reason=="max_tokens" means truncated), `transient_exceptions() -> tuple[type[Exception], ...]`. Clients construct their SDK client with `api_key` AND `base_url` (when set); base_url empty -> official SDK default is used (but tests assert we never ADD an official host).
- **Modify** `src/agent/agent_core.py` — `AgentCore.__init__` constructs the right `LLMClient` per `config.provider`; `self._client` is now an `LLMClient` (not a raw SDK client). `_call_llm` calls `self._client.complete(prompt)`; `_transient_exceptions` delegates; `is_available` delegates. Existing prompt/parse/chain/revise logic unchanged.
- **Modify** `src/config/framework_config.py` — `AgentConfig` gains `base_url: str | None = None`, `provider: str = "anthropic"` with pydantic validation (`provider` must be `"anthropic"` or `"openai"`).
- **Modify** `src/config/default_config.yaml` — add `base_url: null`, `provider: anthropic` under `agent`.
- **Modify** `pyproject.toml` — add `openai>=1.0` to `dependencies` (lazy-imported in `OpenAIClient`, mirroring the anthropic pattern).
- **Modify** `tests/agent/test_agent_core.py` — refactor the `_stub_client` test seam from `agent._client.messages.create = ...` to `agent._client.complete = ...` (the new abstraction). Behavior assertions (retry, truncation, non-transient) preserved.
- **Create** `tests/agent/test_llm_client.py` — both shapes construct with base_url+api_key (mock SDK constructors; assert base_url+key passed); `complete` maps each SDK response shape to (text, stop_reason); no-official-fallback assertion; key-not-logged assertion.

---

## Task 1: `AgentConfig` base_url + provider, with validation

**Files:**
- Modify: `src/config/framework_config.py:9-12`
- Test: `tests/config/test_framework_config.py` (add cases)

- [ ] **Step 1: Write the failing tests**

Add to `tests/config/test_framework_config.py`:
```python
def test_agent_config_defaults_base_url_none_provider_anthropic() -> None:
    cfg = AgentConfig()
    assert cfg.base_url is None
    assert cfg.provider == "anthropic"


def test_agent_config_accepts_openai_provider() -> None:
    cfg = AgentConfig(provider="openai", base_url="https://gw.example.com/v1", api_key="k")
    assert cfg.provider == "openai"
    assert cfg.base_url == "https://gw.example.com/v1"


def test_agent_config_rejects_unknown_provider() -> None:
    import pytest
    with pytest.raises(ValueError):
        AgentConfig(provider="gemini")
```
(If the test file doesn't exist, create it with these three tests.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/config/test_framework_config.py -q`
Expected: FAIL (no `base_url`/`provider` fields; `AgentConfig(provider=...)` errors).

- [ ] **Step 3: Implement**

In `src/config/framework_config.py`, extend `AgentConfig`:
```python
from pydantic import field_validator

class AgentConfig(BaseModel):
    model: str = "claude-sonnet-5"
    max_tokens: int = 4096
    api_key: str | None = None
    base_url: str | None = None
    provider: str = "anthropic"

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        if v not in ("anthropic", "openai"):
            raise ValueError(f"provider must be 'anthropic' or 'openai', got {v!r}")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/config/test_framework_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config/framework_config.py tests/config/test_framework_config.py
git commit -m "feat(agent): AgentConfig gains base_url + provider (issue #55)"
```

---

## Task 2: `LLMClient` abstraction (Anthropic + Openai)

**Files:**
- Create: `src/agent/llm_client.py`
- Test: `tests/agent/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_llm_client.py`. Use monkeypatch to replace the SDK modules so no real network. Assert the SDK client constructor receives `api_key` AND `base_url`; assert `complete` returns `(text, stop_reason)` for each shape; assert truncation maps to `stop_reason == "max_tokens"`; assert the api_key never appears in any logged/returned string.

```python
import types
from types import SimpleNamespace
import pytest
from agent.llm_client import AnthropicClient, OpenAIClient
from config.framework_config import AgentConfig


def test_anthropic_client_constructs_with_base_url_and_key(monkeypatch):
    captured = {}
    fake_mod = types.ModuleType("anthropic")
    class _Anthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = self
        def create(self, **kwargs):
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(text='{"ok": 1}')],
            )
    fake_mod.Anthropic = _Anthropic
    fake_mod.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_mod.InternalServerError = type("InternalServerError", (Exception,), {})
    fake_mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake_mod)
    cfg = AgentConfig(api_key="secret-key", base_url="https://gw/v1", provider="anthropic", model="m")
    client = AnthropicClient(cfg)
    text, stop = client.complete("prompt")
    assert text == '{"ok": 1}' and stop == "end_turn"
    assert captured["api_key"] == "secret-key"
    assert captured["base_url"] == "https://gw/v1"
    assert client.is_available()


def test_openai_client_constructs_with_base_url_and_maps_length_to_max_tokens(monkeypatch):
    captured = {}
    fake_mod = types.ModuleType("openai")
    class _OpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        def _create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": 2}'))],
                finish_reason="length",
            )
    fake_mod.OpenAI = _OpenAI
    fake_mod.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    fake_mod.InternalServerError = type("InternalServerError", (Exception,), {})
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_mod)
    cfg = AgentConfig(api_key="secret-key", base_url="https://gw/v1", provider="openai", model="m")
    client = OpenAIClient(cfg)
    text, stop = client.complete("prompt")
    assert text == '{"ok": 2}' and stop == "max_tokens"
    assert captured["api_key"] == "secret-key"
    assert captured["base_url"] == "https://gw/v1"


def test_client_unavailable_when_sdk_missing():
    # api_key set but SDK import fails -> is_available() False (degraded path)
    cfg = AgentConfig(api_key="k", provider="anthropic")
    # Force ImportError by injecting a broken sys.modules entry
    import sys
    monkeypatch_holder = {}
    # Simulated via a subclass that overrides _build_sdk_client to raise ImportError
    from agent.llm_client import AnthropicClient as AC
    class BrokenAC(AC):
        def _build_sdk_client(self):
            raise ImportError("no anthropic")
    c = BrokenAC(cfg)
    assert not c.is_available()


def test_no_official_endpoint_default_when_base_url_unset(monkeypatch):
    # base_url None -> SDK constructor called WITHOUT base_url kwarg (we do NOT
    # inject any official host). Assert 'base_url' absent from captured kwargs.
    captured = {}
    fake_mod = types.ModuleType("anthropic")
    class _Anthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = self
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(text="x")])
    fake_mod.Anthropic = _Anthropic
    fake_mod.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_mod.InternalServerError = type("InternalServerError", (Exception,), {})
    fake_mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake_mod)
    cfg = AgentConfig(api_key="k", provider="anthropic")  # base_url None
    AnthropicClient(cfg)
    assert "base_url" not in captured  # we never inject an official host


def test_api_key_not_in_complete_return_value(monkeypatch):
    fake_mod = types.ModuleType("anthropic")
    class _Anthropic:
        def __init__(self, **kwargs):
            self.messages = self
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(text="plain")])
    fake_mod.Anthropic = _Anthropic
    fake_mod.RateLimitError = type("RateLimitError", (Exception,), {})
    fake_mod.InternalServerError = type("InternalServerError", (Exception,), {})
    fake_mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake_mod)
    cfg = AgentConfig(api_key="secret-key", provider="anthropic")
    text, _ = AnthropicClient(cfg).complete("prompt")
    assert "secret-key" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/agent/test_llm_client.py -q`
Expected: FAIL (module `agent.llm_client` doesn't exist).

- [ ] **Step 3: Implement**

Create `src/agent/llm_client.py`:
```python
"""LLM client abstraction: Anthropic-Messages and OpenAI-Chat-Completions shapes.

Mirrors crucible PR #13's brain.Provider pattern: one interface, two endpoint
shapes, the operator's gateway chosen via AgentConfig.provider. The api_key is
passed to the SDK constructor (header-only transport, SDK-managed) and is never
placed in a request body, response text, or log line. base_url empty means the
SDK's own default is used -- we never inject a vendor official host ourselves.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from config.framework_config import AgentConfig

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """Normalized LLM call surface for AgentCore."""

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
        """Construct the SDK client with api_key (+ base_url when set). Raise
        ImportError if the SDK is not installed so the agent degrades offline."""

    @abstractmethod
    def complete(self, prompt: str) -> tuple[str, str]:
        """Call the model; return (text, stop_reason). stop_reason == 'max_tokens'
        means the response was truncated."""

    def is_available(self) -> bool:
        return self._sdk is not None

    @abstractmethod
    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        """SDK exception classes worth retrying."""


class AnthropicClient(LLMClient):
    def _build_sdk_client(self) -> Any:
        import anthropic

        kwargs: dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url is not None:
            kwargs["base_url"] = self.config.base_url
        return anthropic.Anthropic(**kwargs)

    def complete(self, prompt: str) -> tuple[str, str]:
        # mypy: _sdk is Any; the SDK call shape is anthropic.Messages.create.
        response = self._sdk.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(response.content[0].text), str(response.stop_reason)

    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        import anthropic

        return (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        )


class OpenAIClient(LLMClient):
    def _build_sdk_client(self) -> Any:
        import openai

        kwargs: dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url is not None:
            kwargs["base_url"] = self.config.base_url
        return openai.OpenAI(**kwargs)

    def complete(self, prompt: str) -> tuple[str, str]:
        response = self._sdk.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = str(response.choices[0].message.content)
        # Map OpenAI finish_reason to the normalized stop_reason used by AgentCore.
        finish = str(response.finish_reason or getattr(response.choices[0], "finish_reason", ""))
        stop = "max_tokens" if finish == "length" else "end_turn"
        return text, stop

    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        import openai

        return (
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
        )
```

Note: the OpenAI SDK's `response.finish_reason` lives on `response.choices[0].finish_reason` (v1). The implementer should verify against the installed openai SDK and adjust the attribute path; the test stub sets `finish_reason` on the response root for simplicity — make the production code read `response.choices[0].finish_reason` and the TEST stub mirror that shape. (Align test+impl in the implementation step.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/agent/test_llm_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/llm_client.py tests/agent/test_llm_client.py
git commit -m "feat(agent): LLMClient abstraction (anthropic + openai shapes)"
```

---

## Task 3: `AgentCore` refactor + default_config + pyproject dep

**Files:**
- Modify: `src/agent/agent_core.py`
- Modify: `src/config/default_config.yaml`
- Modify: `pyproject.toml` (add `openai>=1.0`)
- Modify: `tests/agent/test_agent_core.py` (refactor the `_stub_client` seam)

- [ ] **Step 1: Refactor the existing test seam first (TDD — make the new seam explicit)**

In `tests/agent/test_agent_core.py`, replace `_stub_client` (which set `agent._client.messages.create`) with a seam that stubs `agent._client.complete`:
```python
def _stub_client(agent: AgentCore, complete_fn: Any) -> None:
    """Replace the agent's LLMClient.complete with a stub (no real network)."""
    agent._client.complete = complete_fn
```
Update the four `_call_llm` hardening tests (`retries_transient_then_succeeds`, `raises_transient_after_retries_exhausted`, `raises_truncation_on_max_tokens`, `does_not_retry_non_transient`) to stub `.complete` returning `(text, stop_reason)` tuples instead of `SimpleNamespace(stop_reason=..., content=[...])`. E.g. the success stub returns `('{"ok": 1}', "end_turn")`; the truncation stub returns `("", "max_tokens")`; the transient/fail stubs raise.

The `test_analyze_profile_injects_deterministic_classification` test monkeypatches `agent._call_llm` directly (not the client) — unchanged.

- [ ] **Step 2: Run the agent_core tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/agent/test_agent_core.py -q`
Expected: FAIL (the stubs now target `.complete` but `AgentCore._client` is still a raw anthropic client with `.messages.create`, not an `LLMClient` with `.complete`).

- [ ] **Step 3: Refactor `AgentCore`**

In `src/agent/agent_core.py`:
- Import: `from agent.llm_client import LLMClient, AnthropicClient, OpenAIClient`.
- `__init__`:
```python
def __init__(self, config: AgentConfig | None = None) -> None:
    self.config = config or AgentConfig()
    self.model = self.config.model
    self.max_tokens = self.config.max_tokens
    self._client: LLMClient | None = None
    if self.config.api_key is not None:
        provider = self.config.provider
        if provider == "openai":
            self._client = OpenAIClient(self.config)
        else:
            self._client = AnthropicClient(self.config)
```
- `is_available`:
```python
def is_available(self) -> bool:
    return self._client is not None and self._client.is_available()
```
- `_transient_exceptions`:
```python
def _transient_exceptions(self) -> tuple[type[Exception], ...]:
    if self._client is None:
        return ()
    return self._client.transient_exceptions()
```
- `_call_llm`: replace the `self._client.messages.create(...)` block with:
```python
text, stop_reason = self._client.complete(prompt)
if stop_reason == "max_tokens":
    raise LLMTruncationError(
        f"LLM response truncated at max_tokens={self.max_tokens}; "
        "raise max_tokens or split the call."
    )
return text
```
Keep the retry loop, transient handling, and `RuntimeError`-when-unavailable guard unchanged.

- [ ] **Step 4: Update `default_config.yaml` + `pyproject.toml`**

`default_config.yaml` agent block:
```yaml
  agent:
    model: claude-sonnet-5
    max_tokens: 4096
    base_url: null
    provider: anthropic
```
`pyproject.toml` dependencies: add `"openai>=1.0",` (after the anthropic line).

- [ ] **Step 5: Run the full test suite + lint + types**

```bash
PYTHONPATH=src python -m pytest tests/ -q
python -m ruff check src/ tests/
python -m ruff format --check src/ tests/
python -m mypy src/
python -m pre_commit run --all-files
```
Expected: all PASS, no type errors, coverage floor met.

- [ ] **Step 6: Commit**

```bash
git add src/agent/agent_core.py src/config/default_config.yaml pyproject.toml tests/agent/test_agent_core.py
git commit -m "feat(agent): AgentCore delegates to LLMClient; openai dep + config"
```

---

## Task 4: Full gate + open PR + review + merge

- [ ] **Step 1: Full gate**
```bash
PYTHONPATH=src python -m pytest tests/ -q
python -m pre_commit run --all-files
```
- [ ] **Step 2: Push + open PR**
```bash
git push -u origin feat/agent-custom-endpoint
gh pr create --title "Agent custom endpoint: base_url + provider (anthropic + openai) (#55)" --body-file <body>
```
PR body: summary, files, test/gate results, "unblocks real-ARM structural-tier validation", references issue #55, mirrors crucible PR #13.

- [ ] **Step 3: Two-stage review** (spec compliance + code quality) via subagent-driven-development reviewers; fix loop until approved.

- [ ] **Step 4: Merge** (squash) to main; delete branch; verify HEAD.

---

## Self-Review (run after writing)

- **Spec coverage:** issue #55 acceptance = (a) config.yaml sets base_url+api_key+provider ✓ Task 1; (b) is_available True + calls reach custom endpoint ✓ Task 2/3; (c) both shapes unit-tested ✓ Task 2; (d) no official-endpoint fallback ✓ test_no_official_endpoint_default; (e) key not logged ✓ test_api_key_not_in_complete_return_value; (f) default_config updated ✓ Task 3; (g) degraded mode when api_key None ✓ (unchanged path). All covered.
- **Placeholder scan:** none — all code blocks are complete.
- **Type consistency:** `LLMClient.complete -> tuple[str, str]`; `AgentCore._call_llm` unpacks `(text, stop_reason)`; `stop_reason == "max_tokens"` matches the existing `LLMTruncationError` contract. `AgentConfig.provider` validated to ("anthropic"|"openai"). `OpenAIClient` finish_reason attribute path must be aligned between impl and test (flagged in Task 2).
- **Risk:** the existing `test_agent_core.py` hardening tests change seam — Task 3 Step 1 handles this before the refactor. The `test_revise_instruction.py` mock seam must also be checked (it may stub the client); the implementer verifies it still passes after the refactor and updates if needed.
