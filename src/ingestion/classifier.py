"""Config-driven function source classification."""

import pathlib
import re

import yaml


class LibraryRule:
    """One library's classification rule."""

    name: str
    namespace_patterns: list[re.Pattern[str]]
    header_patterns: list[re.Pattern[str]]

    def __init__(
        self, name: str, namespace_patterns: list[str], header_patterns: list[str]
    ) -> None:
        self.name = name
        self.namespace_patterns = [re.compile(p) for p in namespace_patterns]
        self.header_patterns = [re.compile(p) for p in header_patterns]

    def matches(self, function_name: str) -> bool:
        """Check if a function name matches any of this library's patterns."""
        return any(
            p.search(function_name) for p in (*self.namespace_patterns, *self.header_patterns)
        )


class FunctionClassifier:
    """Classify function names as open_source or customer_custom based on YAML config.

    Args:
        config_path: Path to open_source_libraries.yaml. If None, uses default.
    """

    def __init__(self, config_path: pathlib.Path | None = None) -> None:
        if config_path is None:
            config_path = (
                pathlib.Path(__file__).parent.parent / "config" / "open_source_libraries.yaml"
            )

        with open(config_path) as f:
            data = yaml.safe_load(f)

        self.rules: list[LibraryRule] = [
            LibraryRule(
                name=lib["name"],
                namespace_patterns=lib.get("namespace_patterns", []),
                header_patterns=lib.get("header_patterns", []),
            )
            for lib in data.get("libraries", [])
        ]
        self.default_classification: str = data.get("default_classification", "customer_custom")
        self.default_library: str = data.get("default_library", "custom")

    def classify(self, function_name: str) -> tuple[str, str]:
        """Classify a function as open_source or customer_custom and identify its library.

        Args:
            function_name: C++ function name (e.g., "folly::futures::detail::FutureImpl::then").

        Returns:
            (source, library) tuple where source is "open_source" or "customer_custom".
        """
        for rule in self.rules:
            if rule.matches(function_name):
                return "open_source", rule.name
        return self.default_classification, self.default_library
