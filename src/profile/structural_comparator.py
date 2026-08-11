"""Call-path structural overlap between customer and workload flamegraphs.

Measures how closely the generated workload's call tree mirrors the customer's.
Only trunk + stage + open-source leaf frames are required (custom leaf frames
are expected divergence - they are collapsed into synthesis in the workload).
A required frame is covered iff some workload stack has the same frame at the
same depth with the same parent.
"""

from __future__ import annotations

from typing import Any

from ingestion.classifier import FunctionClassifier


class StructuralComparator:
    """Compare customer and workload call-path structure."""

    def __init__(self, classifier: FunctionClassifier | None = None) -> None:
        self.classifier = classifier or FunctionClassifier()

    def compare(
        self,
        customer_stacks: list[tuple[list[str], int]],
        workload_stacks: list[tuple[list[str], int]],
    ) -> dict[str, Any]:
        """Return a structural-alignment report."""
        required = self._required_frames(customer_stacks)
        workload_pos = self._positions(workload_stacks)

        open_required = {(f, d, p) for f, d, p in required if self._is_open(f)}
        open_covered = sum(1 for r in open_required if r in workload_pos)
        open_total = len(open_required)

        stage_required = [
            (f, d, p) for f, d, p in required if d > 0 and (f, d, p) not in open_required
        ]
        stage_covered = sum(1 for r in stage_required if r in workload_pos)

        trunk_present = any(d == 0 and (f, d, p) in workload_pos for f, d, p in required)

        total = len(required) or 1
        covered = sum(1 for r in required if r in workload_pos)

        return {
            "trunk_present": trunk_present,
            "stage_coverage_pct": (stage_covered / len(stage_required) * 100.0)
            if stage_required
            else 100.0,
            "open_source_structural_coverage_pct": (open_covered / open_total * 100.0)
            if open_total
            else 100.0,
            "overall_overlap_pct": covered / total * 100.0,
        }

    def _required_frames(self, stacks: list[tuple[list[str], int]]) -> list[tuple[str, int, str]]:
        out: list[tuple[str, int, str]] = []
        seen: set[tuple[str, int, str]] = set()
        for frames, _count in stacks:
            for i, frame in enumerate(frames):
                source, _lib = self.classifier.classify(frame)
                if source == "customer_custom" and i == len(frames) - 1:
                    continue  # custom leaf -> expected divergence, not required
                parent = frames[i - 1] if i > 0 else ""
                key = (frame, i, parent)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    @staticmethod
    def _positions(stacks: list[tuple[list[str], int]]) -> set[tuple[str, int, str]]:
        pos: set[tuple[str, int, str]] = set()
        for frames, _count in stacks:
            for i, frame in enumerate(frames):
                pos.add((frame, i, frames[i - 1] if i > 0 else ""))
        return pos

    def _is_open(self, frame: str) -> bool:
        source, _lib = self.classifier.classify(frame)
        return source == "open_source"
