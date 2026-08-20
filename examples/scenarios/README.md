# Steering-validation scenarios

Validate mirage's end-to-end workload simulation + self-iteration steering on a
controlled OSS-stack example. Spec: `docs/superpowers/specs/2026-08-20-steering-validation-taskflow-demos-design.md`.

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
