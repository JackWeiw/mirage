"""Track iteration results and convergence trends across multiple iterations."""

import json
import pathlib

from pydantic import BaseModel, Field, PrivateAttr

from config.framework_config import ComparisonConfig


def compute_score(record: "IterationRecord", comparison: ComparisonConfig | None = None) -> float:
    """Normalized multi-dim score; lower is better.

    score = sum(|topdown_diff| / topdown_threshold)
          + |memory_diff| / memory_threshold
          + max(0, coverage_threshold - coverage) / coverage_threshold

    A converged iteration scores ~0 on every exceeded-threshold term.
    """
    if comparison is None:
        comparison = ComparisonConfig()

    topdown_term = (
        sum(abs(v) for v in record.topdown_diffs.values()) / comparison.topdown_threshold_pct
    )
    memory_term = abs(record.memory_diff_pct) / comparison.memory_threshold_pct
    coverage_term = (
        max(0.0, comparison.coverage_threshold_pct - record.coverage_pct)
        / comparison.coverage_threshold_pct
    )
    return topdown_term + memory_term + coverage_term


class IterationRecord(BaseModel):
    """Record of one iteration's comparison results."""

    iteration: int
    converged: bool
    topdown_diffs: dict[str, float] = Field(default_factory=dict)
    memory_diff_pct: float = 0.0
    coverage_pct: float = 0.0
    strategy_priority: int = 0
    duration_seconds: float = 0.0
    timestamp: str = ""
    # --- Phase 2 extensions ---
    score: float | None = None
    adjustments: list[dict[str, object]] = Field(default_factory=list)  # raw emitted adjustments
    applied_moves: list[dict[str, object]] = Field(default_factory=list)  # {knob, tier, sign}
    observed_effects: dict[str, float] = Field(default_factory=dict)
    failed: bool = False  # run/collect failure
    build_failed: bool = False
    failure_reason: str = ""
    build_stderr: str = ""


class IterationHistory(BaseModel):
    """History of all iterations for a workload simulation run."""

    customer_name: str
    records: list[IterationRecord] = Field(default_factory=list)
    best_iteration: int | None = None
    total_iterations: int = 0
    # set True by the loop driver (PR 3) when the agent is unavailable and the
    # run degrades to runtime-tier-only; surfaced in PipelineResult for honest reporting.
    degraded: bool = False
    _best_index: int = PrivateAttr(default=0)

    def add_record(self, record: IterationRecord) -> None:
        """Add an iteration record and update best_iteration by score.

        Failed / build-failed records are excluded from best_iteration (they
        have no measured score). The score is computed from the record's
        topdown/memory/coverage if the caller did not supply one.
        """
        if record.score is None:
            record.score = compute_score(record)
        self.records.append(record)
        self.total_iterations = len(self.records)

        if record.failed or record.build_failed:
            return  # infra failure: never becomes best_iteration

        new_index = len(self.records) - 1
        if self.best_iteration is None:
            self.best_iteration = record.iteration
            self._best_index = new_index
            return
        current_best = self.records[self._best_index]
        # belt-and-braces: should never be True given the early-return above;
        # failed records never enter _best_index, so current_best is always non-failed.
        if (
            current_best.failed
            or current_best.build_failed
            or (
                record.score
                < (current_best.score if current_best.score is not None else float("inf"))
            )
        ):
            self.best_iteration = record.iteration
            self._best_index = new_index

    def get_convergence_trend(self) -> list[dict[str, float]]:
        """Get convergence trend: Topdown diffs over iterations."""
        return [
            {"iteration": r.iteration, "total_diff": sum(abs(v) for v in r.topdown_diffs.values())}
            for r in self.records
        ]

    def is_converging(self) -> bool:
        """Check if the trend is improving (diffs getting smaller)."""
        if len(self.records) < 2:
            return True
        trend = self.get_convergence_trend()
        recent = trend[-3:]
        return all(
            recent[i]["total_diff"] >= recent[i + 1]["total_diff"] for i in range(len(recent) - 1)
        )

    def recent_adjustments(self, n: int) -> list[dict[str, object]]:
        """Flat list of raw adjustments from the last n records."""
        # n<=0 returns all records' adjustments (records[-0:] == records[0:])
        out: list[dict[str, object]] = []
        for r in self.records[-n:]:
            out.extend(r.adjustments)
        return out

    def no_improvement_for(self, k: int) -> bool:
        """True if the last k *non-failed* iterations failed to set a new best score.

        Failed / build-failed rounds are skipped entirely (infra failure, no
        score) — they neither advance nor reset the streak. A round that
        refreshes the running minimum resets the streak to 0.
        """
        streak = 0
        best = float("inf")
        for r in self.records:
            if r.failed or r.build_failed:
                continue  # invisible to this streak
            # belt-and-braces: add_record always sets score, but guard nonetheless
            score = r.score if r.score is not None else compute_score(r)
            if score < best:
                best = score
                streak = 0
            else:
                streak += 1
                if streak >= k:
                    return True
        return False

    def save(self, filepath: pathlib.Path) -> pathlib.Path:
        """Save iteration history to JSON file."""
        filepath.write_text(self.model_dump_json(indent=2))
        return filepath

    @classmethod
    def load(cls, filepath: pathlib.Path) -> "IterationHistory":
        """Load iteration history from JSON file."""
        data = json.loads(filepath.read_text())
        return cls.model_validate(data)
