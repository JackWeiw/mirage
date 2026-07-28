"""Tests for BehaviorGenerator and StrategyRegistry."""

import pytest

from codegen.behavior_gen import BehaviorGenerator, auto_register
from codegen.strategies.base import StrategyRegistry


def test_strategy_registry_auto_registers() -> None:
    auto_register()
    assert "compute_synthesis" in StrategyRegistry.available()
    assert "memory_synthesis" in StrategyRegistry.available()
    assert "direct_call" in StrategyRegistry.available()
    assert "mixed" in StrategyRegistry.available()


def test_strategy_registry_unknown_raises() -> None:
    auto_register()
    with pytest.raises(KeyError, match="Unknown behavior strategy"):
        StrategyRegistry.get("nonexistent_strategy")


def test_behavior_gen_compute_synthesis() -> None:
    gen = BehaviorGenerator()
    stage = {
        "stage_name": "feature_calc",
        "implementation_strategy": "compute_synthesis",
        "strategies": [
            {
                "strategy": "compute_synthesis",
                "synthesis_config": {"compute_type": "hash", "iterations": 200},
            }
        ],
    }
    filename, content = gen.generate_stage_file(stage)
    assert filename == "feature_calc.h"
    assert "feature_calc_compute" in content
    assert "iterations" in content


def test_behavior_gen_memory_synthesis() -> None:
    gen = BehaviorGenerator()
    stage = {
        "stage_name": "data_lookup",
        "implementation_strategy": "memory_synthesis",
        "strategies": [
            {
                "strategy": "memory_synthesis",
                "synthesis_config": {"access_pattern": "random", "working_set_mb": 64},
            }
        ],
    }
    _filename, content = gen.generate_stage_file(stage)
    assert "data_lookup_memory" in content
    assert "random" in content


def test_behavior_gen_unknown_strategy_raises() -> None:
    gen = BehaviorGenerator()
    stage = {"stage_name": "test", "implementation_strategy": "unknown"}
    with pytest.raises(KeyError):
        gen.generate_stage_file(stage)


def test_behavior_gen_direct_call() -> None:
    gen = BehaviorGenerator()
    stage = {
        "stage_name": "future_then",
        "implementation_strategy": "direct_call",
        "strategies": [
            {
                "function": "folly::futures::Future::then",
                "library": "folly",
                "strategy": "direct_call",
            }
        ],
        "dep_headers": ["folly/futures/Future.h"],
        "call_statement": "folly::futures::Future<int>().then([](int x) { return x + 1; })",
    }
    _filename, content = gen.generate_stage_file(stage)
    assert "future_then_direct_call" in content
    assert "folly/futures/Future.h" in content


def test_behavior_gen_mixed() -> None:
    gen = BehaviorGenerator()
    stage = {
        "stage_name": "pipeline_merge",
        "implementation_strategy": "mixed",
        "strategies": [
            {
                "strategy": "compute_synthesis",
                "synthesis_config": {"compute_type": "sort", "iterations": 50},
            },
            {
                "strategy": "memory_synthesis",
                "synthesis_config": {"access_pattern": "sequential", "working_set_mb": 32},
            },
        ],
    }
    filename, content = gen.generate_stage_file(stage)
    assert filename == "pipeline_merge.h"
    assert "pipeline_merge_compute" in content
    assert "pipeline_merge_memory" in content
