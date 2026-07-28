"""Tests for individual strategy classes."""

import pathlib

import jinja2
import pytest

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
