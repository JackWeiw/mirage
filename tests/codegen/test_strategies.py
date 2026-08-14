"""Tests for individual strategy classes."""

import importlib
import pathlib
import sys
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

import jinja2
import pytest

from codegen.behavior_gen import discover_strategies
from codegen.strategies.base import StrategyRegistry
from codegen.strategies.compute_synthesis import ComputeSynthesisStrategy
from codegen.strategies.direct_call import DirectCallStrategy
from codegen.strategies.memory_synthesis import MemorySynthesisStrategy
from codegen.strategies.mixed import MixedStrategy


def _make_env() -> jinja2.Environment:
    template_dir = pathlib.Path(__file__).parent.parent.parent / "src" / "codegen" / "templates"
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
    )


def test_compute_synthesis_strategy_name() -> None:
    assert ComputeSynthesisStrategy().strategy_name() == "compute_synthesis"


def test_memory_synthesis_strategy_name() -> None:
    assert MemorySynthesisStrategy().strategy_name() == "memory_synthesis"


def test_direct_call_strategy_name() -> None:
    assert DirectCallStrategy().strategy_name() == "direct_call"


def test_mixed_strategy_name() -> None:
    assert MixedStrategy().strategy_name() == "mixed"


def test_compute_synthesis_render() -> None:
    env = _make_env()
    stage = {
        "stage_name": "calc_stage",
        "strategies": [
            {
                "strategy": "compute_synthesis",
                "synthesis_config": {"compute_type": "hash", "iterations": 500},
            },
        ],
    }
    filename, content = ComputeSynthesisStrategy().render(stage, env)
    assert filename == "calc_stage.h"
    assert "calc_stage_compute" in content
    assert "500" in content


def test_memory_synthesis_render() -> None:
    env = _make_env()
    stage = {
        "stage_name": "mem_stage",
        "strategies": [
            {
                "strategy": "memory_synthesis",
                "synthesis_config": {"access_pattern": "sequential", "working_set_mb": 128},
            },
        ],
    }
    filename, content = MemorySynthesisStrategy().render(stage, env)
    assert filename == "mem_stage.h"
    assert "mem_stage_memory" in content
    assert "128" in content


def test_direct_call_render() -> None:
    env = _make_env()
    stage = {
        "stage_name": "call_stage",
        "strategies": [
            {"strategy": "direct_call", "function": "my_func", "library": "mylib"},
        ],
        "dep_headers": ["mylib/my_func.h"],
        "call_statement": "my_func(42)",
    }
    filename, content = DirectCallStrategy().render(stage, env)
    assert filename == "call_stage.h"
    assert "call_stage_direct_call" in content
    assert "mylib/my_func.h" in content
    assert "my_func(42)" in content


def test_mixed_render() -> None:
    env = _make_env()
    stage = {
        "stage_name": "mix_stage",
        "strategies": [
            {
                "strategy": "compute_synthesis",
                "synthesis_config": {"compute_type": "sort", "iterations": 10},
            },
            {"strategy": "direct_call", "function": "foo", "library": "bar"},
        ],
        "dep_headers": ["bar/foo.h"],
        "call_statement": "foo()",
    }
    filename, content = MixedStrategy().render(stage, env)
    assert filename == "mix_stage.h"
    assert "mix_stage_compute" in content
    assert "mix_stage_direct_call" in content


def test_registry_available() -> None:
    # Ensure strategies are registered (they auto-register on import)
    names = StrategyRegistry.available()
    assert "compute_synthesis" in names
    assert "memory_synthesis" in names
    assert "direct_call" in names
    assert "mixed" in names


def test_registry_get_unknown() -> None:
    with pytest.raises(KeyError, match="Unknown behavior strategy"):
        StrategyRegistry.get("does_not_exist")


def test_render_decl_def_default_shim_concat_matches_render() -> None:
    """Default render_def delegates to render(); render_decl is empty."""
    env = _make_env()
    stage = {
        "stage_name": "s",
        "strategies": [
            {
                "strategy": "compute_synthesis",
                "synthesis_config": {"compute_type": "hash", "iterations": 7},
            },
        ],
    }
    _name, content = ComputeSynthesisStrategy().render(stage, env)
    decl = ComputeSynthesisStrategy().render_decl(stage, env)
    definition = ComputeSynthesisStrategy().render_def(stage, env)
    assert decl == ""  # default shim: no declarations split out yet
    assert definition == content  # default shim: render() content is the definition


# --- auto-discovery -----------------------------------------------------------

_FAKE_STRATEGY_SRC = textwrap.dedent(
    """
    import jinja2
    from codegen.strategies.base import BehaviorStrategy, StrategyRegistry

    class _AutoDiscoveredFake(BehaviorStrategy):
        def strategy_name(self) -> str:
            return "test_auto_discovered"

        def render(self, stage, env: jinja2.Environment) -> tuple[str, str]:
            return ("fake.h", "")

    StrategyRegistry.register(_AutoDiscoveredFake())
    """
)


@contextmanager
def _temp_package(
    tmp_path: pathlib.Path,
    pkg_name: str,
    modules: dict[str, str],
) -> Iterator[ModuleType]:
    """Build a throwaway package on sys.path and tear it down on exit."""
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    for mod_name, src in modules.items():
        (pkg_dir / f"{mod_name}.py").write_text(src)
    sys.path.insert(0, str(tmp_path))
    try:
        yield importlib.import_module(pkg_name)
    finally:
        sys.path.remove(str(tmp_path))
        for mod_name in list(sys.modules):
            if mod_name == pkg_name or mod_name.startswith(f"{pkg_name}."):
                del sys.modules[mod_name]
        StrategyRegistry._strategies.pop("test_auto_discovered", None)


def test_discover_strategies_registers_dropped_module(
    tmp_path: pathlib.Path,
) -> None:
    with _temp_package(tmp_path, "tmpstrat", {"fake_mod": _FAKE_STRATEGY_SRC}) as pkg:
        # Sanity: not present before discovery.
        assert "test_auto_discovered" not in StrategyRegistry.available()
        discover_strategies(pkg)
        assert "test_auto_discovered" in StrategyRegistry.available()


def test_discover_strategies_skips_private_module(tmp_path: pathlib.Path) -> None:
    with _temp_package(
        tmp_path,
        "tmpstrat_priv",
        {"_private": "raise RuntimeError('must not import')"},
    ) as pkg:
        discover_strategies(pkg)  # must not raise — _private is skipped


def test_discover_strategies_fails_loud_on_bad_module(
    tmp_path: pathlib.Path,
) -> None:
    with (
        _temp_package(tmp_path, "tmpstrat_bad", {"broken": "raise RuntimeError('boom')"}) as pkg,
        pytest.raises(RuntimeError, match="boom"),
    ):
        discover_strategies(pkg)
