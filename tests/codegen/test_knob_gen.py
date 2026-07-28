"""Tests for KnobGenerator."""

import json
import pathlib
import tempfile

from codegen.knob_gen import KnobGenerator


def test_generate_config() -> None:
    gen = KnobGenerator()
    output_path = pathlib.Path(tempfile.mkdtemp()) / "config.json"
    knobs = {"thread_count": 16, "qps": 500, "warmup_seconds": 10}
    gen.generate_config(knobs, output_path)
    config = json.loads(output_path.read_text())
    assert config["thread_count"] == 16
    assert config["qps"] == 500
    assert config["warmup_seconds"] == 10


def test_generate_config_defaults() -> None:
    gen = KnobGenerator()
    output_path = pathlib.Path(tempfile.mkdtemp()) / "config.json"
    gen.generate_config({}, output_path)
    config = json.loads(output_path.read_text())
    assert config["thread_count"] == 4
    assert config["qps"] == 100


def test_update_config() -> None:
    gen = KnobGenerator()
    output_path = pathlib.Path(tempfile.mkdtemp()) / "config.json"
    gen.generate_config({"thread_count": 4}, output_path)
    gen.update_config(output_path, {"thread_count": 16, "qps": 200})
    config = json.loads(output_path.read_text())
    assert config["thread_count"] == 16
    assert config["qps"] == 200
