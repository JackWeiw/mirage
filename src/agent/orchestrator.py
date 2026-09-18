"""Synthesis orchestrator: fans out per-module body synthesis to a SynthesizerAgent,
assembles the project via WorkloadGenerator.generate_from_module_graph, then patches
the LLM-synthesized modules' .cpp files. Agent-optional: when the synthesizer is
offline, synthesize returns None for every module -> no patch -> the output is
identical to the deterministic generate_from_module_graph project (today's local-only
mode). The .h contracts stay deterministic (contracts-before-impls); only .cpp bodies
are sub-agent-synthesized.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from observability.logging import get_logger

if TYPE_CHECKING:
    import pathlib

    from agent.synthesis_plan import SynthesisPlan, SynthesisTask
    from agent.synthesizer import SynthesizerAgent
    from codegen.generator import WorkloadGenerator
    from codegen.module_graph import ModuleDescriptor, ModuleGraph

logger = get_logger("orchestrator")


class SynthesisOrchestrator:
    """Fan out per-module synthesis + assemble via generate_from_module_graph."""

    def __init__(self, generator: WorkloadGenerator, synthesizer: SynthesizerAgent) -> None:
        self._generator = generator
        self._synthesizer = synthesizer

    def synthesize(self, plan: SynthesisPlan, output_dir: pathlib.Path) -> pathlib.Path:
        """Produce a generated C++ project from a SynthesisPlan.

        1. Group plan.tasks by module.
        2. For modules with >=1 body_synthesis task: if the module is already in
           plan.synthesized_bodies (cache hit), skip re-synthesis (surgical -- keep
           the cached body); otherwise call the synthesizer -> body, store in the cache.
        3. generate_from_module_graph(plan.module_graph, output_dir) (deterministic
           baseline for ALL .cpp; this overwrites any previously-patched bodies, so
           step 4 re-applies the cached bodies).
        4. Overwrite the LLM-synthesized modules' .cpp with their (cached) bodies.

        Cold start (empty cache) -> synthesize all body_synthesis modules. After a
        revise_plan clears a module's cache entry -> that module is cache-miss ->
        re-synthesized; unchanged modules keep their cached body (surgical).
        """
        by_module: dict[str, list[SynthesisTask]] = {}
        for task in plan.tasks:
            by_module.setdefault(task.module, []).append(task)

        for module_name, tasks in by_module.items():
            if not any(t.role == "body_synthesis" for t in tasks):
                continue  # all-deterministic module -> skip the LLM
            if module_name in plan.synthesized_bodies:
                continue  # cache hit -> surgical skip (keep the cached body)
            mod = self._module_by_name(plan.module_graph, module_name)
            if mod is None:
                continue
            sigs = [*mod.public_interface, *mod.internal_functions]
            slice_ = {k: v for t in tasks for k, v in t.signature_slice.items()}
            body = self._synthesizer.synthesize(module_name, mod.namespace, sigs, slice_)
            if body is not None:
                plan.synthesized_bodies[module_name] = body  # cache for surgical reuse

        self._generator.generate_from_module_graph(plan.module_graph, output_dir)

        for module_name, body in plan.synthesized_bodies.items():
            (output_dir / f"{module_name}.cpp").write_text(body)
            logger.info("orchestrator_patched_module_cpp", module=module_name)

        return output_dir

    @staticmethod
    def _module_by_name(graph: ModuleGraph, name: str) -> ModuleDescriptor | None:
        for mod in graph.modules:
            if mod.name == name:
                return mod
        return None
