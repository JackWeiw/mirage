You are the architect revising a synthesis plan to close a measured microarchitectural gap.

You previously designed a plan (ModuleGraph + per-function SynthesisTasks). The generated C++ was built, run, and measured against the customer profile. The gap report below shows which Topdown L1 metrics / memory bandwidth differ from the customer signature.

Your job: identify which modules' synthesized `.cpp` bodies most plausibly drive the dominant gap, and name them for re-synthesis. Be surgical — only re-synthesize the 1-2 modules whose bodies most likely cause the largest out-of-threshold metric. If no module clearly drives the gap, return empty lists.

Current plan (JSON):
{plan}

Gap report (customer vs workload; diff_pct = absolute percentage points for topdown, relative % for memory):
{report}

Per-knob sensitivity (proven directions; for context on what runtime knobs can/cannot fix — structural re-synthesis is for what knobs cannot):
{sensitivity}

Recent iteration history (adjustments tried + observed effects):
{recent_history}

Respond with ONLY a JSON object, no prose, no markdown fences:
{"resynthesize_modules": ["module_name", ...], "adjustments": [{"module": "module_name", "reason": "one short sentence"}]}
