# Steering-Validation Taskflow Demos — Design Spec

**Goal:** Validate mirage's end-to-end workload simulation + self-iteration steering on a *controlled, realistic* example before scaling to real customer business code. Provide (1) real runnable reference programs (OSS `taskflow` + self-developed code) whose captured Topdown/flamegraph is the fitting target, and (2) an in-repo real-ARM driver that runs mirage's synthetic-workload loop (LLM structural tier) to fit that target.

**Architecture:** Two sides that meet at a captured `topdown.json`:
- *Reference side* (run by the user on the Kunpeng ARM box): a real C++ program built on the vendored `taskflow` header + self-developed stages. The user builds it, runs it under `perf` + the devkit Topdown tool, and captures a real `topdown.json` (L1) + flamegraph. This is the "customer" profile — the fitting target. Two reference demos: `memory_bound` (LLC-missing random-scan, backend-bound) and `compute_bound` (dense matmul, retiring-bound).
- *mirage fitting side* (run by the in-repo driver): loads the captured `topdown.json` as the customer `Profile`, plus a synthetic `seed_instruction.json` whose knobs are set so it starts ~35pp out of band on the dominant metric, plus a `sensitivity.json` (directions grounded in the spike's proven findings). `run_loop_demo.py` wires `AgentCore` (the box's LLM gateway via PR #55) and runs `Pipeline.run_iteration_loop` with real codegen+cmake/make+devkit collect. The loop iterates (structural/LLM tier) and steers the synthetic workload's Topdown toward the real target.

**Tech Stack:** C++17 (reference demos), vendored `taskflow` header-only, CMake, `perf` + ARM Topdown devkit (collection), Python 3.13 (driver), mirage `Pipeline`/`AgentCore`/`TopdownParser`/`MetricsCollector`.

---

## Success criteria (explicit, to avoid ambiguity in result interpretation)

A scenario run is judged a **successful steering validation** when **all four** hold:

1. **Steering triggered** — `total_iterations > 0` and at least one iteration ran at priority ≥ 2 (structural/LLM tier). A converged-at-iter-0 result is *not* a pass (it means the seed was in-band; the in-band diagnostic flags it).
2. **Dominant metric monotonically converges** — track the best-so-far dominant gap (`|diff_pct|` on `backend_bound` for `memory_bound`, on `retiring` for `compute_bound`) across iterations. Rule: **at most one iteration across the entire run** may show a best-gap rise over the preceding best (a noise bounce); the iteration following any bounce must resume the non-increasing trend; a **second** rise — consecutive or not — fails this criterion. (Equivalently: no two bounces anywhere in the run.) This is strict but unambiguous; real-hardware noise is one bump, a broken steerer oscillates repeatedly.
3. **Terminal state — dominant metric** — the loop ends either `converged` (all L1 metrics within `--threshold`), OR at `max_iter` with the dominant-metric gap **significantly narrowed** from the seed's ~35 pp starting gap (≤ 10 pp residual, i.e. at least ~3.5× closer than the seed). A run that hits `max_iter` still >10 pp off with criterion 2 violated is a *failure* (steering isn't working), not a pass.
4. **Non-dominant sanity cap** — this validation focuses on **dominant-metric steering**; non-dominant L1 metrics are *soft* (not convergence targets, no monotonic requirement). But to reject the pathological "dominant hit by wrecking the profile" case, at the terminal state **every other L1 metric's `|diff_pct|` must be ≤ 20 pp**. A non-dominant metric >20 pp off → `FAIL` with which metric. This declares the scope explicitly: dominant = hard, non-dominant = bounded sanity, not full-profile fit.

These are checked by the driver and reported in the final summary line: `PASS`/`FAIL` with the four booleans, so the result is unambiguous without the user eyeballing the table.

---

## Why

The prior real-ARM run (`examples/run_real_arm_loop.py`, local-only) converged at iteration 0 — the seed `steerability_spike` workload (matmul-default, retiring ~63) happened to land within the 10 pp band of the real customer (retiring ~73) on all four L1 metrics, so the loop did zero steering work. That validated the pipeline mechanically but NOT the iteration/steering path. Three problems to fix: (a) the driver was local-only, not reproducible in-repo; (b) the seed was in-band; (c) the "customer" target was whatever the box had, not a controlled realistic load. This spec fixes all three: an in-repo scenario-parameterized driver, seeds designed ~35 pp out of band, and realistic OSS-stack reference programs producing the fitting target.

## Third-party OSS stack

**`taskflow`** (header-only library, MIT license), vendored as a header set under `examples/third_party/taskflow/taskflow/` (the library ships as a directory of headers; `#include <taskflow/taskflow.hpp>` is the master include) + `LICENSE`.

**Traceability (done at vendor time, no later work):** `examples/third_party/taskflow/README.md` records (a) the exact official release/tag (e.g. `taskflow v3.x.y`), (b) the source download URL (the GitHub release tarball/zip at that pinned tag), and (c) a `SHA-256` manifest of the vendored header set — the release archive's hash plus a per-file `sha256` listing under `taskflow/`. The vendor commit message repeats the tag + archive hash. This guarantees reproducibility — a later version drift is detectable by re-hashing, and a maintainer can re-fetch the identical headers from the pinned URL.

Rationale (the spike's pain points, all sidestepped):
- Header-only → no `cmake` fetch/build of a dependency, no "nlohmann absent on bare ARM" problem. Builds offline on the Kunpeng box with just the C++17 toolchain.
- A real concurrency/task-graph library makes the reference demos resemble actual customer services (parallel worker stages) — the realism this exercise is meant to validate.
- Self-developed code on top (the scan loop, the matmul kernel) = "based on OSS + self-developed code," matching the user's directive.

Deliberately NOT pulling in Eigen: its headers are ~10 MB to vendor and a hand-rolled `-O2 -march=armv8.2-a` matmul is itself a realistic customer compute kernel (many are hand-rolled). One OSS lib keeps the third-party surface minimal. (Eigen-for-compute is a future option, tracked in the follow-up issue.)

## File layout

```
examples/
  third_party/
    taskflow/
      taskflow/             # vendored header set (committed) — taskflow.hpp master include + subheaders
      LICENSE               # taskflow MIT license
      README.md             # official version/tag + source URL + SHA-256 manifest of the header set (traceability)
  scenarios/
    memory_bound/
      reference/
        CMakeLists.txt      # links the vendored taskflow header; -O2 -march=armv8.2-a
        main.cpp            # taskflow graph of N random-access scan workers
        scan.h / scan.cpp   # self-dev: LLC-missing random scan + aggregation over a large buffer
        README.md           # exact build + run + capture commands for the ARM box
      seed_instruction.json # mirage synthetic seed (out-of-band: compute-dominated)
      sensitivity.json       # per-knob directions (spike-grounded)
      collection.yaml         # shared collection params (duration, interval, perf rate, cpu_mask, numa_node, per-worker buffer size) — read by BOTH collect_reference.py and run_loop_demo.py
      collect_reference.py   # launch binary + perf record + devkit topdown -> topdown.json + flamegraph.svg
      topdown.json           # (GENERATED by the user on ARM — the fitting target; NOT committed hand-authored)
    compute_bound/
      reference/
        CMakeLists.txt
        main.cpp            # taskflow graph of N matmul workers
        matmul.h / matmul.cpp # self-dev: dense matmul kernel + result reduction
        README.md
      seed_instruction.json # out-of-band: memory-dominated
      sensitivity.json
      collection.yaml         # shared collection params (same shape as memory_bound)
      collect_reference.py
      topdown.json           # (GENERATED)
  run_loop_demo.py           # the in-repo driver: --scenario <name> -> run_iteration_loop (fitting)
```

## Reference demo 1 — `memory_bound`

A taskflow graph of N workers (N = core count), each performing **random accesses into its own private buffer sized ≥ 2-3× the single-NUMA-node LLC** (e.g. if a NUMA node's LLC is 16 MB, each worker gets 64 MB). Rationale for per-worker-private, not a shared 256 MB: if a single worker's share is smaller than one core's LLC, random access still hits cache and never reaches high `backend_bound`. Per-worker buffers ≥2-3× the NUMA LLC drive LLC-miss rate > 90% → long memory stalls → `backend_bound`-dominated Topdown (target ~65-75). Total working set scales linearly with core count. Self-developed code: the scan/aggregation loop, the access-pattern generator, the result reduction.

- `CMakeLists.txt`: C++17, `-O2 -march=armv8.2-a -fno-inline-small-functions`, `add_executable(memory_bound_ref main.cpp)`, `target_include_directories(... examples/third_party/taskflow)`.
- `main.cpp`: build a `tf::Taskflow` graph of N worker tasks (one per core via `std::thread::hardware_concurrency()`), each allocating its own ≥2-3× NUMA-LLC `std::vector<uint8_t>` and looping random-access reads over it for the measurement window, aggregating a checksum (prevents the optimizer from eliding the loads). The per-worker buffer size is derived from the box's NUMA LLC size (read at runtime or passed via the scenario config) so it is correct on any Kunpeng SKU. Time-boxed via the same warmup/measurement-seconds contract as mirage's runtime (`std::chrono`); **prints `__MEASUREMENT_WINDOW_START__` to stdout when warmup ends** so `collect_reference.py` aligns collection to the steady-state window (see Collection).
- Knobs the mirage synthetic side will steer toward this profile: `working_set_mb` (16→64→256), `access_pattern` (sequential→mixed→random), `memory_ratio`.

## Reference demo 2 — `compute_bound`

A taskflow graph of N workers, each running a **dense matmul** (self-rolled, e.g. 256×256, -O2 -march=armv8.2-a) repeatedly for the measurement window. Dense FP/int MAC work → `retiring`-dominated Topdown (target ~55-65). Self-developed code: the matmul kernel (blocked/unrolled) + result reduction.

- `CMakeLists.txt`: same flags, `add_executable(compute_bound_ref main.cpp)`.
- `main.cpp`: taskflow graph of N matmul workers; each does `M` matmuls of a fixed `K×K` matrix against a random RHS, accumulating results; time-boxed. Prints `__MEASUREMENT_WINDOW_START__` to stdout when warmup ends (same steady-state-alignment contract as the memory_bound demo).
- Knobs mirage steers toward this: `compute_ratio` (0.2→0.8), `archetype=matmul`, `iterations` (baked work-per-call).

## Collection — `collect_reference.py`

Per-scenario script (`examples/scenarios/<name>/collect_reference.py`) that the user runs on the ARM box to produce the fitting target. It REUSES mirage's own ingestion/collection code (PR #45's devkit CLI fix + `TopdownParser.parse_text`):
- `--binary <path>` (the built reference executable), reads collection params (duration, interval, perf sample rate, CPU mask, NUMA node) from the scenario's `collection.yaml` (see File layout) — NOT hardcoded, so the reference and synthetic sides share one source of truth.
- **Warmup-aligned collection window:** spawns the binary with stdout piped; the reference program runs its warmup loop then **prints a marker line** (e.g. `__MEASUREMENT_WINDOW_START__`) the instant it enters the steady-state measurement window. `collect_reference.py` watches stdout and only **starts `perf record` + the devkit topdown call after the marker**, so the captured profile covers the steady-state window only — warmup transients (page-faults, first-touch allocation, JIT warmup) don't dilute it. The synthetic side (`run_and_collect`) follows the **same** warmup→measure contract, so both profiles are steady-state-aligned. `--duration` bounds the collection, not the binary's total runtime (the binary self-terminates after its measurement window).
- Launches the binary `taskset`-pinned to the **same CPU mask mirage's synthetic workload uses at runtime** (`run_and_collect`'s core-pinning), AND **`numactl`-bound to the NUMA node owning those cores** (`--membind`/`--cpunodebind` matching the mask). CPU pinning alone is insufficient on NUMA boxes: if the buffer lands on a remote NUMA node, `memory_bound`'s `backend_bound` runs artificially high and varies run-to-run. `numactl` forces memory allocation on the bound cores' local node on **both** sides, removing that noise. The mask+node are read from `collection.yaml` (one source, both sides). `perf record -g -F 99` runs over the same marker-triggered window.
- Calls `MetricsCollector(devkit_cmd=...).collect_topdown(output_path, duration, interval, pid=<ref_pid>)` (PR #45's real CLI: `devkit tuner top-down -d <dur> -i <int> -p <pid>`, stdout captured to a `.txt`).
- `TopdownParser.parse_text(<txt>)` → a `Profile` (L1 percentages, devkit-native, post-#46).
- Writes the scenario's `topdown.json` (the fitting target, via `Profile.model_dump_json()`) + folds the perf data to `flamegraph.svg` (via `perf script | flamegraph.pl`, if available; non-fatal if not).
- **LLC-miss gate (memory_bound only):** before declaring the capture done, confirms the LLC-miss rate is > 90% (from `perf stat -e cache-misses,cache-references` over the measurement window); if not, prints a warning that the per-worker buffer is too small for this box's LLC and the capture is not a valid backend-bound target. The user fixes the buffer size in `collection.yaml` and re-captures. This makes the "≥2-3× NUMA LLC" design verifiable, not assumed.
- Prints the captured L1 so the user confirms the demo is dominantly backend/retiring-bound before fitting.

## mirage fitting side

### `seed_instruction.json` (per scenario, out-of-band by design)

Same shape as the spike's `base_instruction()` (project_name, compile_flags, stages with `synthesis_config`, config with runtime knobs). Knobs set so the synthetic workload starts ~35 pp off the expected dominant metric:

- `memory_bound` seed: **compute-dominated** — `comp_stage` matmul heavy, `mem_stage` with `working_set_mb=16`, `access_pattern=sequential`, `memory_ratio=0.2`, `compute_ratio=0.8`. Expected synthetic backend ~30-35 vs target ~70 → ~-35 pp backend gap (>10 pp threshold → priority ≥2 structural).
- `compute_bound` seed: **memory-dominated** — `mem_stage` `working_set_mb=256` `access_pattern=random` `memory_ratio=0.8`, `comp_stage` `iterations=10` `archetype=matmul`, `compute_ratio=0.2`. Expected synthetic retiring ~25 vs target ~60 → ~-35 pp retiring gap → structural.

### `sensitivity.json` (per scenario, spike-grounded directions)

Format: `{knob: {target_metric, expected_direction, verdict, values, metric_values}}`. Directions from the spike's §4 proven findings:
- `memory_bound`: `working_set_mb`→backend up (controllable), `access_pattern`→backend up (categorical, controllable), `memory_ratio`→backend up (controllable), `compute_ratio`→retiring up (secondary), `thread_count`→frontend up, `qps`→bad_speculation down.
- `compute_bound`: `compute_ratio`→retiring up, `iterations`→retiring up (structural), `archetype`→retiring up (categorical, matmul>compute), `memory_ratio`→backend up (secondary), `thread_count`→frontend up, `qps`→bad_speculation down.

### Driver — `examples/run_loop_demo.py`

```
python3 examples/run_loop_demo.py --scenario memory_bound \
    [--max-iter 10] [--threshold 10] [--out-dir run_out] [--no-agent]
```
- Loads the scenario's captured `topdown.json` → `Profile` (via `Profile.model_validate_json`, matching the `model_dump_json` the collector writes), `seed_instruction.json`, `sensitivity.json`.
- Builds `FrameworkConfig` (default_config.yaml + the box's `base_url`+`api_key` for the agent via PR #55), `Pipeline(output_base_dir=out-dir, config=cfg, agent=AgentCore(cfg.agent))`.
- **Agent gate:** if `not agent.is_available()` and `--no-agent` was NOT passed → warn + exit early: both scenarios need the structural/LLM tier (a 35 pp gap can't be closed by runtime knobs alone → would just exhaust at `runtime_tier_exhausted_agent_unavailable`). Set the box's gateway `base_url`+`api_key` (PR #55) first.
- **`--no-agent` (runtime-only mode, default off):** forces the LLM structural tier off — the loop applies only priority==1 runtime adjustments and **stops gracefully** at `runtime_tier_exhausted_agent_unavailable` (treated as an expected stop, not an error). Cheap to implement (skip the agent gate's hard-exit; let the loop's existing runtime-then-stop path run) and out of the core validation scope, but invaluable later for separately validating the runtime tier's real-machine behavior and isolating whether a fitting problem is the runtime or structural tier. In this mode the success criteria's "steering triggered" criterion (priority ≥ 2) is *not* expected to hold — the driver prints `RUNTIME-ONLY` instead of `PASS/FAIL`.
- `--threshold` overrides `comparison.topdown_threshold_pct` (default 10; set 5 to force iteration on borderline gaps).
- **Reads `collection.yaml`** (same file `collect_reference.py` reads) and feeds its params (duration, interval, perf sample rate, CPU mask, NUMA node) into the synthetic side's collection + `numactl`/`taskset` binding. This guarantees the reference and synthetic captures used **identical** collection conditions — no silent divergence from hardcoded reference defaults vs `Pipeline` defaults.
- Runs `pipeline.run_iteration_loop(customer_profile, seed, sensitivity, max_iter, collect=None, build=None)` — `collect=None` → real `run_and_collect` (devkit topdown of the *synthetic* binary, with the `collection.yaml` mask/node/duration); `build=None` → real codegen+cmake+make.
- **Prints a per-iteration steering table:** iteration, converged, priority, topdown_diffs, adjustments, observed_effects, score — so the user watches knobs move metrics toward the target.
- **Prints a final `PASS`/`FAIL` summary** evaluating the four success criteria (see Success criteria section): `PASS` only if steering-triggered ∧ dominant-monotonic ∧ dominant-terminal-state ∧ non-dominant-cap; otherwise `FAIL` with which criterion failed. `--no-agent` mode prints `RUNTIME-ONLY` instead.
- **Honest in-band diagnostic:** if `stop_reason == "converged"` and `total_iterations == 1` → prints `"seed was in-band (no steering needed); re-run with --threshold 5 to force iteration"` — no silent false success.

## Out-of-band guarantee

Seeds are designed with ~35 pp gaps on the dominant metric, which survive real-hardware variance (the prior run's 9.5 pp gap was in-band; 35 pp is not). `--threshold` (tighten 10→5 pp) forces iteration on any borderline case. The post-run in-band diagnostic surfaces a too-easy convergence immediately. Note: the seed's expected dominant-metric value is an *estimate*; the real captured target (from the reference demo) is the ground truth. If the captured target differs enough that the seed lands in-band, the `--threshold` lever fixes it without code change.

## Scope

**In scope:**
- Vendored `taskflow` header (+ license/README).
- Two reference demo programs (memory_bound + compute_bound): CMakeLists + main.cpp + self-dev stage files + README.
- Per-scenario `seed_instruction.json` + `sensitivity.json` + `collection.yaml` (shared collection params) + `collect_reference.py`.
- `examples/run_loop_demo.py` driver (real codegen+build+collect+LLM structural tier, in-band diagnostic).
- This spec + an implementation plan.

**Out of scope (deferred):**
- Real flamegraph/coverage as a steering axis (coverage is structural, not knob-responsive — matches the spike; the flamegraph here is for human inspection, not a loop metric).
- The runtime/deterministic tier on real ARM (already covered by the stub-plant unit tests; the ~35 pp gaps force the structural tier).
- Real OSS library linking in the *synthetic* mirage workload (mirage's codegen uses synthesis archetypes that *simulate* the Topdown mix; the OSS flavor lives in the reference demo, not the synthetic code).
- `Eigen` for the compute demo (hand-rolled matmul instead).
- **Fitting real customer business-code workloads** (richer stages, real hotspot structure, coverage axis) — filed as a high-priority follow-up issue; do this after the two taskflow demos validate fitting OK.

## Verification (user-side, on the Kunpeng box)

1. Build a reference demo: `cmake -S examples/scenarios/memory_bound/reference -B build && cmake --build build`.
2. Capture the target: `python3 examples/scenarios/memory_bound/collect_reference.py --binary build/memory_bound_ref --out-dir examples/scenarios/memory_bound` → `topdown.json` (confirm backend-bound). The script `numactl`+`taskset`-pins the binary per `collection.yaml`, aligns collection to the `__MEASUREMENT_WINDOW_START__` marker, and gates on LLC-miss > 90% — a warning here means the per-worker buffer is too small; fix `collection.yaml` and re-capture.
3. Fit: `PYTHONPATH=src python3 examples/run_loop_demo.py --scenario memory_bound --max-iter 10` → watch the synthetic workload's Topdown converge toward the captured target across iterations; the driver prints a final `PASS`/`FAIL` against the four success criteria. Repeat for `compute_bound`.
4. The per-iteration steering table shows real knob adjustments (LLM structural revisions) moving the dominant metric toward the target — the steering path validated on real ARM.
