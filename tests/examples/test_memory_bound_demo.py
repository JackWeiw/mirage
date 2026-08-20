"""The memory_bound reference demo is buildable: CMakeLists declares the target +
flags, main.cpp prints the steady-state marker, scan.{h,cpp} define the kernel."""

import pathlib

_REF = (
    pathlib.Path(__file__).resolve().parents[2]
    / "examples"
    / "scenarios"
    / "memory_bound"
    / "reference"
)


def test_cmake_declares_target_and_flags() -> None:
    text = (_REF / "CMakeLists.txt").read_text()
    assert "add_executable(memory_bound_ref main.cpp scan.cpp)" in text
    assert "-O2" in text and "-march=armv8.2-a" in text
    assert "third_party/taskflow" in text


def test_main_prints_measurement_marker() -> None:
    text = (_REF / "main.cpp").read_text()
    assert "__MEASUREMENT_WINDOW_START__" in text


def test_scan_kernel_declared() -> None:
    assert (_REF / "scan.h").is_file()
    assert (_REF / "scan.cpp").is_file()
    assert "random_scan" in (_REF / "scan.h").read_text()
