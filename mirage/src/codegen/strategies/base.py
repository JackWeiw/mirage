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
