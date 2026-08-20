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
        text = filepath.read_text(errors="replace")
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
        return Profile(
            metadata=ProfileMetadata(customer="devkit", date="unknown"),
            topdown=topdown_l1,
            topdown_l2=None,
            memory=None,
        )
