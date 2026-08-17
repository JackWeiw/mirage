# Phase 2 PR 2: LLM revise leg — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `AgentCore.revise_instruction` — the LLM realism-preserving leg of the Phase 2 loop — plus its prompt and a mock-agent test proving PR 1's gate catches LLM-hallucinated wrong-direction / wrong-tier adjustments.

**Architecture:** PR 1 landed the deterministic half (`adjustment.py`: `validate_adjustments`, `deterministic_revise`, knob space, `apply_adjustments`) and the history extensions. PR 2 adds the LLM half: `AgentCore.revise_instruction(prior_instruction, report, sensitivity, history) -> (revised_instruction, list[adjustment])`. It loads a prompt, fills placeholders with JSON-serialized inputs, calls the existing `_call_llm_json`, and extracts the response. It does **not** self-gate — the loop driver (PR 3) runs `validate_adjustments` on the returned adjustments before `apply_adjustments`. The mock test feeds `revise_instruction`'s output through `validate_adjustments` to prove a hallucinated wrong-direction adjustment and a runtime-knob-on-structural-tier are both dropped.

**Tech Stack:** Python 3.13, pydantic v2, pytest (`pythonpath=["src"]`, `--cov-fail-under=60`), ruff + ruff-format + mypy strict (src/), pre-commit. Anthropic SDK is optional (agent loads without it); tests mock the LLM, no real call.

---

## File Structure

- **Create:** `src/agent/prompts/revise_instruction.md` — the LLM prompt (4 placeholders, output schema, proven-direction constraint).
- **Modify:** `src/agent/agent_core.py` — append `revise_instruction` + a module-level `_serialize_recent_history` helper + a `_RECENT_HISTORY_N` constant. Reuses `_load_prompt`, `_call_llm_json`, `LLMResponseError`.
- **Create:** `tests/agent/test_revise_instruction.py` — mock-agent tests (no real LLM).

No other files. `validate_adjustments` / `_SENS` shape / report shape come from PR 1 (`src/agent/adjustment.py`) and are reused, not modified.

---

### Task 1: The `revise_instruction.md` prompt

**Files:**
- Create: `src/agent/prompts/revise_instruction.md`
- Test: `tests/agent/test_revise_instruction.py` (new file — create with this task's single test)

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_revise_instruction.py`:

```python
"""Tests for AgentCore.revise_instruction (mock-agent, no real LLM)."""

import json
import pathlib
from typing import Any

import pytest

from agent.agent_core import AgentCore, LLMResponseError


PROMPT_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "agent" / "prompts" / "revise_instruction.md"


def test_revise_instruction_prompt_exists_and_has_placeholders() -> None:
    text = PROMPT_PATH.read_text()
    assert "{prior_instruction}" in text
    assert "{report}" in text
    assert "{sensitivity}" in text
    assert "{recent_history}" in text
    # The prompt must state the proven-direction constraint and the output schema.
    assert "proven direction" in text.lower()
    assert "revised_instruction" in text
    assert "adjustments" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_revise_instruction.py::test_revise_instruction_prompt_exists_and_has_placeholders -q`
Expected: FAIL — `FileNotFoundError` (prompt not created yet).

- [ ] **Step 3: Create the prompt**

Create `src/agent/prompts/revise_instruction.md`:

```markdown
You are a C++ workload-revision engineer for mirage. You revise a prior workload
instruction so the regenerated workload both (a) better replicates the
customer's real code — diverse, non-regular business-logic / structural shapes
that resist chip-optimizer over-fitting to a single hotspot — and (b) moves the
comparison metrics toward target.

Inputs (JSON, one per section):
- PRIOR INSTRUCTION: the instruction used in the previous iteration.
- COMPARISON REPORT: per-metric diffs vs the customer. diff_pct > 0 means the
  workload is TOO HIGH on that metric (it must decrease); diff_pct < 0 means
  too low (it must increase). within_threshold marks metrics already on target.
- SENSITIVITY TABLE: for each knob, the metric it targets and its PROVEN
  direction ("up" raises that metric, "down" lowers it).
- RECENT HISTORY: prior adjustments + their observed effects, so you avoid
  toggling a knob back and forth.

Your job:
1. Pick one or more STRUCTURAL knobs (archetype, access_pattern, working_set_mb,
   iterations) whose PROVEN direction reduces an unsatisfied metric's error.
   You MAY also revise the business-logic / structural shape of the instruction
   to better replicate the customer's real code. Do NOT pick runtime knobs
   (compute_ratio, memory_ratio, thread_count, qps) — those are owned by the
   deterministic tier.
2. Apply the chosen knob changes to the prior instruction, producing a REVISED
   INSTRUCTION with the SAME schema as the prior instruction.
3. Emit the adjustments you applied, each shaped as:
   {"stage": "<stage_name or empty string>", "knob": "<knob>",
    "from": <old value>, "to": <new value>, "rationale": "<short reason>",
    "expected_metric": "<metric>", "expected_direction": "<up|down>"}

CONSTRAINT: every adjustment's direction MUST match the sensitivity table's
PROVEN direction for that knob. Never move a knob against its proven direction.

Output structured JSON matching this schema:
{
  "revised_instruction": { ...the revised instruction, same schema as prior... },
  "adjustments": [
    {"stage": "...", "knob": "...", "from": ..., "to": ...,
     "rationale": "...", "expected_metric": "...", "expected_direction": "..."}
  ]
}

=== PRIOR INSTRUCTION ===
{prior_instruction}

=== COMPARISON REPORT ===
{report}

=== SENSITIVITY TABLE ===
{sensitivity}

=== RECENT HISTORY ===
{recent_history}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/agent/test_revise_instruction.py::test_revise_instruction_prompt_exists_and_has_placeholders -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/prompts/revise_instruction.md tests/agent/test_revise_instruction.py
git commit -m "feat(agent): add revise_instruction prompt template"
```

---

### Task 2: `revise_instruction` method (happy path + malformed-response raise)

**Files:**
- Modify: `src/agent/agent_core.py` (append `revise_instruction`, `_serialize_recent_history`, `_RECENT_HISTORY_N`)
- Test: `tests/agent/test_revise_instruction.py` (append 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/agent/test_revise_instruction.py`:

```python
def _make_agent(recorded_response: dict[str, Any]) -> AgentCore:
    """AgentCore with _call_llm_json mocked to return a recorded response."""
    agent = AgentCore()  # api_key=None -> offline; _call_llm_json is mocked below
    agent._call_llm_json = lambda prompt: recorded_response  # type: ignore[assignment]
    return agent


def test_revise_instruction_returns_revised_and_adjustments() -> None:
    recorded = {
        "revised_instruction": {"project_name": "sim", "stages": [], "config": {}},
        "adjustments": [
            {"stage": "mem_stage", "knob": "working_set_mb", "from": 64, "to": 32,
             "rationale": "lower backend", "expected_metric": "backend_bound",
             "expected_direction": "up"},
        ],
    }
    agent = _make_agent(recorded)
    revised, adjustments = agent.revise_instruction(
        prior_instruction={"project_name": "sim", "stages": [], "config": {}},
        report={"topdown_l1": {"backend_bound": {"diff_pct": 20.0, "within_threshold": False}}},
        sensitivity={"working_set_mb": {"target_metric": "backend_bound", "expected_direction": "up"}},
        history=None,
    )
    assert revised == recorded["revised_instruction"]
    assert adjustments == recorded["adjustments"]


def test_revise_instruction_raises_on_missing_revised_instruction() -> None:
    recorded = {"adjustments": []}  # no revised_instruction
    agent = _make_agent(recorded)
    with pytest.raises(LLMResponseError):
        agent.revise_instruction(
            prior_instruction={}, report={}, sensitivity={}, history=None,
        )


def test_revise_instruction_raises_on_non_dict_adjustment() -> None:
    recorded = {"revised_instruction": {}, "adjustments": [{"knob": "x"}, "not a dict"]}
    agent = _make_agent(recorded)
    with pytest.raises(LLMResponseError):
        agent.revise_instruction(
            prior_instruction={}, report={}, sensitivity={}, history=None,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agent/test_revise_instruction.py -q`
Expected: FAIL — `AttributeError: 'AgentCore' object has no attribute 'revise_instruction'`.

- [ ] **Step 3: Implement**

Append to `src/agent/agent_core.py` (after `run_full_chain`, at module level add the constant + helper, and the method on the class):

```python
# How many recent records' adjustments/effects to surface in the revise prompt.
_RECENT_HISTORY_N = 5


def _serialize_recent_history(history: Any) -> str:
    """Serialize recent adjustments + observed effects for the revise prompt.

    Duck-typed on `history` (an IterationHistory in production, a fake in
    tests) so the agent module need not import observability.
    """
    recs: list[dict[str, Any]] = []
    recent_records = getattr(history, "records", [])[-_RECENT_HISTORY_N:]
    for r in recent_records:
        recs.append(
            {
                "adjustments": list(getattr(r, "adjustments", [])),
                "applied_moves": list(getattr(r, "applied_moves", [])),
                "observed_effects": dict(getattr(r, "observed_effects", {})),
                "score": getattr(r, "score", None),
            }
        )
    return json.dumps(recs)
```

Add the method to `class AgentCore` (after `run_full_chain`):

```python
    def revise_instruction(
        self,
        prior_instruction: dict[str, Any],
        report: dict[str, Any],
        sensitivity: dict[str, dict[str, Any]],
        history: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Revise the prior instruction via the LLM (the realism-preserving leg).

        The LLM owns structural / business-logic revision (diverse, non-regular
        patterns that resist chip-optimizer over-fitting), constrained by the
        sensitivity table's proven directions. Returns the revised instruction
        and the adjustments the LLM applied. The CALLER (loop driver) runs
        validate_adjustments on the adjustments before apply_adjustments — this
        method does NOT self-gate, so a hallucinated wrong-direction adjustment
        is caught downstream, not here.

        Raises LLMResponseError if the response lacks `revised_instruction`
        (not a dict) or `adjustments` (not a list of dicts).
        """
        template = self._load_prompt("revise_instruction.md")
        prompt = (
            template.replace("{prior_instruction}", json.dumps(prior_instruction))
            .replace("{report}", json.dumps(report))
            .replace("{sensitivity}", json.dumps(sensitivity))
            .replace("{recent_history}", _serialize_recent_history(history))
        )
        resp = self._call_llm_json(prompt)
        revised = resp.get("revised_instruction")
        adjustments = resp.get("adjustments")
        if not isinstance(revised, dict):
            raise LLMResponseError(
                f"revise_instruction response missing 'revised_instruction' dict: {str(resp)[:200]!r}"
            )
        if not isinstance(adjustments, list) or not all(
            isinstance(a, dict) for a in adjustments
        ):
            raise LLMResponseError(
                f"revise_instruction response missing 'adjustments' list of dicts: {str(resp)[:200]!r}"
            )
        return revised, [dict(a) for a in adjustments]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_revise_instruction.py -q`
Expected: PASS (3 tests: the prompt-placeholder test from Task 1 + the 3 new = 4 total).

- [ ] **Step 5: Lint/type check**

Run: `python -m ruff check src/agent/agent_core.py tests/agent/test_revise_instruction.py` → clean.
Run: `python -m ruff format --check src/agent/agent_core.py tests/agent/test_revise_instruction.py` → clean.
Run: `python -m mypy src/agent/agent_core.py` → clean (strict).
NOTE: the test's `agent._call_llm_json = lambda ...` may need `# type: ignore[assignment]` (shown) — keep it; mypy scopes to `src/` only so tests aren't type-checked, but keep the ignore in case.

- [ ] **Step 6: Commit**

```bash
git add src/agent/agent_core.py tests/agent/test_revise_instruction.py
git commit -m "feat(agent): AgentCore.revise_instruction LLM revise leg"
```

---

### Task 3: Mock-agent gate test (gate catches hallucination + wrong tier)

**Files:**
- Test: `tests/agent/test_revise_instruction.py` (append 1 test)

- [ ] **Step 1: Write the failing test**

Append to `tests/agent/test_revise_instruction.py`:

```python
def test_revise_instruction_gate_catches_hallucination_and_wrong_tier() -> None:
    # The LLM (mocked) emits 3 adjustments on a STRUCTURAL tier:
    #   - good: working_set_mb 64->32 (decreases backend, which is too high) ✓
    #   - wrong-direction: working_set_mb 64->128 (increases backend further) ✗
    #   - wrong-tier: memory_ratio (a RUNTIME knob) emitted on a structural tier ✗
    # The loop runs validate_adjustments(tier="structural") on the result.
    from agent.adjustment import validate_adjustments

    instr = {
        "stages": [
            {"stage_name": "mem_stage",
             "strategies": [{"synthesis_config": {"working_set_mb": 64, "access_pattern": "random"}}]}
        ],
        "config": {"memory_ratio": 0.5},
    }
    report = {
        "topdown_l1": {
            "backend_bound": {"diff_pct": 20.0, "within_threshold": False},
            "retiring": {"diff_pct": 0.0, "within_threshold": True},
            "frontend_bound": {"diff_pct": 0.0, "within_threshold": True},
            "bad_speculation": {"diff_pct": 0.0, "within_threshold": True},
        },
    }
    sensitivity = {
        "working_set_mb": {"target_metric": "backend_bound", "expected_direction": "up", "verdict": "controllable"},
        "memory_ratio": {"target_metric": "backend_bound", "expected_direction": "up", "verdict": "controllable"},
    }
    recorded = {
        "revised_instruction": instr,  # unchanged for the test
        "adjustments": [
            {"stage": "mem_stage", "knob": "working_set_mb", "from": 64, "to": 32,
             "rationale": "lower backend", "expected_metric": "backend_bound", "expected_direction": "up"},
            {"stage": "mem_stage", "knob": "working_set_mb", "from": 64, "to": 128,
             "rationale": "raise backend (hallucinated)", "expected_metric": "backend_bound", "expected_direction": "up"},
            {"stage": "", "knob": "memory_ratio", "from": 0.5, "to": 0.2,
             "rationale": "runtime knob on structural tier", "expected_metric": "backend_bound", "expected_direction": "up"},
        ],
    }
    agent = _make_agent(recorded)
    _revised, adjustments = agent.revise_instruction(instr, report, sensitivity, history=None)

    accepted, rejected = validate_adjustments(adjustments, instr, report, sensitivity, tier="structural")
    assert len(accepted) == 1
    assert accepted[0]["knob"] == "working_set_mb"
    assert accepted[0]["to"] == 32
    reasons = sorted(r["reason"] for r in rejected)
    assert reasons == ["runtime_knob_not_owned_on_structural_tier", "wrong_direction"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_revise_instruction.py::test_revise_instruction_gate_catches_hallucination_and_wrong_tier -q`
Expected: FAIL (Task 2 not committed yet, OR — if Task 2 done — re-verify the trace below).

TRACE before running (the source of truth — the plan's test must match the gate's actual behavior):
- `good` (working_set_mb 64→32, structural knob, structural tier): domain ✓; tier ✓; direction: backend diff +20 → `_error_sign` returns +1 → `want_down=True`; direction "up" → `knob_raises=True`; `want_increase = (True != True) = False` (want DECREASE); actual=64 (from instr stage), to=32 → `move_sign = 32-64 = -32 < 0` → `move_up=False`; reject branches: `(want_increase(False) and not move_up(True))`=False; `(not want_increase(True) and move_up(False))`=False; move_sign!=0 → **not rejected → accepted** ✓.
- `wrong-direction` (working_set_mb 64→128): `move_sign = +64 > 0` → `move_up=True`; `want_increase=False`; `(not want_increase(True) and move_up(True))` = True → **rejected `wrong_direction`** ✓.
- `wrong-tier` (memory_ratio, runtime knob, tier="structural"): domain ✓ (memory_ratio is a known knob, to=0.2 valid); tier-ownership: `tier=="structural"` and `is_runtime=True` → **rejected `runtime_knob_not_owned_on_structural_tier`** ✓ (before direction is even checked).
- `accepted` = [good]; `rejected` reasons sorted = `["runtime_knob_not_owned_on_structural_tier", "wrong_direction"]` ✓.

If the test fails, DO NOT tweak it to force green — re-derive the trace against the actual `validate_adjustments` (`src/agent/adjustment.py`) and surface the mismatch.

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_revise_instruction.py -q`
Expected: PASS (5 tests total).

- [ ] **Step 4: Commit**

```bash
git add tests/agent/test_revise_instruction.py
git commit -m "test(agent): gate catches revise_instruction hallucination + wrong tier"
```

---

### Task 4: Full quality gate + PR

**Files:** none (verification + push)

- [ ] **Step 1: Run the full test suite (non-integration)**

Run: `python -m pytest tests/ -q`
Expected: PASS, coverage ≥ 60%.

- [ ] **Step 2: Run lint + type checks**

Run: `python -m pre_commit run --all-files`
Expected: ruff, ruff-format, mypy strict (src/), and all hooks PASS.

- [ ] **Step 3: Verify no regressions in the agent + adjustment suites**

Run: `python -m pytest tests/agent/ -q`
Expected: PASS (PR 1's 35 adjustment tests + PR 2's 5 revise tests).

- [ ] **Step 4: Push the branch and open the PR**

```bash
git push -u origin feat/phase2-pr2-revise-instruction
gh pr create --base main --title "Phase 2 PR 2: LLM revise leg" --body "Implements AgentCore.revise_instruction — the LLM realism-preserving leg of the Phase 2 auto-iteration loop per the approved spec.

- src/agent/prompts/revise_instruction.md: prompt (prior instruction + report + sensitivity table + recent history; proven-direction constraint; structural-knob-only)
- src/agent/agent_core.py: revise_instruction(prior_instruction, report, sensitivity, history) -> (revised_instruction, list[adjustment]); reuses _call_llm_json; raises LLMResponseError on malformed response; does NOT self-gate (the loop driver runs validate_adjustments, PR 3)
- tests/agent/test_revise_instruction.py: mock-agent tests (no real LLM) — returns tuple; raises on malformed response; gate catches a hallucinated wrong-direction adjustment + a runtime-knob-on-structural-tier

No loop driver, no DevkitConfig, no atomic writes (all PR 3). Depends on PR 1 (#51, merged)."
```

- [ ] **Step 5: Update project-state memory**

Note in the mirage memory file: PR 2 (LLM revise leg) opened; PR 3 pending.

---

## Self-Review

**1. Spec coverage (PR 2 scope only):**
- `revise_instruction(prior_instruction, report, sensitivity, history) -> (revised_instruction, list[adjustment])` signature (spec §4) → Task 2 ✓
- Prompt receives prior instruction, per-metric diffs + convergence, sensitivity table, recent history (spec §4) → Task 1 ✓ (4 placeholders)
- LLM revises business-logic/structural shape, constrained by sensitivity proven direction (spec §4) → Task 1 prompt ✓
- Reuses `_call_llm_json` + JSON-parse/raise path (spec §4) → Task 2 ✓
- `is_available()` gates (degradation is the loop's job, PR 3) → not in PR 2 scope (revise_instruction raises RuntimeError via `_call_llm` if called unavailable; the loop checks `is_available()` before calling — PR 3). ✓
- Mock-agent test: revised instruction carries emitted adjustments ✓; gate catches wrong-direction hallucination ✓; tier-ownership (LLM emitting runtime knob on structural tier is dropped) ✓ (spec "PR 2" line + test contract) → Task 3 ✓

PR 2 explicitly excludes: loop driver, `DevkitConfig`, `write_config_json_atomic`, error model, degradation, stub-plant integration test (all PR 3). None appear as tasks. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has full code; test code complete. ✓

**3. Type consistency:**
- `revise_instruction(..., history: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]` — `history: Any` matches `deterministic_revise`'s duck-typed `history: Any` (PR 1). ✓
- The adjustment dict shape `{stage, knob, from, to, rationale, expected_metric, expected_direction}` matches `validate_adjustments`'s contract (PR 1, which reads `adj["knob"]`, `adj["to"]`, `adj.get("expected_metric")`, `adj.get("expected_direction")`, `adj.get("from")`). ✓
- `_serialize_recent_history` reads `getattr(r, "adjustments"/"applied_moves"/"observed_effects"/"score")` — all real `IterationRecord` fields (PR 1). ✓
- `LLMResponseError` is an existing class in `agent_core.py`. ✓
- `_RECENT_HISTORY_N` module-level constant; `_serialize_recent_history` module-level function (not a method) — matches the existing `_MAX_RETRIES`/`_BACKOFF_BASE_SECONDS` module-level pattern. ✓

No issues found.
