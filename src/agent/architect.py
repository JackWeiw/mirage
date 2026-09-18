"""Architect agent: consumes the customer signature via digest accessors + a
deterministic ModuleGraph base, emits a SynthesisPlan (refined ModuleGraph +
per-function SynthesisTasks). Agent-optional: degrades to the deterministic base
when no LLM is configured. Subclasses AgentCore to reuse prompt loading, LLM
call+retry, JSON parsing, and is_available(). Replaces run_full_chain's
analyze+plan (detail_fill fans out to Synthesizer sub-agents in Phase C).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agent.agent_core import AgentCore, LLMError, LLMResponseError, _serialize_recent_history
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

    def revise_plan(
        self,
        plan: SynthesisPlan,
        report: dict[str, Any],
        sensitivity: dict[str, dict[str, Any]],
        history: Any,
    ) -> tuple[SynthesisPlan, list[dict[str, Any]]]:
        """Surgical plan revision (Phase D structural tier): the LLM picks which
        modules' bodies to re-synthesize given the gap report; we clear those
        modules' cached synthesized_bodies so the orchestrator re-synthesizes
        ONLY them (surgical), preserving unchanged modules' bodies.

        LLM-only -- the loop calls this when is_available(); on LLMError the loop
        degrades to the runtime tier (config knobs, no rebuild). Returns
        (revised_plan, adjustments). Raises LLMResponseError on a malformed
        response (caller catches via LLMError).
        """
        template = self._load_prompt("revise_plan.md")
        prompt = (
            template.replace("{plan}", plan.model_dump_json())
            .replace("{report}", json.dumps(report))
            .replace("{sensitivity}", json.dumps(sensitivity))
            .replace("{recent_history}", _serialize_recent_history(history))
        )
        resp = self._call_llm_json(prompt)
        resynth = resp.get("resynthesize_modules")
        adjustments = resp.get("adjustments")
        if not isinstance(resynth, list) or not all(isinstance(m, str) for m in resynth):
            raise LLMResponseError(
                f"revise_plan response missing 'resynthesize_modules' list[str]: {str(resp)[:200]!r}"
            )
        if not isinstance(adjustments, list) or not all(isinstance(a, dict) for a in adjustments):
            raise LLMResponseError(
                f"revise_plan response missing 'adjustments' list of dicts: {str(resp)[:200]!r}"
            )
        revised = plan.model_copy(deep=True)
        for mod in resynth:
            revised.synthesized_bodies.pop(mod, None)  # cache-miss -> forces re-synthesis
        logger.info("architect_revise_plan", resynth=resynth, kept=len(revised.synthesized_bodies))
        return revised, [dict(a) for a in adjustments]
