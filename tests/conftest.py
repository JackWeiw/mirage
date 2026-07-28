"""Shared test fixtures."""

import pathlib
from typing import Any

DATA_DIR = pathlib.Path(__file__).parent / "data"


def pytest_configure(config: Any) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks integration tests requiring external tools"
    )
