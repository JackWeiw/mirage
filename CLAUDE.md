# Mirage - Project Guide for AI Assistants

This file provides project-specific conventions, architecture overview, and working guidelines for AI assistants working on this codebase.

## What is Mirage

Mirage is a **synthetic workload simulator for ARM64**: it generates C++ workloads that replicate the Topdown, memory-bandwidth, and hotspot call-path characteristics of customer proprietary software — using open-source library calls where possible and behavior synthesis where not. Like a desert mirage, the output mirrors the real city's shape but is built from different materials. The output runs on bare ARM (Kunpeng-class Neoverse) and is measured with the `devkit` Topdown tuner.

Mirage is **agent-driven**: an LLM brain orchestrates the pipeline — it analyzes the customer profile, plans the business workflow, fills in behavior details, and (in Phase 2) revises the instruction to steer the workload toward the customer's microarchitectural signature. The agent is **not optional by design**, but the pipeline degrades to local-only mode (manual instruction, deterministic controller) when no API key / gateway is configured.

**Phases:**
- **Phase 1** — Single-pass agent chain: ingest → agent analyzes/plans/fills → generate → build. (`Pipeline.run_full_pipeline`)
- **Phase 2** — Auto-iteration loop: collect → compare → decide priority → two-tier adjust (runtime no-rebuild / structural LLM regenerate+rebuild) → gate → apply → record, until converged or a stop condition. (`Pipeline.run_iteration_loop`) — **landed on main**, unit-tested via a stub plant (no ARM/devkit/LLM); real-ARM validation on a Kunpeng box is the active step.

## Build, Lint, Test Commands

```bash
pip install -e ".[dev]"            # editable install + dev deps (ruff, mypy, pytest, pre-commit)
pre-commit install                 # one-time git hook install

PYTHONPATH=src python3 <entry>    # run WITHOUT installing: pyproject has no [build-system]/package-dir,
                                   # so `pip install -e .`'s auto-discovery misses src/ packages, and the
                                   # stdlib `profile` module shadows src/profile/ if mirage is installed.
                                   # On the ARM box: `pip uninstall mirage` then run with PYTHONPATH=src.

ruff check src/ tests/             # lint (target-version py311, line-length 100, ignore E501)
ruff format --check src/ tests/    # format check
mypy src/ --strict --config-file=pyproject.toml   # strict type check (pydantic plugin)
pytest tests/ -k "not integration" --cov=src --cov-report=term-missing   # unit suite + coverage
pre-commit run --all-files         # ruff + mypy + basic checks over the whole tree
```

**Install path caveat:** `pip install -e .` is NOT the intended run path on the ARM box — pyproject has no `[build-system]`/package-dir, default auto-discovery can't find `src/` packages, and the stdlib `profile` module shadows `src/profile/`. Run with `PYTHONPATH=src` (and `pip uninstall mirage` first if it was installed). `pyproject.toml` sets `pythonpath = ["src"]` for pytest so tests resolve packages without an install.

**CI (`.github/workflows/ci.yml`):** two parallel jobs on Python 3.13 — `lint` (ruff check + ruff format check + mypy strict, scoped to `src/` + `tests/`) and `test` (pytest, `not integration`, `--cov=src`). `examples/` is outside CI scope but pre-commit still lints it. `pyproject`'s `--cov-fail-under=60` is the CI floor; actual coverage tracks ~91%.

## Architecture

### Layered architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Entry points                                                         │
│  Pipeline.run_full_pipeline | Pipeline.run_iteration_loop            │
│  examples/run_loop_demo.py | examples/scenarios/*/collect_reference  │
└──────────────────────────────────────────────────────────────────────┘
                          │
   ┌──────────────────────┴───────────────────────┐
   ▼                                              ▼
┌────────────────┐   ┌──────────────┐   ┌────────────────────┐
│ ingestion      │   │ agent        │   │ codegen             │
│ flamegraph +   │   │ AgentCore    │   │ WorkloadGenerator   │
│ topdown parsers│   │ LLMClient    │   │ scaffold/behavior/  │
│ → Profile      │   │ (anthropic/  │   │ knob/strategies      │
│                │   │  openai)     │   │ → C++ project + Jinja2│
└────────────────┘   └──────────────┘   └────────────────────┘
                          │                        │
                          ▼                        ▼
┌──────────────────────────────────────────────────────────────┐
│  harness                                                      │
│  BuildRunner → ExecutionRunner → MetricsCollector(devkit)    │
│  config_writer (atomic config.json) → run_and_collect          │
│  Pipeline orchestrates collect → compare → tier → gate → apply │
└──────────────────────────────────────────────────────────────┘
                          │
   ┌───────────────────────┴──────────────────────┐
   ▼                                              ▼
┌────────────────┐                       ┌────────────────────┐
│ profile         │                       │ observability       │
│ comparator      │                       │ iteration_history   │
│ (topdown L1/L2, │                       │ telemetry           │
│  memory,        │                       │ logging (structlog) │
│  coverage)      │                       │                     │
│ structural_comp │                       │                     │
└────────────────┘                       └────────────────────┘
                          │
                          ▼
                ┌────────────────────┐
                │ config              │
                │ FrameworkConfig     │
                │ (single source of   │
                │  truth: thresholds, │
                │  defaults, env)    │
                └────────────────────┘
```

### Core loop (Phase 2, `Pipeline.run_iteration_loop`)

Two-tier closed-loop controller. Priority from `decide_iteration_priority` (0 = converged, 1 = runtime, ≥2 = structural):

```
acquire instruction (agent.run_full_chain OR seed_instruction)
seed build (must compile; no recovery) ── fail → seed_build_failed
for i in max_iter:
    collect(binary, instruction) ── RunFailure(crash= no-retry / timeout,collect_fail= retry) → run_failure_streak
    compare(customer, workload) → report
    priority = decide_iteration_priority(report)
    priority==0 → converged, break
    tier = runtime if priority==1 else structural
    candidates:
      runtime  → deterministic_revise (bounded runtime-knob move, no rebuild)
      structural+agent → agent.revise_instruction (LLM structural rewrite)
      structural, agent down OR LLMError → degrade to runtime (degraded=True; priority≥2 is NOT a stop)
    gate: validate_adjustments (domain + tier-ownership + direction XOR decoupled from largest-error)
    apply: runtime → write_config_json_atomic (no rebuild) / structural → regenerate + rebuild
    rebuild fail → pending_build_fix (LLM self-corrects from compiler stderr next iter) → build_failure_streak
    record + observed_effects attribution
    terminate on: converged | max_iter | oscillation | no_improvement | run_failure_streak
                  | build_failure_streak | runtime_tier_exhausted_agent_unavailable
```

### Key Packages

| Package | Purpose | Key Files |
|---------|---------|-----------|
| `src/ingestion/` | parse customer flamegraph + Topdown → structured Profile | `flamegraph_parser.py` (folded `.txt` + flamegraph.pl `.svg` by suffix), `topdown_parser.py` (`.json`/`.csv`/`.txt`), `classifier.py` (config-driven FunctionClassifier) |
| `src/profile/` | comparison + persistence | `comparator.py` (topdown L1/L2, memory, coverage; `diff_pct` = absolute percentage points for topdown, relative % for memory), `structural_comparator.py` (call-path alignment), `profile_schema.py` (pydantic Profile/TopdownL1), `profile_store.py` (JSON save/load) |
| `src/agent/` | LLM brain | `agent_core.py` (analyze→plan→detail_fill chain + `revise_instruction`), `llm_client.py` (`LLMClient` ABC + `AnthropicClient`/`OpenAIClient` + `make_client`), `strategy.py` (`decide_iteration_priority`), `adjustment.py` (knob space, `apply_adjustments`, `deterministic_revise`, `validate_adjustments`, `load_sensitivity`), `prompts/*.md` |
| `src/codegen/` | generate C++ workload + config | `generator.py` (`WorkloadGenerator`), `scaffold_gen.py`/`behavior_gen.py`/`knob_gen.py`/`skeleton_gen.py`/`module_graph*.py`/`call_tree.py`/`catalog.py`/`signature.py`, `strategies/` (registry: direct_call/memory_synthesis/compute_synthesis/mixed), `templates/*.j2` |
| `src/harness/` | build/run/collect/loop | `pipeline.py` (orchestrator + `run_iteration_loop`), `build_runner.py` (CMake target-name binary location), `execution_runner.py`, `metrics_collector.py` (devkit `collect_topdown`), `config_writer.py` (`write_config_json_atomic`), `run_config.py` |
| `src/observability/` | logging + history + telemetry | `logging.py` (structlog `configure_logging`/`get_logger`), `iteration_history.py` (`compute_score`, `is_oscillating`, `no_improvement_for`, `best_iteration`), `telemetry.py` (`PipelineTelemetry`) |
| `src/config/` | framework config | `framework_config.py` (pydantic `FrameworkConfig` + `AgentConfig`/`ComparisonConfig`/`DevkitConfig`/…, `from_yaml`/`defaults`/`from_env`), `default_config.yaml`, `behavior_archetypes.yaml`, `open_source_api_catalog.yaml`, `open_source_libraries.yaml` |
| `src/models/` | result models | `results.py` (`BuildResult`, `ExecutionResult`, `CollectionResult`, `RunFailure`, `PipelineResult`) — all pydantic |
| `examples/` | end-to-end demos + scenarios | `run_loop_demo.py` (steering-validation driver), `steerability_spike.py`, `scenarios/{memory_bound,compute_bound}/` (reference C++ + collection.yaml + seed/sensitivity), `search_ranking/` (sample customer data) |

### Templates (`src/codegen/templates/`)

Jinja2 templates are the codegen output surface — `main/main.cpp.j2` (time-boxed wall-clock loop, warmup+measurement, reads `thread_count`/`qps`/`warmup`/`measurement` + per-stage `ratio_expr`/`burst` for `compute_ratio`/`memory_ratio`), `main/config_loader.h.j2` (**nlohmann-free** — `strtod` flat-object scanner with codegen-time baked defaults + runtime override; nlohmann is absent on bare ARM, no CMake change), `cmake/CMakeLists.txt.j2`, `config/config.json.j2`, `module/*.j2`, `service/*.j2`, `behaviors/*.j2`. Treat the vendored `examples/third_party/taskflow/` as machine-managed (do not edit; tracked via `manifest.sha256`).

## Code Conventions

### Python style

- PEP 8; **type hints required on all public APIs** (mypy strict, `disallow_untyped_defs`); max line length **100** (E501 ignored).
- `from __future__ import annotations` is implicit via py311; PEP 604 `int | None` unions in annotations.
- f-strings for formatting; `pathlib.Path` over `os.path`.
- `pydantic` v2 BaseModel for all data/result models; `Field(repr=False)` for secrets.
- Ruff config in `pyproject.toml`: **`target-version = "py311"`**. Rule set: E/W/F/I/N/UP/B/SIM/TCH/RUF.
- **English-only comments and commit messages** — no Chinese in code/comments; GitHub issues/PRs/RFCs in English even when the conversation is Chinese.

### Configuration

- Single source of truth: `src/config/default_config.yaml` → `FrameworkConfig` (pydantic). All thresholds/defaults live there, never hardcoded in modules.
- Operator LLM-gateway overrides via env (precedence: yaml < env), loaded by `FrameworkConfig.from_env`:
  - `MIRAGE_AGENT_API_KEY` (flips agent online), `MIRAGE_AGENT_BASE_URL` (operator's gateway, **never a vendor's official host**), `MIRAGE_AGENT_PROVIDER` (`anthropic` | `openai`), `MIRAGE_AGENT_MODEL`, `MIRAGE_AGENT_MAX_TOKENS` (reasoning models like GLM-4.7/deepseek-r1 burn thousands of tokens on reasoning before the JSON answer; the 4096 default truncates them mid-thought → `LLMTruncationError`).
- `FrameworkConfig.defaults()` is deliberately **env-free** so unit tests get deterministic offline configs.
- Scenario configs: per-scenario `collection.yaml` (shared duration/interval/cpu_mask/numa_node/buffer-size, read by both collect + loop), `seed_instruction.json`, `sensitivity.json`.

### Logging (mandatory)

- **Use the Python `logging` module — never `print`.**
- The framework provides `observability.logging.get_logger(name)` returning a structlog `BoundLogger` bound with `component=name`, configured via `configure_logging(log_level, json_output)` (or `configure_logging_from_env`, reading `MIRAGE_LOG_LEVEL`/`MIRAGE_LOG_JSON`, called once at an application entry point before any log is emitted). structlog routes through the stdlib level filter (`make_filtering_bound_logger`), so stdlib levels (`logging.INFO` etc.) apply.
- **Convention:**
  - All `src/` framework modules use a single style: `from observability.logging import get_logger; logger = get_logger("<module>")` (structured key=value events, e.g. `logger.info("build_succeeded", binary=...)`). Do not use stdlib `logging.getLogger` in `src/`.
  - Levels: `DEBUG` (verbose internal transitions), `INFO` (lifecycle/progress events), `WARNING` (recoverable issues, retries, degradation), `ERROR` (failures — build/run failures, pipeline errors).
  - Library code (`Pipeline` etc.) never calls `configure_logging` — only application entry points do, so imports have no global logging side effects and unit tests stay deterministic.
  - Never log secrets: `AgentConfig.api_key` is `Field(repr=False)`; api keys travel only in SDK auth headers, never in log lines, prompt bodies, or LLM responses.

### Git / commit / PR rules (mandatory)

- **Branch first.** Never commit directly to `main` (pre-commit `no-commit-to-branch` enforces it); cut a feature branch (`feat/...`, `fix/...`, `rfc/...`, `docs/...`).
- **Relevant files only.** `git add` only the files relevant to the change — never `git add -A` with unrelated churn. **Never commit `docs/superpowers/*`** (plans/specs are local working docs).
- **Pre-commit before commit.** Run `pre-commit run --files <files>` (or `--all-files`) on the staged set; fix ruff/mypy failures before committing.
- **No Claude attribution.** Do not add `Co-Authored-By: Claude` or similar trailers to commits. **PR and issue descriptions must also be free of Claude attribution** — no `Generated with Claude Code` footer, no "🤖" markers, no "Claude suggests…". The PR/issue body reads as the author's own words.
- **Conventional Commit prefixes:** `feat:`, `fix:`, `refactor:`, `docs:`, `ci:`, `chore:`.
- **Secret scrubbing.** Never commit real API keys / gateway tokens — leave placeholders. Private `192.168.x.x` addresses are OK; scrub public IPs.
- **Scope rule (user-enforced):** LLM-provider fixes (`src/agent/llm_client.py`, `AgentConfig`, env wiring) MUST be separate PRs off `main` — never committed on an example/demo branch.

## Entry Points

| Entry | Purpose | Key call |
|-------|---------|----------|
| `Pipeline.run_full_pipeline` | Phase 1 single-pass chain | `ingest → generate (agent or instruction) → build` |
| `Pipeline.run_iteration_loop` | Phase 2 two-tier auto-iteration loop | `collect → compare → tier → gate → apply`, injectable `collect`/`build` callables |
| `examples/run_loop_demo.py` | in-repo steering-validation driver | `--scenario/--max-iter/--threshold/--out-dir/--no-agent/--config`, `--no-agent` = runtime-only |
| `examples/scenarios/<s>/collect_reference.py` | collect reference Topdown + flamegraph for a scenario | numactl+taskset pinning, stdout-marker-triggered collection |
| `Pipeline.run_and_collect` | run existing binary + collect Topdown | taskset Popen → warmup → crash-check → `collect_topdown(-p pid)` → wait → parse; returns `Profile` or `RunFailure` |

**ARM run (operator):** `PYTHONPATH=src python3 examples/run_loop_demo.py --scenario memory_bound --config fw.yaml` with `MIRAGE_AGENT_*` env set (`MAX_TOKENS=16384` recommended for reasoning models). On the Kunpeng box, the legacy local helper `examples/run_real_arm_loop.py` may exist locally only (not in the repo).

### LLM client shapes (`src/agent/llm_client.py`)

`LLMClient` ABC with `complete(prompt) -> (text, stop_reason)` + `is_available()` + `transient_exceptions()`. Two impls behind `make_client(config)`:
- `AnthropicClient` — Messages API.
- `OpenAIClient` — Chat Completions; maps `finish_reason` `length`/`content_filter` → `max_tokens`; **reasoning models** (GLM-4.7, deepseek-r1) return `content: null` + answer in `reasoning_content`/`reasoning` — falls back to those before raising "no text content". Uses `max_tokens` (not `max_completion_tokens`; vLLM/GLM gateways ignore the latter).
- Security: `base_url` None → not passed to the SDK (mirage never injects a vendor's official host); `api_key` travels only in SDK constructor kwargs (header transport).

## Common Modifications

### Adding a new codegen strategy
1. Add `src/codegen/strategies/<name>.py` subclassing `base.py` (open-closed registry).
2. Add `templates/behaviors/<name>*.j2`.
3. Register in the strategy registry; add tests under `tests/codegen/`.

### Adding a new adjustable knob
1. Declare it in `src/agent/adjustment.py` knob space (`RUNTIME_KNOBS` vs structural) + domain.
2. Wire it into the relevant template (`main.cpp.j2` for runtime, behavior templates for structural) so the generated binary actually reads it — an unread knob is inert (the §4 spike caught `compute_ratio`/`memory_ratio` inert for this reason).
3. Extend `validate_adjustments` (domain + tier-ownership + direction check) and `deterministic_revise`.
4. Add sensitivity entry + spike verdict (ordinal vs categorical).

### Adding a new scenario
1. `examples/scenarios/<name>/` with `reference/` (CMakeLists + main.cpp + worker sources), `collection.yaml`, `seed_instruction.json`, `sensitivity.json`, `README.md`.
2. Per-worker buffer ≥ 2-3× NUMA LLC; `__MEASUREMENT_WINDOW_START__` marker; warmup/measurement contract.
3. `collect_reference.py` if custom collection is needed (reuse `MetricsCollector.collect_topdown` + `TopdownParser.parse_text`).

### Changing a comparison threshold
Edit `ComparisonConfig` in `src/config/default_config.yaml` (or `FrameworkConfig` overrides). Note: topdown L1 `diff_pct` is **absolute percentage points** (the metrics are already percentages summing ~100); memory bandwidth `diff_pct` is **relative %** (GB/s, large denominator). The loop's gate/priority thresholds (5/10/20) treat `diff_pct` as pp — do not make topdown relative.

## Known Issues / Limitations

1. **`examples/` outside CI scope** — pre-commit still lints it, but CI does not run its tests.
2. **`ExecutionResult.duration_seconds` declared-but-unset** (separate from the resolved `BuildResult.duration_seconds`).
3. **Generated binary post-measurement non-exit** — threadpool cleanup keeps the process alive after the measurement window (codegen-level fix, flagged not started).
4. **config.json path consistency** — codegen writes `scaffold/config.json`, collect reads `build/config.json`, `config_loader` falls back to baked defaults → runtime knob adjustments may not reach the binary (flagged not started).
5. **Two deeper follow-ups await user confirmation**: (a) generated-binary non-exit; (b) config.json path consistency.

## Related Documentation

- [README.md](README.md) — quick start + high-level architecture
- [docs/rfc/](docs/rfc/) — RFCs (0001 thread-pool codegen, 0002 context compaction, 0003 controllability+feedback adjustment; start from [0000-template.md](docs/rfc/0000-template.md))
- `docs/superpowers/specs/` + `docs/superpowers/plans/` — design specs + implementation plans (local working docs; **never committed**)
