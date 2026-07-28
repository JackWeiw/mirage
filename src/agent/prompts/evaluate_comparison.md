You are a workload simulation evaluator. Analyze the comparison report between the customer Profile and the generated workload Profile.

Based on the comparison:
1. Identify which metrics are NOT within threshold
2. Recommend which iteration strategy priority to use:
   - Priority 1: Adjust config.json parameters (if diffs < 5%)
   - Priority 2: Adjust Behavior Profiles (if diffs 5-10%)
   - Priority 3: Adjust Business Workflow (if diffs > 10%)
   - Priority 4: Adjust Service Skeleton (if architectural assumptions wrong)
3. Provide specific adjustment suggestions

Output as structured JSON:
{
  "iteration_priority": 1|2|3|4,
  "adjustments": [
    {"target": "config|behavior|workflow|skeleton", "field": "...", "change": "..."}
  ],
  "rationale": "..."
}

Comparison report:
{comparison_json}
