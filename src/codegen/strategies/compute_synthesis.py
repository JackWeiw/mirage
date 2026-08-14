"""Compute synthesis behavior strategy."""

from typing import Any

import jinja2

from codegen.strategies.base import BehaviorStrategy, StrategyRegistry


class ComputeSynthesisStrategy(BehaviorStrategy):
    def strategy_name(self) -> str:
        return "compute_synthesis"

    def render(self, stage: dict[str, Any], env: jinja2.Environment) -> tuple[str, str]:
        template = env.get_template("behaviors/compute_synthesis.cpp.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        # Honor archetype (descriptor path) with compute_type fallback (legacy path).
        archetype = config.get("archetype") or config.get("compute_type") or "compute"
        context = {
            "stage_name": stage["stage_name"],
            "archetype": archetype,
            "synthesis_config": config,
        }
        content = template.render(**context)
        filename = f"{stage['stage_name']}.h"
        return filename, content

    def render_decl(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        template = env.get_template("behaviors/compute_synthesis_decl.h.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        archetype = config.get("archetype") or config.get("compute_type") or "compute"
        decl: str = template.render(
            stage_name=stage["stage_name"], archetype=archetype, synthesis_config=config
        )
        return decl

    def render_def(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        template = env.get_template("behaviors/compute_synthesis_def.cpp.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        archetype = config.get("archetype") or config.get("compute_type") or "compute"
        definition: str = template.render(
            stage_name=stage["stage_name"], archetype=archetype, synthesis_config=config
        )
        return definition


StrategyRegistry.register(ComputeSynthesisStrategy())
