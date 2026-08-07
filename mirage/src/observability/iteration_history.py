"""Track iteration results and convergence trends across multiple iterations."""

import json
import pathlib

from pydantic import BaseModel, Field, PrivateAttr


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


class IterationHistory(BaseModel):
    """History of all iterations for a workload simulation run."""

    customer_name: str
    records: list[IterationRecord] = Field(default_factory=list)
    best_iteration: int | None = None
    total_iterations: int = 0
    _best_index: int = PrivateAttr(default=0)

    def add_record(self, record: IterationRecord) -> None:
        """Add an iteration record and update best_iteration."""
        self.records.append(record)
        self.total_iterations = len(self.records)
        new_index = len(self.records) - 1
        new_score = sum(abs(v) for v in record.topdown_diffs.values())

        if self.best_iteration is None:
            self.best_iteration = record.iteration
            self._best_index = new_index
        else:
            current_best = self.records[self._best_index]
            current_best_score = sum(abs(v) for v in current_best.topdown_diffs.values())
            if new_score < current_best_score:
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

    def save(self, filepath: pathlib.Path) -> pathlib.Path:
        """Save iteration history to JSON file."""
        filepath.write_text(self.model_dump_json(indent=2))
        return filepath

    @classmethod
    def load(cls, filepath: pathlib.Path) -> "IterationHistory":
        """Load iteration history from JSON file."""
        data = json.loads(filepath.read_text())
        return cls.model_validate(data)
