"""Core Profile data models for the Workload Simulation Framework."""

from pydantic import BaseModel, Field


class SoftwareDependency(BaseModel):
    name: str
    version: str
    compile_flags: str | None = None
    config: dict[str, object] | None = None


class ProfileMetadata(BaseModel):
    customer: str
    date: str
    platform: str = "arm64"
    kernel_version: str | None = None
    neoverse_core: str | None = None
    software_stack: list[SoftwareDependency] = Field(default_factory=list)


class HotspotFunction(BaseModel):
    function: str
    library: str
    source: str  # "open_source" or "customer_custom"
    self_pct: float
    cumulative_pct: float
    call_path: list[str] = Field(default_factory=list)


class TopdownL1(BaseModel):
    """ARM Top-Down Microarchitecture Analysis level-1 breakdown.

    All four fields are PERCENTAGES (0-100, summing to ~100), NOT fractions.
    Every parse path yields percentages: `parse_json`/`parse_csv` pass the
    fixture values through (fixtures store percentages), and `parse_text`
    parses the devkit `tuner top-down` report which emits percentages natively.
    The comparator therefore computes ABSOLUTE percentage-point diffs
    (workload - customer) against `topdown_threshold_pct`, not relative diffs.
    """

    frontend_bound: float
    backend_bound: float
    bad_speculation: float
    retiring: float


class TopdownL2Frontend(BaseModel):
    branch_detect: float | None = None
    fetch_latency: float | None = None
    icache_misses: float | None = None


class TopdownL2Backend(BaseModel):
    memory_bound: float | None = None
    core_bound: float | None = None


class TopdownL2BadSpec(BaseModel):
    branch_mispredict: float | None = None
    other: float | None = None


class TopdownL2Retiring(BaseModel):
    heavy_ops: float | None = None
    light_ops: float | None = None


class TopdownL2(BaseModel):
    frontend_bound: TopdownL2Frontend | None = None
    backend_bound: TopdownL2Backend | None = None
    bad_speculation: TopdownL2BadSpec | None = None
    retiring: TopdownL2Retiring | None = None


class TopdownNode(BaseModel):
    """One node of the hierarchical devkit topdown tree (L1->L4).

    `value` is the Bound(%) as-printed by devkit (a percentage). `children`
    holds the deeper levels. `sampling_event` is the "Preferred Sampling Event"
    column; devkit prints "--" for most rows, mapped to None here.
    """

    name: str
    value: float
    children: list["TopdownNode"] = Field(default_factory=list)
    sampling_event: str | None = None


TopdownNode.model_rebuild()


class TopdownSummary(BaseModel):
    """Per-capture raw counters from the devkit "TOP-DOWN Summary Report" header.

    Averaged across the -i interval blocks when parsed from a multi-block text
    report. `cycles`/`instructions` are raw integer counts; `ipc` is
    instructions-per-cycle as-printed.
    """

    cycles: int
    instructions: int
    ipc: float


class MemoryProfile(BaseModel):
    bandwidth_gbps: float | None = None
    l3_miss_rate: float | None = None
    tlb_miss_rate: float | None = None
    working_set_size_mb: float | None = None


class OptimizationRecord(BaseModel):
    strategy: str
    impact: str
    verified: bool = False
    context: str | None = None


class CallgraphSummary(BaseModel):
    total_unique_functions: int | None = None
    open_source_functions: int | None = None
    customer_custom_functions: int | None = None
    open_source_hotspot_pct: float | None = None
    customer_custom_hotspot_pct: float | None = None


class CallTreeNode(BaseModel):
    """One node of the faithful flamegraph call tree (per-call-site).

    Identity is the node's position in the tree (its path), not its function
    name — the same function called from two distinct parents is two nodes,
    preserving per-call-site self-time (the field the flat HotspotFunction
    view loses). `library`/`source` are FunctionClassifier outputs (captured
    signal); `archetype` is NOT here — it is a derived digest accessor (spec
    §5/§7) so the profile layer has no codegen import edge.
    """

    function: str
    library: str | None = None
    source: str  # open_source | customer_custom
    self_pct: float = 0.0  # at THIS call-site
    cumulative_pct: float = 0.0
    self_samples: int = 0
    cumulative_samples: int = 0
    children: list["CallTreeNode"] = Field(default_factory=list)
    depth: int = 0


CallTreeNode.model_rebuild()


class Stage(BaseModel):
    name: str
    bottleneck: str  # topdown node path, e.g. backend_bound.memory_bound.l3_bound
    dominant_self_pct: float
    representative_function: str | None = None


class ThreadPool(BaseModel):
    name: str
    functions: list[str] = Field(default_factory=list)
    self_pct: float = 0.0


class BusinessModel(BaseModel):
    """Customer-level business/archetype descriptor (LLM-assisted, stable across
    iterations). Extraction is deferred to the synthesis spec (spec §6.3); the
    foundation adds only the model + field.

    `thread_pools` is None when per-thread flamegraph capture is unavailable —
    FlamegraphParser merges stacks across threads, so per-thread origin is gone
    at ingestion (RFC 0001 P2 non-goal for the same reason).
    """

    archetype: str  # memory_bound | compute_bound | mixed
    stages: list[Stage] = Field(default_factory=list)
    thread_pools: list[ThreadPool] | None = None


class Profile(BaseModel):
    metadata: ProfileMetadata
    hotspots: list[HotspotFunction] = Field(default_factory=list)
    topdown: TopdownL1 | None = None
    topdown_l2: TopdownL2 | None = None
    summary: TopdownSummary | None = None
    topdown_tree: list[TopdownNode] | None = None
    memory: MemoryProfile | None = None
    optimizations: list[OptimizationRecord] = Field(default_factory=list)
    business_logic: str | None = None
    callgraph_summary: CallgraphSummary | None = None
    call_tree: list[CallTreeNode] | None = None
    business_model: BusinessModel | None = None
