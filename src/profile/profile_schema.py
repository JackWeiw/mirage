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


class Profile(BaseModel):
    metadata: ProfileMetadata
    hotspots: list[HotspotFunction] = Field(default_factory=list)
    topdown: TopdownL1 | None = None
    topdown_l2: TopdownL2 | None = None
    memory: MemoryProfile | None = None
    optimizations: list[OptimizationRecord] = Field(default_factory=list)
    business_logic: str | None = None
    callgraph_summary: CallgraphSummary | None = None
