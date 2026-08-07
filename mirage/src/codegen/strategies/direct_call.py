"""Direct call wrapper behavior strategy."""

from typing import Any

import jinja2

from codegen.strategies.base import BehaviorStrategy, StrategyRegistry


class DirectCallStrategy(BehaviorStrategy):
    def strategy_name(self) -> str:
        return "direct_call"

    def render(self, stage: dict[str, Any], env: jinja2.Environment) -> tuple[str, str]:
        template = env.get_template("behaviors/direct_call_wrapper.cpp.j2")
        strat = stage.get("strategies", [{}])[0]
        context = {
            "stage_name": stage["stage_name"],
            "function": strat.get("function", "unknown"),
            "library": strat.get("library", "unknown"),
            "dep_headers": stage.get("dep_headers", []),
            "call_statement": stage.get("call_statement", "/* direct call placeholder */"),
        }
        content = template.render(**context)
        filename = f"{stage['stage_name']}.h"
        return filename, content


StrategyRegistry.register(DirectCallStrategy())
