# Mirage

**Synthetic workload simulation that mirrors customer real-software microarchitectural behavior on ARM64.**

Mirage generates C++ workloads that replicate the Topdown, memory bandwidth, and hotspot call-path characteristics of customer proprietary software — using open-source library calls where possible and behavior synthesis where not. Like a desert mirage, the output mirrors the real city's shape, but it's constructed from different materials.

## Quick Start

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=your-key-here
pytest tests/ -v --no-cov
pre-commit install
```

## Architecture

Agent-driven framework: the LLM brain orchestrates the entire pipeline.

**Core loop:**

```text
Customer data → Agent analyzes → Agent plans workflow → Agent fills details
→ Code Gen generates C++ workload → Harness builds/runs/collects
→ Comparator measures alignment → Agent evaluates → Agent decides iteration
→ repeat until converged
```

**Five components:**

1. **Data Ingestion** — Parse flamegraph, Topdown, memory data → structured Profile (config-driven classifier)
2. **Profile Store** — Save/load Profiles as JSON, track iteration history
3. **Agent Core** — LLM brain: analyzes Profiles, plans Business Workflow, fills behavior implementation, evaluates comparison results, decides iteration strategy. **This is not optional — mirage is an agent-driven system.**
4. **Code Gen Engine** — Generate C++ workload + config from Agent instructions (strategy registry pattern — open-closed)
5. **Harness** — Build, run, collect, compare; feeds results back to Agent for iteration decisions

**Cross-cutting:**

- **Observability** — structlog structured logging, IterationHistory (convergence trends), PipelineTelemetry (step timing)
- **Config** — FrameworkConfig from YAML (single source of truth for all thresholds/defaults)
- **Result Models** — Unified pydantic models (BuildResult, ExecutionResult, etc.)

## Current Status

**Phase 1** — Single-pass agent chain works end-to-end:
ingest → Agent analyzes/plans/fills → generate → build → (manual run/collect/compare).

**Phase 2 (next)** — Auto-iteration loop: run → collect → compare → Agent evaluates → Agent adjusts → regenerate → repeat until Topdown < 10%, memory bandwidth < 5%, hotspot coverage > 80%.

## Usage

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

Set `ANTHROPIC_API_KEY` environment variable before running. The Agent drives all analysis, planning, and generation decisions.

## Quality Gates

- **pre-commit**: ruff (lint + format), mypy (strict), basic checks
- **pytest**: 87% coverage (104 tests passing)
- **mypy**: strict mode with pydantic plugin — 37 source files, zero errors
- **GitHub CI**: runs on every push/PR to main

Run all checks locally: `pre-commit run --all-files`

## Design Docs

- [Design Spec](docs/superpowers/specs/2026-07-27-workload-simulation-design.md)
- [Implementation Plan](docs/superpowers/plans/2026-07-27-workload-simulation-phase1-revised.md)
