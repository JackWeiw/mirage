"""Architect agent: consumes the customer signature via digest accessors + a
deterministic ModuleGraph base, emits a SynthesisPlan (refined ModuleGraph +
per-function SynthesisTasks). Agent-optional: degrades to the deterministic base
when no LLM is configured. Subclasses AgentCore to reuse prompt loading, LLM
call+retry, JSON parsing, and is_available(). Replaces run_full_chain's
analyze+plan (detail_fill fans out to Synthesizer sub-agents in Phase C).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agent.agent_core import AgentCore, LLMError
from agent.synthesis_plan import SynthesisPlan
from codegen.module_graph_builder import ModuleGraphBuilder, fail_on_cycle, fail_on_name_collision
from observability.logging import get_logger
from profile import digest

if TYPE_CHECKING:
    from codegen.module_graph import ModuleGraph
    from profile.profile_schema import Profile

logger = get_logger("architect")


class ArchitectAgent(AgentCore):
    """LLM architect over the customer signature."""

    def design_plan(self, profile: Profile, top_k: int = 20) -> SynthesisPlan:
        """Design a SynthesisPlan from the customer Profile.

        Always builds the deterministic ModuleGraph base (ModuleGraphBuilder,
        dual-path). If the LLM is available, asks it to refine the base plan
        given the digest accessors; otherwise returns the base enveloped as a
        plan (agent-optional degradation).
        """
        base_graph = ModuleGraphBuilder().build(profile, project_name=profile.metadata.customer)
        if not self.is_available():
            logger.info("architect_offline_used_deterministic_base")
            return SynthesisPlan.from_graph(base_graph, source="deterministic")
        plan = self._design_with_llm(profile, base_graph, top_k)
        if plan is None:
            logger.warning("architect_llm_plan_invalid_falling_back_to_base")
            return SynthesisPlan.from_graph(base_graph, source="deterministic")
        return plan

    def _design_with_llm(
        self, profile: Profile, base_graph: ModuleGraph, top_k: int
    ) -> SynthesisPlan | None:
        """Call the LLM with the framework_design prompt + digest data; parse
        + validate the response into a SynthesisPlan. Returns None if the LLM
        call fails, the response is not valid JSON, the plan fails pydantic
        validation, or the module graph is not buildable (cycle/collision) --
        caller falls back to the deterministic base."""
        template = self._load_prompt("framework_design.md")
        prompt = template.replace("{digest_json}", self._render_digest(profile, top_k)).replace(
            "{base_graph_json}", base_graph.model_dump_json()
        )
        try:
            data = self._call_llm_json(prompt)
            plan = SynthesisPlan.model_validate(data)
            fail_on_name_collision(plan.module_graph)
            fail_on_cycle(plan.module_graph)
        except (LLMError, ValueError) as exc:
            # LLMError: LLM call/parse/truncation failure.
            # ValueError: pydantic ValidationError (v2 subclasses ValueError) OR
            # the cycle/collision guards. Any -> fall back to the deterministic base.
            logger.warning("architect_plan_invalid", error=str(exc))
            return None
        return plan

    @staticmethod
    def _render_digest(profile: Profile, top_k: int) -> str:
        """Render the digest accessors as JSON for the prompt."""
        return json.dumps(
            {
                "hotspot_subtree": [n.model_dump() for n in digest.hotspot_subtree(profile, top_k)],
                "dominant_bottleneck": digest.dominant_bottleneck(profile),
                "per_module_self_pct": digest.per_module_self_pct(profile),
                "stages": [s.model_dump() for s in digest.stages(profile)],
            }
        )
