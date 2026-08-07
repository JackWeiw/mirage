"""Build Runner — compile generated C++ workload project."""

import pathlib
import subprocess

from models.results import BuildResult
from observability.logging import get_logger

logger = get_logger("build_runner")


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
            BuildResult with success status, output, error, and binary path if successful.
        """
        build_dir = project_dir / self.build_dir_suffix
        build_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: cmake configure
        cmake_cmd = [self.cmake_path, "-S", str(project_dir), "-B", str(build_dir)]
        logger.info("running_cmake", cmd=cmake_cmd)

        try:
            cmake_result = subprocess.run(cmake_cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return BuildResult(success=False, stderr="cmake timed out after 120s")
        except FileNotFoundError:
            return BuildResult(success=False, stderr=f"cmake not found at {self.cmake_path}")

        if cmake_result.returncode != 0:
            return BuildResult(
                success=False, stdout=cmake_result.stdout, stderr=cmake_result.stderr
            )

        # Step 2: make compile
        make_cmd = [self.make_path, "-C", str(build_dir)]
        logger.info("running_make", cmd=make_cmd)

        try:
            make_result = subprocess.run(make_cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return BuildResult(success=False, stderr="make timed out after 300s")
        except FileNotFoundError:
            return BuildResult(success=False, stderr=f"make not found at {self.make_path}")

        if make_result.returncode != 0:
            return BuildResult(success=False, stdout=make_result.stdout, stderr=make_result.stderr)

        # Step 3: locate binary
        binary: str | None = None
        for candidate in build_dir.rglob("*"):
            if (
                candidate.is_file()
                and not candidate.name.startswith(".")
                and candidate.suffix not in [".o", ".cmake", ".txt", ".json", ".make", ".h", ".cpp"]
            ):
                binary = str(candidate)
                break

        return BuildResult(
            success=True,
            stdout=make_result.stdout,
            stderr=make_result.stderr,
            binary_path=binary,
        )
