"""compute_bound reference demo: CMake target + flags, marker, matmul kernel."""

import pathlib

_REF = (
    pathlib.Path(__file__).resolve().parents[2]
    / "examples"
    / "scenarios"
    / "compute_bound"
    / "reference"
)


def test_cmake_declares_target_and_flags() -> None:
    text = (_REF / "CMakeLists.txt").read_text()
    assert "add_executable(compute_bound_ref main.cpp matmul.cpp)" in text
    assert "-O2" in text and "-march=armv8.2-a" in text
    assert "third_party/taskflow" in text


def test_main_prints_measurement_marker() -> None:
    assert "__MEASUREMENT_WINDOW_START__" in (_REF / "main.cpp").read_text()


def test_matmul_kernel_declared() -> None:
    assert (_REF / "matmul.h").is_file()
    assert "matmul_checksum" in (_REF / "matmul.h").read_text()
