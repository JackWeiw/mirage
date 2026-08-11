"""Agent Core — orchestrates LLM prompt chain for workload simulation.

Phase 1: Uses Claude API directly with prompt templates.
Phase 2: Will use MCP protocol for tool access.
Agent is optional — Pipeline works in local-only mode without it.
"""

import json
import logging
import pathlib
from typing import Any, cast

from config.framework_config import AgentConfig

logger = logging.getLogger(__name__)

PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"


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
        self._client: Any = None

        if self.config.api_key is not None:
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=self.config.api_key)
            except ImportError:
                logger.warning("anthropic_not_installed")

    def is_available(self) -> bool:
        """Check if the agent can make LLM calls."""
        return self._client is not None

    def _load_prompt(self, name: str) -> str:
        """Load a prompt template from the prompts directory."""
        filepath = PROMPTS_DIR / name
        return filepath.read_text()

    def _parse_json_response(self, response_text: str) -> dict[str, Any]:
        """Parse JSON from an LLM response text.

        Finds the first { ... } block and parses it.
        Falls back to raw_response dict if parsing fails.
        """
        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return cast("dict[str, Any]", json.loads(response_text[json_start:json_end]))
        except json.JSONDecodeError:
            pass
        return {"raw_response": response_text}

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM with a prompt and return the response text.

        Raises:
            RuntimeError: If agent is not available (no API key/client).
        """
        if self._client is None:
            raise RuntimeError(
                "Agent not available — no API key configured. Use local-only mode instead."
            )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(response.content[0].text)

    def analyze_profile(self, profile_json: str) -> dict[str, Any]:
        """Analyze a customer Profile and produce structured analysis.

        Hotspot classification is injected deterministically from the input
        Profile (already classified by FunctionClassifier at ingestion) rather
        than re-derived by the LLM, so downstream plan_workflow uses the
        classifier's source of truth.
        """
        template = self._load_prompt("analyze_profile.md")
        prompt = template.replace("{profile_json}", profile_json)
        response_text = self._call_llm(prompt)
        analysis = self._parse_json_response(response_text)
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
        response_text = self._call_llm(prompt)
        return self._parse_json_response(response_text)

    def detail_fill(self, workflow_json: str) -> dict[str, Any]:
        """Fill in implementation details for each workflow stage."""
        template = self._load_prompt("detail_fill.md")
        prompt = template.replace("{workflow_json}", workflow_json)
        response_text = self._call_llm(prompt)
        return self._parse_json_response(response_text)

    def evaluate_comparison(self, comparison_json: str) -> dict[str, Any]:
        """Evaluate comparison report and recommend iteration adjustments."""
        template = self._load_prompt("evaluate_comparison.md")
        prompt = template.replace("{comparison_json}", comparison_json)
        response_text = self._call_llm(prompt)
        return self._parse_json_response(response_text)

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
