"""Mixed behavior strategy — combines compute + memory + direct call."""

from typing import Any

import jinja2

from codegen.strategies.base import BehaviorStrategy, StrategyRegistry


class MixedStrategy(BehaviorStrategy):
    def strategy_name(self) -> str:
        return "mixed"

    def render(self, stage: dict[str, Any], env: jinja2.Environment) -> tuple[str, str]:
        sections: list[str] = []
        for strat in stage.get("strategies", []):
            if strat.get("strategy") == "compute_synthesis":
                tmpl = env.get_template("behaviors/compute_synthesis.cpp.j2")
                sections.append(
                    tmpl.render(
                        stage_name=stage["stage_name"],
                        synthesis_config=strat.get("synthesis_config", {}),
                    )
                )
            elif strat.get("strategy") == "memory_synthesis":
                tmpl = env.get_template("behaviors/memory_synthesis.cpp.j2")
                sections.append(
                    tmpl.render(
                        stage_name=stage["stage_name"],
                        synthesis_config=strat.get("synthesis_config", {}),
                    )
                )
            elif strat.get("strategy") == "direct_call":
                tmpl = env.get_template("behaviors/direct_call_wrapper.cpp.j2")
                sections.append(
                    tmpl.render(
                        stage_name=stage["stage_name"],
                        function=strat.get("function", "unknown"),
                        library=strat.get("library", "unknown"),
                        dep_headers=stage.get("dep_headers", []),
                        call_statement=stage.get("call_statement", "/* direct call */"),
                    )
                )

        filename = f"{stage['stage_name']}.h"
        content = (
            f"// Mixed stage: {stage['stage_name']}\n#pragma once\n\n"
            + "\n\n".join(sections)
            + "\n"
        )
        return filename, content


StrategyRegistry.register(MixedStrategy())
