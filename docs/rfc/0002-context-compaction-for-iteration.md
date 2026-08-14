# RFC 0002: Context compaction for automated iteration

- **Status:** Draft
- **Number:** 0002
- **Author:** JackWeiw
- **Date:** 2026-08-14
- **Related issues/PRs:** #39 (P1 module-graph IR)

## Summary

Define a compaction strategy for mirage's automated iteration loop so that
multi-round context — customer flamegraph/Topdown input, codegen plans, and
profiling results — is distilled rather than accumulated verbatim. Without
compaction, automated convergence blows the context budget within a few
iterations; this RFC proposes tiered, lossy-where-safe summarization at
iteration boundaries.

## Motivation

- **Problem:** In automated iteration, each round injects large inputs:
  - Customer flamegraph / Topdown breakdown (raw frames, hundreds–thousands).
  - The codegen plan and its materialized module graph (grows with project
    size).
  - Profiling results from the last synthesized workload (per-function self%,
    deltas vs. the customer target).
  Stacked verbatim over rounds, this exceeds the context window well before
  convergence — the agent either truncates (losing fidelity) or stalls.
- **Who hits it:** any automated-mode run that iterates more than a handful of
  times on a non-trivial customer profile. Interactive single-shot runs do not
  hit it (hence compaction is a Phase-2, not Phase-1, need).
- **Why now:** the P1 deterministic path (module-graph recovery + modular
  codegen) makes automated iteration *viable* — there is now a stable IR to
  iterate against. Compaction becomes the next blocker once iteration is real.
- **Origin:** explicitly identified during the module-graph brainstorm as a
  real Phase-2 need (unlike session persistence / sub-agents / approval
  policies, which a batch generator does not need).

## Proposed design

### Shape of the change

A **compaction pass** runs at each iteration boundary. It classifies each
accumulated artifact into a tier and rewrites the context accordingly:

| Artifact | Tier | Compaction action |
|---|---|---|
| Raw flamegraph frames | Lossy | Replace with the distilled `ModuleGraph` (P1 IR) + a per-function self% summary. Raw frames are dropped after the first recovery. |
| Topdown breakdown | Lossy | Keep the current-level metrics + the gap-to-target per node; drop the full tree once the gap list is computed. |
| Codegen plan (text) | Lossless | Replace with the materialized `ModuleGraph`/`ModuleDescriptor` IR — the plan's decisions are encoded in the IR, the prose is redundant. |
| Last profiling result | Lossy | Keep per-function self% + cumulative + the delta-vs-target; drop raw sample counts and call stacks. |
| Decisions / rationale | Lossless | Summarize as one-line bullets, but never drop a *decision* (a reversed choice would silently re-diverge). |
| Customer target profile | Lossless | Never compact — it is the convergence reference. |

### How it fits the existing architecture

- Compaction is a **loop-level** concern, not a codegen concern: it sits in the
  automated iteration driver (the P3 sub-agent orchestrator's context), not in
  `generate_from_module_graph`. The P1 IR is already the compact form of the
  flamegraph — compaction reuses it rather than inventing a second summary.
- The customer target `Profile` and the current `ModuleGraph` are the two
  anchors preserved across iterations; everything else is re-derivable.
- No change to the deterministic codegen path; compaction only governs *what
  the agent sees*, not *what the generator emits*.

### Key invariants

- **Fidelity floor:** compaction must never drop the customer target, the
  current `ModuleGraph`, or any prior *decision*. Lossy tiers may drop raw
  inputs only *after* their distilled form (IR / gap list) is computed.
- **Reversibility of re-derivation:** any dropped raw artifact must be
  re-loadable from disk (the original `Profile` file / perf data) if a later
  iteration needs it — compaction trades context for a disk read, not for
  information loss.

### Non-goals

- No general-purpose conversation memory / session persistence (a batch
  generator does not need cross-session state — established during
  brainstorming).
- No sub-agent context isolation (P3 concern; compaction is about the
  *main* loop's context).
- No LLM-driven "summarize everything" — compaction is deterministic
  structured distillation; an LLM summary is a possible *additive* layer only
  if the structured tiers prove insufficient.

## Alternatives considered

- **No compaction, just truncate oldest:** simple, but truncation is
  content-blind — it can drop the customer target or a decision, breaking
  convergence. Rejected.
- **LLM-summary of the whole context each round:** flexible but
  non-deterministic and lossy in uncontrollable ways; risks silently dropping
  a decision. Keep as a fallback layer, not the primary mechanism.
- **Re-derive everything from disk each iteration (no carried context):**
  maximally clean but throws away the iteration's *decisions* and *deltas*,
  forcing the agent to re-reason from scratch — defeats convergence. Rejected.

## Risks / trade-offs

- **Over-aggressive compaction:** dropping a still-needed raw artifact forces a
  disk re-read mid-iteration (latency, not correctness). Mitigation: lossy tiers
  trigger only after the distilled form is verified present; re-derivation is
  always available.
- **Decision leakage:** if a "decision" is misclassified as lossy, the agent may
  re-litigate it and oscillate. Mitigation: the decisions tier is lossless by
  construction and checked in tests.
- **Tier boundaries drift:** as the IR evolves, what's "distilled" changes.
  Mitigation: tiers are keyed to IR types (`ModuleGraph`, `Profile`), not to
  ad-hoc text patterns.

## Rollout / migration

- Additive: compaction is a no-op until the automated iteration loop exists
  (P3). Land the tier table + a `compact_context()` helper behind the loop
  driver; P1/P2 single-shot paths are unaffected.
- Tests assert the fidelity floor (target + ModuleGraph + decisions survive)
  and that raw frames are droppable *after* IR recovery.

## Open questions

- At which iteration count / context-size threshold does compaction trigger —
  every round, or only past a watermark?
- Is one global compaction pass enough, or do we need per-sub-agent compaction
  once fan-out (P3) lands?
- Should the decisions log live as a structured file on disk (durable across
  re-derivation) rather than in-context?

## Links

- PR #39 (P1 module-graph IR — the distilled form compaction reuses)
- Spec: `docs/superpowers/specs/2026-08-14-module-graph-ir-design.md` (P1)
