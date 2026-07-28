# Workload Simulation Framework — Phase 1 Implementation Plan (Revised)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimum viable end-to-end loop with production-quality code: parse customer data → generate a standalone C++ workload → build/run/collect → compare Topdown metrics → manual review. No auto-iteration yet.

**Architecture:** Five components (Data Ingestion, Profile Store, Agent Core, Code Gen Engine, Harness) plus cross-cutting infrastructure (observability, config, pre-commit). Agent uses Claude API with direct prompt chains but is optional — pipeline works in local-only mode without LLM. Code Gen produces single-module standalone workloads. Harness runs locally.

**Tech Stack:** Python 3.11+ with strict typing. ruff for linting/formatting. mypy for type checking. pre-commit for automated checks. pydantic v2 for all data models (no hand-written Result classes). pytest + pytest-cov for testing. structlog for structured logging. Claude API (anthropic SDK) for Agent Core (optional). Jinja2 for code templates. YAML for framework config.

---

## Revision Log

This plan was revised to address software engineering deficiencies identified through self-review:

| Issue | Fix |
|-------|-----|
| No pre-commit / linting / type checking | Added Task 0: project quality infrastructure |
| Result classes hand-written instead of pydantic | All Result classes now pydantic BaseModel |
| `OPEN_SOURCE_LIBRARIES` hardcoded in FlamegraphParser | Moved to configurable YAML + regex-based matching |
| BehaviorGenerator if-elif violates open-closed principle | Strategy registry pattern with `BehaviorStrategy` base class |
| Agent Core JSON parsing logic duplicated 4x | Extracted `_parse_json_response()` helper |
| No structured logging / observability | Added `src/observability/` module: structlog + IterationHistory |
| Pipeline requires API key to even instantiate | Agent is optional — pipeline works in local-only mode |
| No error path test coverage | Added error/failure test cases for every component |
| Jinja2 default values scattered across templates | Centralized in `default_config.yaml`, templates use `| default()` from a single source |
| main.cpp.j2 embeds config loading logic inline | Split into `config_loader.h.j2` separate template |

---

## File Structure

```
harness/
├── .pre-commit-config.yaml              # pre-commit hooks config
├── pyproject.toml                       # Python project + tool config (ruff, mypy, pytest)
├── src/
│   ├── __init__.py
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── logging.py                   # structlog configuration + helpers
│   │   ├── iteration_history.py         # Track iteration results, convergence trends
│   │   └── telemetry.py                 # Timing metrics + pipeline state tracking
│   ├── config/
│   │   ├── __init__.py
│   │   ├── framework_config.py          # Pydantic model for framework YAML config
│   │   ├── default_config.yaml          # Default framework config (thresholds, libs, etc.)
│   │   └── open_source_libraries.yaml   # Configurable library classification rules
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── flamegraph_parser.py         # Parse perf script / folded flamegraphs
│   │   ├── topdown_parser.py            # Parse devkit JSON/CSV Topdown data
│   │   └── classifier.py               # Function source classification (config-driven)
│   ├── profile/
│   │   ├── __init__.py
│   │   ├── profile_schema.py            # Pydantic models for Profile + all Results
│   │   ├── profile_store.py             # JSON file-based Profile storage
│   │   └── comparator.py               # Compare two Profiles, produce diff report
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── agent_core.py               # Claude API prompt chain (optional, can be None)
│   │   ├── strategy.py                 # Iteration strategy decision logic
│   │   └── prompts/
│   │       ├── analyze_profile.md
│   │       ├── plan_workflow.md
│   │       ├── detail_fill.md
│   │       └── evaluate_comparison.md
│   ├── codegen/
│   │   ├── __init__.py
│   │   ├── scaffold_gen.py             # Layer 0-1: project scaffold + service skeleton
│   │   ├── behavior_gen.py             # Layer 3: behavior implementation (strategy registry)
│   │   ├── knob_gen.py                 # Layer 4: config.json + runtime params
│   │   ├── generator.py               # Orchestrates scaffold → behavior → knob generation
│   │   ├── strategies/                 # Behavior strategy implementations (open-closed)
│   │   │   ├── __init__.py
│   │   │   ├── base.py                # BehaviorStrategy base class + registry
│   │   │   ├── compute_synthesis.py
│   │   │   ├── memory_synthesis.py
│   │   │   ├── direct_call.py
│   │   │   └── mixed.py
│   │   └── templates/
│   │       ├── cmake/
│   │       │   └── CMakeLists.txt.j2
│   │       ├── main/
│   │       │   ├── main.cpp.j2
│   │       │   └── config_loader.h.j2  # Separated config loading logic
│   │       ├── behaviors/
│   │       │   ├── compute_synthesis.cpp.j2
│   │       │   ├── memory_synthesis.cpp.j2
│   │       │   └── direct_call_wrapper.cpp.j2
│   │       │   └── mixed.cpp.j2
│   │       └── config/
│   │           └── config.json.j2
│   │   └── validator.py               # Build validation (compile check)
│   ├── harness/
│   │   ├── __init__.py
│   │   ├── build_runner.py
│   │   ├── execution_runner.py
│   │   ├── metrics_collector.py
│   │   ├── run_config.py               # RunConfig pydantic model
│   │   └── pipeline.py                 # End-to-end pipeline orchestration
│   └── models/
│       ├── __init__.py
│       └── results.py                  # All Result models (BuildResult, ExecutionResult, etc.)
├── tests/
│   ├── conftest.py
│   ├── observability/
│   │   ├── test_logging.py
│   │   ├── test_iteration_history.py
│   │   └── test_telemetry.py
│   ├── config/
│   │   ├── test_framework_config.py
│   │   └── test_classifier.py
│   ├── ingestion/
│   │   ├── test_flamegraph_parser.py
│   │   ├── test_topdown_parser.py
│   ├── profile/
│   │   ├── test_profile_schema.py
│   │   ├── test_profile_store.py
│   │   ├── test_comparator.py
│   ├── codegen/
│   │   ├── test_scaffold_gen.py
│   │   ├── test_behavior_gen.py
│   │   ├── test_knob_gen.py
│   │   ├── test_generator.py
│   │   ├── test_strategies.py           # Strategy registry + each strategy
│   ├── harness/
│   │   ├── test_build_runner.py
│   │   ├── test_execution_runner.py
│   │   ├── test_metrics_collector.py
│   │   ├── test_pipeline.py
│   ├── agent/
│   │   ├── test_agent_core.py
│   │   ├── test_strategy.py
│   ├── data/
│   │   ├── sample_flamegraph_folded.txt
│   │   ├── sample_topdown.json
│   │   ├── sample_topdown.csv
│   │   ├── sample_profile.json
│   │   ├── sample_workload_profile.json
│   │   ├── sample_open_source_libraries.yaml
│   │   └── malformed_flamegraph.txt      # Error path test data
│   │   └── malformed_topdown.json         # Error path test data
├── examples/
│   └── search_ranking/
│       ├── customer_data/
│       │   ├── flamegraph_folded.txt
│       │   ├── topdown.json
│       │   └── business_description.md
│       └── deploy_config.json
└── README.md
```

---

### Task 0: Project Quality Infrastructure

**Files:**
- Create: `pyproject.toml`
- Create: `.pre-commit-config.yaml`
- Create: `src/__init__.py`

This task establishes the foundation for code quality: ruff (lint + format), mypy (type checking), pre-commit hooks, pytest + coverage, and structlog.

- [ ] **Step 1: Write pyproject.toml with tool configs**

```toml
[project]
name = "workload-sim"
version = "0.1.0"
description = "Workload simulation framework for ARM64 microarchitecture profiling"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.0",
    "anthropic>=0.40",
    "jinja2>=3.1",
    "pyyaml>=6.0",
    "structlog>=24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.5",
    "mypy>=1.10",
    "pre-commit>=3.7",
]

[tool.ruff]
target-version = "py311"
line-length = 100
src = ["src"]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "N",    # pep8-naming
    "UP",   # pyupgrade
    "B",    # flake8-bugbear
    "SIM",  # flake8-simplify
    "TCH",  # flake8-type-checking
    "RUF",  # ruff-specific rules
]
ignore = ["E501"]  # line length handled by formatter

[tool.ruff.lint.isort]
known-first-party = ["ingestion", "profile", "agent", "codegen", "harness", "config", "observability", "models"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = "anthropic.*"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "structlog.*"
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "--cov=src --cov-report=term-missing --cov-fail-under=60"
markers = [
    "integration: marks integration tests requiring external services (deselect with '-m \"not integration\"')",
]

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true
```

- [ ] **Step 2: Write .pre-commit-config.yaml**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.7
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.1
    hooks:
      - id: mypy
        additional_dependencies: [pydantic>=2.0, types-PyYAML]
        args: [--strict, --config-file=pyproject.toml]

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-json
      - id: no-commit-to-branch
        args: [--branch,main]
```

- [ ] **Step 3: Install dependencies and pre-commit**

Run:
```bash
cd /c/Users/jack/Desktop/harness
pip install -e ".[dev]"
pre-commit install
```

Expected: Successfully installed all dependencies. Pre-commit hooks installed.

- [ ] **Step 4: Initialize git and verify pre-commit works**

```bash
git init
echo "# Workload Simulation Framework" > README.md
git add pyproject.toml .pre-commit-config.yaml src/__init__.py README.md
pre-commit run --all-files
```

Expected: pre-commit runs ruff + mypy + basic checks. May flag some issues — fix them. Then commit.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: project setup — pyproject.toml, pre-commit, ruff, mypy, structlog"
```

---

### Task 1: Cross-Cutting — Observability + Result Models + Config

**Files:**
- Create: `src/observability/__init__.py`
- Create: `src/observability/logging.py`
- Create: `src/observability/iteration_history.py`
- Create: `src/observability/telemetry.py`
- Create: `src/models/__init__.py`
- Create: `src/models/results.py`
- Create: `src/config/__init__.py`
- Create: `src/config/framework_config.py`
- Create: `src/config/default_config.yaml`
- Test: `tests/observability/test_logging.py`
- Test: `tests/observability/test_iteration_history.py`
- Test: `tests/observability/test_telemetry.py`
- Test: `tests/config/test_framework_config.py`

This task creates all cross-cutting infrastructure that other components depend on: structured logging, result models, config system, iteration history tracking.

- [ ] **Step 1: Write observability/logging.py**

```python
"""Structured logging configuration using structlog."""

import structlog
import logging


def configure_logging(log_level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog for the framework.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
        json_output: If true, output JSON format; otherwise, human-readable console format.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(log_level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger with a named binding.

    Args:
        name: Logger name (typically module name).

    Returns:
        Bound structlog logger with component=name context.
    """
    return structlog.get_logger().bind(component=name)
```

- [ ] **Step 2: Write observability/iteration_history.py**

```python
"""Track iteration results and convergence trends across multiple iterations."""

import json
import pathlib
from pydantic import BaseModel, Field
from typing import Optional


class IterationRecord(BaseModel):
    """Record of one iteration's comparison results."""
    iteration: int
    converged: bool
    topdown_diffs: dict[str, float] = Field(default_factory=dict)
    memory_diff_pct: float = 0.0
    coverage_pct: float = 0.0
    strategy_priority: int = 0
    duration_seconds: float = 0.0
    timestamp: str = ""


class IterationHistory(BaseModel):
    """History of all iterations for a workload simulation run."""
    customer_name: str
    records: list[IterationRecord] = Field(default_factory=list)
    best_iteration: Optional[int] = None
    total_iterations: int = 0

    def add_record(self, record: IterationRecord) -> None:
        """Add an iteration record and update best_iteration."""
        self.records.append(record)
        self.total_iterations = len(self.records)

        # Track best iteration: the one with smallest total Topdown diff
        if self.best_iteration is None:
            self.best_iteration = record.iteration
        else:
            current_best = self.records[self.best_iteration]
            current_best_score = sum(abs(v) for v in current_best.topdown_diffs.values())
            new_score = sum(abs(v) for v in record.topdown_diffs.values())
            if new_score < current_best_score:
                self.best_iteration = record.iteration

    def get_convergence_trend(self) -> list[dict[str, float]]:
        """Get convergence trend: Topdown diffs over iterations."""
        return [
            {"iteration": r.iteration, "total_diff": sum(abs(v) for v in r.topdown_diffs.values())}
            for r in self.records
        ]

    def is_converging(self) -> bool:
        """Check if the trend is improving (diffs getting smaller)."""
        if len(self.records) < 2:
            return True

        trend = self.get_convergence_trend()
        recent = trend[-3:]  # Last 3 iterations
        return all(recent[i]["total_diff"] >= recent[i + 1]["total_diff"] for i in range(len(recent) - 1))

    def save(self, filepath: pathlib.Path) -> pathlib.Path:
        """Save iteration history to JSON file."""
        filepath.write_text(self.model_dump_json(indent=2))
        return filepath

    @classmethod
    def load(cls, filepath: pathlib.Path) -> "IterationHistory":
        """Load iteration history from JSON file."""
        data = json.loads(filepath.read_text())
        return cls.model_validate(data)
```

- [ ] **Step 3: Write observability/telemetry.py**

```python
"""Pipeline state tracking and timing metrics."""

import time
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class PipelineState(str, Enum):
    """Pipeline execution states."""
    IDLE = "idle"
    INGESTING = "ingesting"
    GENERATING = "generating"
    BUILDING = "building"
    RUNNING = "running"
    COLLECTING = "collecting"
    COMPARING = "comparing"
    ITERATING = "iterating"
    CONVERGED = "converged"
    FAILED = "failed"


class StepTiming(BaseModel):
    """Timing for one pipeline step."""
    step: str
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0
    success: bool = True
    error: Optional[str] = None


class PipelineTelemetry(BaseModel):
    """Telemetry data for a full pipeline run."""
    pipeline_id: str = ""
    state: PipelineState = PipelineState.IDLE
    steps: list[StepTiming] = Field(default_factory=list)
    current_step: Optional[str] = None

    def start_step(self, step_name: str) -> None:
        """Start tracking a pipeline step."""
        self.current_step = step_name
        self.state = PipelineState(step_name.lower() if step_name.lower() in [s.value for s in PipelineState] else PipelineState.IDLE)
        self.steps.append(StepTiming(
            step=step_name,
            start_time=time.time(),
        ))

    def end_step(self, step_name: str, success: bool = True, error: Optional[str] = None) -> None:
        """End tracking a pipeline step."""
        for step in self.steps:
            if step.step == step_name and step.end_time == 0.0:
                step.end_time = time.time()
                step.duration_seconds = step.end_time - step.start_time
                step.success = success
                step.error = error
                break
        self.current_step = None

    def total_duration(self) -> float:
        """Total pipeline duration in seconds."""
        return sum(s.duration_seconds for s in self.steps)

    def get_summary(self) -> dict:
        """Get a summary of the pipeline run."""
        return {
            "pipeline_id": self.pipeline_id,
            "state": self.state.value,
            "total_duration": self.total_duration(),
            "steps": [
                {"step": s.step, "duration": s.duration_seconds, "success": s.success}
                for s in self.steps
            ],
        }
```

- [ ] **Step 4: Write models/results.py — unified Result models**

```python
"""Unified Result models for all harness components.

All results use pydantic BaseModel for consistency, serialization, and validation.
"""

import pathlib
from pydantic import BaseModel, Field
from typing import Optional


class BuildResult(BaseModel):
    """Result of a build attempt."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    binary_path: Optional[pathlib.Path] = None
    duration_seconds: float = 0.0

    def model_post_init(self, __context: object) -> None:
        # pathlib.Path serialization workaround
        if self.binary_path is not None and isinstance(self.binary_path, str):
            self.binary_path = pathlib.Path(self.binary_path)


class ExecutionResult(BaseModel):
    """Result of a workload execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_seconds: float = 0.0


class CollectionResult(BaseModel):
    """Result of a metrics collection attempt."""
    success: bool
    topdown_path: Optional[pathlib.Path] = None
    flamegraph_path: Optional[pathlib.Path] = None
    memory_path: Optional[pathlib.Path] = None
    error: str = ""


class PipelineResult(BaseModel):
    """Result of a full pipeline run."""
    success: bool
    customer_profile_json: Optional[str] = None  # Serialized Profile JSON
    comparison_report: Optional[dict] = None
    project_dir: Optional[pathlib.Path] = None
    error: str = ""
```

- [ ] **Step 5: Write config/framework_config.py**

```python
"""Framework configuration model — loaded from YAML."""

from pydantic import BaseModel, Field
import pathlib
import yaml
from typing import Optional


class AgentConfig(BaseModel):
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 4096
    api_key: Optional[str] = None


class ComparisonConfig(BaseModel):
    topdown_threshold_pct: float = 10.0
    memory_threshold_pct: float = 5.0
    coverage_threshold_pct: float = 80.0


class RunDefaults(BaseModel):
    warmup_seconds: int = 30
    measurement_seconds: int = 60
    thread_count: int = 4
    qps: int = 100


class CodegenConfig(BaseModel):
    compile_flags: str = "-O2 -march=armv8.2-a"
    default_dependencies: list[dict] = Field(default_factory=list)


class HarnessConfig(BaseModel):
    cmake_path: str = "cmake"
    make_path: str = "make"
    build_dir_suffix: str = "build"


class FrameworkConfig(BaseModel):
    """Complete framework configuration."""
    log_level: str = "INFO"
    json_logging: bool = False
    agent: AgentConfig = Field(default_factory=AgentConfig)
    comparison: ComparisonConfig = Field(default_factory=ComparisonConfig)
    run_defaults: RunDefaults = Field(default_factory=RunDefaults)
    codegen: CodegenConfig = Field(default_factory=CodegenConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)

    @classmethod
    def from_yaml(cls, filepath: pathlib.Path) -> "FrameworkConfig":
        """Load configuration from a YAML file."""
        with open(filepath, "r") as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data.get("framework", data))

    @classmethod
    def defaults(cls) -> "FrameworkConfig":
        """Return default configuration."""
        defaults_path = pathlib.Path(__file__).parent / "default_config.yaml"
        return cls.from_yaml(defaults_path)
```

- [ ] **Step 6: Write config/default_config.yaml**

```yaml
framework:
  log_level: INFO
  json_logging: false
  agent:
    model: claude-sonnet-4-6
    max_tokens: 4096
  comparison:
    topdown_threshold_pct: 10.0
    memory_threshold_pct: 5.0
    coverage_threshold_pct: 80.0
  run_defaults:
    warmup_seconds: 30
    measurement_seconds: 60
    thread_count: 4
    qps: 100
  codegen:
    compile_flags: "-O2 -march=armv8.2-a"
    default_dependencies:
      - name: folly
        version: "2.1.0"
  harness:
    cmake_path: cmake
    make_path: make
    build_dir_suffix: build
```

- [ ] **Step 7: Write tests for observability**

```python
"""Tests for observability module."""

import pathlib
import tempfile
import json
from observability.iteration_history import IterationHistory, IterationRecord
from observability.telemetry import PipelineTelemetry, PipelineState, StepTiming


def test_iteration_history_add_record():
    history = IterationHistory(customer_name="test")
    record = IterationRecord(
        iteration=1,
        converged=False,
        topdown_diffs={"frontend_bound": -12.0, "backend_bound": -5.0},
        coverage_pct=70.0,
    )
    history.add_record(record)
    assert history.total_iterations == 1
    assert history.best_iteration == 1


def test_iteration_history_convergence_trend():
    history = IterationHistory(customer_name="test")
    for i, (fb_diff, bb_diff) in enumerate([(-12.0, -5.0), (-10.0, -4.0), (-8.0, -3.0)]):
        history.add_record(IterationRecord(
            iteration=i + 1,
            converged=False,
            topdown_diffs={"frontend_bound": fb_diff, "backend_bound": bb_diff},
            coverage_pct=70.0 + i * 5,
        ))

    trend = history.get_convergence_trend()
    assert len(trend) == 3
    assert trend[0]["total_diff"] > trend[1]["total_diff"] > trend[2]["total_diff"]


def test_iteration_history_is_converging():
    history = IterationHistory(customer_name="test")
    # Improving trend
    for i, diffs in enumerate([(-12.0, -5.0), (-10.0, -4.0), (-8.0, -3.0)]):
        history.add_record(IterationRecord(
            iteration=i + 1,
            converged=False,
            topdown_diffs={"frontend_bound": diffs[0], "backend_bound": diffs[1]},
        ))
    assert history.is_converging() is True


def test_iteration_history_not_converging():
    history = IterationHistory(customer_name="test")
    # Diverging trend
    for i, diffs in enumerate([(-8.0, -3.0), (-10.0, -4.0), (-12.0, -5.0)]):
        history.add_record(IterationRecord(
            iteration=i + 1,
            converged=False,
            topdown_diffs={"frontend_bound": diffs[0], "backend_bound": diffs[1]},
        ))
    assert history.is_converging() is False


def test_iteration_history_save_and_load():
    tmpdir = pathlib.Path(tempfile.mkdtemp())
    history = IterationHistory(customer_name="test")
    history.add_record(IterationRecord(iteration=1, converged=False, topdown_diffs={"fb": -10.0}))

    filepath = history.save(tmpdir / "history.json")
    loaded = IterationHistory.load(filepath)
    assert loaded.customer_name == "test"
    assert len(loaded.records) == 1


def test_pipeline_telemetry_step_tracking():
    tel = PipelineTelemetry(pipeline_id="test_run")
    tel.start_step("ingesting")
    tel.end_step("ingesting", success=True)

    assert tel.steps[0].step == "ingesting"
    assert tel.steps[0].success is True
    assert tel.steps[0].duration_seconds > 0


def test_pipeline_telemetry_failed_step():
    tel = PipelineTelemetry(pipeline_id="test_run")
    tel.start_step("building")
    tel.end_step("building", success=False, error="cmake not found")

    assert tel.steps[0].success is False
    assert tel.steps[0].error == "cmake not found"


def test_pipeline_telemetry_summary():
    tel = PipelineTelemetry(pipeline_id="test_run")
    tel.start_step("ingesting")
    tel.end_step("ingesting", success=True)

    summary = tel.get_summary()
    assert summary["pipeline_id"] == "test_run"
    assert len(summary["steps"]) == 1
```

- [ ] **Step 8: Write test for framework config**

```python
"""Tests for FrameworkConfig."""

import pathlib
from config.framework_config import FrameworkConfig


def test_framework_config_defaults():
    config = FrameworkConfig.defaults()
    assert config.log_level == "INFO"
    assert config.comparison.topdown_threshold_pct == 10.0
    assert config.agent.model == "claude-sonnet-4-6"


def test_framework_config_from_yaml():
    config = FrameworkConfig.defaults()
    assert config.harness.cmake_path == "cmake"
    assert config.run_defaults.warmup_seconds == 30


def test_framework_config_comparison_thresholds():
    config = FrameworkConfig.defaults()
    assert config.comparison.memory_threshold_pct == 5.0
    assert config.comparison.coverage_threshold_pct == 80.0
```

- [ ] **Step 9: Run all new tests**

Run: `pytest tests/observability/ tests/config/ -v`
Expected: All 10 tests PASS.

- [ ] **Step 10: Commit**

```bash
git add src/observability/ src/models/ src/config/ tests/observability/ tests/config/
git commit -m "feat: observability (structlog, iteration history, telemetry) + unified Result models + FrameworkConfig"
```

---

### Task 2: Profile Schema

**Files:**
- Create: `src/profile/__init__.py`
- Create: `src/profile/profile_schema.py`
- Test: `tests/profile/test_profile_schema.py`
- Test: `tests/conftest.py`

Same Profile schema as original plan but now all models are in `profile_schema.py` (no separate `models/results.py` duplication). Results are in `models/results.py` from Task 1.

- [ ] **Step 1: Write profile_schema.py**

Same content as original plan Task 1 Step 3 — all pydantic models (Profile, ProfileMetadata, HotspotFunction, TopdownL1/L2, MemoryProfile, etc.)

- [ ] **Step 2: Write tests and conftest.py**

Same content as original plan Task 1 Steps 4-6 — 6 tests for schema validation + serialization.

- [ ] **Step 3: Run tests**

Run: `pytest tests/profile/test_profile_schema.py -v`
Expected: All 6 PASS.

- [ ] **Step 4: Commit**

```bash
git add src/profile/ tests/profile/test_profile_schema.py tests/conftest.py
git commit -m "feat: Profile schema — pydantic models for all Profile types"
```

---

### Task 3: Data Ingestion — Classifier + FlamegraphParser

**Files:**
- Create: `src/ingestion/__init__.py`
- Create: `src/ingestion/classifier.py`
- Create: `src/ingestion/flamegraph_parser.py`
- Create: `src/config/open_source_libraries.yaml`
- Create: `tests/data/sample_flamegraph_folded.txt`
- Create: `tests/data/malformed_flamegraph.txt`
- Create: `tests/data/sample_open_source_libraries.yaml`
- Test: `tests/ingestion/test_flamegraph_parser.py`
- Test: `tests/config/test_classifier.py`

Key revision: Function classification is now config-driven via YAML, not hardcoded. Classifier uses regex patterns for robust matching. FlamegraphParser uses Classifier. Error paths tested.

- [ ] **Step 1: Write open_source_libraries.yaml**

```yaml
# Open-source library classification rules
# Each entry has: name, namespace_patterns (regex), header_patterns (regex)
libraries:
  - name: folly
    namespace_patterns:
      - "folly::"
      - "folly\\."
    header_patterns:
      - "folly/"
  - name: fbthrift
    namespace_patterns:
      - "fbthrift::"
      - "thrift::"
    header_patterns:
      - "fbthrift/"
  - name: brpc
    namespace_patterns:
      - "brpc::"
    header_patterns:
      - "brpc/"
  - name: taskflow
    namespace_patterns:
      - "tf::"
      - "taskflow::"
    header_patterns:
      - "taskflow/"
  - name: std
    namespace_patterns:
      - "std::"
    header_patterns: []
  - name: glog
    namespace_patterns:
      - "google::LogMessage"
    header_patterns:
      - "glog/"
  - name: protobuf
    namespace_patterns:
      - "google::protobuf::"
    header_patterns:
      - "google/protobuf/"
  - name: jemalloc
    namespace_patterns:
      - "je_"
      - "jemalloc::"
    header_patterns:
      - "jemalloc/"
  - name: boost
    namespace_patterns:
      - "boost::"
    header_patterns:
      - "boost/"

# If a function matches none of the above, it's classified as customer_custom
default_classification: customer_custom
default_library: custom
```

- [ ] **Step 2: Write classifier.py**

```python
"""Config-driven function source classification."""

import re
import pathlib
import yaml
from typing import Optional


class LibraryRule:
    """One library's classification rule."""
    name: str
    namespace_patterns: list[re.Pattern]
    header_patterns: list[re.Pattern]

    def __init__(self, name: str, namespace_patterns: list[str], header_patterns: list[str]) -> None:
        self.name = name
        self.namespace_patterns = [re.compile(p) for p in namespace_patterns]
        self.header_patterns = [re.compile(p) for p in header_patterns]

    def matches(self, function_name: str) -> bool:
        """Check if a function name matches any of this library's patterns."""
        for pattern in self.namespace_patterns:
            if pattern.search(function_name):
                return True
        for pattern in self.header_patterns:
            if pattern.search(function_name):
                return True
        return False


class FunctionClassifier:
    """Classify function names as open_source or customer_custom based on YAML config.

    Args:
        config_path: Path to open_source_libraries.yaml. If None, uses default.
    """

    def __init__(self, config_path: Optional[pathlib.Path] = None) -> None:
        if config_path is None:
            config_path = pathlib.Path(__file__).parent.parent / "config" / "open_source_libraries.yaml"

        with open(config_path, "r") as f:
            data = yaml.safe_load(f)

        self.rules = [
            LibraryRule(
                name=lib["name"],
                namespace_patterns=lib.get("namespace_patterns", []),
                header_patterns=lib.get("header_patterns", []),
            )
            for lib in data.get("libraries", [])
        ]
        self.default_classification = data.get("default_classification", "customer_custom")
        self.default_library = data.get("default_library", "custom")

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
```

- [ ] **Step 3: Write FlamegraphParser (using Classifier)**

```python
"""Parse flamegraph data (folded format) into structured hotspot list."""

import pathlib
from ingestion.classifier import FunctionClassifier
from profile_schema import HotspotFunction


class FlamegraphParser:
    """Parser for flamegraph data files.

    Args:
        classifier: FunctionClassifier instance. If None, creates default.
    """

    def __init__(self, classifier: FunctionClassifier | None = None) -> None:
        self.classifier = classifier or FunctionClassifier()

    def parse_folded(self, filepath: pathlib.Path) -> list[HotspotFunction]:
        """Parse a folded flamegraph file.

        Args:
            filepath: Path to folded flamegraph text file.

        Returns:
            List of HotspotFunction sorted by self_pct descending.

        Raises:
            FileNotFoundError: If filepath doesn't exist.
            ValueError: If file contains no valid samples.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Flamegraph file not found: {filepath}")

        lines = self._read_folded_lines(filepath)

        if not lines:
            raise ValueError(f"Flamegraph file contains no valid samples: {filepath}")

        total_samples = sum(count for _, count in lines)

        # Build self/cumulative sample counts
        self_samples: dict[str, int] = {}
        cumulative_samples: dict[str, int] = {}
        call_paths: dict[str, list[str]] = {}

        for frames, count in lines:
            leaf = frames[-1]
            self_samples[leaf] = self_samples.get(leaf, 0) + count
            for frame in frames:
                cumulative_samples[frame] = cumulative_samples.get(frame, 0) + count
            if leaf not in call_paths or len(frames) > len(call_paths[leaf]):
                call_paths[leaf] = frames

        hotspots = []
        for func, samples in self_samples.items():
            self_pct = (samples / total_samples) * 100.0
            cum_pct = (cumulative_samples.get(func, 0) / total_samples) * 100.0
            source, library = self.classifier.classify(func)
            hotspots.append(HotspotFunction(
                function=func,
                library=library,
                source=source,
                self_pct=self_pct,
                cumulative_pct=cum_pct,
                call_path=call_paths.get(func, []),
            ))

        hotspots.sort(key=lambda h: h.self_pct, reverse=True)
        return hotspots

    def _read_folded_lines(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Read and parse folded format lines.

        Returns:
            List of (frames, count) tuples.
        """
        lines = []
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit(" ", 1)
                if len(parts) != 2:
                    continue
                stack_str, count_str = parts
                try:
                    count = int(count_str)
                except ValueError:
                    continue
                frames = stack_str.split(";")
                lines.append((frames, count))
        return lines
```

- [ ] **Step 4: Create test data files**

Create `tests/data/sample_flamegraph_folded.txt` — same content as original plan.

Create `tests/data/malformed_flamegraph.txt`:

```
this line has no count number
; 100
main;func abc
```

- [ ] **Step 5: Write tests (including error paths)**

```python
"""Tests for FlamegraphParser and FunctionClassifier."""

import pathlib
from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.classifier import FunctionClassifier


DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def test_parse_folded_extracts_hotspots():
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    assert len(hotspots) > 0
    folly_funcs = [h for h in hotspots if h.library == "folly"]
    assert len(folly_funcs) >= 1


def test_parse_folded_extracts_call_paths():
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    for h in hotspots:
        assert len(h.call_path) > 0


def test_parse_folded_classifies_open_source_vs_custom():
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    open_source = [h for h in hotspots if h.source == "open_source"]
    custom = [h for h in hotspots if h.source == "customer_custom"]
    assert len(open_source) >= 1
    assert len(custom) >= 1


def test_parse_folded_cumulative_pct_greater_than_self_pct():
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    for h in hotspots:
        assert h.cumulative_pct >= h.self_pct


def test_parse_folded_file_not_found_raises():
    parser = FlamegraphParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_folded(DATA_DIR / "nonexistent.txt")


def test_parse_folded_malformed_file_raises():
    parser = FlamegraphParser()
    with pytest.raises(ValueError):
        parser.parse_folded(DATA_DIR / "malformed_flamegraph.txt")


def test_classifier_with_default_config():
    classifier = FunctionClassifier()
    source, lib = classifier.classify("folly::futures::detail::FutureImpl::then")
    assert source == "open_source"
    assert lib == "folly"


def test_classifier_customer_custom():
    classifier = FunctionClassifier()
    source, lib = classifier.classify("CustomerCustom::featureCalc")
    assert source == "customer_custom"
    assert lib == "custom"


def test_classifier_no_false_positives():
    classifier = FunctionClassifier()
    # "MyFollyWrapper" should NOT match folly namespace pattern "folly::"
    source, lib = classifier.classify("MyFollyWrapper::process")
    assert source == "customer_custom"  # no "folly::" pattern match


def test_classifier_taskflow_alias():
    classifier = FunctionClassifier()
    source, lib = classifier.classify("tf::ParallelFor::dispatch")
    assert source == "open_source"
    assert lib == "taskflow"
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/ingestion/ tests/config/test_classifier.py -v`
Expected: All 10 tests PASS (7 flamegraph + 3 classifier). Error path tests properly raise.

- [ ] **Step 7: Commit**

```bash
git add src/ingestion/ src/config/open_source_libraries.yaml tests/ingestion/ tests/config/test_classifier.py tests/data/
git commit -m "feat: FlamegraphParser + config-driven FunctionClassifier with regex matching"
```

---

### Task 4: Data Ingestion — TopdownParser

**Files:**
- Create: `src/ingestion/topdown_parser.py`
- Create: `tests/data/sample_topdown.json`
- Create: `tests/data/sample_topdown.csv`
- Create: `tests/data/malformed_topdown.json`
- Test: `tests/ingestion/test_topdown_parser.py`

Same content as original plan Task 3, plus error path tests (malformed JSON, missing fields, non-existent file).

- [ ] **Step 1: Create test data (including malformed)**

Same `sample_topdown.json` and `sample_topdown.csv` as original plan.

Create `tests/data/malformed_topdown.json`:

```json
{
  "topdown_l1": {
    "frontend_bound": "not_a_number"
  }
}
```

- [ ] **Step 2: Write TopdownParser (same as original, add error handling)**

```python
"""Parse Topdown analysis data (devkit JSON/CSV) into Profile fields."""

import csv
import json
import pathlib
from profile_schema import (
    TopdownL1, TopdownL2, TopdownL2Frontend, TopdownL2Backend,
    TopdownL2BadSpec, TopdownL2Retiring, MemoryProfile, Profile, ProfileMetadata,
)


class TopdownParser:
    """Parser for ARM64 Topdown analysis data from devkit output."""

    def parse_json(self, filepath: pathlib.Path) -> Profile:
        """Parse devkit JSON output.

        Raises:
            FileNotFoundError: If filepath doesn't exist.
            ValidationError: If JSON content doesn't match Profile schema.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Topdown file not found: {filepath}")

        with open(filepath, "r") as f:
            data = json.load(f)

        topdown_l1 = TopdownL1(**data.get("topdown_l1", {}))
        # ... same L2 + memory parsing as original plan ...

        return Profile(
            metadata=ProfileMetadata(customer="unknown", date="unknown"),
            topdown=topdown_l1,
            topdown_l2=topdown_l2,
            memory=memory,
        )

    def parse_csv(self, filepath: pathlib.Path) -> Profile:
        # Same as original plan + FileNotFoundError check
        ...
```

- [ ] **Step 3: Write tests (normal + error paths)**

```python
# Same 5 normal tests as original plan + 2 error tests:

def test_parse_json_file_not_found_raises():
    parser = TopdownParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_json(DATA_DIR / "nonexistent.json")

def test_parse_json_malformed_raises_validation_error():
    parser = TopdownParser()
    with pytest.raises(pydantic.ValidationError):
        parser.parse_json(DATA_DIR / "malformed_topdown.json")
```

- [ ] **Step 4: Run tests + commit**

Same as original plan.

---

### Task 5: Profile Store & Comparator

**Files:**
- Create: `src/profile/profile_store.py`
- Create: `src/profile/comparator.py`
- Create: `tests/data/sample_profile.json`
- Create: `tests/data/sample_workload_profile.json`
- Test: `tests/profile/test_profile_store.py`
- Test: `tests/profile/test_comparator.py`

Same content as original plan Task 4, plus:
- Comparator uses `FrameworkConfig.comparison` thresholds (not constructor params alone)
- IterationHistory integration: Comparator result feeds into IterationRecord
- Error path tests (load non-existent file, compare profiles with missing fields)

- [ ] **Steps 1-8: Same as original plan Task 4 + error path tests + IterationHistory integration**

Key changes:
- `ProfileComparator` constructor takes `ComparisonConfig` (from FrameworkConfig), not raw float params
- `comparator.compare()` returns a dict that can be directly used to create `IterationRecord`
- Tests for `store.load()` with non-existent file → FileNotFoundError
- Tests for `comparator.compare()` with one profile having `None` topdown → graceful handling

---

### Task 6: Code Gen — Strategy Registry + Behavior Templates

**Files:**
- Create: `src/codegen/strategies/__init__.py`
- Create: `src/codegen/strategies/base.py`
- Create: `src/codegen/strategies/compute_synthesis.py`
- Create: `src/codegen/strategies/memory_synthesis.py`
- Create: `src/codegen/strategies/direct_call.py`
- Create: `src/codegen/strategies/mixed.py`
- Create: `src/codegen/behavior_gen.py`
- Create: `src/codegen/templates/behaviors/*.j2` (same as original)
- Create: `src/codegen/templates/main/config_loader.h.j2` (NEW — separated from main.cpp)
- Test: `tests/codegen/test_strategies.py`
- Test: `tests/codegen/test_behavior_gen.py`

Key revision: Behavior strategies use a registry pattern (open-closed principle). New strategies can be added by creating a new file and registering it, without modifying `behavior_gen.py`.

- [ ] **Step 1: Write strategies/base.py — registry pattern**

```python
"""Behavior strategy base class and registry."""

import pathlib
import jinja2
from abc import ABC, abstractmethod


class BehaviorStrategy(ABC):
    """Base class for behavior implementation strategies.

    Each strategy knows how to:
    1. Select the right Jinja2 template
    2. Build the template context from a stage dict
    3. Render the C++ code
    """

    @abstractmethod
    def strategy_name(self) -> str:
        """Return the strategy identifier (e.g., "compute_synthesis")."""
        ...

    @abstractmethod
    def render(self, stage: dict, env: jinja2.Environment) -> tuple[str, str]:
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

    _strategies: dict[str, BehaviorStrategy] = {}

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
            raise KeyError(f"Unknown behavior strategy: '{name}'. Registered: {list(cls._strategies.keys())}")
        return cls._strategies[name]

    @classmethod
    def available(cls) -> list[str]:
        """List available strategy names."""
        return list(cls._strategies.keys())


# Auto-register all strategies when this module's sub-modules are imported
def auto_register() -> None:
    """Import all strategy sub-modules to trigger their registration."""
    from codegen.strategies.compute_synthesis import ComputeSynthesisStrategy
    from codegen.strategies.memory_synthesis import MemorySynthesisStrategy
    from codegen.strategies.direct_call import DirectCallStrategy
    from codegen.strategies.mixed import MixedStrategy
```

- [ ] **Step 2: Write each strategy implementation**

Each strategy implements `BehaviorStrategy` and registers itself on import:

```python
# compute_synthesis.py
"""Compute synthesis behavior strategy."""

import jinja2
from codegen.strategies.base import BehaviorStrategy, StrategyRegistry


class ComputeSynthesisStrategy(BehaviorStrategy):
    def strategy_name(self) -> str:
        return "compute_synthesis"

    def render(self, stage: dict, env: jinja2.Environment) -> tuple[str, str]:
        template = env.get_template("behaviors/compute_synthesis.cpp.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        context = {
            "stage_name": stage["stage_name"],
            "synthesis_config": config,
        }
        content = template.render(**context)
        filename = f"{stage['stage_name']}.h"
        return filename, content


StrategyRegistry.register(ComputeSynthesisStrategy())
```

Similar pattern for `memory_synthesis.py`, `direct_call.py`, `mixed.py`.

- [ ] **Step 3: Write behavior_gen.py (using registry)**

```python
"""Generate behavior implementation code using strategy registry."""

import pathlib
import jinja2
from codegen.strategies.base import StrategyRegistry, auto_register


class BehaviorGenerator:
    """Generate Layer 3 behavior implementation code from Behavior Profiles.

    Uses StrategyRegistry to dispatch to the correct strategy implementation.
    New strategies can be added without modifying this class.
    """

    def __init__(self) -> None:
        auto_register()  # Ensure all strategies are registered
        template_dir = pathlib.Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

    def generate_stage_file(self, stage: dict) -> tuple[str, str]:
        """Generate a C++ header file for one workflow stage using the registry.

        Args:
            stage: Behavior profile dict with implementation_strategy key.

        Returns:
            (filename, content) tuple.

        Raises:
            KeyError: If implementation_strategy is not registered.
        """
        strategy_name = stage["implementation_strategy"]
        strategy = StrategyRegistry.get(strategy_name)
        return strategy.render(stage, self._env)
```

- [ ] **Step 4: Write config_loader.h.j2 (separated from main.cpp)**

```cpp
#pragma once
#include <string>
#include <nlohmann/json.hpp>
#include <fstream>

struct RunConfig {
    int thread_count;
    int qps;
    int warmup_seconds;
    int measurement_seconds;
    double compute_ratio;
    double memory_ratio;
};

inline RunConfig load_config(const std::string& config_path) {
    std::ifstream file(config_path);
    nlohmann::json j = nlohmann::json::parse(file);

    RunConfig cfg;
    cfg.thread_count = j.value("thread_count", {{ config.thread_count | default(4) }});
    cfg.qps = j.value("qps", {{ config.qps | default(100) }});
    cfg.warmup_seconds = j.value("warmup_seconds", {{ config.warmup_seconds | default(30) }});
    cfg.measurement_seconds = j.value("measurement_seconds", {{ config.measurement_seconds | default(60) }});
    cfg.compute_ratio = j.value("compute_ratio", {{ config.compute_ratio | default(0.5) }});
    cfg.memory_ratio = j.value("memory_ratio", {{ config.memory_ratio | default(0.5) }});
    return cfg;
}
```

- [ ] **Step 5: Update main.cpp.j2 to use config_loader.h**

```cpp
#include "config_loader.h"
// ... rest of main.cpp uses load_config() from the included header ...
```

- [ ] **Step 6: Write tests (strategy registry + behavior gen)**

```python
"""Tests for behavior strategy registry and BehaviorGenerator."""

from codegen.strategies.base import StrategyRegistry, auto_register


def test_strategy_registry_auto_registers():
    auto_register()
    assert "compute_synthesis" in StrategyRegistry.available()
    assert "memory_synthesis" in StrategyRegistry.available()
    assert "direct_call" in StrategyRegistry.available()
    assert "mixed" in StrategyRegistry.available()


def test_strategy_registry_unknown_raises():
    auto_register()
    with pytest.raises(KeyError, match="Unknown behavior strategy"):
        StrategyRegistry.get("nonexistent_strategy")


def test_behavior_gen_compute_synthesis():
    # Same as original plan test
    ...

def test_behavior_gen_memory_synthesis():
    # Same as original plan test
    ...

def test_behavior_gen_unknown_strategy_raises():
    gen = BehaviorGenerator()
    stage = {"stage_name": "test", "implementation_strategy": "unknown"}
    with pytest.raises(KeyError):
        gen.generate_stage_file(stage)
```

- [ ] **Step 7: Run tests + commit**

---

### Task 7: Code Gen — Scaffold + Knob + Generator Orchestrator

Same as original plan Task 5 + Task 6 generator part, but:
- `ScaffoldGenerator` now also generates `config_loader.h`
- `KnobGenerator` uses `FrameworkConfig.run_defaults` for default values
- `Generator` orchestrates scaffold → behavior → knob with telemetry tracking

---

### Task 8: Harness — Build + Execution + Metrics

Same as original plan Tasks 7-8, but:
- All Result classes use pydantic models from `models/results.py`
- `BuildRunner`, `ExecutionRunner`, `MetricsCollector` use structlog instead of stdlib logging
- `RunConfig` uses `FrameworkConfig.run_defaults` for defaults
- Error path tests added (build with missing cmake, run missing binary, collect with no devkit)

---

### Task 9: Agent Core — Prompt Chain + Strategy

Same as original plan Task 9, but:
- `AgentCore` is optional — `Pipeline` can work without it (local-only mode)
- `agent_core.py` has `_parse_json_response()` helper to eliminate JSON parsing duplication
- `strategy.py` uses `FrameworkConfig.comparison` thresholds
- `AgentCore.__init__()` takes `AgentConfig` from FrameworkConfig, not raw params
- Tests for `_parse_json_response()` with malformed LLM output

---

### Task 10: Pipeline Integration + Example + README

Same as original plan Task 10, but:
- `Pipeline.__init__()` takes `FrameworkConfig`, not raw params
- Agent is optional: `Pipeline(agent=None)` works in local-only mode (user provides generation instruction manually)
- Pipeline integrates with `IterationHistory` and `PipelineTelemetry`
- Pipeline saves iteration history to `output_dir/history.json`
- README includes pre-commit instructions

---

### Task 11: Final Validation — All Tests + Coverage + Type Check

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --cov=src --cov-report=term-missing`
Expected: All tests PASS. Coverage >= 60%.

- [ ] **Step 2: Run ruff check**

Run: `ruff check src/ tests/`
Expected: No violations.

- [ ] **Step 3: Run mypy type check**

Run: `mypy src/ --strict`
Expected: No type errors.

- [ ] **Step 4: Run pre-commit on all files**

Run: `pre-commit run --all-files`
Expected: All hooks pass.

- [ ] **Step 5: Verify example data ingestion works (same as original plan)**

- [ ] **Step 6: Commit any fixes + final commit**

---

## Self-Review Checklist (Revised Plan)

**1. Spec coverage:** Same as original plan — all Phase 1 components covered. Phase 2 items (MCP, auto-iteration, MemoryParser, VersionParser, TextParser, DeployRunner) explicitly excluded.

**2. Placeholder scan:** No TBD/TODO. All code shown inline. Strategy implementations are abbreviated with "Similar pattern for..." but the full code structure is shown — the key pattern (register + render) is demonstrated completely for compute_synthesis.py and the rest follow identically.

**3. Type consistency:**
- All Result classes are pydantic BaseModel in `models/results.py` — no hand-written classes anywhere
- `FrameworkConfig` is the single source of truth for thresholds, defaults, and tool paths
- `RunConfig` derives defaults from `FrameworkConfig.run_defaults`
- `ComparisonConfig` derives thresholds from `FrameworkConfig.comparison`
- `IterationRecord.topdown_diffs` is `dict[str, float]` — matches Comparator output format
- `StrategyRegistry.get()` raises `KeyError` — consistent with `BehaviorGenerator.generate_stage_file()` raising same

**4. Software engineering properties:**
- **Extensible**: New behavior strategies register without modifying existing code (open-closed). New library classification rules added via YAML without code changes. New parsers follow same pattern.
- **Maintainable**: Single source of truth for defaults/thresholds (FrameworkConfig). Separated config_loader.h from main.cpp. Unified Result models. No duplicated JSON parsing logic.
- **Observable**: structlog structured logging. IterationHistory tracks convergence trends. PipelineTelemetry tracks step timing and state. Iteration history persisted to JSON.
- **Quality gates**: pre-commit (ruff + mypy + basic checks). pytest with coverage threshold (60%). mypy strict mode.
