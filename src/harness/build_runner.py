"""Build Runner — compile generated C++ workload project."""

import pathlib
import re
import subprocess
import time

from models.results import BuildResult
from observability.logging import get_logger

logger = get_logger("build_runner")

# Files rglob may surface in a CMake build tree that are NOT the workload
# binary. The Makefile has no suffix (""), so a suffix blacklist alone lets it
# through -- it is also excluded by name in _locate_binary's fallback.
_NON_BINARY_SUFFIXES = {".o", ".cmake", ".txt", ".json", ".make", ".h", ".cpp"}
_CMAKE_TARGET_RE = re.compile(r"add_executable\(\s*([A-Za-z_][A-Za-z0-9_]*)")


def _read_cmake_target(project_dir: pathlib.Path) -> str | None:
    """Read the CMake target name from project_dir/CMakeLists.txt.

    The codegen template names the executable after project_name via
    add_executable(<name> ...). Returns None if CMakeLists.txt is missing or no
    target is declared.
    """
    cmake_file = project_dir / "CMakeLists.txt"
    if not cmake_file.is_file():
        return None
    match = _CMAKE_TARGET_RE.search(cmake_file.read_text(errors="replace"))
    return match.group(1) if match else None


def _locate_binary(build_dir: pathlib.Path, project_dir: pathlib.Path) -> str | None:
    """Locate the compiled workload executable in build_dir.

    Primary path: build_dir/<cmake_target> (target read from CMakeLists.txt).
    The Makefile is never named <target>, so this resolves the binary directly
    without globbing and without relying on execute bits (keeps it working on
    Windows dev machines where os.access X_OK is unreliable -- the bug that
    caused BuildRunner to return the Makefile as binary_path on ARM).

    Fallback: first regular file whose suffix is not a known non-binary type,
    whose name is not "Makefile", and which is not inside a CMakeFiles/ dir.
    Returns None if nothing matches.
    """
    target = _read_cmake_target(project_dir)
    if target:
        candidate = build_dir / target
        if candidate.is_file():
            return str(candidate)
    for candidate in build_dir.rglob("*"):
        if (
            candidate.is_file()
            and not candidate.name.startswith(".")
            and candidate.suffix not in _NON_BINARY_SUFFIXES
            and candidate.name != "Makefile"
            and "CMakeFiles" not in candidate.parts
        ):
            return str(candidate)
    return None


class BuildRunner:
    """Compile a generated C++ workload project using cmake + make.

    Args:
        cmake_path: Path to cmake executable.
        make_path: Path to make executable.
        build_dir_suffix: Subdirectory name for build output.
    """

    def __init__(
        self, cmake_path: str = "cmake", make_path: str = "make", build_dir_suffix: str = "build"
    ) -> None:
        self.cmake_path = cmake_path
        self.make_path = make_path
        self.build_dir_suffix = build_dir_suffix

    def build(self, project_dir: pathlib.Path) -> BuildResult:
        """Build a C++ project in project_dir.

        Steps:
        1. Create build subdirectory
        2. Run cmake to configure
        3. Run make to compile
        4. Locate the binary

        Args:
            project_dir: Path to the project directory containing CMakeLists.txt.

        Returns:
            BuildResult with success status, output, error, and binary path if
            successful. duration_seconds is the wall-clock build time (cmake +
            make), populated on every return path so it is honest telemetry
            rather than a dead 0.0 default.
        """
        start = time.monotonic()
        build_dir = project_dir / self.build_dir_suffix
        build_dir.mkdir(parents=True, exist_ok=True)

        cmake_cmd = [self.cmake_path, "-S", str(project_dir), "-B", str(build_dir)]
        logger.info("running_cmake", cmd=cmake_cmd)

        try:
            cmake_result = subprocess.run(cmake_cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                stderr="cmake timed out after 120s",
                duration_seconds=time.monotonic() - start,
            )
        except FileNotFoundError:
            return BuildResult(
                success=False,
                stderr=f"cmake not found at {self.cmake_path}",
                duration_seconds=time.monotonic() - start,
            )

        if cmake_result.returncode != 0:
            return BuildResult(
                success=False,
                stdout=cmake_result.stdout,
                stderr=cmake_result.stderr,
                duration_seconds=time.monotonic() - start,
            )

        make_cmd = [self.make_path, "-C", str(build_dir)]
        logger.info("running_make", cmd=make_cmd)

        try:
            make_result = subprocess.run(make_cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                stderr="make timed out after 300s",
                duration_seconds=time.monotonic() - start,
            )
        except FileNotFoundError:
            return BuildResult(
                success=False,
                stderr=f"make not found at {self.make_path}",
                duration_seconds=time.monotonic() - start,
            )

        if make_result.returncode != 0:
            return BuildResult(
                success=False,
                stdout=make_result.stdout,
                stderr=make_result.stderr,
                duration_seconds=time.monotonic() - start,
            )

        binary = _locate_binary(build_dir, project_dir)

        return BuildResult(
            success=True,
            stdout=make_result.stdout,
            stderr=make_result.stderr,
            binary_path=binary,
            duration_seconds=time.monotonic() - start,
        )
