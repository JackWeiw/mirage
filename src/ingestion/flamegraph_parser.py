"""Parse flamegraph data (folded text or flamegraph SVG) into structured hotspots."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from ingestion.classifier import FunctionClassifier
from observability.logging import get_logger
from profile.profile_schema import HotspotFunction

if TYPE_CHECKING:
    import pathlib

# Tolerance (in SVG user units) for x-containment checks; absorbs the sub-pixel
# rounding flamegraph.pl applies to rect coordinates.
_SVG_EPS = 0.5

# SVG <title> text produced by flamegraph.pl: "<function> (<count> <unit>, <pct>%)".
# e.g. "main (1000 samples, 100%)". The function name is greedy so names that
# themselves contain " (...)" are preserved; the count is anchored on the
# trailing " (<int> <unit>, <float>%)" suffix. Unit word is flexible ("samples",
# "ns", "G_cycles", ...) since flamegraph.pl varies it with --count mode.
_SVG_TITLE_RE = re.compile(r"^(?P<func>.*) \((?P<count>\d+) \w+, [\d.]+%\)$")

logger = get_logger("flamegraph_parser")


class FlamegraphParser:
    """Parser for flamegraph data files.

    Accepts two input formats, dispatched by file suffix:

    - Folded text (.txt): one stack per line, "frame;frame;... count".
    - flamegraph.pl SVG (.svg): the call tree is reconstructed from the spatial
      layout (rect x/y/width encodes stack depth and sample proportion). Only
      <g class="func_g"> groups are treated as frames; the banner / defs /
      background elements flamegraph.pl emits are ignored.

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
        lines = self._to_stacks(filepath)

        if not lines:
            raise ValueError(f"Flamegraph file contains no valid samples: {filepath}")

        total_samples = sum(count for _, count in lines)

        if total_samples <= 0:
            # Lines exist but every count is zero (or sums to non-positive) —
            # the downstream self_pct/cum_pct divisions would divide by zero.
            raise ValueError(f"Flamegraph file has zero total samples: {filepath}")

        self_samples: dict[str, int] = {}
        cumulative_samples: dict[str, int] = {}
        call_paths: dict[str, list[str]] = {}
        call_path_counts: dict[str, int] = {}

        for frames, count in lines:
            leaf = frames[-1]
            self_samples[leaf] = self_samples.get(leaf, 0) + count
            for frame in frames:
                cumulative_samples[frame] = cumulative_samples.get(frame, 0) + count
            # Keep the most-sampled path as the representative call_path: the
            # path carrying the most samples for a leaf is its dominant context,
            # more representative than the (rare) deepest stack containing it.
            if leaf not in call_paths or count > call_path_counts[leaf]:
                call_paths[leaf] = frames
                call_path_counts[leaf] = count

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

        Raises:
            FileNotFoundError: If filepath doesn't exist.
        """
        return self._to_stacks(filepath)

    def _to_stacks(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Dispatch parsing by suffix; raise FileNotFoundError if the path is missing."""
        if not filepath.exists():
            raise FileNotFoundError(f"Flamegraph file not found: {filepath}")
        if filepath.suffix.lower() == ".svg":
            return self._svg_to_stacks(filepath)
        return self._read_folded_lines(filepath)

    def _read_folded_lines(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
        """Read and parse folded-format lines."""
        lines: list[tuple[list[str], int]] = []
        skipped = 0
        # Explicit UTF-8 with replace so a stray non-UTF-8 byte (common in raw
        # perf output) doesn't crash with UnicodeDecodeError under a strict
        # locale; the malformed line is then skipped by the parser below.
        with open(filepath, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    # Blank lines are benign, not malformed.
                    continue
                parts = line.rsplit(" ", 1)
                if len(parts) != 2:
                    skipped += 1
                    continue
                stack_str, count_str = parts
                try:
                    count = int(count_str)
                except ValueError:
                    skipped += 1
                    continue
                if count < 0:
                    # A negative count corrupts aggregation; skip the malformed line.
                    skipped += 1
                    continue
                frames = stack_str.split(";")
                if not frames or any(f == "" for f in frames):
                    skipped += 1
                    continue
                lines.append((frames, count))
        if skipped:
            logger.warning("skipped_malformed_folded_lines", filepath=str(filepath), count=skipped)
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
            rects = _parse_svg_rects(data)
        except ElementTree.ParseError:
            return []
        if not rects:
            return []
        _link_parents(rects)
        _resolve_counts(rects)
        return _emit_stacks(rects)


def _parse_svg_rects(data: str) -> list[_SvgRect]:
    """Extract one _SvgRect per flamegraph <g class="func_g"> group."""
    root = ElementTree.fromstring(data)
    rects: list[_SvgRect] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "g":
            continue
        if "func_g" not in (element.get("class") or "").split():
            continue
        title_el = _find_local(element, "title")
        rect_el = _find_local(element, "rect")
        if title_el is None or rect_el is None:
            continue
        func, title_count = _parse_title(title_el.text or "")
        x = _attr_float(rect_el, "x")
        y = _attr_float(rect_el, "y")
        width = _attr_float(rect_el, "width")
        if x is None or y is None or width is None or width <= 0 or not func:
            continue
        height = _attr_float(rect_el, "height")
        rects.append(
            _SvgRect(
                func=func,
                x=x,
                y=y,
                width=width,
                height=height if height is not None else 0.0,
                title_count=title_count,
            )
        )
    return rects


def _link_parents(rects: list[_SvgRect]) -> None:
    """Link each rect to its parent: closest rect below with a containing x-range.

    flamegraph.pl stacks each depth on its own y row, so a rect's parent sits in
    the row directly below it. Rows are scanned closest-first, so the first
    containing rect is the closest-below parent (matching the naive O(n^2)
    result while staying ~linear across the contiguous-row layout flamegraph.pl
    produces). A fallback scans further-below rows in case of a depth gap.
    """
    rows: dict[float, list[_SvgRect]] = {}
    for rect in rects:
        rows.setdefault(rect.y, []).append(rect)
    ys = sorted(rows, reverse=True)  # largest y (root row) first
    for i in range(len(ys)):
        for rect in rows[ys[i]]:
            for j in range(i - 1, -1, -1):  # rows below, closest first
                for cand in rows[ys[j]]:
                    if _x_contains(cand, rect):
                        rect.parent = cand
                        cand.children.append(rect)
                        break
                if rect.parent is not None:
                    break


def _resolve_counts(rects: list[_SvgRect]) -> None:
    """Resolve inclusive counts: title count if present, else width-derived."""
    root = max(rects, key=lambda r: r.width)
    root_count = root.title_count if root.title_count is not None else round(root.width)
    for rect in rects:
        if rect.title_count is not None:
            rect.inclusive = rect.title_count
        elif root.width > 0:
            rect.inclusive = round(rect.width / root.width * root_count)
        else:
            rect.inclusive = round(rect.width)


def _emit_stacks(rects: list[_SvgRect]) -> list[tuple[list[str], int]]:
    """Emit one folded stack per rect whose self count (inclusive - children) > 0.

    Children's inclusive counts are capped at the parent's inclusive so rounding
    in the width-derived fallback can never push self count negative (a no-op
    for the exact title-count path, where children always sum to <= the parent).
    """
    stacks: list[tuple[list[str], int]] = []
    for rect in rects:
        children_inclusive = min(sum(c.inclusive for c in rect.children), rect.inclusive)
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


def _x_contains(parent: _SvgRect, child: _SvgRect) -> bool:
    """True if parent's x-range contains child's x-range (within sub-pixel tolerance)."""
    return (
        parent.x <= child.x + _SVG_EPS
        and child.x + child.width <= parent.x + parent.width + _SVG_EPS
    )


def _parse_title(title: str) -> tuple[str, int | None]:
    """Split a flamegraph <title> into (function_name, inclusive_count|None)."""
    match = _SVG_TITLE_RE.match(title)
    if match is None:
        return title.strip() or "", None
    return match.group("func"), int(match.group("count"))


def _find_local(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    """Find the first direct child whose local (namespace-stripped) tag matches."""
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == name:
            return child
    return None


def _attr_float(element: ElementTree.Element, name: str) -> float | None:
    """Read an SVG attribute as float; None if missing or unparseable."""
    raw = element.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
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
