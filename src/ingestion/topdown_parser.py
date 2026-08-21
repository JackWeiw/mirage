"""Parse Topdown analysis data (devkit JSON/CSV/TEXT) into Profile fields."""

import csv
import json
import pathlib
import re

from profile.profile_schema import (
    MemoryProfile,
    Profile,
    ProfileMetadata,
    TopdownL1,
    TopdownL2,
    TopdownL2Backend,
    TopdownL2BadSpec,
    TopdownL2Frontend,
    TopdownL2Retiring,
    TopdownNode,
    TopdownSummary,
)

# devkit `tuner top-down` emits a human-readable TEXT table, e.g.:
#   Backend Bound                    72.01    --
#   Frontend Bound                   17.59    --
#   Bad Speculation                   3.01    --
#   Retiring                           7.38    --
# Pull the four L1 category percentages (case-insensitive label, first float).
_TOPDOWN_L1_RE = re.compile(
    r"^\s*(backend bound|frontend bound|bad speculation|retiring)\s+([\d.]+)",
    re.IGNORECASE | re.MULTILINE,
)

# A new interval block begins at each "TOP-DOWN Summary Report" banner. The L1
# mean above already handles multi-block values by label; the tree and summary
# counters are positional within a block, so they need real block splitting.
_BLOCK_HEADER_RE = re.compile(r"TOP-DOWN Summary Report", re.IGNORECASE)

# Per-block raw counters (thousands-separated). A block missing any of the
# three yields no summary record (best-effort, like the tree below).
_CYCLES_RE = re.compile(r"^\s*Cycles\s+([\d,]+)\s*$", re.MULTILINE)
_INSTRUCTIONS_RE = re.compile(r"^\s*Instructions\s+([\d,]+)\s*$", re.MULTILINE)
_IPC_RE = re.compile(r"^\s*IPC\s+([\d.]+)\s*$", re.MULTILINE)


def _split_blocks(text: str) -> list[str]:
    """Split a devkit text report into per-interval block bodies."""
    return [b for b in _BLOCK_HEADER_RE.split(text) if b.strip()]


def _parse_summary_block(block: str) -> TopdownSummary | None:
    cyc = _CYCLES_RE.search(block)
    ins = _INSTRUCTIONS_RE.search(block)
    ipc = _IPC_RE.search(block)
    if not (cyc and ins and ipc):
        return None
    return TopdownSummary(
        cycles=int(cyc.group(1).replace(",", "")),
        instructions=int(ins.group(1).replace(",", "")),
        ipc=float(ipc.group(1)),
    )


def _mean_summaries(items: list[TopdownSummary]) -> TopdownSummary | None:
    if not items:
        return None
    n = len(items)
    return TopdownSummary(
        cycles=round(sum(i.cycles for i in items) / n),
        instructions=round(sum(i.instructions for i in items) / n),
        ipc=sum(i.ipc for i in items) / n,
    )


# The 4-char tree-prefix groups devkit uses for indentation/continuation. A
# node's depth = (count of these groups) // 4; L1 category lines (no connector)
# are depth-0 roots.
_TREE_GROUPS = ("│   ", "    ", "├── ", "└── ")
_TREE_VALUE_RE = re.compile(r"^\d+\.\d+$")


def _parse_tree_line(line: str) -> tuple[int, TopdownNode] | None:
    """Parse one devkit tree line into (depth, TopdownNode), or None if not a
    tree node (dividers, header rows, raw-counter rows, elapsed-time line).

    A line is a tree node iff, after the 4-char prefix, its tokens are at least
    [name..., value, event] and the second-to-last token is a float (e.g.
    "3.01"). The trailing token is the "Preferred Sampling Event" column
    ("--" -> None). Old 4-column reports without the event column therefore do
    not match (value would be the last token, event missing) -> tree is None.
    """
    # Strip devkit's 2-space table margin; the remaining prefix is a run of
    # 4-char tree groups (vertical/continuation/connector).
    rest = line[2:] if line.startswith("  ") else line
    prefix_len = 0
    while rest[prefix_len : prefix_len + 4] in _TREE_GROUPS:
        prefix_len += 4
    depth = prefix_len // 4
    tokens = rest[prefix_len:].split()
    if len(tokens) < 3:
        return None
    value_str = tokens[-2]
    if not _TREE_VALUE_RE.match(value_str):
        return None
    event_str = tokens[-1]
    name = " ".join(tokens[:-2])
    if not name:
        return None
    return depth, TopdownNode(
        name=name,
        value=float(value_str),
        children=[],
        sampling_event=None if event_str == "--" else event_str,
    )


def _parse_tree_block(block: str) -> list[TopdownNode]:
    """Parse one interval block into a forest of L1-root TopdownNodes."""
    roots: list[TopdownNode] = []
    stack: list[tuple[int, TopdownNode]] = []
    for line in block.splitlines():
        parsed = _parse_tree_line(line)
        if parsed is None:
            continue
        depth, node = parsed
        # Pop until the stack top is strictly shallower than this node — i.e.
        # the parent. `>=` (not `>`) pops same-depth siblings so they attach
        # to the shared parent instead of nesting under each other.
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack:
            roots.append(node)
        else:
            stack[-1][1].children.append(node)
        stack.append((depth, node))
    return roots


def _merge_forests(forests: list[list[TopdownNode]]) -> list[TopdownNode]:
    """Mean node values across interval blocks, grouping siblings by name.

    Recursion scopes each merge to one parent's child list, so grouping by
    local node name (not a global ancestor path) is unambiguous.
    `sampling_event` is constant per (parent, name); take the first non-None.
    A block missing a subtree contributes nothing (the `if n.children` filter),
    so the mean falls back to the blocks where the path is present.
    """
    if not forests:
        return []
    by_name: dict[str, list[TopdownNode]] = {}
    order: list[str] = []
    for forest in forests:
        for node in forest:
            if node.name not in by_name:
                by_name[node.name] = []
                order.append(node.name)
            by_name[node.name].append(node)
    merged: list[TopdownNode] = []
    for name in order:
        nodes = by_name[name]
        value = sum(n.value for n in nodes) / len(nodes)
        event = next((n.sampling_event for n in nodes if n.sampling_event is not None), None)
        child_forests = [n.children for n in nodes if n.children]
        children = _merge_forests(child_forests) if child_forests else []
        merged.append(TopdownNode(name=name, value=value, children=children, sampling_event=event))
    return merged


class TopdownParser:
    """Parser for ARM64 Topdown analysis data from devkit output."""

    def parse_json(self, filepath: pathlib.Path) -> Profile:
        """Parse devkit JSON output.

        Args:
            filepath: Path to devkit JSON file containing topdown and memory data.

        Returns:
            Profile with topdown and memory fields populated.

        Raises:
            FileNotFoundError: If filepath doesn't exist.
            pydantic.ValidationError: If JSON content doesn't match Profile schema.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Topdown file not found: {filepath}")

        with open(filepath) as f:
            data = json.load(f)

        topdown_l1 = TopdownL1(**data.get("topdown_l1", {}))

        l2_raw = data.get("topdown_l2", {})
        topdown_l2 = TopdownL2(
            frontend_bound=(
                TopdownL2Frontend(**l2_raw.get("frontend_bound", {}))
                if "frontend_bound" in l2_raw
                else None
            ),
            backend_bound=(
                TopdownL2Backend(**l2_raw.get("backend_bound", {}))
                if "backend_bound" in l2_raw
                else None
            ),
            bad_speculation=(
                TopdownL2BadSpec(**l2_raw.get("bad_speculation", {}))
                if "bad_speculation" in l2_raw
                else None
            ),
            retiring=(
                TopdownL2Retiring(**l2_raw.get("retiring", {})) if "retiring" in l2_raw else None
            ),
        )

        memory = MemoryProfile(**data.get("memory", {}))

        return Profile(
            metadata=ProfileMetadata(customer="unknown", date="unknown"),
            topdown=topdown_l1,
            topdown_l2=topdown_l2,
            memory=memory,
        )

    def parse_csv(self, filepath: pathlib.Path) -> Profile:
        """Parse devkit CSV output.

        CSV format: each row is "metric,value" where metric can be dotted like
        "frontend_bound.fetch_latency".

        Args:
            filepath: Path to devkit CSV file.

        Returns:
            Profile with topdown and memory fields populated.

        Raises:
            FileNotFoundError: If filepath doesn't exist.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Topdown CSV file not found: {filepath}")

        metrics: dict[str, float] = {}
        with open(filepath) as f:
            reader = csv.DictReader(f)
            for row in reader:
                metric = row["metric"].strip()
                value = float(row["value"].strip())
                metrics[metric] = value

        topdown_l1 = TopdownL1(
            frontend_bound=metrics.get("frontend_bound", 0.0),
            backend_bound=metrics.get("backend_bound", 0.0),
            bad_speculation=metrics.get("bad_speculation", 0.0),
            retiring=metrics.get("retiring", 0.0),
        )

        fb_raw = {
            k.split(".")[-1]: v for k, v in metrics.items() if k.startswith("frontend_bound.")
        }
        bb_raw = {k.split(".")[-1]: v for k, v in metrics.items() if k.startswith("backend_bound.")}
        bs_raw = {
            k.split(".")[-1]: v for k, v in metrics.items() if k.startswith("bad_speculation.")
        }
        rt_raw = {k.split(".")[-1]: v for k, v in metrics.items() if k.startswith("retiring.")}

        topdown_l2 = TopdownL2(
            frontend_bound=TopdownL2Frontend(**fb_raw) if fb_raw else None,
            backend_bound=TopdownL2Backend(**bb_raw) if bb_raw else None,
            bad_speculation=TopdownL2BadSpec(**bs_raw) if bs_raw else None,
            retiring=TopdownL2Retiring(**rt_raw) if rt_raw else None,
        )

        mem_raw = {k.split(".")[-1]: v for k, v in metrics.items() if k.startswith("memory.")}
        memory = MemoryProfile(**mem_raw) if mem_raw else None

        return Profile(
            metadata=ProfileMetadata(customer="unknown", date="unknown"),
            topdown=topdown_l1,
            topdown_l2=topdown_l2,
            memory=memory,
        )

    def parse_text(self, filepath: pathlib.Path) -> Profile:
        """Parse devkit `tuner top-down` TEXT report.

        devkit emits a human-readable table (not JSON/CSV), e.g.::

            Backend Bound                    72.01    --
            Frontend Bound                   17.59    --
            Bad Speculation                   3.01    --
            Retiring                           7.38    --

        Values are PERCENTAGES (devkit native). L2 sub-metrics and memory
        bandwidth are not present in the text report, so topdown_l2 and memory
        are None. NOTE: parse_json/parse_csv fixtures historically use FRACTIONS
        (0.40) while this returns percentages (72.01); that units inconsistency
        is tracked separately and not normalized here.

        Raises:
            FileNotFoundError: If filepath doesn't exist.
            ValueError: If no L1 lines are found (so a format change surfaces
                instead of silently returning zeros).
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Topdown text file not found: {filepath}")
        text = filepath.read_text(encoding="utf-8", errors="replace")
        matches = _TOPDOWN_L1_RE.findall(text)
        if not matches:
            raise ValueError(
                f"No Topdown L1 lines found in {filepath}; first 200 chars: {text[:200]!r}"
            )
        # The devkit emits one L1 block per -i interval, so a -d 20 -i 3 capture
        # carries ~6 blocks. Mean across blocks is the steady-state topdown
        # (last-wins grabbed an arbitrary single interval; with one block the mean
        # is that block, so single-report behavior is unchanged).
        by_label: dict[str, list[float]] = {}
        for label, value in matches:
            by_label.setdefault(label.lower(), []).append(float(value))
        found = {label: sum(vals) / len(vals) for label, vals in by_label.items()}
        topdown_l1 = TopdownL1(
            frontend_bound=found.get("frontend bound", 0.0),
            backend_bound=found.get("backend bound", 0.0),
            bad_speculation=found.get("bad speculation", 0.0),
            retiring=found.get("retiring", 0.0),
        )
        blocks = _split_blocks(text)
        summaries = [s for s in (_parse_summary_block(b) for b in blocks) if s]
        summary = _mean_summaries(summaries)
        forests = [f for f in (_parse_tree_block(b) for b in blocks) if f]
        topdown_tree = _merge_forests(forests) if forests else None
        return Profile(
            metadata=ProfileMetadata(customer="devkit", date="unknown"),
            topdown=topdown_l1,
            topdown_l2=None,
            memory=None,
            summary=summary,
            topdown_tree=topdown_tree,
        )
