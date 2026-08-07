"""Generate behavior implementation code using strategy registry."""

import pathlib
from typing import Any

import jinja2

from codegen.call_tree import SkeletonDescriptor, service_node_of
from codegen.skeleton_gen import sanitize_identifier
from codegen.strategies.base import StrategyRegistry


def auto_register() -> None:
    """Import all strategy sub-modules to trigger their registration."""
    from codegen.strategies.compute_synthesis import ComputeSynthesisStrategy  # noqa: F401
    from codegen.strategies.direct_call import DirectCallStrategy  # noqa: F401
    from codegen.strategies.memory_synthesis import MemorySynthesisStrategy  # noqa: F401
    from codegen.strategies.mixed import MixedStrategy  # noqa: F401


class BehaviorGenerator:
    """Generate Layer 3 behavior implementation code from Behavior Profiles.

    Uses StrategyRegistry to dispatch to the correct strategy implementation.
    New strategies can be added without modifying this class.
    """

    def __init__(self) -> None:
        auto_register()
        template_dir = pathlib.Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

    def generate_stage_file(self, stage: dict[str, Any]) -> tuple[str, str]:
        """Generate a C++ header file for one workflow stage using the registry.

        Raises:
            KeyError: If implementation_strategy is not registered.
        """
        strategy_name = stage["implementation_strategy"]
        strategy = StrategyRegistry.get(strategy_name)
        return strategy.render(stage, self._env)

    def generate_synth_header(
        self, stage_name: str, archetype: str, units: int, working_set_mb: int
    ) -> tuple[str, str]:
        """Render a noinline custom_synth() header for one collapsed custom subtree."""
        template = self._env.get_template("behaviors/custom_synth.cpp.j2")
        content = template.render(
            stage_name=stage_name,
            archetype=archetype,
            iterations=max(1, units or 100),
            working_set_mb=working_set_mb or 64,
        )
        return f"{stage_name}_synth.h", content

    def generate_synth_headers(
        self, desc: SkeletonDescriptor, output_dir: pathlib.Path
    ) -> list[str]:
        """Write a custom_synth header per stage that has a collapsed custom subtree."""
        files: list[str] = []
        service = service_node_of(desc.root)
        working_set_mb = int(desc.config.get("working_set_mb", 64))
        for stage in service.children:
            synth = next((c for c in stage.children if c.node_kind == "custom_synth"), None)
            if synth is None:
                continue
            method = sanitize_identifier(stage.function)
            filename, content = self.generate_synth_header(
                method, synth.self_work.archetype, synth.self_work.units, working_set_mb
            )
            (output_dir / filename).write_text(content)
            files.append(filename)
        return files
