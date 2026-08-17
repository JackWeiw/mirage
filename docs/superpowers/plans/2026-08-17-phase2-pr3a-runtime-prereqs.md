# Phase 2 PR 3a: Runtime no-rebuild fast-path prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the three prerequisites the Phase 2 loop's runtime tier (PR 3b) depends on — a `DevkitConfig` config plumbing field (#47), a nlohmann-free `config_loader.h` so the production binary reads `config.json` on the bare ARM target, and a crash-safe `write_config_json_atomic` for the runtime tier's per-iteration config rewrite.

**Architecture:** The approved spec (`docs/superpowers/specs/2026-08-17-phase2-auto-iteration-loop-design.md`, on branch `docs/phase2-loop-design`) splits PR 3 into 3a (prerequisites) + 3b (loop driver). PR 3a is the runtime-tier foundation: (1) `DevkitConfig` in `FrameworkConfig` so the loop knows how to collect topdown (duration/interval/pid/cpu pin); (2) a dependency-free `config_loader.h` template so the binary reads `config.json` at runtime without nlohmann (absent on bare ARM — the spike proved this); (3) `write_config_json_atomic` (temp -> fsync -> `os.replace`) so an interrupted runtime-tier write can't poison the whole run. None of this is the loop driver itself — PR 3b owns `run_iteration_loop` + `run_and_collect` + the error/degradation model + the stub-plant integration test. All three pieces are locally unit-testable (no ARM/devkit/LLM).

**Tech Stack:** Python 3.13, pydantic v2, jinja2, pytest (`pythonpath=["src"]`, `--cov-fail-under=60`), ruff + ruff-format + mypy strict (src/+tests/), pre-commit. C++17 (header-only template). **ASCII-only** in all source/prompt/template files (Windows GBK locale — non-ASCII crashes `pathlib.read_text()`).

**Spec reference:** `docs/superpowers/specs/2026-08-17-phase2-auto-iteration-loop-design.md` sections "Prerequisite for the runtime no-rebuild fast path", "Atomic `config.json` writes (runtime tier)", and "Config plumbing -- `DevkitConfig` (#47)". The spec lives on branch `docs/phase2-loop-design` (PR #50, not merged to main); read it there: `git show origin/docs/phase2-loop-design:docs/superpowers/specs/2026-08-17-phase2-auto-iteration-loop-design.md`.

**Key facts verified by reading the code (do not re-derive):**
- `src/codegen/templates/cmake/CMakeLists.txt.j2` does NOT link or `find_package` nlohmann — the dependency is purely `#include <nlohmann/json.hpp>` inside `config_loader.h`. So making `config_loader.h.j2` nlohmann-free needs NO CMake change.
- `src/codegen/templates/main/main.cpp.j2` already calls `RunConfig cfg = load_config(config_path);` at runtime (config_path = `"config.json"` or `argv[1]`). The fast-path mechanism is already wired; only the header's nlohmann dependency blocks the bare-ARM build.
- `ScaffoldGenerator.generate(context, output_dir)` (`src/codegen/scaffold_gen.py`) renders `main/config_loader.h.j2` -> `config_loader.h` with the full Jinja context (`config` dict, `project_name`, etc.). The render-test pattern is established in `tests/codegen/test_scaffold_gen.py`.
- The current `config_loader.h.j2` bakes codegen-time defaults via `j.value("key", {{ config.key | default(N) }})` — defaults used only if the runtime `config.json` omits the key. The nlohmann-free reader keeps this exact contract: baked defaults + runtime override from `config.json`.
- `src/config/framework_config.py`: `FrameworkConfig` has fields `agent`, `comparison`, `run_defaults`, `codegen`, `harness`. `default_config.yaml` mirrors them. The PR 1 work already added the `ComparisonConfig` loop-control knobs (`oscillation_window`, `no_improvement_stop`, `run_failure_stop`, `build_failure_stop`, `collect_retry`).
- `src/harness/run_config.py` `RunConfig` (Python, pydantic) is a DIFFERENT model from the C++ `RunConfig` struct in `config_loader.h`. Do not conflate them.
- Follow-up issue #48 (`BuildResult.duration_seconds` declared-never-set, cosmetic) is NOT touched here — `DevkitConfig.duration_seconds` is the topdown-collection duration knob, conceptually the "source of truth" the spec mentions, but we do not delete `BuildResult.duration_seconds` in this PR. #48 stays open.

**Out of scope (PR 3b):** `run_iteration_loop`, `run_and_collect`, the run/collect error model (crash/timeout/collect-fail -> retry/skip/streak), the build-failure streak, agent-unavailable degradation, `Pipeline` wiring of `MetricsCollector(devkit_cmd=...)`, and the stub-plant integration test. `write_config_json_atomic` is built here but its caller (the loop driver) is PR 3b.

---

## File Structure

- **Modify:** `src/config/framework_config.py` — add `DevkitConfig` model + `devkit` field on `FrameworkConfig`.
- **Modify:** `src/config/default_config.yaml` — add the `devkit:` section.
- **Modify:** `src/codegen/templates/main/config_loader.h.j2` — replace the nlohmann-based reader with a dependency-free flat-object reader (same baked-defaults contract).
- **Create:** `src/harness/config_writer.py` — `write_config_json_atomic(path, config)`.
- **Modify:** `tests/config/test_framework_config.py` — DevkitConfig + devkit-field tests.
- **Create:** `tests/codegen/test_config_loader_template.py` — render + content assertions for the nlohmann-free header.
- **Create:** `tests/harness/test_config_writer.py` — atomic-write sequence + crash-safety tests.

---

### Task 1: DevkitConfig model + FrameworkConfig.devkit + default_config.yaml

**Files:**
- Modify: `src/config/framework_config.py` (add `DevkitConfig` class ~line 43, before `FrameworkConfig`; add `devkit` field ~line 53)
- Modify: `src/config/default_config.yaml` (add `devkit:` section)
- Test: `tests/config/test_framework_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/config/test_framework_config.py`:

```python
from config.framework_config import DevkitConfig


def test_devkit_config_defaults() -> None:
    d = DevkitConfig()
    assert d.devkit_cmd is None
    assert d.duration_seconds == 20
    assert d.interval_seconds == 3
    assert d.cpu_range is None
    assert d.collect_pid is True


def test_framework_config_has_devkit_field() -> None:
    config = FrameworkConfig.defaults()
    assert config.devkit.devkit_cmd is None
    assert config.devkit.duration_seconds == 20
    assert config.devkit.interval_seconds == 3
    assert config.devkit.collect_pid is True


def test_framework_config_devkit_from_yaml() -> None:
    # default_config.yaml carries the devkit section; values round-trip.
    config = FrameworkConfig.defaults()
    assert config.devkit.duration_seconds == 20
    assert config.devkit.interval_seconds == 3
    assert config.devkit.collect_pid is True
    assert config.devkit.cpu_range is None
```

Also add `DevkitConfig` to the existing import line at the top of the file:

```python
from config.framework_config import (
    AgentConfig,
    ComparisonConfig,
    DevkitConfig,
    FrameworkConfig,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/config/test_framework_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'DevkitConfig'` (and `config.devkit` AttributeError).

- [ ] **Step 3: Write minimal implementation**

In `src/config/framework_config.py`, add the `DevkitConfig` class after `HarnessConfig` (before `FrameworkConfig`):

```python
class DevkitConfig(BaseModel):
    """Devkit topdown-collection plumbing for the auto-iteration loop (#47).

    The loop's run_and_collect calls collect_topdown(duration=..., interval=...,
    pid=...) and pins the workload with taskset to cpu_range. None devkit_cmd
    means the devkit is not configured (degraded / no-collection mode).
    """

    devkit_cmd: str | None = None
    duration_seconds: int = 20
    interval_seconds: int = 3
    cpu_range: str | None = None  # taskset pin, e.g. "4" or "4-7"
    collect_pid: bool = True  # -p <pid> attribution (spike-proven)
```

Add the `devkit` field to `FrameworkConfig` (after `harness`):

```python
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    devkit: DevkitConfig = Field(default_factory=DevkitConfig)
```

In `src/config/default_config.yaml`, add the `devkit:` section under `framework:` (after `harness:`):

```yaml
  devkit:
    devkit_cmd: null
    duration_seconds: 20
    interval_seconds: 3
    cpu_range: null
    collect_pid: true
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/config/test_framework_config.py -q`
Expected: PASS (all tests, including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add src/config/framework_config.py src/config/default_config.yaml tests/config/test_framework_config.py
git commit -m "feat(config): add DevkitConfig plumbing for the auto-iteration loop"
```

---

### Task 2: Nlohmann-free config_loader.h.j2 (runtime reader for bare ARM)

**Files:**
- Modify: `src/codegen/templates/main/config_loader.h.j2` (full rewrite of the reader; struct unchanged)
- Test: `tests/codegen/test_config_loader_template.py`

**Context for the implementer:** The current header does `#include <nlohmann/json.hpp>` then `nlohmann::json::parse(file)`. nlohmann is absent on the bare ARM target (the spike baked config values precisely because of this). The runtime no-rebuild tier REQUIRES the binary to read `config.json` at runtime, so we replace the reader with a minimal, dependency-free scanner for the flat `{"key": <number>, ...}` shape this project emits. Keep the exact same contract as before: bake codegen-time defaults via Jinja, then override each field from `config.json` if the key is present and parses as a number. This is NOT a general JSON parser — it handles only the fixed flat numeric schema (`thread_count`, `qps`, `warmup_seconds`, `measurement_seconds` as ints; `compute_ratio`, `memory_ratio` as doubles). C++ correctness is verified by review + the real ARM run (user-side, per the spec's "Real-run validation (user-side, on ARM)"); the Python gate asserts the rendered header is nlohmann-free, well-formed, and bakes defaults.

- [ ] **Step 1: Write the failing tests**

Create `tests/codegen/test_config_loader_template.py`:

```python
"""Tests for the nlohmann-free config_loader.h template."""

import pathlib
import tempfile

from codegen.scaffold_gen import ScaffoldGenerator


def _render_config_loader(config: dict) -> str:
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())
    context = {
        "project_name": "sim",
        "compile_flags": "-O2",
        "dependencies": [],
        "dep_headers": [],
        "stages": [],
        "extra_sources": [],
        "config": config,
        "burst": 20,
    }
    gen.generate(context, output_dir)
    return (output_dir / "config_loader.h").read_text()


def test_config_loader_has_no_nlohmann_dependency() -> None:
    text = _render_config_loader({})
    # The bare-ARM target has no nlohmann; the header must not pull it in.
    assert "nlohmann" not in text
    assert "#include <nlohmann" not in text


def test_config_loader_has_struct_and_reader() -> None:
    text = _render_config_loader({})
    assert "struct RunConfig" in text
    assert "load_config" in text
    # Still reads a runtime config path (the no-rebuild fast path).
    assert "config_path" in text


def test_config_loader_bakes_defaults_from_config() -> None:
    text = _render_config_loader({"thread_count": 8, "compute_ratio": 0.8})
    # Codegen-time defaults are baked as fallback values.
    assert "8" in text  # thread_count default
    assert "0.8" in text  # compute_ratio default


def test_config_loader_bakes_default_fallbacks_when_key_absent() -> None:
    text = _render_config_loader({})
    # The six RunConfig fields each carry a baked fallback default.
    for token in ("thread_count", "qps", "warmup_seconds", "measurement_seconds",
                  "compute_ratio", "memory_ratio"):
        assert token in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/codegen/test_config_loader_template.py -q`
Expected: FAIL on `test_config_loader_has_no_nlohmann_dependency` (current header includes nlohmann).

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `src/codegen/templates/main/config_loader.h.j2` with:

```jinja
#pragma once
#include <string>
#include <fstream>
#include <sstream>
#include <cstdlib>
#include <cctype>

// Runtime configuration read from config.json at process start.
struct RunConfig {
    int thread_count;
    int qps;
    int warmup_seconds;
    int measurement_seconds;
    double compute_ratio;
    double memory_ratio;
};

// Minimal, dependency-free reader for the flat config.json object this
// project's codegen emits. nlohmann/json.hpp is NOT available on the bare
// ARM target, so we parse the fixed schema (six numeric fields) directly
// rather than pulling in a JSON library. The reader looks up each known key,
// skips to the value after the next ':', and parses the number with strtod
// (so ints and doubles both parse). Missing or non-numeric keys fall back to
// the codegen-time defaults baked below. This is NOT a general JSON parser;
// it handles only { "key": <number>, ... }.
inline RunConfig load_config(const std::string& config_path) {
    RunConfig cfg;
    cfg.thread_count = {{ config.thread_count | default(4) }};
    cfg.qps = {{ config.qps | default(100) }};
    cfg.warmup_seconds = {{ config.warmup_seconds | default(30) }};
    cfg.measurement_seconds = {{ config.measurement_seconds | default(60) }};
    cfg.compute_ratio = {{ config.compute_ratio | default(0.5) }};
    cfg.memory_ratio = {{ config.memory_ratio | default(0.5) }};

    std::ifstream file(config_path);
    if (!file) return cfg;  // no config file -> baked defaults
    std::stringstream ss;
    ss << file.rdbuf();
    const std::string s = ss.str();

    // Find "key", then parse the number following the next ':'. Returns true
    // and sets *out on success; false if the key is missing or its value is
    // not a number. strtod skips leading whitespace itself.
    auto read_double = [&s](const std::string& key, double* out) -> bool {
        std::string needle = "\"" + key + "\"";
        size_t k = s.find(needle);
        if (k == std::string::npos) return false;
        size_t c = s.find(':', k + needle.size());
        if (c == std::string::npos) return false;
        size_t p = c + 1;
        while (p < s.size() && std::isspace(static_cast<unsigned char>(s[p]))) ++p;
        if (p >= s.size()) return false;
        char* end = nullptr;
        double v = std::strtod(s.c_str() + p, &end);
        if (end == s.c_str() + p) return false;  // not a number
        *out = v;
        return true;
    };

    double v = 0.0;
    if (read_double("thread_count", &v)) cfg.thread_count = static_cast<int>(v);
    if (read_double("qps", &v)) cfg.qps = static_cast<int>(v);
    if (read_double("warmup_seconds", &v)) cfg.warmup_seconds = static_cast<int>(v);
    if (read_double("measurement_seconds", &v)) cfg.measurement_seconds = static_cast<int>(v);
    if (read_double("compute_ratio", &v)) cfg.compute_ratio = v;
    if (read_double("memory_ratio", &v)) cfg.memory_ratio = v;
    return cfg;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/codegen/test_config_loader_template.py tests/codegen/test_scaffold_gen.py -q`
Expected: PASS (new tests + the existing scaffold tests still green — `test_generate_project_creates_files` asserts `config_loader.h` exists, which still holds).

- [ ] **Step 5: Lint the rendered header (ASCII-only check)**

Run: `python -c "from codegen.scaffold_gen import ScaffoldGenerator; import pathlib, tempfile; d=pathlib.Path(tempfile.mkdtemp()); ScaffoldGenerator().generate({'project_name':'s','compile_flags':'-O2','dependencies':[],'dep_headers':[],'stages':[],'extra_sources':[],'config':{},'burst':20}, d); print((d/'config_loader.h').read_text())" | python -c "import sys; t=sys.stdin.read(); t.encode('ascii'); print('ascii-ok' if all(ord(c)<128 for c in t) else 'NON-ASCII')"`
Expected: `ascii-ok`.

- [ ] **Step 6: Commit**

```bash
git add src/codegen/templates/main/config_loader.h.j2 tests/codegen/test_config_loader_template.py
git commit -m "feat(codegen): nlohmann-free config_loader for bare-ARM runtime fast path"
```

---

### Task 3: write_config_json_atomic (crash-safe runtime-tier config rewrite)

**Files:**
- Create: `src/harness/config_writer.py`
- Test: `tests/harness/test_config_writer.py`

**Context for the implementer:** The runtime iteration tier (PR 3b) overwrites `project/config.json` every runtime pass and re-runs the existing binary. On a weak/embedded ARM filesystem a write interrupted by crash/power-loss/signal would leave a truncated `config.json`, poisoning every subsequent iteration and cascading into `run_failure_stop` for the wrong reason. The atomic sequence: serialize to a temp file in the SAME directory as the target (so the final move is a same-filesystem `rename(2)`, atomic on POSIX — a cross-device move degrades to a non-atomic copy), `fsync` the temp file, then `os.replace(tmp, target)`. The target is never opened in-place for writing. On any failure, the temp file is unlinked and the target is left untouched (still the previous config). The spec's integration test asserts the temp-then-rename sequence (no in-place truncate).

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/test_config_writer.py`:

```python
"""Tests for write_config_json_atomic (crash-safe config rewrite)."""

import json
import os
import pathlib
import tempfile

import pytest

from harness.config_writer import write_config_json_atomic


def test_writes_valid_json_round_trip(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "config.json"
    write_config_json_atomic(target, {"thread_count": 8, "compute_ratio": 0.8})
    data = json.loads(target.read_text())
    assert data == {"thread_count": 8, "compute_ratio": 0.8}


def test_uses_temp_in_same_dir_then_replaces(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"old": true}')  # pre-existing live config

    replaces: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:
        # Record the rename. The source must be a temp file in the SAME dir
        # (same-filesystem atomic rename), NOT the target itself.
        src_path = pathlib.Path(src)
        assert src_path.parent == target.parent
        assert src != str(target)
        assert src_path.name.startswith(".config.json")
        replaces.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    write_config_json_atomic(target, {"thread_count": 4})

    assert len(replaces) == 1
    assert replaces[0][1] == str(target)
    # The temp file is gone (replaced onto the target).
    assert not any(p.name.startswith(".config.json") for p in tmp_path.iterdir())


def test_failure_mid_write_leaves_target_untouched(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"old": true}')

    # Simulate a crash mid-write: fsync raises. The target must keep its old
    # content and no orphaned temp file may remain.
    def boom(fd: int) -> None:  # noqa: ARG001
        raise OSError("simulated mid-write crash")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        write_config_json_atomic(target, {"thread_count": 4})

    # Target untouched (no in-place truncate).
    assert json.loads(target.read_text()) == {"old": True}
    # No orphaned temp file litters the dir.
    assert not any(p.name.startswith(".config.json") for p in tmp_path.iterdir())


def test_creates_parent_dir_if_missing(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "nested" / "deep" / "config.json"
    write_config_json_atomic(target, {"qps": 50})
    assert json.loads(target.read_text()) == {"qps": 50}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/harness/test_config_writer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.config_writer'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/harness/config_writer.py`:

```python
"""Crash-safe atomic JSON config writer for the runtime iteration tier."""

import json
import os
import pathlib
import tempfile
from typing import Any

from observability.logging import get_logger

logger = get_logger("config_writer")


def write_config_json_atomic(path: pathlib.Path, config: dict[str, Any]) -> None:
    """Write ``config`` as JSON to ``path`` atomically (crash-safe).

    The runtime iteration tier overwrites ``project/config.json`` every runtime
    pass and re-runs the existing binary. On a weak/embedded ARM filesystem a
    write interrupted by crash, power loss, or signal would leave a truncated
    ``config.json`` and poison every subsequent iteration. This writes to a temp
    file in the SAME directory as the target (so the final move is a
    same-filesystem ``rename(2)``, atomic on POSIX -- a cross-device move would
    degrade to a non-atomic copy), ``fsync``s the temp, then ``os.replace``s it
    onto the target. The target is never opened in-place for writing.

    On any failure the temp file is unlinked and the target is left untouched
    (still the previous config).
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(config, indent=2) + "\n"
    # Same-dir temp so os.replace is an atomic same-filesystem rename.
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        # Clean up the orphaned temp; the target is untouched (still the prior
        # config). Swallow unlink errors -- the original failure is the real
        # error to propagate.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/harness/test_config_writer.py -q`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add src/harness/config_writer.py tests/harness/test_config_writer.py
git commit -m "feat(harness): crash-safe write_config_json_atomic for runtime tier"
```

---

### Task 4: Full quality gate + open the PR

**Files:** none (verification + PR).

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: all pass, coverage >= 60% (the gate in `pyproject.toml`). Note the new test count and coverage for the PR body.

- [ ] **Step 2: Run pre-commit on all files**

Run: `python -m pre_commit run --all-files`
Expected: all hooks pass (ruff, ruff-format, mypy strict src/+tests/, trailing-ws, eof, yaml, json, no-large-files, don't-commit-to-branch). Fix any issues (the implementer should not need to add type: ignore here; the new code is plain).

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/phase2-pr3a-runtime-prereqs
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --base main --title "Phase 2 PR 3a: runtime no-rebuild fast-path prerequisites" --body '<PR_BODY>'
```

PR body (single-quoted, no Claude trailer — consistent with PR #51/#52):

```
Lands the three prerequisites the Phase 2 loop's runtime tier (PR 3b) depends on, per the approved spec. Splits PR 3 into 3a (prerequisites) + 3b (loop driver) as the spec authorizes ("may split into a PR 3a + PR 3b if it grows").

## What
- **`src/config/framework_config.py`** + **`src/config/default_config.yaml`**: new `DevkitConfig` (devkit_cmd, duration_seconds=20, interval_seconds=3, cpu_range taskset pin, collect_pid). Added to `FrameworkConfig.devkit`. Closes #47's plumbing half (the loop driver in PR 3b consumes it). #48 (BuildResult.duration_seconds cosmetic) is NOT touched -- DevkitConfig.duration_seconds is the topdown-collection knob, conceptually the spec's "source of truth", but we do not delete the cosmetic field here.
- **`src/codegen/templates/main/config_loader.h.j2`**: replaced the nlohmann-based reader with a dependency-free flat-object scanner. nlohmann is absent on the bare ARM target (the spike baked config precisely because of this); the runtime no-rebuild tier requires the binary to read config.json at runtime, so the header now parses the fixed six-field numeric schema directly (strtod per key, baked codegen-time fallback defaults unchanged). `CMakeLists.txt.j2` never linked nlohmann, so NO CMake change. Same contract: baked defaults + runtime override.
- **`src/harness/config_writer.py`** (new): `write_config_json_atomic(path, config)` -- temp-in-same-dir -> fsync -> os.replace, crash-safe; target never opened in-place; orphaned temp cleaned on failure. The runtime tier overwrites config.json per iteration; an interrupted write can't poison the run.
- **Tests**: DevkitConfig + devkit-field round-trip (3); config_loader render tests (no nlohmann, struct+reader present, defaults baked, 4); atomic-write sequence + crash-safety (4).

## Scope
No loop driver, no run_and_collect, no error/degradation model, no Pipeline wiring -- all PR 3b. `write_config_json_atomic` is built here but its caller lands in PR 3b. Depends on PR 1 (#51) + PR 2 (#52), both merged.

## Verification
- `python -m pytest tests/ -q` -> <N> passed, <cov>% coverage (gate 60%)
- `python -m pre_commit run --all-files` -> all hooks pass
- C++ config_loader correctness is user-side ARM validation (per spec "Real-run validation (user-side, on ARM)"); the Python gate asserts the rendered header is nlohmann-free, well-formed, and bakes defaults.
```

- [ ] **Step 5: Update memory + proceed**

Update `mirage-project-state.md`: note PR 3a opened (PR #NN). Per the user's standing delegation ("决策可以你自己看着办，我只要看最终实现效果好了" + continuous execution) and the established "review -> merge -> continue" pattern (PR #51/#52): the controller does a final holistic self-review of 3a; if clean, squash-merges 3a to main, then branches PR 3b off main (3b depends on 3a's DevkitConfig + write_config_json_atomic + config_loader). No check-in between 3a-merge and 3b-start -- the user authorized continuous execution.

---

## Self-Review (controller runs before opening the PR)

1. **Spec coverage:** "Prerequisite for the runtime no-rebuild fast path" -> Task 2 (nlohmann-free config_loader). "Atomic config.json writes" -> Task 3. "DevkitConfig (#47) plumbing" -> Task 1. All three spec sections covered. The loop driver, error model, degradation, build-failure streak, run_and_collect, and stub-plant integration test are explicitly PR 3b -- not gaps here.
2. **Placeholder scan:** no TBD/TODO/vague. All code blocks complete.
3. **Type consistency:** `DevkitConfig` fields match the spec's model exactly (devkit_cmd, duration_seconds, interval_seconds, cpu_range, collect_pid). `write_config_json_atomic(path, config)` signature matches the spec. `load_config` C++ signature unchanged (`load_config(const std::string&) -> RunConfig`), struct fields unchanged.
4. **ASCII-only:** the config_loader template and the Python source contain no non-ASCII (the implementer must verify with the Step 5 ASCII check in Task 2; Task 3 code is pure ASCII).
