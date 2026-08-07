"""Generate Layer 4 tuning knob configuration."""

import json
import pathlib
from typing import Any


class KnobGenerator:
    """Generate config.json with runtime tuning parameters."""

    def generate_config(self, knobs: dict[str, Any], output_path: pathlib.Path) -> pathlib.Path:
        """Write a config.json file with tuning parameters.

        Args:
            knobs: Dict with keys like thread_count, qps, warmup_seconds,
                measurement_seconds, compute_ratio, memory_ratio, etc.
            output_path: Path to write config.json.

        Returns:
            Path to the written file.
        """
        config = {
            "thread_count": knobs.get("thread_count", 4),
            "qps": knobs.get("qps", 100),
            "warmup_seconds": knobs.get("warmup_seconds", 30),
            "measurement_seconds": knobs.get("measurement_seconds", 60),
            "compute_ratio": knobs.get("compute_ratio", 0.5),
            "memory_ratio": knobs.get("memory_ratio", 0.5),
            "ramp_up_seconds": knobs.get("ramp_up_seconds", 10),
        }
        output_path.write_text(json.dumps(config, indent=2))
        return output_path

    def update_config(self, config_path: pathlib.Path, updates: dict[str, Any]) -> pathlib.Path:
        """Update specific fields in an existing config.json."""
        with open(config_path) as f:
            config = json.load(f)
        config.update(updates)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        return config_path
