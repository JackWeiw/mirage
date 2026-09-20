"""Synthesizer sub-agent: produces the module.cpp body for one module's
SynthesisTasks (Layer 2 fan-out unit). Agent-optional: returns None when no LLM
is configured, and the orchestrator falls back to the deterministic body that
generate_from_module_graph already emitted. Subclasses AgentCore to reuse prompt
loading, the raw-text LLM call+retry, and is_available(). One module -> one call.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agent.agent_core import AgentCore, LLMError
from observability.logging import get_logger

if TYPE_CHECKING:
    from codegen.module_graph import FunctionSignature

logger = get_logger("synthesizer")


class SynthesizerAgent(AgentCore):
    """Per-module .cpp body synthesizer (Layer 2 fan-out unit)."""

    def synthesize(
        self,
        module_name: str,
        namespace: str,
        signatures: list[FunctionSignature],
        digest_slice: dict[str, object],
    ) -> str | None:
        """Synthesize the full {module_name}.cpp body for one module.

        Returns the C++ file content, or None if the LLM is unavailable or the
        call fails (the orchestrator then leaves the deterministic body in place).
        """
        if not self.is_available():
            logger.info("synthesizer_offline_skipped", module=module_name)
            return None
        template = self._load_prompt("synthesize_body.md")
        prompt = (
            template.replace("{module_name}", module_name)
            .replace("{namespace}", namespace)
            .replace("{signatures_json}", json.dumps([s.model_dump() for s in signatures]))
            .replace("{digest_slice_json}", json.dumps(digest_slice))
        )
        try:
            body = self._call_llm(prompt)
        except LLMError as exc:
            logger.warning("synthesizer_llm_failed", module=module_name, error=str(exc))
            return None
        if not body.strip():
            logger.warning("synthesizer_empty_body", module=module_name)
            return None
        logger.info("synthesizer_body_synthesized", module=module_name, chars=len(body))
        return body
