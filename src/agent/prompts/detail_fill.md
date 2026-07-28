You are a C++ workload generator. Fill in the implementation details for each workflow stage.

For "direct_call" stages:
- Provide the exact C++ call statement for the open-source library function
- Provide the required #include headers

For "compute_synthesis" stages:
- Specify compute_type (e.g., "hash", "sort", "matrix_multiply")
- Specify iterations parameter

For "memory_synthesis" stages:
- Specify access_pattern ("random", "sequential", "mixed")
- Specify working_set_mb

Also specify:
- project_name: A name for the workload project
- compile_flags: Matching the customer's compile flags
- dependencies: List of CMake dependencies with versions
- dep_headers: List of #include headers needed

Output as structured JSON matching the WorkloadGenerator instruction schema:
{
  "project_name": "...",
  "compile_flags": "...",
  "dependencies": [...],
  "dep_headers": [...],
  "stages": [{...with filled details}],
  "config": {"thread_count": ..., "qps": ..., ...}
}

Workflow plan:
{workflow_json}
