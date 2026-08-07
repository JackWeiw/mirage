"""Parse flamegraph data (folded format) into structured hotspot list."""

import pathlib

from ingestion.classifier import FunctionClassifier
from profile.profile_schema import HotspotFunction


class FlamegraphParser:
    """Parser for flamegraph data files.

    Args:
        classifier: FunctionClassifier instance. If None, creates default from YAML config.
    """

    def __init__(self, classifier: FunctionClassifier | None = None) -> None:
        self.classifier = classifier or FunctionClassifier()

    def parse_folded(self, filepath: pathlib.Path) -> list[HotspotFunction]:
        """Parse a folded flamegraph file.

        Args:
            filepath: Path to folded flamegraph text file.

        Returns:
            List of HotspotFunction sorted by self_pct descending.

        Raises:
            FileNotFoundError: If filepath doesn't exist.
            ValueError: If file contains no valid samples.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Flamegraph file not found: {filepath}")

        lines = self._read_folded_lines(filepath)

        if not lines:
            raise ValueError(f"Flamegraph file contains no valid samples: {filepath}")

        total_samples = sum(count for _, count in lines)

        self_samples: dict[str, int] = {}
        cumulative_samples: dict[str, int] = {}
        call_paths: dict[str, list[str]] = {}

        for frames, count in lines:
            leaf = frames[-1]
            self_samples[leaf] = self_samples.get(leaf, 0) + count
            for frame in frames:
                cumulative_samples[frame] = cumulative_samples.get(frame, 0) + count
            if leaf not in call_paths or len(frames) > len(call_paths[leaf]):
                call_paths[leaf] = frames

        hotspots: list[HotspotFunction] = []
        for func, samples in self_samples.items():
            self_pct = (samples / total_samples) * 100.0
            cum_pct = (cumulative_samples.get(func, 0) / total_samples) * 100.0
            source, library = self.classifier.classify(func)
            hotspots.append(
                HotspotFunction(
                    function=func,
                    library=library,
                    source=source,
                    self_pct=self_pct,
                    cumulative_pct=cum_pct,
                    call_path=call_paths.get(func, []),
                )
            )

        hotspots.sort(key=lambda h: h.self_pct, reverse=True)
        return hotspots

    def parse_stacks(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Return all (frames, count) stacks from a folded flamegraph file.

        Unlike parse_folded (which aggregates by leaf name), this preserves
        per-path counts so CallTreeBuilder can compute per-node self-time at
        exact positions in the call tree.
        """
        return self._read_folded_lines(filepath)

    def _read_folded_lines(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Read and parse folded format lines."""
        lines: list[tuple[list[str], int]] = []
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit(" ", 1)
                if len(parts) != 2:
                    continue
                stack_str, count_str = parts
                try:
                    count = int(count_str)
                except ValueError:
                    continue
                frames = stack_str.split(";")
                if not frames or any(f == "" for f in frames):
                    continue
                lines.append((frames, count))
        return lines
