You are a workload simulation architect. Based on the analysis below, design the Business Workflow for the simulated workload.

For each workflow stage, specify:
- **stage_name**: Short identifier
- **implementation_strategy**: One of "compute_synthesis", "memory_synthesis", "direct_call", or "mixed"
- **strategies**: For each hotspot in this stage, specify the behavior implementation strategy
  - open_source hotspots -> "direct_call" (call the real open-source library function)
  - customer_custom hotspots -> "compute_synthesis" or "memory_synthesis" based on the Topdown bottleneck
- **target_topdown**: Target Topdown L1 metrics for this stage

Output as structured JSON matching this schema:
{
  "stages": [{
    "stage_name": "...",
    "implementation_strategy": "...",
    "target_topdown": {"frontend_bound": ..., "backend_bound": ..., "bad_speculation": ..., "retiring": ...},
    "strategies": [{
      "function": "...",
      "source": "open_source|customer_custom",
      "strategy": "direct_call|compute_synthesis|memory_synthesis|mixed",
      "weight_pct": ...,
      "synthesis_config": {...}
    }]
  }]
}

Analysis result:
{analysis_json}
