"""Agent Core — orchestrates LLM prompt chain for workload simulation.

Phase 1: Uses Claude API directly with prompt templates.
Phase 2: Will use MCP protocol for tool access.
Agent is optional — Pipeline works in local-only mode without it.
"""

import json
import pathlib
import time
from typing import Any, cast

from agent.llm_client import LLMClient, make_client
from config.framework_config import AgentConfig
from observability.logging import get_logger

logger = get_logger("agent_core")

PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"

# Retry policy for transient LLM API errors (rate limit, 5xx, connection).
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0

# How many recent records' adjustments/effects to surface in the revise prompt.
_RECENT_HISTORY_N = 5


def _serialize_recent_history(history: Any) -> str:
    """Serialize recent adjustments + observed effects for the revise prompt.

    Duck-typed on `history` (an IterationHistory in production, a fake in
    tests) so the agent module need not import observability.
    """
    recs: list[dict[str, Any]] = []
    recent_records = getattr(history, "records", [])[-_RECENT_HISTORY_N:]
    for r in recent_records:
        rec: dict[str, Any] = {
            "adjustments": list(getattr(r, "adjustments", [])),
            "applied_moves": list(getattr(r, "applied_moves", [])),
            "observed_effects": dict(getattr(r, "observed_effects", {})),
            "score": getattr(r, "score", None),
        }
        # Surface build failures so the LLM can self-correct a codegen
        # compile error on the pending_build_fix path (#3b-fu1). Only
        # populated when a build actually failed, to avoid clutter.
        build_failed = bool(getattr(r, "build_failed", False))
        build_stderr = str(getattr(r, "build_stderr", "") or "")
        if build_failed or build_stderr:
            rec["build_failed"] = build_failed
            rec["build_stderr"] = build_stderr
        recs.append(rec)
    return json.dumps(recs)


class LLMError(RuntimeError):
    """Base error for AgentCore LLM failures."""


class LLMTruncationError(LLMError):
    """Raised when the LLM response hit max_tokens before finishing."""


class LLMResponseError(LLMError):
    """Raised when the LLM response cannot be parsed as JSON."""


class LLMTransientError(LLMError):
    """Raised when transient API errors exhaust the retry budget."""


class AgentCore:
    """Orchestrate the LLM prompt chain for workload simulation.

    Args:
        config: AgentConfig from FrameworkConfig. If api_key is None, the agent
            is in offline mode and will raise on any LLM call.
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self.model = self.config.model
        self.max_tokens = self.config.max_tokens
        self._client: LLMClient | None = None
        if self.config.api_key is not None:
            self._client = make_client(self.config)

    def is_available(self) -> bool:
        """Check if the agent can make LLM calls."""
        return self._client is not None and self._client.is_available()

    def _load_prompt(self, name: str) -> str:
        """Load a prompt template from the prompts directory."""
        filepath = PROMPTS_DIR / name
        return filepath.read_text()

    def _transient_exceptions(self) -> tuple[type[Exception], ...]:
        """Exception classes worth retrying (rate limit, 5xx, connection).

        Delegated to the LLMClient (SDK-specific). Returns () when no client is
        configured so the retry path degrades safely in offline mode.
        """
        if self._client is None:
            return ()
        return self._client.transient_exceptions()

    def _parse_json_response(self, response_text: str) -> dict[str, Any]:
        """Parse JSON from an LLM response text.

        Finds the first { ... } block and parses it. Raises LLMResponseError if
        no JSON can be parsed (no silent fallback) so a malformed response
        surfaces loudly instead of producing an empty workload downstream.
        """
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            try:
                return cast("dict[str, Any]", json.loads(response_text[json_start:json_end]))
            except json.JSONDecodeError:
                pass
        raise LLMResponseError(f"LLM response was not valid JSON: {response_text[:200]!r}")

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM with a prompt and return the response text.

        Retries transient API errors (rate limit / 5xx / connection) with
        exponential backoff. Raises LLMTruncationError if the response hit
        max_tokens; LLMTransientError if the retry budget is exhausted.

        Raises:
            RuntimeError: If the agent is not available (no API key/client).
        """
        if self._client is None or not self._client.is_available():
            raise RuntimeError(
                "Agent not available — no API key configured. Use local-only mode instead."
            )
        transient = self._transient_exceptions()
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                text, stop_reason = self._client.complete(prompt)
                if stop_reason == "max_tokens":
                    raise LLMTruncationError(
                        f"LLM response truncated at max_tokens={self.max_tokens}; "
                        "raise max_tokens or split the call."
                    )
                return text
            except transient as exc:
                last_exc = exc
                if attempt == _MAX_RETRIES:
                    raise LLMTransientError(
                        f"LLM call failed after {_MAX_RETRIES + 1} attempts"
                    ) from exc
                logger.warning("llm_transient_retry", attempt=attempt, error=exc)
                time.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
        raise LLMTransientError(f"LLM call failed after {_MAX_RETRIES + 1} attempts") from last_exc

    def _call_llm_json(self, prompt: str) -> dict[str, Any]:
        """Call the LLM and parse the response as JSON; raise on failure."""
        return self._parse_json_response(self._call_llm(prompt))

    def analyze_profile(self, profile_json: str) -> dict[str, Any]:
        """Analyze a customer Profile and produce structured analysis.

        Hotspot classification is injected deterministically from the input
        Profile (already classified by FunctionClassifier at ingestion) rather
        than re-derived by the LLM, so downstream plan_workflow uses the
        classifier's source of truth.
        """
        template = self._load_prompt("analyze_profile.md")
        prompt = template.replace("{profile_json}", profile_json)
        analysis = self._call_llm_json(prompt)
        analysis["hotspot_classification"] = self._classification_from_profile(profile_json)
        return analysis

    @staticmethod
    def _classification_from_profile(profile_json: str) -> list[dict[str, Any]]:
        """Extract the deterministic hotspot classification from a Profile JSON.

        The Profile's hotspots are already classified by FunctionClassifier at
        ingestion; return [{function, source, library}] verbatim so the analysis
        output carries the source of truth instead of an LLM re-derivation.
        """
        try:
            data = json.loads(profile_json)
        except json.JSONDecodeError:
            return []
        hotspots = data.get("hotspots", []) if isinstance(data, dict) else []
        classification: list[dict[str, Any]] = []
        for hotspot in hotspots:
            if not isinstance(hotspot, dict):
                continue
            if "function" in hotspot and "source" in hotspot and "library" in hotspot:
                classification.append(
                    {
                        "function": hotspot["function"],
                        "source": hotspot["source"],
                        "library": hotspot["library"],
                    }
                )
        return classification

    def plan_workflow(self, analysis_json: str) -> dict[str, Any]:
        """Plan Business Workflow stages based on analysis."""
        template = self._load_prompt("plan_workflow.md")
        prompt = template.replace("{analysis_json}", analysis_json)
        return self._call_llm_json(prompt)

    def detail_fill(self, workflow_json: str) -> dict[str, Any]:
        """Fill in implementation details for each workflow stage."""
        template = self._load_prompt("detail_fill.md")
        prompt = template.replace("{workflow_json}", workflow_json)
        return self._call_llm_json(prompt)

    def run_full_chain(self, profile_json: str) -> dict[str, Any]:
        """Run the full prompt chain: analyze -> plan -> detail_fill."""
        logger.info("agent_chain_start")
        analysis = self.analyze_profile(profile_json)
        logger.info("agent_chain_analyze_done")
        workflow = self.plan_workflow(json.dumps(analysis))
        logger.info("agent_chain_plan_done")
        instruction = self.detail_fill(json.dumps(workflow))
        logger.info("agent_chain_detail_fill_done")
        return instruction

    def revise_instruction(
        self,
        prior_instruction: dict[str, Any],
        report: dict[str, Any],
        sensitivity: dict[str, dict[str, Any]],
        history: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Revise the prior instruction via the LLM (the realism-preserving leg).

        The LLM owns structural / business-logic revision (diverse, non-regular
        patterns that resist chip-optimizer over-fitting), constrained by the
        sensitivity table's proven directions. Returns the revised instruction
        and the adjustments the LLM applied. The CALLER (loop driver) runs
        validate_adjustments on the adjustments before apply_adjustments -- this
        method does NOT self-gate, so a hallucinated wrong-direction adjustment
        is caught downstream, not here.

        Raises LLMResponseError if the response lacks `revised_instruction`
        (not a dict) or `adjustments` (not a list of dicts).
        """
        template = self._load_prompt("revise_instruction.md")
        prompt = (
            template.replace("{prior_instruction}", json.dumps(prior_instruction))
            .replace("{report}", json.dumps(report))
            .replace("{sensitivity}", json.dumps(sensitivity))
            .replace("{recent_history}", _serialize_recent_history(history))
        )
        resp = self._call_llm_json(prompt)
        revised = resp.get("revised_instruction")
        adjustments = resp.get("adjustments")
        if not isinstance(revised, dict):
            raise LLMResponseError(
                f"revise_instruction response missing 'revised_instruction' dict: {str(resp)[:200]!r}"
            )
        if not isinstance(adjustments, list) or not all(isinstance(a, dict) for a in adjustments):
            raise LLMResponseError(
                f"revise_instruction response missing 'adjustments' list of dicts: {str(resp)[:200]!r}"
            )
        return revised, [dict(a) for a in adjustments]
