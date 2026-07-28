You are a workload simulation expert. Analyze the following customer Profile data and produce:

1. **Topdown bottleneck identification**: Which Topdown category is dominant? What does it imply about the workload's microarchitectural behavior?

2. **Hotspot classification**: For each hotspot function, classify it as either:
   - "open_source" (comes from a known open-source library like folly, fbthrift, brpc, taskflow)
   - "customer_custom" (customer's proprietary code)

3. **Workflow stage proposal**: Based on the call paths and business logic, propose how to divide the workload into processing stages. For each stage, estimate its target Topdown characteristics.

4. **Key observations**: Any notable patterns (e.g., high memory_bound suggesting data-heavy workload, high bad_speculation suggesting branch-heavy logic).

Output your analysis as structured JSON matching the following schema:
{
  "bottleneck_type": "backend_bound",
  "bottleneck_subtype": "memory_bound",
  "hotspot_classification": [{"function": "...", "source": "open_source|customer_custom", "library": "..."}],
  "workflow_stages": [{"stage_name": "...", "target_topdown": {...}, "description": "..."}],
  "observations": ["..."]
}

Customer Profile:
{profile_json}
