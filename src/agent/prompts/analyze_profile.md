You are a workload simulation expert. Analyze the following customer Profile data and produce:

1. **Topdown bottleneck identification**: Which Topdown category is dominant? What does it imply about the workload's microarchitectural behavior?

2. **Workflow stage proposal**: Based on the call paths and business logic, propose how to divide the workload into processing stages. For each stage, estimate its target Topdown characteristics.

3. **Key observations**: Any notable patterns (e.g., high memory_bound suggesting data-heavy workload, high bad_speculation suggesting branch-heavy logic).

Note: hotspot source classification (open_source vs customer_custom, and the library) is already
determined deterministically in the Profile's `source`/`library` fields (by mirage's
FunctionClassifier at ingestion). Do NOT re-classify hotspots; the pipeline injects the
classification downstream. Focus on bottleneck analysis, workflow stage proposal, and
observations.

Output your analysis as structured JSON matching the following schema:
{
  "bottleneck_type": "backend_bound",
  "bottleneck_subtype": "memory_bound",
  "workflow_stages": [{"stage_name": "...", "target_topdown": {...}, "description": "..."}],
  "observations": ["..."]
}

Customer Profile:
{profile_json}
