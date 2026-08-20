"""Pytest config: put examples/ + examples/scenarios/ on sys.path so test modules
can `import run_loop_demo` and `import collect_common` (mirrors steerability_spike's
sys.path prepend). Bare mirage packages (harness, config, ...) already resolve via
pyproject's `pythonpath=["src"]`."""

import pathlib
import sys

_EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"
sys.path.insert(0, str(_EXAMPLES))
sys.path.insert(0, str(_EXAMPLES / "scenarios"))
