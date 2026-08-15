# Design — wire ratio knobs + fix the categorical verdict

- **Date:** 2026-08-15
- **Author:** Jack Wei
- **Related:** RFC 0003 (PR #43), PR #45, follow-up issues #46/#47/#48
- **Status:** Approved (self-driven per user delegation)

## Context

The RFC 0003 §4 steerability spike (full sweep, no `--only-knob`) returned:

| knob | target_metric | expected | metric_values | verdict |
|---|---|---|---|---|
| working_set_mb | backend_bound | up | 58.52→69.78→72.59 | controllable |
| access_pattern | backend_bound | up | 60.98→62.95→69.64 | controllable |
| iterations_mem | backend_bound | up | 59.74→69.71→78.43 | controllable |
| archetype | retiring | up | 23.01→21.24→63.59 | weak |
| compute_ratio | retiring | up | 23.05→23.05→23.02 | weak |
| memory_ratio | backend_bound | up | 69.97→69.8→70.03 | weak |

The §4 gate (pass iff all five of `working_set_mb`/`access_pattern`/`archetype`/
`compute_ratio`/`memory_ratio` are `controllable`) **fails as written: 2/5.**
But the three `weak` verdicts are **not plant-steerability failures**:

- `compute_ratio` / `memory_ratio` are **inert by codegen omission**: `config_loader.h.j2`
  loads them into `cfg`, but `main.cpp.j2` never reads `cfg.compute_ratio` /
  `cfg.memory_ratio` — so the spike's baked values are dropped. Spread ≈0.03 = dead.
- `archetype` is **mislabeled by a categorical-blind verdict**: `verdict_for` applies a
  strict ordinal-monotonic test; archetype's values `[compute, hash, matmul]` have no
  guaranteed monotonic relation to retiring, and the real signal (matmul 63.59 vs ~21)
  is a 42-point spread — strong steering — masked by the non-monotonic dip
  compute(23.01)→hash(21.24).

The plant IS steerable on the dimensions the loop will adjust. This design closes the
gate by (1) wiring the two ratio knobs into `main.cpp.j2` and (2) teaching the spike's
verdict classifier to handle categorical knobs by discrimination.

## Section 1 — Codegen: ratio knobs drive per-stage call frequency

**Files:** `src/codegen/generator.py`, `src/codegen/templates/main/main.cpp.j2`.
No change to `config_loader.h.j2` / `config.json.j2` / `knob_gen.py` — they already
load `cfg.compute_ratio` / `cfg.memory_ratio`.

**`generator.py`** (build `stage_ctx`, ~line 194): attach a per-stage `ratio_expr`
from `implementation_strategy`, and emit a top-level `burst` into the scaffold context:

```python
ratio_expr = "cfg.memory_ratio" if impl == "memory_synthesis" else "cfg.compute_ratio"
stage_ctx = {
    "include_statement": f'#include "{filename}"',
    "ratio_expr": ratio_expr,
    "warmup_call": loop_call,
    "loop_call": loop_call,
    "measure_call": f"// {stage['stage_name']} measurement start",
}
```
and in `scaffold_context` add `"burst": instruction.get("burst", 20)`.

**`main.cpp.j2`** — both warmup and measurement loop bodies, from:
```cpp
{% for stage in stages %}
{{ stage.loop_call }}
{% endfor %}
```
to:
```cpp
{% for stage in stages %}
for (int _r = 0; _r < int({{ stage.ratio_expr }} * {{ burst }}); ++_r) {
    {{ stage.loop_call }}
}
{% endfor %}
```

### Semantics & safety

- `compute_ratio` = compute-stage calls per tick; `memory_ratio` = memory-stage calls
  per tick. **Independent** — matches the spike's independent OAT sweep (each knob
  moved while the other holds its default 0.5).
- The structural `iterations` knob stays baked as each behavior function's default
  arg → still drives **work-per-call** (proven controllable: 59.74→69.71→78.43) →
  **orthogonal**, not clobbered. (Rejected: scaling the `iterations` argument from
  `main.cpp`, which would override the baked default and deaden `iterations_mem`.)
- At default ratios `0.5/0.5` with `burst=20`: 10 compute + 10 memory calls/tick =
  1:1 mix — identical *relative* mix to today's 1:1, just more work/tick. Topdown is
  a percentage → scale-invariant → the existing controllable knobs stay monotonic.
  (Re-spike re-confirms; the full 6-knob sweep is re-run as the safety check.)
- `compute_ratio 0.2→0.5→0.8` → 4→10→16 compute calls (memory fixed at 10) → compute
  share rises → **retiring ↑**. `memory_ratio 0.2→0.5→0.8` → 4→10→16 memory calls →
  **backend_bound ↑**. Both match `expected: up`.
- `burst` default 20 chosen so `round(0.2*20)=4`, `round(0.5*20)=10`, `round(0.8*20)=16`
  are well-separated and monotonic in the ratio (no ties at the low end).

### Tests (PR C)

Extend an existing codegen test: generate a project with two stages (one
`compute_synthesis`, one `memory_synthesis`) and assert the rendered `main.cpp`
contains `for (int _r = 0; _r < int(cfg.compute_ratio * 20); ++r) { ..._compute(); }`
for the compute stage and `cfg.memory_ratio` for the memory stage, and that `burst`
defaults to 20 when absent from the instruction.

## Section 2 — Spike verdict: discrimination test for categorical knobs

**File:** `examples/steerability_spike.py` on `rfc/0003-controllability` (amends PR #43).
**Function:** `verdict_for`.

Add a per-sweep `ordinal` flag (default `True` for numeric knobs, `False` for
categorical string-valued knobs like `archetype`). Split the classifier:

- **Ordinal path** (unchanged behavior): `dead` if all values equal; else `controllable`
  if monotonic in `expected` direction, otherwise `weak`.
- **Categorical path** (discrimination): `spread = max(vals) - min(vals)`.
  - `dead` if `spread <= EPS` (EPS = 0.01).
  - `controllable` if `spread >= DISCRIM_THRESHOLD` (propose **5.0 pp**).
  - else `weak`.

Threshold rationale: archetype's spread is 42.35; the inert ratio knobs' spread is
~0.03. A 5.0 pp threshold cleanly separates real steering from noise/dead. Mark
archetype's sweep `ordinal: False` → re-verdicts `controllable`. The ratio sweeps stay
ordinal (numeric, expected-monotonic) and will flip dead→controllable once Section 1
lands and they actually move.

Rejected: reordering archetype's values to force monotonicity (gaming the test —
categorical knobs have no guaranteed monotonic relation); relaxing the ordinal bar
for *all* knobs (would hide real non-monotonic ordinal regressions like a cache cliff).

## Section 3 — Process, PRs, re-spike

Two PRs, relevant-only commits, off the right base:

- **PR C — codegen** (off `main`, `fix/wire-ratio-knobs`): `generator.py` +
  `main.cpp.j2` + `burst` field + codegen test. Does not touch the spike.
- **PR D — verdict classifier** (on `rfc/0003-controllability`, amends PR #43):
  `verdict_for` split + `ordinal` flags on the sweeps. Production code untouched.

**Re-spike (user-side, after both merge):** re-run the **full 6-knob sweep** (not just
the three fixed ones) — the codegen change shifts per-tick work, so re-confirming
`working_set_mb`/`access_pattern`/`iterations_mem` stay monotonic is the safety check.
Expected outcome: `compute_ratio`/`memory_ratio` dead→controllable; `archetype`
weak→controllable. Then the §4 gate passes on all five and the loop is unblocked for
writing-plans.

## Out of scope

- The loop driver itself (still gated on the re-spike passing).
- Units renormalization (#46); DevkitConfig plumbing (#47); `duration_seconds` (#48).
- Reconciling spike↔production `_parse_topdown_text` duplication (post-loop cleanup).
