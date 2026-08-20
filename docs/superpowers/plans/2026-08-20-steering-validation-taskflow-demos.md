# Steering-Validation Taskflow Demos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two taskflow-based reference demos + a scenario-parameterized in-repo driver that validates mirage's end-to-end workload simulation + self-iteration steering on real ARM (fit a captured Topdown target via the LLM structural tier).

**Architecture:** Two sides meet at a captured `topdown.json`. *Reference side* (user runs on Kunpeng ARM): vendored `taskflow` (v3.9.0, C++17, header-only) + self-dev C++ programs (`memory_bound` LLC-missing scan, `compute_bound` matmul) → `collect_reference.py` captures a real L1 Topdown + flamegraph. *mirage fitting side*: `run_loop_demo.py` loads that `topdown.json` as the customer `Profile`, an out-of-band `seed_instruction.json` (~35 pp off the dominant metric), spike-grounded `sensitivity.json`, and a shared `collection.yaml`; it runs `Pipeline.run_iteration_loop` with a custom `collect` callable (numactl+taskset-pinned, mirroring `run_and_collect`), then evaluates four success criteria from `history.json` and prints `PASS`/`FAIL`/`RUNTIME-ONLY`.

**Tech Stack:** C++17 + taskflow v3.9.0 (vendored), CMake, perf + ARM Topdown devkit, Python 3.13, pydantic v2, pytest (`pythonpath=["src"]`, `--cov-fail-under=60`), ruff + ruff-format (pre-commit lints `examples/*.py`), mypy strict (`src/`+`tests/` only — `examples/` is NOT mypy-checked).

**Spec:** `docs/superpowers/specs/2026-08-20-steering-validation-taskflow-demos-design.md`

**Branch:** `feat/steering-validation-taskflow-demos` (off `main` `242ce03`; spec already committed at tip).

---

## Ground-truth API facts (verified first-hand — do NOT re-derive)

- `Pipeline(output_base_dir: pathlib.Path, config: FrameworkConfig | None = None, agent: AgentCore | None = None)` — `src/harness/pipeline.py:62`. If `agent is None` → constructs `AgentCore(config=self.config.agent)` (so pass an explicit `AgentCore`).
- `Pipeline.run_iteration_loop(customer_profile: Profile, seed_instruction: dict|None, sensitivity: dict|None, max_iter: int=10, collect: Callable[[str, dict], Profile|RunFailure]|None=None, build: Callable[[dict], BuildResult]|None=None) -> PipelineResult` — `src/harness/pipeline.py:557`.
- `PipelineResult` fields (`src/models/results.py:48`): `success`, `stop_reason`, `best_iteration`, `degraded`, `history_path`, `error`. **No `total_iterations` and no `comparison_report` populated by the loop.**
- `IterationHistory.load(filepath: pathlib.Path) -> IterationHistory` — `src/observability/iteration_history.py:219`. Fields: `records: list[IterationRecord]`, `total_iterations: int`, `best_iteration: int|None`, `degraded: bool`. `save` writes `model_dump_json`.
- `IterationRecord` fields (`src/observability/iteration_history.py:35`): `iteration: int`, `converged: bool`, `topdown_diffs: dict[str,float]` (per-metric `diff_pct`, keys `frontend_bound`/`backend_bound`/`bad_speculation`/`retiring`), `memory_diff_pct`, `coverage_pct`, `strategy_priority: int` (priority==1 runtime, ≥2 structural), `score: float|None`, `adjustments`, `applied_moves`, `observed_effects`, `failed`, `build_failed`, `build_stderr`. **The full comparison `report` is NOT persisted** — only `topdown_diffs` scalars.
- L1 metric keys (canonical, underscore): `"frontend_bound"`, `"backend_bound"`, `"bad_speculation"`, `"retiring"`. `TopdownL1` fields are these four, as **percentages 0-100**.
- `MetricsCollector(devkit_cmd: str|None=None, perf_cmd: str="perf")` — `src/harness/metrics_collector.py:25`. `collect_topdown(output_path: pathlib.Path, duration: int=60, interval: int=3, pid: int|None=None) -> CollectionResult` — `src/harness/metrics_collector.py:31`. `parse_topdown_file(filepath) -> Profile` dispatches `.txt`→`parse_text`.
- `TopdownParser.parse_text(filepath) -> Profile` — `src/ingestion/topdown_parser.py:146`. Regex: `r"^\s*(backend bound|frontend bound|bad speculation|retiring)\s+([\d.]+)"`.
- `Profile` (`src/profile/profile_schema.py:98`): L1 field is `topdown: TopdownL1 | None` (NOT `topdown_l1`). Has `model_dump_json()`/`model_validate_json()`.
- `AgentCore(config: AgentConfig | None = None)` — `src/agent/agent_core.py:80`. `is_available() -> bool` (`:88`): True iff `api_key is not None` AND client available. So `AgentConfig(api_key=None)` → `is_available()` False → degraded runtime-only.
- `FrameworkConfig.from_yaml(path)` / `.defaults()` — `src/config/framework_config.py:85`. Sub-models: `agent{model,max_tokens,api_key,base_url,provider}`, `comparison{topdown_threshold_pct:10.0,...}`, `devkit{devkit_cmd,duration_seconds:20,interval_seconds:3,cpu_range:str|None,collect_pid:True}`, `run_defaults{warmup_seconds:30,measurement_seconds:60,thread_count:4,qps:100}`.
- `run_and_collect` (`src/harness/pipeline.py:256`): launches `["taskset","-c",cpu_range,binary,config_path]` (taskset only, NO numactl), `time.sleep(warmup_seconds)`, crash-check, `collect_topdown(pid)`, `wait`, `parse_topdown_file`. Reads `project_dir/config.json` (pre-written by codegen; runtime tier rewrites it at `:842`). **A custom `collect` callable must mirror this, prepending `numactl` to the launch.**
- `load_sensitivity(path) -> dict[str, dict]` — `src/agent/adjustment.py:98`. On-disk shape: `{"verdicts": [{"knob","target_metric","expected","verdict","values","metric_values"}]}`. **Critical rename:** loader exposes `"expected"` as `"expected_direction"`.
- Seed shape (`examples/steerability_spike.py:96` `base_instruction()`): top-level keys `project_name`, `compile_flags`, `dependencies`, `dep_headers`, `stages` (each `{implementation_strategy, stage_name, strategies:[{strategy, synthesis_config}]}`), `config` (`{thread_count,qps,warmup_seconds,measurement_seconds,compute_ratio,memory_ratio,ramp_up_seconds}`).

**Import convention:** tests use bare packages (`from harness...`, `from config...`, `from models...`, `from observability...`, `from profile...`, `from ingestion...`, `from agent...`) — works via `pythonpath=["src"]`. **No `tests/examples/` exists; new example-adjacent tests go under `tests/examples/`.** `examples/*.py` use a `sys.path.insert` prepend (see `steerability_spike.py:71`) to reach `src/`; follow that pattern.

---

## File Structure

```
examples/
  third_party/
    taskflow/
      taskflow/            # vendored header set (taskflow.hpp master + subheaders) — committed
      LICENSE              # taskflow MIT
      README.md            # tag v3.9.0 + source URL + SHA-256 manifest
  scenarios/
    collect_common.py      # CollectionConfig (from_yaml) + numactl_taskset_prefix + synthetic_collect
    memory_bound/
      reference/{CMakeLists.txt, main.cpp, scan.h, scan.cpp, README.md}
      collection.yaml      # shared collection params (read by collect_reference.py AND run_loop_demo.py)
      seed_instruction.json
      sensitivity.json
      collect_reference.py
    compute_bound/
      reference/{CMakeLists.txt, main.cpp, matmul.h, matmul.cpp, README.md}
      collection.yaml
      seed_instruction.json
      sensitivity.json
      collect_reference.py
  run_loop_demo.py          # driver: --scenario --max-iter --threshold --out-dir --no-agent
tests/
  examples/
    conftest.py             # puts examples/ + examples/scenarios/ on sys.path
    test_taskflow_vendor.py
    test_collect_common.py
    test_scenarios_seeds.py
    test_collect_reference.py
    test_run_loop_demo.py
```

**Responsibilities:** `collect_common.py` is the ONE shared module (DRY) — both `collect_reference.py` and `run_loop_demo.py` import it. `criteria.py` logic lives inside `run_loop_demo.py` (a pure function `evaluate_criteria`) so it's unit-testable. `conftest.py` makes the `examples/` modules importable from `tests/examples/` (mirrors `steerability_spike.py`'s sys.path prepend).

---

## Task 1: Vendor taskflow v3.9.0 + traceability

**Files:**
- Create: `examples/third_party/taskflow/taskflow/` (header set, ~50 files)
- Create: `examples/third_party/taskflow/LICENSE`
- Create: `examples/third_party/taskflow/README.md`
- Test: `tests/examples/conftest.py`, `tests/examples/test_taskflow_vendor.py`

- [ ] **Step 1: Fetch the v3.9.0 release archive + extract the header set**

Run (requires network — confirmed available):
```bash
cd mirage
curl -L --fail -o /tmp/tf.tar.gz https://github.com/taskflow/taskflow/archive/refs/tags/v3.9.0.tar.gz
mkdir -p examples/third_party/taskflow
tar -xzf /tmp/tf.tar.gz -C /tmp
cp -r /tmp/taskflow-3.9.0/taskflow examples/third_party/taskflow/taskflow
cp /tmp/taskflow-3.9.0/LICENSE examples/third_party/taskflow/LICENSE
```
Expected: `examples/third_party/taskflow/taskflow/taskflow.hpp` exists (the master include).

- [ ] **Step 1b: Handle the `check-added-large-files` pre-commit hook**

taskflow's header set is ~2 MB total; the master `taskflow.hpp` may exceed the hook's default 500kb ceiling. Check the largest file:
```bash
cd mirage
find examples/third_party/taskflow -name '*.hpp' -exec ls -la {} + | sort -k5 -n | tail -3
```
If the largest exceeds 500kb, the `check-added-large-files` hook will block the commit. Fix by raising the hook's `--maxkb` to 3072 in `.pre-commit-config.yaml` (deliberate vendor of a known-2MB header-only lib). The change:
```yaml
    hooks:
      - id: check-added-large-files
        args: [--maxkb=3072]
```
(Only touch that one hook's args; leave the others.) Commit this `.pre-commit-config.yaml` change with the vendor commit in Step 7.

- [ ] **Step 2: Compute the SHA-256 manifest + archive hash**

Run:
```bash
sha256sum /tmp/tf.tar.gz | cut -d' ' -f1 > /tmp/archive.sha
cd examples/third_party/taskflow
find taskflow -type f -name '*.hpp' | sort | xargs sha256sum > manifest.sha256
cd -
```
Expected: `manifest.sha256` lists every vendored header's sha256.

- [ ] **Step 3: Write `examples/third_party/taskflow/README.md`**

```markdown
# taskflow (vendored)

- **Upstream:** https://github.com/taskflow/taskflow
- **Pinned tag:** v3.9.0 (last v3.x; stays C++17 — v4.x requires C++20)
- **Source archive:** https://github.com/taskflow/taskflow/archive/refs/tags/v3.9.0.tar.gz
- **Archive SHA-256:** <paste /tmp/archive.sha contents>
- **License:** MIT (see `LICENSE`)

## Per-file SHA-256 manifest

See `manifest.sha256`. Re-verify with:
```
cd examples/third_party/taskflow && sha256sum -c manifest.sha256
```

## How it was vendored

Fetched the v3.9.0 tarball, copied `taskflow/` (the header set) + `LICENSE`. No patches. The C++17
reference demos include it via `#include <taskflow/taskflow.hpp>` with the include directory pointed
at `examples/third_party/taskflow` (so the `<taskflow/...>` path resolves).
```
Replace `<paste /tmp/archive.sha contents>` with the actual hash string. Save `manifest.sha256` into `examples/third_party/taskflow/manifest.sha256` (committed).

- [ ] **Step 4: Write `tests/examples/conftest.py`** (makes example modules importable)

```python
"""Pytest config: put examples/ + examples/scenarios/ on sys.path so test modules
can `import run_loop_demo` and `import collect_common` (mirrors steerability_spike's
sys.path prepend). Bare mirage packages (harness, config, ...) already resolve via
pyproject's `pythonpath=["src"]`."""

import pathlib
import sys

_EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"
sys.path.insert(0, str(_EXAMPLES))
sys.path.insert(0, str(_EXAMPLES / "scenarios"))
```

- [ ] **Step 5: Write the failing test `tests/examples/test_taskflow_vendor.py`**

```python
"""Vendored taskflow is traceable: header set present, LICENSE is MIT, README records
tag + URL + manifest, and the manifest still matches the files on disk."""

import pathlib
import subprocess

_VENDOR = pathlib.Path(__file__).resolve().parents[2] / "examples" / "third_party" / "taskflow"


def test_master_include_present() -> None:
    assert (_VENDOR / "taskflow" / "taskflow.hpp").is_file()


def test_license_is_mit() -> None:
    text = (_VENDOR / "LICENSE").read_text()
    assert "MIT" in text and "permission" in text.lower()


def test_readme_records_tag_url_manifest() -> None:
    text = (_VENDOR / "README.md").read_text()
    assert "v3.9.0" in text
    assert "github.com/taskflow/taskflow" in text
    assert "SHA-256" in text


def test_manifest_matches_files_on_disk() -> None:
    manifest = _VENDOR / "manifest.sha256"
    assert manifest.is_file()
    result = subprocess.run(
        ["sha256sum", "-c", "manifest.sha256"],
        cwd=str(_VENDOR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_taskflow_vendor.py -q`
Expected: PASS (4 tests). If `sha256sum` is absent on Windows, the implementer swaps it for a Python `hashlib` re-implementation in the test — but the `sha256sum` CLI is available in this Git-Bash environment.

- [ ] **Step 7: Commit**

```bash
cd mirage
git add examples/third_party/taskflow tests/examples/conftest.py tests/examples/test_taskflow_vendor.py
git commit -m "feat: vendor taskflow v3.9.0 with SHA-256 manifest for steering-validation demos"
```

---

## Task 2: `collection.yaml` (memory_bound) + `CollectionConfig` loader

**Files:**
- Create: `examples/scenarios/memory_bound/collection.yaml`
- Create: `examples/scenarios/collect_common.py`
- Test: `tests/examples/test_collect_common.py`

- [ ] **Step 1: Write `examples/scenarios/memory_bound/collection.yaml`**

These are the shared collection parameters — read by BOTH `collect_reference.py` and `run_loop_demo.py`, guaranteeing identical collection conditions on the reference and synthetic sides. CPU mask + NUMA node are placeholders the user edits for their Kunpeng SKU; `per_worker_buffer_mb=64` assumes a 16 MB per-NUMA-node LLC (≥2-3× → 64 MB).

```yaml
# Shared collection parameters for the memory_bound scenario.
# Read by collect_reference.py (reference side) AND run_loop_demo.py (synthetic side)
# so both captures use IDENTICAL conditions: same duration, interval, perf rate, CPU
# pinning, NUMA binding. Edit cpu_mask + numa_node for your Kunpeng SKU.
duration_seconds: 20
interval_seconds: 3
perf_freq: 99
cpu_mask: "0-63"        # taskset -c cpu list; pin to the cores the workload owns
numa_node: "0"          # numactl --cpunodebind/--membind node owning cpu_mask
per_worker_buffer_mb: 64   # >= 2-3x the per-NUMA-node LLC (16MB LLC -> 64MB)
warmup_seconds: 5
measurement_seconds: 20
llc_miss_floor_pct: 90.0   # memory_bound capture gate: cache-miss rate must exceed this
```

- [ ] **Step 2: Write the failing test `tests/examples/test_collect_common.py`** (CollectionConfig portion)

```python
"""CollectionConfig loads collection.yaml and exposes typed fields used by both
the reference collector and the driver's synthetic collect callable."""

import pathlib

import collect_common  # type: ignore[import-not-found]

_SCENARIO = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios" / "memory_bound"


def test_collection_config_loads_typed_fields() -> None:
    cfg = collect_common.CollectionConfig.from_yaml(_SCENARIO / "collection.yaml")
    assert cfg.duration_seconds == 20
    assert cfg.interval_seconds == 3
    assert cfg.perf_freq == 99
    assert cfg.cpu_mask == "0-63"
    assert cfg.numa_node == "0"
    assert cfg.per_worker_buffer_mb == 64
    assert cfg.warmup_seconds == 5
    assert cfg.measurement_seconds == 20
    assert cfg.llc_miss_floor_pct == 90.0
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_collect_common.py -q`
Expected: FAIL (module `collect_common` has no `CollectionConfig`).

- [ ] **Step 4: Write minimal `examples/scenarios/collect_common.py`** (CollectionConfig only, rest added in Task 3)

```python
"""Shared collection utilities for the steering-validation demos.

Imported by both collect_reference.py (reference side) and run_loop_demo.py
(synthetic side) so both captures use identical collection conditions from one
collection.yaml. Mirrors steerability_spike's sys.path-prepend convention for
reaching src/ packages (harness, config, ...).
"""

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import yaml  # type: ignore[import-not-found]


class CollectionConfig:
    """Typed view of a scenario's collection.yaml (shared by both sides)."""

    def __init__(
        self,
        duration_seconds: int,
        interval_seconds: int,
        perf_freq: int,
        cpu_mask: str,
        numa_node: str,
        per_worker_buffer_mb: int,
        warmup_seconds: int,
        measurement_seconds: int,
        llc_miss_floor_pct: float = 0.0,
    ) -> None:
        self.duration_seconds = duration_seconds
        self.interval_seconds = interval_seconds
        self.perf_freq = perf_freq
        self.cpu_mask = cpu_mask
        self.numa_node = numa_node
        self.per_worker_buffer_mb = per_worker_buffer_mb
        self.warmup_seconds = warmup_seconds
        self.measurement_seconds = measurement_seconds
        self.llc_miss_floor_pct = llc_miss_floor_pct

    @classmethod
    def from_yaml(cls, path: pathlib.Path) -> "CollectionConfig":
        data = yaml.safe_load(path.read_text())
        return cls(
            duration_seconds=int(data["duration_seconds"]),
            interval_seconds=int(data["interval_seconds"]),
            perf_freq=int(data["perf_freq"]),
            cpu_mask=str(data["cpu_mask"]),
            numa_node=str(data["numa_node"]),
            per_worker_buffer_mb=int(data["per_worker_buffer_mb"]),
            warmup_seconds=int(data["warmup_seconds"]),
            measurement_seconds=int(data["measurement_seconds"]),
            llc_miss_floor_pct=float(data.get("llc_miss_floor_pct", 0.0)),
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_collect_common.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd mirage
git add examples/scenarios/memory_bound/collection.yaml examples/scenarios/collect_common.py tests/examples/test_collect_common.py
git commit -m "feat: add collection.yaml + CollectionConfig loader for memory_bound"
```

---

## Task 3: `numactl_taskset_prefix` + `synthetic_collect` + compute_bound `collection.yaml`

**Files:**
- Modify: `examples/scenarios/collect_common.py` (add prefix + synthetic_collect)
- Create: `examples/scenarios/compute_bound/collection.yaml`
- Test: `tests/examples/test_collect_common.py` (add tests)

- [ ] **Step 1: Write `examples/scenarios/compute_bound/collection.yaml`**

Same shape; no LLC gate needed for compute_bound (the gate is memory_bound-only, so the floor stays 0.0).

```yaml
# Shared collection parameters for the compute_bound scenario.
duration_seconds: 20
interval_seconds: 3
perf_freq: 99
cpu_mask: "0-63"
numa_node: "0"
per_worker_buffer_mb: 8      # compute-bound; small buffer, the work is the matmul
warmup_seconds: 5
measurement_seconds: 20
llc_miss_floor_pct: 0.0     # no LLC gate for compute_bound
```

- [ ] **Step 2: Add the failing tests to `tests/examples/test_collect_common.py`**

Append:
```python
import subprocess  # noqa: E402  (top-of-file in practice; shown appended for the plan)


def test_numactl_prefix_orders_numactl_before_taskset() -> None:
    prefix = collect_common.numactl_taskset_prefix(cpu_mask="4-7", numa_node="1")
    # numactl must wrap taskset so BOTH cpu + memory bind apply to the binary.
    assert prefix[:2] == ["numactl", "--cpunodebind=1"]
    assert "--membind=1" in prefix
    i_taskset = prefix.index("taskset")
    assert prefix[i_taskset + 1] == "-c"
    assert prefix[i_taskset + 2] == "4-7"


def test_synthetic_collect_launches_with_numactl_and_collects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path  # noqa: ARG001
) -> None:
    """The synthetic collect callable mirrors run_and_collect but prepends numactl.
    Asserts the launched argv carries numactl+taskset and collect_topdown is invoked."""
    cfg = collect_common.CollectionConfig(
        duration_seconds=20, interval_seconds=3, perf_freq=99,
        cpu_mask="0-3", numa_node="0", per_worker_buffer_mb=8,
        warmup_seconds=0, measurement_seconds=1,
    )
    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 4242
        def poll(self) -> int | None:
            return None  # alive through warmup
        def wait(self, timeout: float | None = None) -> int:
            return 0
        def kill(self) -> None:
            return None

    def fake_popen(cmd, **_kw):
        captured["cmd"] = cmd
        return _FakeProc()

    def fake_sleep(_s: float) -> None:
        return None

    class _Coll:
        success = True
        topdown_path = str(tmp_path / "topdown.txt")
        error = ""

    fake_metrics = type("M", (), {})()
    fake_metrics.collect_topdown = lambda out, duration, interval, pid: _Coll()  # noqa: ARG005
    fake_metrics.parse_topdown_file = lambda p: _make_profile()

    monkeypatch.setattr(collect_common.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(collect_common.time, "sleep", fake_sleep)

    # config.json must already exist (codegen writes it; run_and_collect assumes it).
    (tmp_path / "config.json").write_text("{}")
    binary = tmp_path / "wk"
    binary.write_text("#!/bin/sh\n")

    prof = collect_common.synthetic_collect(
        str(binary), {"config": {"warmup_seconds": 0, "measurement_seconds": 1}},
        cfg=cfg, metrics=fake_metrics, project_dir=tmp_path,
    )
    cmd = list(captured["cmd"])  # type: ignore[arg-type]
    assert "numactl" in cmd
    assert "taskset" in cmd
    assert prof is not None  # returned a Profile


def _make_profile():
    from profile.profile_schema import Profile, ProfileMetadata, TopdownL1  # type: ignore[import-not-found]
    return Profile(
        metadata=ProfileMetadata(customer="devkit", date="unknown"),
        topdown=TopdownL1(frontend_bound=10.0, backend_bound=65.0, bad_speculation=5.0, retiring=20.0),
    )
```
Also add `import pytest` at the top of the test file (it's needed for `pytest.MonkeyPatch`).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_collect_common.py -q`
Expected: FAIL (`numactl_taskset_prefix` / `synthetic_collect` undefined; `subprocess`/`time` not imported in collect_common).

- [ ] **Step 4: Extend `examples/scenarios/collect_common.py`** — add `import subprocess`, `import time`, `numactl_taskset_prefix`, `synthetic_collect`. Append after the `CollectionConfig` class:

```python
import subprocess
import time

# Re-export for tests to monkeypatch (matches BuildRunner's br.subprocess pattern intent
# but on the stdlib module so monkeypatch.setattr(collect_common.subprocess, ...) works).
subprocess = subprocess  # noqa: PLW0127 (keeps the name live for monkeypatch)


def numactl_taskset_prefix(cpu_mask: str, numa_node: str) -> list[str]:
    """Build the launch prefix that binds CPU + memory (numactl) then pins cores (taskset).

    numactl wraps taskset so BOTH the cpu node bind and the memory bind apply to the
    binary; taskset then further restricts to cpu_mask within that node. Order matters:
    `numactl --cpunodebind=N --membind=N taskset -c <mask> <binary> ...`
    """
    return [
        "numactl",
        f"--cpunodebind={numa_node}",
        f"--membind={numa_node}",
        "taskset",
        "-c",
        cpu_mask,
    ]


def synthetic_collect(
    binary: str,
    instr: dict[str, object],
    *,
    cfg: CollectionConfig,
    metrics: object,
    project_dir: pathlib.Path,
) -> object:
    """Custom `collect` callable for run_loop_demo: mirrors Pipeline.run_and_collect
    but prepends numactl (NUMA-bound memory) to the launch. Reads the pre-written
    project_dir/config.json (codegen writes it; the runtime tier rewrites it). Returns
    a workload Profile or a RunFailure.

    Mirrors src/harness/pipeline.py:256 run_and_collect's happy path; run failures
    return a RunFailure and the loop's run_failure_streak handles retries.
    """
    from models.results import RunFailure  # type: ignore[import-not-found]
    from profile.profile_schema import Profile  # type: ignore[import-not-found]

    config_path = str((project_dir / "config.json").resolve())
    launch_cmd: list[str] = numactl_taskset_prefix(cfg.cpu_mask, cfg.numa_node) + [
        str(binary),
        config_path,
    ]
    try:
        proc = subprocess.Popen(
            launch_cmd,
            cwd=str(project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return RunFailure(reason=f"binary_launch_failed: {exc}", kind="crash")

    instr_cfg = instr.get("config", {}) if isinstance(instr, dict) else {}
    warmup = int(instr_cfg.get("warmup_seconds", cfg.warmup_seconds))  # type: ignore[union-attr]
    time.sleep(warmup)
    if proc.poll() is not None:
        return RunFailure(
            reason=f"workload_exited_during_warmup rc={proc.returncode}",
            kind="crash",
        )

    td_path = project_dir / "topdown.txt"
    pid = int(proc.pid)
    coll = metrics.collect_topdown(  # type: ignore[attr-defined]
        td_path,
        duration=cfg.measurement_seconds,
        interval=cfg.interval_seconds,
        pid=pid,
    )
    if not coll.success or coll.topdown_path is None:
        proc.wait(timeout=cfg.measurement_seconds + 30)
        return RunFailure(reason=coll.error or "collect_failed", kind="collect_fail")
    try:
        proc.wait(timeout=cfg.measurement_seconds + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return RunFailure(reason="workload_hang", kind="timeout")
    prof = metrics.parse_topdown_file(pathlib.Path(coll.topdown_path))  # type: ignore[attr-defined]
    if prof.topdown is None:  # type: ignore[attr-defined]
        return RunFailure(reason="no_topdown_l1_lines", kind="collect_fail")
    return prof
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_collect_common.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd mirage
git add examples/scenarios/collect_common.py examples/scenarios/compute_bound/collection.yaml tests/examples/test_collect_common.py
git commit -m "feat: numactl+taskset prefix + synthetic_collect callable, compute_bound collection.yaml"
```

---

## Task 4: `memory_bound` reference demo (C++)

**Files:**
- Create: `examples/scenarios/memory_bound/reference/CMakeLists.txt`
- Create: `examples/scenarios/memory_bound/reference/scan.h`
- Create: `examples/scenarios/memory_bound/reference/scan.cpp`
- Create: `examples/scenarios/memory_bound/reference/main.cpp`
- Create: `examples/scenarios/memory_bound/reference/README.md`
- Test: `tests/examples/test_memory_bound_demo.py`

- [ ] **Step 1: Write `scan.h`**

```cpp
#pragma once
// Self-developed LLC-missing random-access scan. Each worker owns a private buffer
// sized >= 2-3x the per-NUMA-node LLC; random access into it defeats the cache.
#include <cstdint>
#include <random>
#include <vector>

// Random-access `accesses` reads over `buf`; returns a checksum so the optimizer
// cannot elide the loads. `rng` is per-worker (no shared state).
uint64_t random_scan(const std::vector<uint8_t>& buf, uint64_t accesses, std::mt19937_64& rng);
```

- [ ] **Step 2: Write `scan.cpp`**

```cpp
#include "scan.h"

uint64_t random_scan(const std::vector<uint8_t>& buf, uint64_t accesses, std::mt19937_64& rng) {
    uint64_t checksum = 0;
    const uint64_t size = buf.size();
    for (uint64_t i = 0; i < accesses; ++i) {
        const uint64_t idx = rng() % size;
        checksum += buf[idx];
    }
    return checksum;
}
```

- [ ] **Step 3: Write `main.cpp`**

```cpp
// memory_bound reference demo: a taskflow graph of N workers, each performing
// LLC-missing random-access reads over its OWN buffer (>= 2-3x per-NUMA-LLC).
// Prints __MEASUREMENT_WINDOW_START__ when warmup ends so collect_reference.py
// aligns perf/devkit collection to the steady-state window only.
#include <taskflow/taskflow.hpp>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>
#include "scan.h"

int main(int argc, char** argv) {
    // config.json path is argv[1] (mirage runtime contract); the reference demo
    // also accepts overrides via env so it can run standalone on the ARM box.
    const unsigned cores = std::thread::hardware_concurrency();
    const unsigned n_workers = cores > 0 ? cores : 1;
    const uint64_t per_worker_mb =
        argc > 2 ? std::stoull(argv[2]) : 64;  // default 64MB (>= 2-3x a 16MB LLC)
    const uint64_t accesses = 50'000'000ULL;   // work-per-call, time-boxed below
    const int warmup_seconds = argc > 3 ? std::stoi(argv[3]) : 5;
    const int measurement_seconds = argc > 4 ? std::stoi(argv[4]) : 20;

    // First-touch: allocate per-worker buffers NOW (numactl has already bound
    // memory to the local node, so first-touch placement is correct).
    std::vector<std::vector<uint8_t>> buffers(n_workers);
    std::vector<uint64_t> sums(n_workers, 0);
    for (auto& b : buffers) {
        b.resize(per_worker_mb * 1024ULL * 1024ULL);
        std::fill(b.begin(), b.end(), 0xA5);
    }

    auto run_workers = [&](int seconds) {
        tf::Executor executor;
        tf::Taskflow tf;
        for (unsigned w = 0; w < n_workers; ++w) {
            tf.emplace([&, w] {
                std::mt19937_64 rng(0x600d + w);
                sums[w] += random_scan(buffers[w], accesses, rng);
            });
        }
        // Run for `seconds`: loop the graph until the window elapses.
        auto deadline = std::chrono::steady_clock::now()
                        + std::chrono::seconds(seconds);
        while (std::chrono::steady_clock::now() < deadline) {
            executor.run(tf).wait();
        }
    };

    run_workers(warmup_seconds);
    std::cout << "__MEASUREMENT_WINDOW_START__" << std::endl;
    run_workers(measurement_seconds);

    // Sink the sums so nothing is optimized out.
    uint64_t total = 0;
    for (auto s : sums) total += s;
    std::cerr << "checksum_total=" << total << std::endl;
    return 0;
}
```

- [ ] **Step 4: Write `CMakeLists.txt`**

```cmake
cmake_minimum_required(VERSION 3.16)
project(memory_bound_ref CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(memory_bound_ref main.cpp scan.cpp)
target_compile_options(memory_bound_ref PRIVATE -O2 -march=armv8.2-a -fno-inline-small-functions)
# include dir -> examples/third_party/taskflow  so <taskflow/taskflow.hpp> resolves
target_include_directories(memory_bound_ref PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../../third_party/taskflow)
```

- [ ] **Step 5: Write `reference/README.md`**

```markdown
# memory_bound reference demo

LLC-missing random-access scan over per-worker buffers (>= 2-3x per-NUMA-LLC),
backend_bound-dominated Topdown target ~65-75.

## Build (on the Kunpeng ARM box)
```
cmake -S examples/scenarios/memory_bound/reference -B build/mem
cmake --build build/mem
```

## Run standalone (sanity)
```
numactl --cpunodebind=0 --membind=0 taskset -c 0-63 ./build/mem/memory_bound_ref
```

## Capture the fitting target
```
python3 examples/scenarios/memory_bound/collect_reference.py \
    --binary build/mem/memory_bound_ref --out-dir examples/scenarios/memory_bound
```
The collector watches stdout for `__MEASUREMENT_WINDOW_START__`, then starts perf +
devkit topdown (steady-state only). It gates on LLC-miss > 90%; if the per-worker
buffer is too small for this box's LLC, it warns — fix `collection.yaml`'s
`per_worker_buffer_mb` and re-capture.
```

- [ ] **Step 6: Write the failing test `tests/examples/test_memory_bound_demo.py`**

```python
"""The memory_bound reference demo is buildable: CMakeLists declares the target +
flags, main.cpp prints the steady-state marker, scan.{h,cpp} define the kernel."""

import pathlib

_REF = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios" / "memory_bound" / "reference"


def test_cmake_declares_target_and_flags() -> None:
    text = (_REF / "CMakeLists.txt").read_text()
    assert "add_executable(memory_bound_ref main.cpp scan.cpp)" in text
    assert "-O2" in text and "-march=armv8.2-a" in text
    assert "third_party/taskflow" in text


def test_main_prints_measurement_marker() -> None:
    text = (_REF / "main.cpp").read_text()
    assert "__MEASUREMENT_WINDOW_START__" in text


def test_scan_kernel_declared() -> None:
    assert (_REF / "scan.h").is_file()
    assert (_REF / "scan.cpp").is_file()
    assert "random_scan" in (_REF / "scan.h").read_text()
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_memory_bound_demo.py -q`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
cd mirage
git add examples/scenarios/memory_bound/reference tests/examples/test_memory_bound_demo.py
git commit -m "feat: memory_bound reference demo (taskflow LLC-missing scan) + marker"
```

---

## Task 5: `compute_bound` reference demo (C++)

**Files:**
- Create: `examples/scenarios/compute_bound/reference/{CMakeLists.txt, matmul.h, matmul.cpp, main.cpp, README.md}`
- Test: `tests/examples/test_compute_bound_demo.py`

- [ ] **Step 1: Write `matmul.h`**

```cpp
#pragma once
// Self-developed dense matmul kernel (blocked/unrolled). FP MAC work -> retiring-bound.
#include <cstdint>
#include <vector>

// C = A * B  (K x K), accumulate into C. Return a checksum of C for sink.
double matmul_checksum(const std::vector<double>& A,
                       const std::vector<double>& B,
                       int K);
```

- [ ] **Step 2: Write `matmul.cpp`** (a naïve-but-real blocked matmul)

```cpp
#include "matmul.h"
#include <cmath>

double matmul_checksum(const std::vector<double>& A,
                      const std::vector<double>& B,
                      int K) {
    std::vector<double> C(static_cast<size_t>(K) * K, 0.0);
    const int BS = 64;  // block size
    for (int ii = 0; ii < K; ii += BS) {
        for (int jj = 0; jj < K; jj += BS) {
            for (int kk = 0; kk < K; kk += BS) {
                int i_end = std::min(ii + BS, K);
                int j_end = std::min(jj + BS, K);
                int k_end = std::min(kk + BS, K);
                for (int i = ii; i < i_end; ++i) {
                    for (int j = jj; j < j_end; ++j) {
                        double acc = C[i * K + j];
                        for (int k = kk; k < k_end; ++k) {
                            acc += A[i * K + k] * B[k * K + j];
                        }
                        C[i * K + j] = acc;
                    }
                }
            }
        }
    }
    double checksum = 0.0;
    for (double v : C) checksum += v;
    return checksum;
}
```

- [ ] **Step 3: Write `main.cpp`**

```cpp
// compute_bound reference demo: a taskflow graph of N workers, each running dense
// matmul repeatedly for the measurement window. retiring-dominated Topdown ~55-65.
#include <taskflow/taskflow.hpp>
#include <chrono>
#include <iostream>
#include <random>
#include <thread>
#include <vector>
#include "matmul.h"

int main(int argc, char** argv) {
    const unsigned n_workers = std::thread::hardware_concurrency() ?: 1;
    const int K = argc > 2 ? std::stoi(argv[2]) : 256;
    const int warmup_seconds = argc > 3 ? std::stoi(argv[3]) : 5;
    const int measurement_seconds = argc > 4 ? std::stoi(argv[4]) : 20;

    std::vector<double> sums(n_workers, 0.0);
    auto run_workers = [&](int seconds) {
        tf::Executor executor;
        tf::Taskflow tf;
        for (unsigned w = 0; w < n_workers; ++w) {
            tf.emplace([&, w] {
                std::mt19937_64 rng(0x600d + w);
                std::vector<double> A(static_cast<size_t>(K) * K), B(static_cast<size_t>(K) * K);
                for (auto& v : A) v = static_cast<double>(rng()) / rng.max();
                for (auto& v : B) v = static_cast<double>(rng()) / rng.max();
                double local = 0.0;
                auto deadline = std::chrono::steady_clock::now()
                                + std::chrono::seconds(seconds);
                while (std::chrono::steady_clock::now() < deadline) {
                    local += matmul_checksum(A, B, K);
                }
                sums[w] = local;
            });
        }
        executor.run(tf).wait();
    };

    run_workers(warmup_seconds);
    std::cout << "__MEASUREMENT_WINDOW_START__" << std::endl;
    run_workers(measurement_seconds);

    double total = 0.0;
    for (double s : sums) total += s;
    std::cerr << "checksum_total=" << total << std::endl;
    return 0;
}
```

- [ ] **Step 4: Write `CMakeLists.txt`** (same shape as memory_bound)

```cmake
cmake_minimum_required(VERSION 3.16)
project(compute_bound_ref CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(compute_bound_ref main.cpp matmul.cpp)
target_compile_options(compute_bound_ref PRIVATE -O2 -march=armv8.2-a)
target_include_directories(compute_bound_ref PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../../third_party/taskflow)
```

- [ ] **Step 5: Write `reference/README.md`** (mirror memory_bound's; capture command points at `compute_bound_ref`)

```markdown
# compute_bound reference demo

Dense matmul across N taskflow workers, retiring_bound-dominated Topdown target ~55-65.

## Build
```
cmake -S examples/scenarios/compute_bound/reference -B build/cmp
cmake --build build/cmp
```

## Capture the fitting target
```
python3 examples/scenarios/compute_bound/collect_reference.py \
    --binary build/cmp/compute_bound_ref --out-dir examples/scenarios/compute_bound
```
No LLC-miss gate for compute_bound; the collector just confirms the capture is
retiring-dominated before fitting.
```

- [ ] **Step 6: Write `tests/examples/test_compute_bound_demo.py`** (mirror the memory_bound test)

```python
"""compute_bound reference demo: CMake target + flags, marker, matmul kernel."""

import pathlib

_REF = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios" / "compute_bound" / "reference"


def test_cmake_declares_target_and_flags() -> None:
    text = (_REF / "CMakeLists.txt").read_text()
    assert "add_executable(compute_bound_ref main.cpp matmul.cpp)" in text
    assert "-O2" in text and "-march=armv8.2-a" in text
    assert "third_party/taskflow" in text


def test_main_prints_measurement_marker() -> None:
    assert "__MEASUREMENT_WINDOW_START__" in (_REF / "main.cpp").read_text()


def test_matmul_kernel_declared() -> None:
    assert (_REF / "matmul.h").is_file()
    assert "matmul_checksum" in (_REF / "matmul.h").read_text()
```

- [ ] **Step 7: Run + commit**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_compute_bound_demo.py -q` → PASS.
```bash
cd mirage
git add examples/scenarios/compute_bound/reference tests/examples/test_compute_bound_demo.py
git commit -m "feat: compute_bound reference demo (taskflow matmul) + marker"
```

---

## Task 6: `memory_bound` seed + sensitivity

**Files:**
- Create: `examples/scenarios/memory_bound/seed_instruction.json`
- Create: `examples/scenarios/memory_bound/sensitivity.json`
- Test: `tests/examples/test_scenarios_seeds.py`

**Design (from spec):** the memory_bound seed is **compute-dominated** so the synthetic workload starts ~35 pp off the target's dominant `backend_bound` (target ~70). Knobs: `mem_stage` `working_set_mb=16`, `access_pattern=sequential`, `memory_ratio=0.2`; `comp_stage` matmul-heavy, `compute_ratio=0.8`.

- [ ] **Step 1: Write `seed_instruction.json`** (same shape as `steerability_spike.base_instruction()`)

```json
{
  "project_name": "memory_bound_seed",
  "compile_flags": "-O2 -march=armv8.2-a -fno-inline-small-functions",
  "dependencies": [],
  "dep_headers": [],
  "stages": [
    {
      "implementation_strategy": "memory_synthesis",
      "stage_name": "mem_stage",
      "strategies": [
        {
          "strategy": "memory_synthesis",
          "synthesis_config": {
            "iterations": 100,
            "working_set_mb": 16,
            "access_pattern": "sequential"
          }
        }
      ]
    },
    {
      "implementation_strategy": "compute_synthesis",
      "stage_name": "comp_stage",
      "strategies": [
        {
          "strategy": "compute_synthesis",
          "synthesis_config": {"archetype": "matmul", "iterations": 100}
        }
      ]
    }
  ],
  "config": {
    "thread_count": 4,
    "qps": 100,
    "warmup_seconds": 5,
    "measurement_seconds": 20,
    "compute_ratio": 0.8,
    "memory_ratio": 0.2,
    "ramp_up_seconds": 5
  }
}
```

- [ ] **Step 2: Write `sensitivity.json`** (on-disk shape `{"verdicts": [...]}` — `load_sensitivity` reads it)

```json
{
  "rows": [],
  "verdicts": [
    {"knob": "working_set_mb", "target_metric": "backend_bound", "expected": "up",
     "verdict": "controllable", "values": [16, 64, 256], "metric_values": [30.0, 50.0, 70.0]},
    {"knob": "access_pattern", "target_metric": "backend_bound", "expected": "up",
     "verdict": "controllable", "values": ["sequential", "mixed", "random"], "metric_values": [30.0, 50.0, 70.0]},
    {"knob": "memory_ratio", "target_metric": "backend_bound", "expected": "up",
     "verdict": "controllable", "values": [0.2, 0.5, 0.8], "metric_values": [35.0, 55.0, 68.0]},
    {"knob": "compute_ratio", "target_metric": "retiring", "expected": "up",
     "verdict": "secondary", "values": [0.2, 0.5, 0.8], "metric_values": [25.0, 40.0, 55.0]},
    {"knob": "thread_count", "target_metric": "frontend_bound", "expected": "up",
     "verdict": "secondary", "values": [4, 8, 16], "metric_values": [12.0, 16.0, 22.0]},
    {"knob": "qps", "target_metric": "bad_speculation", "expected": "down",
     "verdict": "secondary", "values": [100, 500, 1000], "metric_values": [5.0, 4.0, 3.0]}
  ]
}
```

- [ ] **Step 3: Write `tests/examples/test_scenarios_seeds.py`** (memory_bound portion)

```python
"""Seeds are out-of-band (~35pp off the dominant metric) and sensitivity loads via
the production load_sensitivity (exercising the expected->expected_direction rename)."""

import json
import pathlib

from agent.adjustment import load_sensitivity  # type: ignore[import-not-found]

_SCEN = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios"


def test_memory_bound_seed_is_compute_dominated() -> None:
    seed = json.loads((_SCEN / "memory_bound" / "seed_instruction.json").read_text())
    assert seed["config"]["memory_ratio"] == 0.2
    assert seed["config"]["compute_ratio"] == 0.8
    mem = next(s for s in seed["stages"] if s["stage_name"] == "mem_stage")
    assert mem["strategies"][0]["synthesis_config"]["working_set_mb"] == 16
    assert mem["strategies"][0]["synthesis_config"]["access_pattern"] == "sequential"


def test_memory_bound_sensitivity_loads_with_rename() -> None:
    table = load_sensitivity(_SCEN / "memory_bound" / "sensitivity.json")
    assert table["working_set_mb"]["target_metric"] == "backend_bound"
    # the loader renames on-disk "expected" -> in-memory "expected_direction"
    assert table["working_set_mb"]["expected_direction"] == "up"
    assert table["working_set_mb"]["verdict"] == "controllable"
```

- [ ] **Step 4: Run + commit**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_scenarios_seeds.py -q` → PASS.
```bash
cd mirage
git add examples/scenarios/memory_bound/seed_instruction.json examples/scenarios/memory_bound/sensitivity.json tests/examples/test_scenarios_seeds.py
git commit -m "feat: memory_bound out-of-band seed + spike-grounded sensitivity"
```

---

## Task 7: `compute_bound` seed + sensitivity

**Files:**
- Create: `examples/scenarios/compute_bound/seed_instruction.json`
- Create: `examples/scenarios/compute_bound/sensitivity.json`
- Test: extend `tests/examples/test_scenarios_seeds.py`

**Design:** compute_bound seed is **memory-dominated** so the synthetic workload starts ~35 pp off the target's dominant `retiring` (target ~60). `mem_stage` `working_set_mb=256`, `access_pattern=random`, `memory_ratio=0.8`; `comp_stage` `iterations=10`, `archetype=matmul`, `compute_ratio=0.2`.

- [ ] **Step 1: Write `compute_bound/seed_instruction.json`**

```json
{
  "project_name": "compute_bound_seed",
  "compile_flags": "-O2 -march=armv8.2-a -fno-inline-small-functions",
  "dependencies": [],
  "dep_headers": [],
  "stages": [
    {
      "implementation_strategy": "memory_synthesis",
      "stage_name": "mem_stage",
      "strategies": [
        {
          "strategy": "memory_synthesis",
          "synthesis_config": {
            "iterations": 100,
            "working_set_mb": 256,
            "access_pattern": "random"
          }
        }
      ]
    },
    {
      "implementation_strategy": "compute_synthesis",
      "stage_name": "comp_stage",
      "strategies": [
        {
          "strategy": "compute_synthesis",
          "synthesis_config": {"archetype": "matmul", "iterations": 10}
        }
      ]
    }
  ],
  "config": {
    "thread_count": 4,
    "qps": 100,
    "warmup_seconds": 5,
    "measurement_seconds": 20,
    "compute_ratio": 0.2,
    "memory_ratio": 0.8,
    "ramp_up_seconds": 5
  }
}
```

- [ ] **Step 2: Write `compute_bound/sensitivity.json`**

```json
{
  "rows": [],
  "verdicts": [
    {"knob": "compute_ratio", "target_metric": "retiring", "expected": "up",
     "verdict": "controllable", "values": [0.2, 0.5, 0.8], "metric_values": [25.0, 40.0, 55.0]},
    {"knob": "iterations", "target_metric": "retiring", "expected": "up",
     "verdict": "controllable", "values": [10, 50, 100], "metric_values": [30.0, 45.0, 58.0]},
    {"knob": "archetype", "target_metric": "retiring", "expected": "up",
     "verdict": "controllable", "values": ["compute", "matmul"], "metric_values": [35.0, 55.0]},
    {"knob": "memory_ratio", "target_metric": "backend_bound", "expected": "up",
     "verdict": "secondary", "values": [0.2, 0.5, 0.8], "metric_values": [30.0, 50.0, 65.0]},
    {"knob": "thread_count", "target_metric": "frontend_bound", "expected": "up",
     "verdict": "secondary", "values": [4, 8, 16], "metric_values": [12.0, 16.0, 22.0]},
    {"knob": "qps", "target_metric": "bad_speculation", "expected": "down",
     "verdict": "secondary", "values": [100, 500, 1000], "metric_values": [5.0, 4.0, 3.0]}
  ]
}
```

- [ ] **Step 3: Extend `tests/examples/test_scenarios_seeds.py`** with:

```python
def test_compute_bound_seed_is_memory_dominated() -> None:
    seed = json.loads((_SCEN / "compute_bound" / "seed_instruction.json").read_text())
    assert seed["config"]["memory_ratio"] == 0.8
    assert seed["config"]["compute_ratio"] == 0.2
    mem = next(s for s in seed["stages"] if s["stage_name"] == "mem_stage")
    assert mem["strategies"][0]["synthesis_config"]["working_set_mb"] == 256
    assert mem["strategies"][0]["synthesis_config"]["access_pattern"] == "random"


def test_compute_bound_sensitivity_loads() -> None:
    table = load_sensitivity(_SCEN / "compute_bound" / "sensitivity.json")
    assert table["compute_ratio"]["target_metric"] == "retiring"
    assert table["compute_ratio"]["expected_direction"] == "up"
    assert table["archetype"]["verdict"] == "controllable"
```

- [ ] **Step 4: Run + commit**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_scenarios_seeds.py -q` → PASS (4 tests).
```bash
cd mirage
git add examples/scenarios/compute_bound/seed_instruction.json examples/scenarios/compute_bound/sensitivity.json tests/examples/test_scenarios_seeds.py
git commit -m "feat: compute_bound out-of-band seed + spike-grounded sensitivity"
```

---

## Task 8: `collect_reference.py` (reference-side capture)

**Files:**
- Create: `examples/scenarios/memory_bound/collect_reference.py`
- Create: `examples/scenarios/compute_bound/collect_reference.py` (a thin shim)
- Test: `tests/examples/test_collect_reference.py`

**Behavior (from spec):** reads `collection.yaml`; `numactl`+`taskset`-launches the reference binary with stdout piped; watches stdout for `__MEASUREMENT_WINDOW_START__` and only THEN starts `perf record` + `MetricsCollector.collect_topdown(pid)`; `parse_topdown_file` → `Profile`; writes `topdown.json` via `Profile.model_dump_json()` + folds `perf script | flamegraph.pl` (non-fatal); LLC-miss gate (>90%) for memory_bound; prints captured L1.

- [ ] **Step 1: Write `examples/scenarios/memory_bound/collect_reference.py`**

```python
#!/usr/bin/env python3
"""Collect the reference fitting target for the memory_bound scenario on the ARM box.

numactl+taskset-pinned launch; collection starts at the __MEASUREMENT_WINDOW_START__
stdout marker (steady-state only); LLC-miss gate (>90%) rejects a too-small buffer;
writes topdown.json (Profile.model_dump_json) + flamegraph.svg (non-fatal).

Run:  python3 collect_reference.py --binary build/mem/memory_bound_ref \
        [--out-dir .]
"""
import argparse
import pathlib
import subprocess
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE.parent) not in sys.path:  # examples/scenarios for collect_common
    sys.path.insert(0, str(_HERE.parent))

import collect_common  # type: ignore[import-not-found]
from harness.metrics_collector import MetricsCollector  # type: ignore[import-not-found]
from ingestion.topdown_parser import TopdownParser  # type: ignore[import-not-found]

_MARKER = "__MEASUREMENT_WINDOW_START__"


def _wait_for_marker(proc: subprocess.Popen, timeout: int) -> bool:
    """Block until the binary prints the marker; return False on timeout/early exit."""
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    for line in proc.stdout:
        if _MARKER in line:
            return True
        if time.monotonic() > deadline:
            return False
    return False  # process exited before marker


def _llc_miss_rate(binary: str, cfg: collect_common.CollectionConfig,
                   project_dir: pathlib.Path) -> float:
    """cache-miss / cache-references over a short perf-stat window (memory_bound gate)."""
    try:
        out = subprocess.run(
            ["perf", "stat", "-e", "cache-misses,cache-references",
             "--", "taskset", "-c", cfg.cpu_mask, binary,
             str(project_dir / "config.json")],
            capture_output=True, text=True, timeout=cfg.measurement_seconds + 10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 100.0  # perf stat unavailable -> don't block
    text = out.stderr + out.stdout
    misses = _extract_perf_value(text, "cache-misses")
    refs = _extract_perf_value(text, "cache-references")
    if refs == 0:
        return 100.0
    return (misses / refs) * 100.0


def _extract_perf_value(text: str, event: str) -> int:
    for line in text.splitlines():
        if event in line:
            parts = line.split()
            for p in parts:
                p = p.replace(",", "")
                if p.isdigit():
                    return int(p)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--out-dir", default=str(_HERE))
    ap.add_argument("--devkit-cmd", default=None)
    args = ap.parse_args()

    cfg = collect_common.CollectionConfig.from_yaml(_HERE / "collection.yaml")
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = MetricsCollector(devkit_cmd=args.devkit_cmd)
    project_dir = pathlib.Path(args.binary).resolve().parent

    # LLC-miss gate (memory_bound): reject a buffer that's too small for this LLC.
    if cfg.llc_miss_floor_pct > 0.0:
        miss_rate = _llc_miss_rate(args.binary, cfg, project_dir)
        if miss_rate < cfg.llc_miss_floor_pct:
            print(f"WARNING: LLC-miss rate {miss_rate:.1f}% < {cfg.llc_miss_floor_pct}% "
                  f"— per_worker_buffer_mb={cfg.per_worker_buffer_mb} is too small for "
                  f"this box's LLC. Increase it in collection.yaml and re-capture.")
            return 2

    launch = collect_common.numactl_taskset_prefix(cfg.cpu_mask, cfg.numa_node) + [
        args.binary, str(project_dir / "config.json")
    ]
    perf_rec = subprocess.Popen(
        ["perf", "record", "-g", "-F", str(cfg.perf_freq), "-o", str(out_dir / "perf.data"),
         "--", *launch],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if not _wait_for_marker(perf_rec, cfg.warmup_seconds + 30):
        print("ERROR: binary did not print the measurement marker in time.")
        perf_rec.kill()
        return 1

    td_path = out_dir / "topdown.txt"
    coll = metrics.collect_topdown(
        td_path, duration=cfg.measurement_seconds, interval=cfg.interval_seconds,
        pid=perf_rec.pid,
    )
    if not coll.success or coll.topdown_path is None:
        print(f"ERROR: collect_topdown failed: {coll.error}")
        perf_rec.kill()
        return 1
    try:
        perf_rec.wait(timeout=cfg.measurement_seconds + 30)
    except subprocess.TimeoutExpired:
        perf_rec.kill()

    profile = TopdownParser().parse_text(pathlib.Path(coll.topdown_path))
    (out_dir / "topdown.json").write_text(profile.model_dump_json(indent=2))

    # Flamegraph (non-fatal).
    try:
        subprocess.run(
            f"perf script -i {out_dir/'perf.data'} | flamegraph.pl > {out_dir/'flamegraph.svg'}",
            shell=True, check=False, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    td = profile.topdown
    print(f"Captured L1: frontend={td.frontend_bound:.1f} backend={td.backend_bound:.1f} "
          f"bad_spec={td.bad_speculation:.1f} retiring={td.retiring:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write `examples/scenarios/compute_bound/collect_reference.py`** as a thin shim (identical logic; the shim reuses memory_bound's module to stay DRY)

```python
#!/usr/bin/env python3
"""compute_bound reference capture — reuses the memory_bound collector (same logic;
no LLC gate here because collection.yaml sets llc_miss_floor_pct=0.0)."""
import pathlib
import runpy
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_MEM = _HERE.parent / "memory_bound" / "collect_reference.py"
sys.argv = [str(_MEM), f"--out-dir={_HERE}"] + sys.argv[1:]
raise SystemExit(runpy.run_path(str(_MEM), run_name="__main__") is not None)
```
Note: the shim re-runs the memory_bound collector but with the compute_bound `collection.yaml` (the collector loads `collection.yaml` from its own `_HERE`, so the shim must keep its own scenario dir). Adjust the shim to call the memory_bound module's `main()` directly with `collect_common` loading THIS scenario's `collection.yaml`. **Simpler honest alternative:** copy the collector body into both (DRY violation) OR factor the collector into `collect_common.run_reference_capture(cfg, binary, out_dir, devkit_cmd)`. Use the factor approach:

Replace the shim: add to `collect_common.py` a `run_reference_capture(...)` function holding the body of `main()` above, and have BOTH scenario `collect_reference.py` files be 4-line entry points that load their own `collection.yaml` + call it. This is cleaner DRY. **The implementer refactors Task 8 Step 1's `main()` body into `collect_common.run_reference_capture(binary, scenario_dir, devkit_cmd)` and both entry points call it.**

- [ ] **Step 3: Refactor** — move `main()`'s body into `collect_common.run_reference_capture(binary: str, scenario_dir: pathlib.Path, devkit_cmd: str | None) -> int`; rewrite `memory_bound/collect_reference.py` to:
```python
import pathlib, sys, runpy  # noqa
_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
import collect_common  # type: ignore[import-not-found]
raise SystemExit(collect_common.run_reference_capture(
    binary=sys.argv[1] if len(sys.argv) > 1 else "", scenario_dir=_HERE, devkit_cmd=None))
```
(Use `argparse` inside `run_reference_capture` for `--binary`/`--out-dir`/`--devkit-cmd` so the entry point stays thin.) `compute_bound/collect_reference.py` is identical but `scenario_dir=_HERE` resolves to the compute_bound dir (so its `collection.yaml` loads).

- [ ] **Step 4: Write `tests/examples/test_collect_reference.py`** — assert (mocked) the launch carries numactl+taskset, the marker gates collection, and `topdown.json` is written via `Profile.model_dump_json`.

```python
"""collect_reference gates collection on the marker, numactl-pins, writes topdown.json."""
import json
import pathlib
import subprocess

import collect_common  # type: ignore[import-not-found]

_SCEN = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios" / "memory_bound"


def test_reference_capture_numactl_pins_and_gates_on_marker(
    monkeypatch, tmp_path  # noqa: ANN001
) -> None:
    cfg = collect_common.CollectionConfig.from_yaml(_SCEN / "collection.yaml")

    class _FakePerf:
        pid = 999
        stdout = iter(["__MEASUREMENT_WINDOW_START__\n", "data\n"])
        def kill(self) -> None: ...
        def wait(self, timeout=None) -> int:  # noqa: ANN001
            return 0

    fake_perf = _FakePerf()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **_k: fake_perf)
    monkeypatch.setattr(collect_common.subprocess, "Popen", lambda *a, **_k: fake_perf)

    class _Coll:
        success = True
        topdown_path = str(tmp_path / "topdown.txt")
        error = ""

    fake_metrics = type("M", (), {})()
    fake_metrics.collect_topdown = lambda *a, **_k: _Coll()  # noqa: ARG005

    # parse_text returns a Profile; write it.
    from profile.profile_schema import Profile, ProfileMetadata, TopdownL1  # type: ignore[import-not-found]
    prof = Profile(metadata=ProfileMetadata(customer="devkit", date="unknown"),
                   topdown=TopdownL1(frontend_bound=10.0, backend_bound=68.0,
                                     bad_speculation=5.0, retiring=17.0))
    fake_metrics.parse_topdown_file = lambda *_a, **_k: prof  # noqa: ARG005

    # Bypass the LLC perf-stat gate by stubbing _llc_miss_rate via the module fn.
    monkeypatch.setattr(collect_common, "_llc_miss_rate", lambda *a, **_k: 95.0)  # noqa: ARG005

    rc = collect_common.run_reference_capture(
        binary=str(tmp_path / "ref"), scenario_dir=tmp_path,
        devkit_cmd=None, cfg=cfg, metrics=fake_metrics,
    )
    assert rc == 0
    written = json.loads((tmp_path / "topdown.json").read_text())
    assert written["topdown"]["backend_bound"] == 68.0
```
(Adjust `run_reference_capture`'s signature to accept `cfg` + `metrics` for testability — defaults load them from `scenario_dir` when not passed.)

- [ ] **Step 5: Run + commit**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_collect_reference.py -q` → PASS.
```bash
cd mirage
git add examples/scenarios/memory_bound/collect_reference.py examples/scenarios/compute_bound/collect_reference.py examples/scenarios/collect_common.py tests/examples/test_collect_reference.py
git commit -m "feat: collect_reference.py (marker-gated, numactl-pinned, LLC gate) + DRY common body"
```

---

## Task 9: `run_loop_demo.py` driver + success-criteria evaluator

**Files:**
- Create: `examples/run_loop_demo.py`
- Test: `tests/examples/test_run_loop_demo.py`

This is the core deliverable. It: parses args (`--scenario`, `--max-iter`, `--threshold`, `--out-dir`, `--no-agent`); loads `topdown.json`→`Profile`, `seed_instruction.json`, `sensitivity.json`, `collection.yaml`; agent gate (+`--no-agent` degraded); constructs `Pipeline` + a `collect` callable wrapping `collect_common.synthetic_collect`; runs `run_iteration_loop`; loads `history.json`; prints the per-iteration steering table; evaluates the four success criteria; prints `PASS`/`FAIL`/`RUNTIME-ONLY`; in-band diagnostic.

- [ ] **Step 1: Write `tests/examples/test_run_loop_demo.py`** — the criteria evaluator is pure logic; test it with a hand-built `IterationHistory`.

```python
"""evaluate_criteria evaluates the four success criteria from a history's
topdown_diffs + stop_reason. This is the judgment the driver prints PASS/FAIL on,
so it is unit-tested in isolation (no real loop run)."""

import run_loop_demo  # type: ignore[import-not-found]
from observability.iteration_history import IterationHistory, IterationRecord  # type: ignore[import-not-found]


def _hist(diffs: list[dict[str, float]], converged: bool = False, priorities: list[int] | None = None) -> IterationHistory:
    """Build a history with per-iteration topdown_diffs (dominant=backend_bound)."""
    records = []
    pr = priorities or [2] * len(diffs)
    for i, d in enumerate(diffs):
        records.append(IterationRecord(
            iteration=i, converged=(converged and i == len(diffs) - 1),
            topdown_diffs=d, strategy_priority=pr[i],
        ))
    h = IterationHistory(customer_name="t")
    for r in records:
        h.add_record(r)
    h.total_iterations = len(records)
    return h


def test_pass_when_steered_monotone_and_converged() -> None:
    # seed gap 35 -> 22 -> 8, one structural iteration, converged.
    h = _hist([
        {"backend_bound": -35.0}, {"backend_bound": -22.0}, {"backend_bound": 8.0},
    ], converged=True)
    res = run_loop_demo.evaluate_criteria(h, "converged", "backend_bound", threshold=10.0)
    assert res.verdict == "PASS"
    assert res.criteria == [True, True, True, True]


def test_fail_when_no_structural_iteration() -> None:
    # only runtime (priority 1) iterations -> steering not triggered.
    h = _hist([{"backend_bound": -35.0}, {"backend_bound": -33.0}], priorities=[1, 1])
    res = run_loop_demo.evaluate_criteria(h, "max_iter", "backend_bound", threshold=10.0)
    assert res.verdict == "FAIL"
    assert res.criteria[0] is False  # steering triggered = False


def test_fail_on_two_bounces() -> None:
    # 35 -> 22 (better) -> 30 (bounce) -> 18 (better) -> 25 (second bounce) = FAIL monotonic
    h = _hist([{"backend_bound": -35.0}, {"backend_bound": -22.0},
               {"backend_bound": -30.0}, {"backend_bound": -18.0},
               {"backend_bound": -25.0}])
    res = run_loop_demo.evaluate_criteria(h, "max_iter", "backend_bound", threshold=10.0)
    assert res.criteria[1] is False  # monotonic violated (two bounces)


def test_fail_when_non_dominant_exceeds_20pp_at_terminal() -> None:
    # dominant converges but bad_speculation is 25pp off -> criterion 4 fails.
    h = _hist([{"backend_bound": -35.0}, {"backend_bound": 5.0, "bad_speculation": -25.0}],
              converged=True)
    res = run_loop_demo.evaluate_criteria(h, "converged", "backend_bound", threshold=10.0)
    assert res.criteria[3] is False
    assert res.verdict == "FAIL"


def test_max_iter_pass_when_dominant_narrowed_to_under_10pp() -> None:
    # not converged, but final dominant gap 6pp and monotone -> pass terminal.
    h = _hist([{"backend_bound": -35.0}, {"backend_bound": -12.0}, {"backend_bound": -6.0}])
    res = run_loop_demo.evaluate_criteria(h, "max_iter", "backend_bound", threshold=10.0)
    assert res.criteria[2] is True
    assert res.verdict == "PASS"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_run_loop_demo.py -q`
Expected: FAIL (`run_loop_demo` not importable / `evaluate_criteria` undefined).

- [ ] **Step 3: Write `examples/run_loop_demo.py`**

```python
#!/usr/bin/env python3
"""Steering-validation driver: fit mirage's synthetic workload to a captured
reference Topdown via the LLM structural tier, then judge the four success criteria.

  PYTHONPATH=src python3 examples/run_loop_demo.py --scenario memory_bound \
      [--max-iter 10] [--threshold 10] [--out-dir run_out] [--no-agent]
"""
import argparse
import pathlib
import sys
from dataclasses import dataclass

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE / "scenarios") not in sys.path:
    sys.path.insert(0, str(_HERE / "scenarios"))

import collect_common  # type: ignore[import-not-found]
from agent.agent_core import AgentCore  # type: ignore[import-not-found]
from agent.adjustment import load_sensitivity  # type: ignore[import-not-found]
from config.framework_config import AgentConfig, FrameworkConfig  # type: ignore[import-not-found]
from harness.metrics_collector import MetricsCollector  # type: ignore[import-not-found]
from harness.pipeline import Pipeline  # type: ignore[import-not-found]
from observability.iteration_history import IterationHistory  # type: ignore[import-not-found]
from profile.profile_schema import Profile  # type: ignore[import-not-found]

DOMINANT = {"memory_bound": "backend_bound", "compute_bound": "retiring"}


@dataclass
class CriteriaResult:
    verdict: str  # "PASS" | "FAIL" | "RUNTIME-ONLY"
    criteria: list[bool]  # [triggered, monotonic, terminal, non_dominant_cap]


def evaluate_criteria(
    history: IterationHistory, stop_reason: str, dominant: str, threshold: float,
) -> CriteriaResult:
    """Judge the four success criteria (see spec §Success criteria) from history."""
    records = history.records
    # 1. Steering triggered: at least one structural (priority>=2) iteration.
    triggered = any(r.strategy_priority >= 2 for r in records)
    # 2. Monotonic: best-so-far dominant gap non-increasing, at most ONE bounce.
    best = float("inf")
    bounces = 0
    for r in records:
        gap = abs(r.topdown_diffs.get(dominant, 0.0))
        if gap > best + 1e-9:
            bounces += 1  # a rise vs the running best
        else:
            best = gap
    monotonic = bounces <= 1
    # 3. Terminal: converged OR (max_iter and final dominant gap <= 10pp).
    final_gap = abs(records[-1].topdown_diffs.get(dominant, 0.0)) if records else 999.0
    terminal = (stop_reason == "converged") or (stop_reason == "max_iter" and final_gap <= 10.0)
    # 4. Non-dominant cap: every other L1 metric <= 20pp at terminal state.
    others = ["frontend_bound", "bad_speculation", "retiring"] if dominant == "backend_bound" \
        else ["frontend_bound", "backend_bound", "bad_speculation"] if dominant == "retiring" \
        else ["frontend_bound", "backend_bound", "bad_speculation", "retiring"]
    cap_ok = True
    if records:
        for m in others:
            if m == dominant:
                continue
            if abs(records[-1].topdown_diffs.get(m, 0.0)) > 20.0:
                cap_ok = False
                break
    criteria = [triggered, monotonic, terminal, cap_ok]
    verdict = "PASS" if all(criteria) else "FAIL"
    return CriteriaResult(verdict=verdict, criteria=criteria)


def _print_table(history: IterationHistory) -> None:
    print("iter | conv | prio | backend | frontend | badspec | retiring | score")
    for r in history.records:
        td = r.topdown_diffs
        print(f"{r.iteration:4d} | {str(r.converged):5s} | {r.strategy_priority:4d} | "
              f"{td.get('backend_bound', 0):7.1f} | {td.get('frontend_bound', 0):7.1f} | "
              f"{td.get('bad_speculation', 0):7.1f} | {td.get('retiring', 0):7.1f} | {r.score}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=list(DOMINANT))
    ap.add_argument("--max-iter", type=int, default=10)
    ap.add_argument("--threshold", type=float, default=10.0)
    ap.add_argument("--out-dir", default="run_out")
    ap.add_argument("--no-agent", action="store_true")
    args = ap.parse_args()

    scen_dir = _HERE / "scenarios" / args.scenario
    profile = Profile.model_validate_json((scen_dir / "topdown.json").read_text())
    seed = __import__("json").loads((scen_dir / "seed_instruction.json").read_text())
    sens = load_sensitivity(scen_dir / "sensitivity.json")
    cfg = collect_common.CollectionConfig.from_yaml(scen_dir / "collection.yaml")

    fw = FrameworkConfig.defaults()
    fw.comparison.topdown_threshold_pct = args.threshold
    if args.no_agent:
        fw.agent = AgentConfig(model=fw.agent.model, api_key=None)  # -> degraded runtime-only
    agent = AgentCore(fw.agent)

    # Agent gate: structural tier required unless --no-agent.
    if not args.no_agent and not agent.is_available():
        print("ERROR: agent unavailable and --no-agent not set. A ~35pp gap needs the "
              "structural tier. Set the box's gateway base_url+api_key (PR #55) first, "
              "or re-run with --no-agent for runtime-only.")
        return 1

    out_dir = pathlib.Path(args.out_dir)
    pipeline = Pipeline(output_base_dir=out_dir, config=fw, agent=agent)
    metrics = MetricsCollector(devkit_cmd=fw.devkit.devkit_cmd)
    # Sync the synthetic side's taskset pin to collection.yaml's mask.
    fw.devkit.cpu_range = cfg.cpu_mask

    def collect(binary: str, instr: dict) -> object:  # noqa: ANN001
        return collect_common.synthetic_collect(
            binary, instr, cfg=cfg, metrics=metrics,
            project_dir=pathlib.Path(binary).resolve().parent,
        )

    result = pipeline.run_iteration_loop(
        customer_profile=profile, seed_instruction=seed, sensitivity=sens,
        max_iter=args.max_iter, collect=collect, build=None,
    )

    history = IterationHistory.load(pathlib.Path(result.history_path))
    _print_table(history)
    print(f"stop_reason={result.stop_reason} best_iteration={result.best_iteration} "
          f"total_iterations={history.total_iterations}")

    # In-band diagnostic: no silent false success.
    if result.stop_reason == "converged" and history.total_iterations <= 1:
        print("NOTE: seed was in-band (no steering needed); re-run with --threshold 5 "
              "to force iteration.")

    if args.no_agent:
        print("RUNTIME-ONLY")
        return 0

    dominant = DOMINANT[args.scenario]
    res = evaluate_criteria(history, result.stop_reason, dominant, args.threshold)
    names = ["triggered", "monotonic", "terminal", "non_dominant_cap"]
    print(f"{res.verdict}  " + ", ".join(f"{n}={c}" for n, c in zip(names, res.criteria)))
    return 0 if res.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src python -m pytest tests/examples/test_run_loop_demo.py -q`
Expected: PASS (5 tests). If `evaluate_criteria`'s bounce logic mismatches a test, fix the logic (not the test) — the tests pin the spec.

- [ ] **Step 5: Commit**

```bash
cd mirage
git add examples/run_loop_demo.py tests/examples/test_run_loop_demo.py
git commit -m "feat: run_loop_demo driver + four-criteria evaluator (PASS/FAIL/RUNTIME-ONLY)"
```

---

## Task 10: Gate + scenarios README + finish

**Files:**
- Create: `examples/scenarios/README.md`
- Verify: full gate

- [ ] **Step 1: Write `examples/scenarios/README.md`** — the user-facing entry point tying the 3-step workflow together (build reference → capture target → fit).

```markdown
# Steering-validation scenarios

Validate mirage's end-to-end workload simulation + self-iteration steering on a
controlled OSS-stack example. Spec: `docs/superpowers/specs/2026-08-20-...md`.

## Workflow (3 steps)

1. **Build a reference demo** (on the Kunpeng ARM box):
   `cmake -S examples/scenarios/memory_bound/reference -B build/mem && cmake --build build/mem`
2. **Capture the fitting target**:
   `python3 examples/scenarios/memory_bound/collect_reference.py --binary build/mem/memory_bound_ref`
   → `topdown.json` (confirm backend-bound; the LLC-miss gate guards the buffer size).
3. **Fit**:
   `PYTHONPATH=src python3 examples/run_loop_demo.py --scenario memory_bound --max-iter 10`
   → the driver prints a per-iteration steering table + a final PASS/FAIL.

## Scenarios
- `memory_bound` — LLC-missing random scan; dominant metric `backend_bound` (~70).
- `compute_bound` — dense matmul; dominant metric `retiring` (~60).

## Tuning the fit
- `--threshold 5` — force iteration on borderline (in-band) gaps.
- `--no-agent` — runtime-only mode (skip the LLM tier; prints `RUNTIME-ONLY`).
- `collection.yaml` — shared collection params (cpu_mask, numa_node, buffer size) read
  by BOTH the reference collector and the driver; edit for your Kunpeng SKU.

## PASS requires all four: steering triggered (a priority>=2 iteration) AND dominant
metric monotonically narrows (at most one bounce) AND terminal (converged, or max_iter
with dominant gap <=10pp) AND every non-dominant L1 metric <=20pp off.
```

- [ ] **Step 2: Run the full gate**

```bash
cd mirage
python -m ruff format examples/ tests/ src/ 2>/dev/null || true
python -m ruff check examples/ tests/ src/ 2>&1 | tee /tmp/ruff.log
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m mypy --config-file=pyproject.toml src/ tests/
python -m pre_commit run --all-files
```
Expected: ruff clean (examples/ IS ruff-linted by pre-commit), pytest green with cov ≥ 60%, mypy clean (examples/ excluded from mypy; `tests/examples/` is checked — `# type: ignore[import-not-found]` on the examples-module imports keeps it green), pre-commit clean. Fix any failures inline.

- [ ] **Step 3: Commit + open PR**

```bash
cd mirage
git add examples/scenarios/README.md
git commit -m "docs: scenarios README (3-step workflow + PASS criteria)"
git push -u origin feat/steering-validation-taskflow-demos
gh pr create --title "feat: steering-validation taskflow demos" --body "Implements the approved spec ..." --base main
```
Two-stage review (spec compliance, then code quality), squash-merge, delete branch. The user then runs the 3-step workflow on the Kunpeng box.

---

## Verification (end-state)

- All `tests/examples/` tests green; cov ≥ 60%; mypy clean; pre-commit clean.
- Vendored taskflow: `sha256sum -c manifest.sha256` passes; README records v3.9.0 + URL + archive hash.
- `collect_common.CollectionConfig` round-trips both `collection.yaml` files; `synthetic_collect` launches with numactl+taskset (verified by the monkeypatched argv test).
- `evaluate_criteria` correctly judges all five spec cases (pass, no-structural, two-bounces, non-dominant-cap, max_iter-narrowed).
- User-side (on ARM): build → capture (marker-gated, LLC-gated) → fit → `PASS`/`FAIL`/`RUNTIME-ONLY`.

## Out of scope (filed as issue #62)
- Fitting real customer business-code workloads (richer stages, coverage axis).
- Synthetic-side stdout-marker gating (codegen doesn't emit `__MEASUREMENT_WINDOW_START__`; `run_and_collect`'s sleep-past-warmup is the equivalent steady-state alignment).
- `Eigen` for compute (hand-rolled matmul instead).
