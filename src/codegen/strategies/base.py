"""Behavior strategy base class and registry."""

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import jinja2


class BehaviorStrategy(ABC):
    """Base class for behavior implementation strategies.

    Each strategy knows how to select the right Jinja2 template,
    build the template context from a stage dict, and render the C++ code.
    """

    @abstractmethod
    def strategy_name(self) -> str:
        """Return the strategy identifier."""
        ...

    @abstractmethod
    def render(self, stage: dict[str, Any], env: jinja2.Environment) -> tuple[str, str]:
        """Render the C++ header file for this stage.

        Args:
            stage: Behavior profile dict for one stage.
            env: Jinja2 Environment with templates loaded.

        Returns:
            (filename, content) tuple.
        """
        ...

    def render_decl(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        """Return the public declarations for module.h.

        Default shim: the fused ``render()`` output is treated as the whole
        definition, so no declarations are split out. Strategies override this
        to emit header-side prototypes.
        """
        del env  # default shim ignores the environment
        _ = stage
        return ""

    def render_def(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        """Return the implementation for module.cpp.

        Default shim: delegate to ``render()`` — the content half of the
        ``(filename, content)`` tuple is the definition. Strategies override
        this to emit a decl/def split.
        """
        return self.render(stage, env)[1]


class StrategyRegistry:
    """Registry of behavior strategies. New strategies register themselves."""

    _strategies: ClassVar[dict[str, BehaviorStrategy]] = {}

    @classmethod
    def register(cls, strategy: BehaviorStrategy) -> None:
        """Register a strategy instance."""
        cls._strategies[strategy.strategy_name()] = strategy

    @classmethod
    def get(cls, name: str) -> BehaviorStrategy:
        """Get a strategy by name.

        Raises:
            KeyError: If strategy name is not registered.
        """
        if name not in cls._strategies:
            raise KeyError(
                f"Unknown behavior strategy: '{name}'. Registered: {list(cls._strategies.keys())}"
            )
        return cls._strategies[name]

    @classmethod
    def available(cls) -> list[str]:
        """List available strategy names."""
        return list(cls._strategies.keys())
