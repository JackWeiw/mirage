"""CollectionConfig loads collection.yaml and exposes typed fields used by both
the reference collector and the driver's synthetic collect callable."""

import pathlib

import collect_common  # type: ignore[import-not-found]

_SCENARIO = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios" / "memory_bound"


def test_collection_config_loads_typed_fields() -> None:
    cfg = collect_common.CollectionConfig.from_yaml(_SCENARIO / "collection.yaml")
    assert cfg.duration_seconds == 20
    assert cfg.interval_seconds == 3
    assert cfg.perf_freq == 99
    assert cfg.cpu_mask == "0-63"
    assert cfg.numa_node == "0"
    assert cfg.per_worker_buffer_mb == 64
    assert cfg.warmup_seconds == 5
    assert cfg.measurement_seconds == 20
    assert cfg.llc_miss_floor_pct == 90.0
