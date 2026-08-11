"""Parse flamegraph data (folded text or flamegraph SVG) into structured hotspots."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from ingestion.classifier import FunctionClassifier
from profile.profile_schema import HotspotFunction

if TYPE_CHECKING:
    import pathlib

# Tolerance (in SVG user units) for x-containment checks; absorbs the sub-pixel
# rounding flamegraph.pl applies to rect coordinates.
_SVG_EPS = 0.5

# SVG <title> text produced by flamegraph.pl: "<function> (<count> <unit>, <pct>%)""
# e.g. "main (1000 samples, 100%)". The function name is greedy so names that
# themselves contain " (...)" are preserved; the count is anchored on the
# trailing " (<int> <unit>, <float>%)" suffix. Unit word is flexible ("samples",
# "ns", "G_cycles", ...) since flamegraph.pl varies it with --count mode.
_SVG_TITLE_RE = re.compile(r"^(?P<func>.*) \((?P<count>\d+) \w+, [\d.]+%\)$")


class FlamegraphParser:
    """Parser for flamegraph data files.

    Accepts two input formats, dispatched by file suffix:

    - Folded text (.txt): one stack per line, "frame;frame;... count".
    - flamegraph.pl SVG (.svg): the call tree is reconstructed from the spatial
      layout (rect x/y/width encodes stack depth and sample proportion).

    Args:
        classifier: FunctionClassifier instance. If None, creates default from YAML config.
    """

    def __init__(self, classifier: FunctionClassifier | None = None) -> None:
        self.classifier = classifier or FunctionClassifier()

    def parse_folded(self, filepath: pathlib.Path) -> list[HotspotFunction]:
        """Parse a flamegraph file (folded .txt or .svg) into hotspots.

        Args:
            filepath: Path to a folded flamegraph text file or a flamegraph SVG.

        Returns:
            List of HotspotFunction sorted by self_pct descending.

        Raises:
            FileNotFoundError: If filepath doesn't exist.
            ValueError: If file contains no valid samples.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Flamegraph file not found: {filepath}")

        lines = self._to_stacks(filepath)

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
        """Return all (frames, count) stacks from a flamegraph file.

        Unlike parse_folded (which aggregates by leaf name), this preserves
        per-path counts so CallTreeBuilder can compute per-node self-time at
        exact positions in the call tree. Supports both folded .txt and .svg.
        """
        return self._to_stacks(filepath)

    def _to_stacks(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Dispatch parsing by suffix: SVG -> spatial reconstruction, else folded."""
        if filepath.suffix.lower() == ".svg":
            return self._svg_to_stacks(filepath)
        return self._read_folded_lines(filepath)

    def _read_folded_lines(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Read and parse folded-format lines."""
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

    # -- flamegraph.pl SVG reconstruction ------------------------------------
    def _svg_to_stacks(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Reconstruct folded stacks from a flamegraph.pl SVG.

        Each <g class="func_g"> holds a <title> ("<func> (<count> <unit>, <pct>%)")
        and a <rect> with x/y/width/height. A rect's parent is the closest rect
        directly below it (larger y) whose x-range contains this rect's x-range.
        A rect's inclusive sample count comes from its title, or is derived
        proportionally from its width relative to the root. Its self count is
        inclusive minus the sum of its children's inclusive counts; each self
        count > 0 becomes one folded stack line (root -> that frame).

        Malformed XML yields an empty result so callers raise ValueError.
        """
        data = filepath.read_text(encoding="utf-8", errors="replace")
        if not data.strip():
            return []
        try:
            rects = self._parse_svg_rects(data)
        except ElementTree.ParseError:
            return []
        if not rects:
            return []
        self._link_parents(rects)
        self._resolve_counts(rects)
        return self._emit_stacks(rects)

    @staticmethod
    def _parse_svg_rects(data: str) -> list[_SvgRect]:
        """Extract one _SvgRect per flamegraph <g> group (title + rect)."""
        root = ElementTree.fromstring(data)
        rects: list[_SvgRect] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "g":
                continue
            title_el = FlamegraphParser._find_local(element, "title")
            rect_el = FlamegraphParser._find_local(element, "rect")
            if title_el is None or rect_el is None:
                continue
            func, title_count = FlamegraphParser._parse_title(title_el.text or "")
            width = _to_float(rect_el.get("width"))
            if width <= 0 or not func:
                continue
            rects.append(
                _SvgRect(
                    func=func,
                    x=_to_float(rect_el.get("x")),
                    y=_to_float(rect_el.get("y")),
                    width=width,
                    height=_to_float(rect_el.get("height")),
                    title_count=title_count,
                )
            )
        return rects

    @staticmethod
    def _link_parents(rects: list[_SvgRect]) -> None:
        """Link each rect to its parent: closest rect below with a containing x-range."""
        for rect in rects:
            r_min = rect.x
            r_max = rect.x + rect.width
            parent: _SvgRect | None = None
            for cand in rects:
                if cand is rect:
                    continue
                c_min = cand.x
                c_max = cand.x + cand.width
                below = cand.y > rect.y
                contains = c_min <= r_min + _SVG_EPS and r_max <= c_max + _SVG_EPS
                if below and contains and (parent is None or cand.y < parent.y):
                    parent = cand
            rect.parent = parent
            if parent is not None:
                parent.children.append(rect)

    @staticmethod
    def _resolve_counts(rects: list[_SvgRect]) -> None:
        """Resolve inclusive counts: title count if present, else width-derived."""
        root = max(rects, key=lambda r: r.width)
        root_count = root.title_count if root.title_count is not None else int(round(root.width))
        for rect in rects:
            if rect.title_count is not None:
                rect.inclusive = rect.title_count
            elif root.width > 0:
                rect.inclusive = int(round(rect.width / root.width * root_count))
            else:
                rect.inclusive = int(round(rect.width))

    @staticmethod
    def _emit_stacks(rects: list[_SvgRect]) -> list[tuple[list[str], int]]:
        """Emit one folded stack per rect whose self count (inclusive - children) > 0."""
        stacks: list[tuple[list[str], int]] = []
        for rect in rects:
            children_inclusive = sum(c.inclusive for c in rect.children)
            self_count = rect.inclusive - children_inclusive
            if self_count <= 0:
                continue
            path: list[str] = []
            node: _SvgRect | None = rect
            while node is not None:
                path.append(node.func)
                node = node.parent
            path.reverse()
            stacks.append((path, self_count))
        return stacks

    @staticmethod
    def _parse_title(title: str) -> tuple[str, int | None]:
        """Split a flamegraph <title> into (function_name, inclusive_count|None)."""
        match = _SVG_TITLE_RE.match(title)
        if match is None:
            return title.strip() or "", None
        return match.group("func"), int(match.group("count"))

    @staticmethod
    def _find_local(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
        """Find the first direct child whose local (namespace-stripped) tag matches."""
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == name:
                return child
        return None


@dataclass
class _SvgRect:
    """One flamegraph frame rect and its reconstructed tree links."""

    func: str
    x: float
    y: float
    width: float
    height: float
    title_count: int | None
    inclusive: int = 0
    parent: _SvgRect | None = None
    children: list[_SvgRect] = field(default_factory=list)


def _to_float(value: str | None) -> float:
    """Parse a nullable SVG attribute as float, tolerating missing/garbage values."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0
