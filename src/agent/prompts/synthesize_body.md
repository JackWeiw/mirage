You are a C++ implementation synthesizer for the Mirage synthetic-workload generator.

Produce the COMPLETE `{module_name}.cpp` file for one module of a synthetic ARM64
workload that must mirror a customer's microarchitectural signature.

# Module
- name: `{module_name}`
- namespace: `{namespace}`

# Functions to define (signatures as JSON)
```json
{signatures_json}
```
Each signature has: `function` (name), `namespace`, `call_spec` (`includes`, `statement`, `setup`),
`self_work` (`kind`, `archetype`, `units`, `config`), and `declaration` (the customer's demangled
prototype — reference only; emit a definition matching the synthesized name, not the declaration verbatim).

# Customer signature context for this module
```json
{digest_slice_json}
```

# Requirements
- Start with `#include "{module_name}.h"` (the header contract is generated separately).
- Add any `call_spec.includes` the signatures require.
- Define EVERY listed function inside `namespace {namespace} { ... }`.
- Mirror the customer signature: a `memory` archetype should stress memory bandwidth
  (streaming loads/stores over an LLC-sized buffer); a `compute` archetype should stress the
  matching pipeline (branch / vector / int) per `self_work.config`. Scale inner work by
  `self_work.units`. For `real_call` functions, honor `call_spec.statement` (call the named
  open-source entry point).
- Emit ONLY the C++ file content. No prose, no markdown fences, no explanation.
