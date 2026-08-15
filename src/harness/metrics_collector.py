"""Metrics Collector — collect and parse Topdown, flamegraph, and memory data."""

import pathlib
import re
import subprocess
from collections import Counter

from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.topdown_parser import TopdownParser
from models.results import CollectionResult
from observability.logging import get_logger
from profile.profile_schema import HotspotFunction, Profile

logger = get_logger("metrics_collector")


class MetricsCollector:
    """Collect performance metrics from a workload run on the target machine.

    Args:
        devkit_cmd: Path to devkit command. None means devkit is not configured.
        perf_cmd: Path to perf command.
    """

    def __init__(self, devkit_cmd: str | None = None, perf_cmd: str = "perf") -> None:
        self.devkit_cmd = devkit_cmd
        self.perf_cmd = perf_cmd
        self.topdown_parser = TopdownParser()
        self.flamegraph_parser = FlamegraphParser()

    def collect_topdown(
        self,
        output_path: pathlib.Path,
        duration: int = 60,
        interval: int = 3,
        pid: int | None = None,
    ) -> CollectionResult:
        """Collect Topdown data using devkit.

        Runs `devkit tuner top-down -d <dur> -i <int> [-p <pid>]` and writes the
        TEXT report (emitted on stdout) to output_path. pid attributes the
        topdown to a workload process; None collects system-wide. devkit forbids
        -p together with --cpu, so core scoping is NOT done here -- pin the
        workload with taskset in the runner instead.
        """
        if self.devkit_cmd is None:
            logger.warning("devkit_not_configured")
            return CollectionResult(success=False, error="devkit_cmd not configured")

        cmd = [
            self.devkit_cmd,
            "tuner",
            "top-down",
            "-d",
            str(duration),
            "-i",
            str(interval),
        ]
        if pid is not None:
            cmd += ["-p", str(pid)]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=duration + 30, check=False
            )
            if result.returncode != 0:
                return CollectionResult(success=False, error=result.stderr)
            # devkit emits the report on stdout (there is no --output flag);
            # capture it and persist it so parse_topdown_file can read it back.
            output_path.write_text(result.stdout)
            return CollectionResult(success=True, topdown_path=str(output_path))
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return CollectionResult(success=False, error=str(e))

    def collect_flamegraph(
        self, output_path: pathlib.Path, duration: int = 60, pid: int | None = None
    ) -> CollectionResult:
        """Collect a folded flamegraph via perf record + perf script + stackcollapse.

        Requires perf on the ARM target. Returns failure if perf is unavailable
        (e.g. on a dev machine without perf).
        """
        if self.perf_cmd is None:
            return CollectionResult(success=False, error="perf_cmd not configured")
        try:
            if pid is not None:
                record_cmd = [
                    self.perf_cmd,
                    "record",
                    "-g",
                    "-p",
                    str(pid),
                    "--",
                    "sleep",
                    str(duration),
                ]
            else:
                record_cmd = [
                    self.perf_cmd,
                    "record",
                    "-g",
                    "--",
                    "sleep",
                    str(duration),
                ]
            subprocess.run(
                record_cmd, capture_output=True, text=True, timeout=duration + 30, check=False
            )
            script = subprocess.run(
                [self.perf_cmd, "script"],
                capture_output=True,
                text=True,
                timeout=duration + 30,
            )
            if script.returncode != 0:
                return CollectionResult(success=False, error=script.stderr)
            output_path.write_text(self._stackcollapse(script.stdout))
            return CollectionResult(success=True, flamegraph_path=str(output_path))
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return CollectionResult(success=False, error=str(e))

    # A perf-script sample header, e.g. "swapper 0 [000] 12345.678:  cpu-clock:".
    # Distinguished from call-chain lines by the pid[/tid], optional [cpu], and
    # the colon-terminated timestamp. Such lines must not become frames.
    _SAMPLE_HEADER_RE = re.compile(r"^\S+\s+\d+(?:/\d+)?\s+(?:\[\d+\]\s+)?[\d.]+:\s")

    # A realistic perf-script call-chain entry: "[addr] sym+offset (dso)".
    # The address prefix is optional (some perf --fields modes omit it). We keep
    # only the symbol and discard the address and the (dso) annotation.
    _SYMBOL_LINE_RE = re.compile(r"^(?:\w+\s+)?(?P<sym>\S+)\s+\([^)]+\)\s*$")

    # Trailing "+0x..." offset on a symbol; stripped to match folded-stack
    # convention (stackcollapse-perf.pl strips offsets by default).
    _OFFSET_RE = re.compile(r"\+0x[0-9a-fA-F]+$")

    @staticmethod
    def _extract_frame(line: str) -> str:
        """Return the frame name for a single perf-script call-chain line.

        Lines in the realistic "addr sym+offset (dso)" form contribute only the
        symbol (address and dso dropped, trailing offset stripped). Bare symbol
        lines (no address/dso) are returned verbatim so synthetic/legacy input
        keeps working.
        """
        match = MetricsCollector._SYMBOL_LINE_RE.match(line)
        symbol = match.group("sym") if match else line
        return MetricsCollector._OFFSET_RE.sub("", symbol)

    @staticmethod
    def _stackcollapse(perf_script_output: str) -> str:
        """Convert perf-script output to folded stacks ("frame;frame count").

        Sample header lines (comm pid/tid [cpu] timestamp: event:) are excluded
        from the stack. Call-chain entries contribute only the symbol (address,
        dso, and "+0x..." offset are dropped). Stacks are emitted in file order
        (no reversal of the perf call chain), and identical stacks are merged
        with summed sample counts, matching stackcollapse-perf.pl semantics.
        """
        counts: Counter[str] = Counter()
        stack: list[str] = []

        def _flush() -> None:
            if stack:
                counts[";".join(stack)] += 1
                stack.clear()

        for raw in perf_script_output.splitlines():
            line = raw.strip()
            if not line:
                _flush()
                continue
            if line.startswith("#"):
                continue
            if MetricsCollector._SAMPLE_HEADER_RE.match(line):
                # A header opens a new sample; finalize the previous stack.
                _flush()
                continue
            frame = MetricsCollector._extract_frame(line)
            if frame:
                stack.append(frame)
        _flush()
        return "\n".join(f"{frames} {count}" for frames, count in counts.items())

    def parse_topdown_file(self, filepath: pathlib.Path) -> Profile:
        """Parse a previously collected Topdown file (JSON/CSV/TEXT)."""
        suffix = filepath.suffix.lower()
        if suffix == ".csv":
            return self.topdown_parser.parse_csv(filepath)
        if suffix in (".txt", ".text"):
            return self.topdown_parser.parse_text(filepath)
        return self.topdown_parser.parse_json(filepath)

    def parse_flamegraph_file(self, filepath: pathlib.Path) -> list[HotspotFunction]:
        """Parse a previously collected flamegraph folded file."""
        return self.flamegraph_parser.parse_folded(filepath)
