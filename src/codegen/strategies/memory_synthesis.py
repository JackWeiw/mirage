"""Memory synthesis behavior strategy."""

from typing import Any

import jinja2

from codegen.strategies.base import BehaviorStrategy, StrategyRegistry


class MemorySynthesisStrategy(BehaviorStrategy):
    def strategy_name(self) -> str:
        return "memory_synthesis"

    def render(self, stage: dict[str, Any], env: jinja2.Environment) -> tuple[str, str]:
        template = env.get_template("behaviors/memory_synthesis.cpp.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        context = {
            "stage_name": stage["stage_name"],
            "synthesis_config": config,
        }
        content = template.render(**context)
        filename = f"{stage['stage_name']}.h"
        return filename, content

    def render_decl(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        template = env.get_template("behaviors/memory_synthesis_decl.h.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        decl: str = template.render(stage_name=stage["stage_name"], synthesis_config=config)
        return decl

    def render_def(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        template = env.get_template("behaviors/memory_synthesis_def.cpp.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        definition: str = template.render(stage_name=stage["stage_name"], synthesis_config=config)
        return definition


StrategyRegistry.register(MemorySynthesisStrategy())
