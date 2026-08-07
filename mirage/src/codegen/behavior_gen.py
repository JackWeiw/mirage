"""Generate behavior implementation code using strategy registry."""

import pathlib
from typing import Any

import jinja2

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
