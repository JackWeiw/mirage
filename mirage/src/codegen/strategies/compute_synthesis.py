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
        context = {
            "stage_name": stage["stage_name"],
            "synthesis_config": config,
        }
        content = template.render(**context)
        filename = f"{stage['stage_name']}.h"
        return filename, content


StrategyRegistry.register(ComputeSynthesisStrategy())
