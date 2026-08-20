# Steering-validation scenarios

Validate mirage's end-to-end workload simulation + self-iteration steering on a
controlled OSS-stack example. Spec: `docs/superpowers/specs/2026-08-20-steering-validation-taskflow-demos-design.md`.

## Workflow (3 steps)

1. **Build a reference demo** (on the Kunpeng ARM box):
   `cmake -S examples/scenarios/memory_bound/reference -B build/mem && cmake --build build/mem`
   → binary at `build/mem/memory_bound_ref` (CMake target from `reference/CMakeLists.txt`;
   taskflow is the vendored header at `examples/third_party/taskflow/` — no external deps).
2. **Capture the fitting target**:
   `python3 examples/scenarios/memory_bound/collect_reference.py --binary build/mem/memory_bound_ref --devkit-cmd /opt/devkit/bin/devkit`
   → `topdown.json` (confirm backend-bound; the LLC-miss gate guards the buffer size).
   `--devkit-cmd` points at the devkit binary whose `tuner top-down` subcommand emits the
   L1 report on stdout; required (omit it -> "devkit_cmd not configured" and capture aborts).
3. **Fit** (devkit via `--config` yaml, LLM gateway via `MIRAGE_AGENT_*` env layered over it):
   `PYTHONPATH=src MIRAGE_AGENT_API_KEY=sk-... MIRAGE_AGENT_BASE_URL=https://gw/v1 \
      MIRAGE_AGENT_PROVIDER=openai MIRAGE_AGENT_MODEL=gpt-4o \
      python3 examples/run_loop_demo.py --scenario memory_bound --max-iter 10 --config fw.yaml`
   → the driver prints a per-iteration steering table + a final PASS/FAIL.

`fw.yaml` carries the devkit (kept out of env); precedence is `yaml < env`, so the
`MIRAGE_AGENT_*` env still selects the LLM gateway while the devkit stays in the file:

```yaml
framework:
  devkit:
    devkit_cmd: /opt/devkit/bin/devkit
    cpu_range: "0-63"            # taskset pin; must match collection.yaml's cpu_mask
    duration_seconds: 20
    interval_seconds: 3
    collect_pid: true
```

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
