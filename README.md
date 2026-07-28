# Workload Simulation Framework

Generate synthetic C++ workloads that mimic the microarchitectural behavior (Topdown, memory bandwidth, hotspot call paths) of customer real-world software on ARM64 platforms.

## Quick Start

```bash
pip install -e ".[dev]"
pytest tests/ -v --no-cov  # run all tests (skip coverage threshold for initial run)
pre-commit install          # install pre-commit hooks
```

## Architecture

Five core components + cross-cutting infrastructure:

1. **Data Ingestion** — Parse flamegraph, Topdown, memory data -> structured Profile (config-driven classifier)
2. **Profile Store** — Save/load Profiles as JSON
3. **Agent Core** — LLM prompt chain: analyze -> plan -> generate instruction (OPTIONAL — pipeline works without it)
4. **Code Gen Engine** — Generate C++ workload + config (strategy registry pattern — open-closed)
5. **Harness** — Build, run, collect, compare

Cross-cutting:

- **Observability** — structlog structured logging, IterationHistory (convergence trends), PipelineTelemetry (step timing)
- **Config** — FrameworkConfig from YAML (single source of truth for all thresholds/defaults)
- **Result Models** — Unified pydantic models (BuildResult, ExecutionResult, etc.)

## Phase 1 Status

End-to-end loop works: ingest -> agent -> generate -> build -> (manual run/collect/compare).
No auto-iteration yet (Phase 2). Agent is optional — works in local-only mode with manual instruction.

## Usage

### Full pipeline with Agent (requires ANTHROPIC_API_KEY)

```python
from harness.pipeline import Pipeline

pipeline = Pipeline(output_base_dir="./output")
result = pipeline.run_full_pipeline(
    flamegraph_path="examples/search_ranking/customer_data/flamegraph_folded.txt",
    topdown_path="examples/search_ranking/customer_data/topdown.json",
    customer_name="search_ranking",
    metadata={"neoverse_core": "N2"},
)
```

### Local-only mode (no API key needed)

```python
from harness.pipeline import Pipeline
from agent.agent_core import AgentCore
from config.framework_config import AgentConfig

no_agent = AgentCore(config=AgentConfig(api_key=None))
pipeline = Pipeline(output_base_dir="./output", agent=no_agent)

profile = pipeline.ingest_customer_data(
    flamegraph_path="path/to/flamegraph.txt",
    topdown_path="path/to/topdown.json",
)

# Provide manual generation instruction
instruction = {
    "project_name": "my_workload",
    "compile_flags": "-O2",
    "dependencies": [{"name": "folly", "version": "2.1.0"}],
    "stages": [...],
    "config": {"thread_count": 8, "qps": 500},
}
project_dir = pipeline.generate_workload(profile, instruction=instruction)
```

### Individual pipeline steps

```python
pipeline = Pipeline(output_base_dir="./output", agent=no_agent)

# Step 1: Ingest
profile = pipeline.ingest_customer_data(flamegraph_path=..., topdown_path=...)

# Step 2: Generate (with manual instruction)
project_dir = pipeline.generate_workload(profile, instruction=instruction)

# Step 3: Build
binary_path = pipeline.build_workload(project_dir)

# Step 4: Run (requires actual ARM64 hardware)
result = pipeline.run_workload(binary_path)

# Step 5: Compare (after collecting workload metrics)
report = pipeline.compare_results(customer_profile, workload_profile)
```

## Quality Gates

- **pre-commit**: ruff (lint + format), mypy (strict), basic checks
- **pytest**: coverage threshold >= 60%
- **mypy**: strict mode with pydantic plugin

Run all checks: `pre-commit run --all-files`

## Design Doc

See `docs/superpowers/specs/2026-07-27-workload-simulation-design.md` for the full design specification.
See `docs/superpowers/plans/2026-07-27-workload-simulation-phase1-revised.md` for the implementation plan.
