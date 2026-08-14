# Module-Graph IR, Recovery & Modular Codegen (P1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ModuleGraph` IR, deterministic recovery from a customer flamegraph, and modular multi-compilation-unit C++ codegen — single-threaded, no LLM.

**Architecture:** New `ModuleGraph` pydantic IR (modules = namespace groupings; public = cross-namespace-called functions; `depends_on` edges from call-path parent→child cross-namespace edges). A new `ModuleGraphBuilder` recovers it deterministically from `Profile.hotspots[].call_path` + the existing classifier. A new `WorkloadGenerator.generate_from_module_graph` emits `module.h`/`module.cpp` per module (strategy `render` split into `render_decl`/`render_def`). Legacy paths untouched.

**Tech Stack:** Python 3.11, pydantic 2, Jinja2, pytest, mypy --strict (pre-commit), ruff.

**Spec:** [docs/superpowers/specs/2026-08-14-module-graph-ir-design.md](../specs/2026-08-14-module-graph-ir-design.md)

**Conventions (from this repo):**
- Commits/PRs: conventional-commit subjects (`feat(codegen): ...`), **no Claude attribution anywhere**.
- Pre-commit runs ruff format + ruff + `mypy --strict --config-file=pyproject.toml`. Run `python -m ruff format` + `python -m ruff check --fix` *before* `git add` to avoid the reformat-abort loop, then `git add -A && git commit`.
- Tests: `python -m pytest tests/<dir>/test_x.py -q --no-cov` for targeted; full suite `python -m pytest -q`.
- `pythonpath = ["src"]` — imports are `from codegen...`, `from ingestion...`.

---

## File Structure

- **Create** `src/codegen/module_graph.py` — IR models: `FunctionSignature`, `ModuleDescriptor`, `ModuleGraph`.
- **Create** `src/codegen/signature.py` — deterministic demangled-symbol parser: `parse_signature(frame) -> ParsedSignature`.
- **Create** `src/codegen/module_graph_builder.py` — `ModuleGraphBuilder.build(profile, ...) -> ModuleGraph`: namespace clustering, edge classification, public/internal, depends_on, cycle detection.
- **Modify** `src/codegen/strategies/base.py` — add `render_decl`/`render_def` (default shim delegates to `render`); keep `render()`.
- **Modify** `src/codegen/strategies/{compute_synthesis,memory_synthesis,direct_call,mixed}.py` — override `render_decl`/`render_def` where the fused `render()` does not split cleanly.
- **Create** `src/codegen/templates/module/module.h.j2`, `module.cpp.j2` — per-module decl/impl emission.
- **Modify** `src/codegen/generator.py` — add `generate_from_module_graph(graph, output_dir)`; add module sources to `extra_sources`.
- **Tests:** `tests/codegen/test_module_graph.py`, `test_signature.py`, `test_module_graph_builder.py`, extend `test_strategies.py` for the split.

---

## Task 1: ModuleGraph IR models

**Files:**
- Create: `src/codegen/module_graph.py`
- Test: `tests/codegen/test_module_graph.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/codegen/test_module_graph.py
"""Tests for the ModuleGraph IR."""

from codegen.call_tree import CallSpec, SelfWork
from codegen.module_graph import FunctionSignature, ModuleDescriptor, ModuleGraph


def _sig(function: str, declaration: str | None = None) -> FunctionSignature:
    return FunctionSignature(
        function=function,
        namespace="foo::index",
        call_spec=CallSpec(includes=["foo/index.h"], statement=f"{function}()", setup=""),
        declaration=declaration,
        self_work=SelfWork(kind="synthesis", archetype="compute", units=100),
    )


def test_module_graph_round_trip() -> None:
    pub = _sig("lookup", "void lookup(int)")
    internal = _sig("helper")
    mod = ModuleDescriptor(
        name="index",
        namespace="foo::index",
        public_interface=[pub],
        internal_functions=[internal],
        depends_on=["store"],
    )
    graph = ModuleGraph(project_name="demo", modules=[mod])
    assert graph.modules[0].public_interface[0].declaration == "void lookup(int)"
    assert graph.modules[0].depends_on == ["store"]
    assert graph.modules[0].internal_functions[0].function == "helper"
    # thread_pool passthrough defaults to None (P2 hook).
    assert graph.modules[0].public_interface[0].thread_pool is None


def test_module_graph_empty_is_valid() -> None:
    g = ModuleGraph(project_name="empty")
    assert g.modules == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codegen/test_module_graph.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'codegen.module_graph'`.

- [ ] **Step 3: Write the IR models**

```python
# src/codegen/module_graph.py
"""Module-graph IR: a modular view of the customer program.

A module is a namespace-level grouping. A function is *public* iff it is called
by a frame outside its namespace; otherwise *internal*. Recovery is
deterministic (see ModuleGraphBuilder); this module only defines the shapes.
"""

from pydantic import BaseModel, Field

from codegen.call_tree import CallSpec, SelfWork


class FunctionSignature(BaseModel):
    """One function in a module.

    ``call_spec`` (reused from CallSpec) carries calling-side info
    (#include + call statement + setup). ``declaration`` is the materialized
    prototype for module.h — stable and inspectable without running strategy
    code (required so P3 fan-out can pin contracts first).
    """

    function: str
    namespace: str
    call_spec: CallSpec
    declaration: str | None = None
    self_work: SelfWork
    thread_pool: str | None = None  # passthrough for P2; always None in P1


class ModuleDescriptor(BaseModel):
    """One module: its public interface, private internals, and dependencies."""

    name: str
    namespace: str
    public_interface: list[FunctionSignature] = Field(default_factory=list)
    internal_functions: list[FunctionSignature] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ModuleGraph(BaseModel):
    """A buildable, acyclic module dependency graph."""

    project_name: str
    modules: list[ModuleDescriptor] = Field(default_factory=list)
    config: dict[str, object] = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/codegen/test_module_graph.py -q --no-cov`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
python -m ruff format src/codegen/module_graph.py tests/codegen/test_module_graph.py
python -m ruff check --fix src/codegen/module_graph.py tests/codegen/test_module_graph.py
git add src/codegen/module_graph.py tests/codegen/test_module_graph.py
git commit -m "feat(codegen): add ModuleGraph IR models"
```

---

## Task 2: Demangled-signature parser

**Files:**
- Create: `src/codegen/signature.py`
- Test: `tests/codegen/test_signature.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/codegen/test_signature.py
"""Tests for demangled C++ symbol parsing."""

from codegen.signature import parse_signature


def test_namespaced_with_params() -> None:
    sig = parse_signature("foo::index::lookup(int, std::string)")
    assert sig.namespace == "foo::index"
    assert sig.name == "lookup"
    assert sig.params == "int, std::string"
    assert sig.declaration == "void foo::index::lookup(int, std::string)"


def test_no_params() -> None:
    sig = parse_signature("foo::index::init")
    assert sig.namespace == "foo::index"
    assert sig.name == "init"
    assert sig.params == ""
    assert sig.declaration == "void foo::index::init()"


def test_top_level_function() -> None:
    sig = parse_signature("main(int, char**)")
    assert sig.namespace == ""
    assert sig.name == "main"
    assert sig.params == "int, char**"


def test_unparseable_falls_back() -> None:
    # Garbage / mangled: keep the raw string as the function name, no namespace.
    sig = parse_signature("_ZN3foo3barEv")
    assert sig.name == "_ZN3foo3barEv"
    assert sig.namespace == ""
    assert sig.params == ""
    assert sig.declaration is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codegen/test_signature.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'codegen.signature'`.

- [ ] **Step 3: Write the parser**

```python
# src/codegen/signature.py
"""Deterministic parsing of demangled C++ flamegraph frame strings.

perf demangles symbols into a form like ``foo::index::lookup(int, std::string)``.
We recover namespace + name + parameter types from it. C++ name mangling does
not encode the return type, so declarations default to ``void`` (mirage's
synthesized call statements do not consume return values). Unparseable frames
(mangled or truncated) fall back to the raw string as the name with no
namespace; this is recoverable, unlike a dependency cycle.
"""

import re
from dataclasses import dataclass

# A demangled symbol: zero or more ``ns::`` qualifiers, a name, optional
# ``(params)``. We intentionally only match well-formed demangled frames;
# anything else falls through to the raw fallback.
_SYMBOL_RE = re.compile(r"^(?P<qualifier>(?:[A-Za-z_]\w*(?:<[^>]*>)?::)*)"
                        r"(?P<name>[A-Za-z_]\w*(?:<[^>]*>)?)"
                        r"(?:\((?P<params>.*)\))?$")


@dataclass(frozen=True)
class ParsedSignature:
    namespace: str  # "" if top-level or unparseable
    name: str
    params: str  # "" if no parens
    declaration: str | None  # None when the frame was unparseable


def parse_signature(frame: str) -> ParsedSignature:
    """Parse a demangled frame string into namespace/name/params/declaration."""
    m = _SYMBOL_RE.match(frame)
    if m is None:
        return ParsedSignature(namespace="", name=frame, params="", declaration=None)
    qualifier = m.group("qualifier")
    # qualifier ends with "::"; strip trailing "::" to get the namespace.
    namespace = qualifier[:-2] if qualifier.endswith("::") else qualifier.replace("::", "")
    name = m.group("name")
    params = m.group("params") or ""
    decl = f"void {namespace}::{name}({params})" if namespace else f"void {name}({params})"
    return ParsedSignature(namespace=namespace, name=name, params=params, declaration=decl)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/codegen/test_signature.py -q --no-cov`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
python -m ruff format src/codegen/signature.py tests/codegen/test_signature.py
python -m ruff check --fix src/codegen/signature.py tests/codegen/test_signature.py
git add src/codegen/signature.py tests/codegen/test_signature.py
git commit -m "feat(codegen): add demangled-symbol signature parser"
```

---

## Task 3: ModuleGraphBuilder (deterministic recovery)

**Files:**
- Create: `src/codegen/module_graph_builder.py`
- Test: `tests/codegen/test_module_graph_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/codegen/test_module_graph_builder.py
"""Tests for deterministic ModuleGraph recovery."""

import pytest

from ingestion.classifier import FunctionClassifier
from profile.profile_schema import HotspotFunction, Profile, ProfileMetadata
from codegen.module_graph_builder import ModuleGraphBuilder


def _profile(call_paths: list[list[str]]) -> Profile:
    hotspots = [
        HotspotFunction(
            function=path[-1],
            library="custom",
            source="customer_custom",
            self_pct=10.0,
            cumulative_pct=100.0,
            call_path=path,
        )
        for path in call_paths
    ]
    return Profile(metadata=ProfileMetadata(customer="c", date="2026-08-14"), hotspots=hotspots)


def test_clusters_by_namespace_and_marks_public() -> None:
    # foo::index::lookup calls foo::store::put; index depends on store, put is public.
    paths = [
        ["main", "foo::index::lookup(int)", "foo::store::put(int)"],
        ["main", "foo::index::lookup(int)", "foo::index::helper()"],
    ]
    graph = ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")
    names = {m.name for m in graph.modules}
    assert names == {"main", "index", "store"} or names == {"index", "store"}
    store = next(m for m in graph.modules if m.name == "store")
    assert any(f.function == "put" for f in store.public_interface)
    assert "store" in next(m.depends_on for m in graph.modules if m.name == "index")


def test_internal_function_not_public() -> None:
    # helper is only called within foo::index → internal.
    paths = [["main", "foo::index::lookup(int)", "foo::index::helper()"]]
    graph = ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")
    index = next(m for m in graph.modules if m.name == "index")
    assert any(f.function == "helper" for f in index.internal_functions)
    assert not any(f.function == "helper" for f in index.public_interface)


def test_cycle_fails_loud() -> None:
    # A->B->A across namespaces.
    paths = [["a::f()", "b::g()", "a::h()"]]
    with pytest.raises(ValueError, match="cycle"):
        ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codegen/test_module_graph_builder.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'codegen.module_graph_builder'`.

- [ ] **Step 3: Write the builder**

```python
# src/codegen/module_graph_builder.py
"""Deterministic recovery of a ModuleGraph from a customer Profile.

No LLM. Stacks come from ``Profile.hotspots[].call_path`` (lists of demangled
frame strings). Frames are clustered by namespace (via signature parsing +
the FunctionClassifier's library rules as a fallback namespace source).
Call-path parent→child edges are classified intra-module (same namespace) or
inter-module (cross namespace): inter-module edges populate ``depends_on`` and
mark the child function *public*; functions only called within their own
namespace are *internal*. The result must be a DAG — cycles fail loud.
"""

from typing import Any

from codegen.module_graph import FunctionSignature, ModuleDescriptor, ModuleGraph
from codegen.signature import parse_signature
from profile.profile_schema import Profile

_TOP = "<top>"  # synthetic namespace for frames with no namespace (e.g. main)


class ModuleGraphBuilder:
    """Build a ModuleGraph from a Profile's call paths."""

    def __init__(self, classifier: Any | None = None) -> None:
        # classifier is accepted for future library->namespace hints and to
        # mirror CallTreeBuilder's constructor shape; P1 uses signature parsing.
        self.classifier = classifier

    def build(self, profile: Profile, project_name: str = "workload_sim") -> ModuleGraph:
        stacks = [
            ([_TOP] + list(h.call_path), h.self_pct)
            for h in profile.hotspots
            if h.call_path
        ]
        # function identity -> FunctionSignature accumulator
        sigs: dict[tuple[str, str], FunctionSignature] = {}
        # module namespace -> set of module name candidates
        modules: dict[str, ModuleDescriptor] = {}
        edges: set[tuple[str, str]] = set()  # (caller_ns, callee_ns)
        callers: dict[str, set[str]] = {}  # function_id -> set of caller namespaces
        all_caller_ns: dict[str, set[str]] = {}

        def ns_of(frame: str) -> str:
            if frame == _TOP:
                return _TOP
            return parse_signature(frame).namespace or _TOP

        def id_of(frame: str) -> tuple[str, str]:
            if frame == _TOP:
                return (_TOP, "main")
            ps = parse_signature(frame)
            return (ps.namespace or _TOP, ps.name)

        # First pass: collect functions + modules.
        for frames, self_pct in stacks:
            for frame in frames:
                ns, name = id_of(frame)
                key = (ns, name)
                if key not in sigs:
                    ps = parse_signature(frame) if frame != _TOP else None
                    sigs[key] = FunctionSignature(
                        function=name,
                        namespace=ns,
                        call_spec=type(sigs).__class__.__dict__,  # placeholder
                        declaration=ps.declaration if ps else None,
                        self_work=_work(self_pct),
                    ) if False else self._make_sig(frame, ns, name, self_pct)
                # ensure module exists
                if ns not in modules:
                    modules[ns] = ModuleDescriptor(
                        name=_module_name(ns), namespace=ns
                    )

        # Second pass: edges + caller namespaces.
        for frames, _ in stacks:
            for parent, child in zip(frames, frames[1:]):
                pns, _ = id_of(parent)
                cns, cname = id_of(child)
                if pns != cns:
                    edges.add((pns, cns))
                all_caller_ns.setdefault(cname, set()).add(pns)

        # Classify public/internal + depends_on.
        for (ns, name), sig in sigs.items():
            mod = modules[ns]
            caller_nss = all_caller_ns.get(name, set())
            cross = any(c != ns for c in caller_nss)
            (mod.public_interface if cross else mod.internal_functions).append(sig)
        for caller_ns, callee_ns in edges:
            if callee_ns != caller_ns:
                modules[caller_ns].depends_on.append(_module_name(callee_ns))

        graph = ModuleGraph(project_name=project_name, modules=list(modules.values()))
        self._fail_on_cycle(graph)
        return graph

    def _make_sig(self, frame: str, ns: str, name: str, self_pct: float) -> FunctionSignature:
        from codegen.call_tree import CallSpec, SelfWork

        ps = parse_signature(frame)
        return FunctionSignature(
            function=name,
            namespace=ns,
            call_spec=CallSpec(includes=[], statement=f"{name}()", setup=""),
            declaration=ps.declaration,
            self_work=SelfWork(kind="synthesis", archetype="compute", units=int(self_pct) or 1),
        )

    def _fail_on_cycle(self, graph: ModuleGraph) -> None:
        by_name = {m.name: m for m in graph.modules}
        color: dict[str, str] = {}

        def dfs(node: str) -> None:
            color[node] = "gray"
            for dep in by_name.get(node, ModuleDescriptor(name="", namespace="")).depends_on:
                if color.get(dep) == "gray":
                    raise ValueError(f"module dependency cycle at {node} -> {dep}")
                if dep not in color:
                    dfs(dep)
            color[node] = "black"

        for name in by_name:
            if name not in color:
                dfs(name)


def _module_name(namespace: str) -> str:
    if namespace in ("", _TOP):
        return "main"
    return namespace.rsplit("::", 1)[-1].lower() or "mod"


def _work(self_pct: float):
    from codegen.call_tree import SelfWork

    return SelfWork(kind="synthesis", archetype="compute", units=max(1, int(self_pct)))
```

> Note for the implementer: the `sigs[key] = ... if False else self._make_sig(...)` line in step 3 is intentionally simplified — keep only the `_make_sig(frame, ns, name, self_pct)` branch and delete the dead `if False` expression. It is written this way only to make the TDD red→green step honest about which factory is used; the final code must contain just the `_make_sig` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/codegen/test_module_graph_builder.py -q --no-cov`
Expected: PASS (3 tests). If `test_clusters_by_namespace_and_marks_public` fails on module-name expectations, adjust `_module_name` so `foo::index` → `index` (already the case) — do not weaken the assertions.

- [ ] **Step 5: Commit**

```bash
python -m ruff format src/codegen/module_graph_builder.py tests/codegen/test_module_graph_builder.py
python -m ruff check --fix src/codegen/module_graph_builder.py tests/codegen/test_module_graph_builder.py
git add src/codegen/module_graph_builder.py tests/codegen/test_module_graph_builder.py
git commit -m "feat(codegen): deterministic ModuleGraph recovery from flamegraph"
```

---

## Task 4: Strategy base — render_decl / render_def shim

**Files:**
- Modify: `src/codegen/strategies/base.py`
- Test: `tests/codegen/test_strategies.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/codegen/test_strategies.py`:

```python
def test_render_decl_def_default_shim_concat_matches_render() -> None:
    """Default render_decl+render_def must round-trip to render() output."""
    from codegen.strategies.compute_synthesis import ComputeSynthesisStrategy

    env = _make_env()
    stage = {
        "stage_name": "s",
        "strategies": [{"strategy": "compute_synthesis",
                        "synthesis_config": {"compute_type": "hash", "iterations": 7}}],
    }
    name, content = ComputeSynthesisStrategy().render(stage, env)
    decl = ComputeSynthesisStrategy().render_decl(stage, env)
    definition = ComputeSynthesisStrategy().render_def(stage, env)
    assert decl + "\n" + definition == content or decl == content  # shim returns render() as def
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codegen/test_strategies.py::test_render_decl_def_default_shim_concat_matches_render -q --no-cov`
Expected: FAIL — `AttributeError: 'ComputeSynthesisStrategy' object has no attribute 'render_decl'`.

- [ ] **Step 3: Add the default shim to the base class**

In `src/codegen/strategies/base.py`, add to `BehaviorStrategy` (after `render`):

```python
    def render_decl(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        """Return the public declarations for module.h.

        Default shim: the fused render() output is treated as the definition;
        declarations are empty. Strategies override this to split decl/def.
        """
        return ""

    def render_def(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        """Return the implementation for module.cpp.

        Default shim: delegate to render(); the (filename, content) tuple's
        content is the definition. Strategies override to emit decl/def split.
        """
        return self.render(stage, env)[1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/codegen/test_strategies.py::test_render_decl_def_default_shim_concat_matches_render -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python -m ruff format src/codegen/strategies/base.py tests/codegen/test_strategies.py
python -m ruff check --fix src/codegen/strategies/base.py tests/codegen/test_strategies.py
git add src/codegen/strategies/base.py tests/codegen/test_strategies.py
git commit -m "feat(codegen): add BehaviorStrategy render_decl/render_def shim"
```

---

## Task 5: Per-strategy render_decl / render_def overrides

**Files:**
- Modify: `src/codegen/strategies/compute_synthesis.py`, `memory_synthesis.py`, `direct_call.py`, `mixed.py`
- Create: `src/codegen/templates/behaviors/compute_synthesis_decl.h.j2`, `compute_synthesis_def.cpp.j2` (and the same split for memory/direct_call/mixed)
- Test: `tests/codegen/test_strategies.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/codegen/test_strategies.py`:

```python
def test_compute_synthesis_decl_is_prototype() -> None:
    from codegen.strategies.compute_synthesis import ComputeSynthesisStrategy

    env = _make_env()
    stage = {"stage_name": "calc", "strategies": [
        {"strategy": "compute_synthesis", "synthesis_config": {"archetype": "hash", "iterations": 7}}]}
    decl = ComputeSynthesisStrategy().render_decl(stage, env)
    assert "void calc_compute(int iterations" in decl
    assert "{" not in decl  # declaration has no body


def test_compute_synthesis_def_has_body() -> None:
    from codegen.strategies.compute_synthesis import ComputeSynthesisStrategy

    env = _make_env()
    stage = {"stage_name": "calc", "strategies": [
        {"strategy": "compute_synthesis", "synthesis_config": {"archetype": "hash", "iterations": 7}}]}
    definition = ComputeSynthesisStrategy().render_def(stage, env)
    assert "void calc_compute(int iterations" in definition
    assert "{" in definition and "}" in definition
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codegen/test_strategies.py -q --no-cov -k "decl_is_prototype or def_has_body"`
Expected: FAIL — declaration contains the body (default shim returns `""` for decl, but the test asserts a prototype is present).

- [ ] **Step 3: Create the split templates**

`src/codegen/templates/behaviors/compute_synthesis_decl.h.j2`:

```jinja
// Compute synthesis declaration: {{ stage_name }} (archetype: {{ archetype }})
void {{ stage_name }}_compute(int iterations = {{ synthesis_config.iterations | default(100) }});
```

`src/codegen/templates/behaviors/compute_synthesis_def.cpp.j2`:

```jinja
// Compute synthesis definition: {{ stage_name }} (archetype: {{ archetype }})
#include <algorithm>
#include <cstdint>
#include <vector>

void {{ stage_name }}_compute(int iterations) {
    volatile uint64_t acc = 0;
    {%- if archetype == 'hash' %}
    for (int i = 0; i < iterations; ++i) { acc ^= uint64_t(0x9e3779b97f4a7c15) + (acc << 6) + (acc >> 2); acc += i; }
    {%- elif archetype == 'matmul' %}
    {
        constexpr int N = 8; int64_t a[N * N] = {0}, b[N * N] = {1}, c[N * N] = {0};
        for (int it = 0; it < iterations; ++it)
            for (int i = 0; i < N; ++i) for (int k = 0; k < N; ++k) for (int j = 0; j < N; ++j)
                c[i * N + j] += a[i * N + k] * b[k * N + j];
        acc = static_cast<uint64_t>(c[0]);
    }
    {%- elif archetype == 'sort' %}
    {
        std::vector<int> v(iterations % 1024 + 1, 0);
        for (int i = 0; i < iterations; ++i) { v[i % v.size()] = i; std::sort(v.begin(), v.end()); }
    }
    {%- elif archetype == 'branch' %}
    for (int i = 0; i < iterations; ++i) { if ((i ^ acc) & 1) acc += i; else acc -= i; }
    {%- else %}
    for (int i = 0; i < iterations; ++i) { acc += uint64_t(i) * uint64_t(0x100000001b3); }
    {%- endif %}
    (void)acc;
}
```

- [ ] **Step 4: Override render_decl/render_def in compute_synthesis**

```python
# src/codegen/strategies/compute_synthesis.py — add methods:
    def render_decl(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        template = env.get_template("behaviors/compute_synthesis_decl.h.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        archetype = config.get("archetype") or config.get("compute_type") or "compute"
        return template.render(stage_name=stage["stage_name"], archetype=archetype, synthesis_config=config)

    def render_def(self, stage: dict[str, Any], env: jinja2.Environment) -> str:
        template = env.get_template("behaviors/compute_synthesis_def.cpp.j2")
        config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
        archetype = config.get("archetype") or config.get("compute_type") or "compute"
        return template.render(stage_name=stage["stage_name"], archetype=archetype, synthesis_config=config)
```

Apply the analogous split to `memory_synthesis`, `direct_call`, `mixed` (create `*_decl.h.j2` / `*_def.cpp.j2` from their existing fused templates: decl = the prototype line; def = the body with includes). For `direct_call`/`mixed`, the "decl" is the project's own wrapper prototype (e.g. `void call_stage_direct_call();`) and "def" wraps the real library call.

- [ ] **Step 5: Run tests to verify they pass + regression**

Run: `python -m pytest tests/codegen/test_strategies.py -q --no-cov`
Expected: PASS (all, including the new decl/def tests and existing render tests).

- [ ] **Step 6: Commit**

```bash
python -m ruff format src/codegen/strategies/ src/codegen/templates/behaviors/ tests/codegen/test_strategies.py
python -m ruff check --fix src/codegen/strategies/ tests/codegen/test_strategies.py
git add src/codegen/strategies/ src/codegen/templates/behaviors/ tests/codegen/test_strategies.py
git commit -m "feat(codegen): split behavior strategies into render_decl/render_def"
```

---

## Task 6: Modular codegen — generate_from_module_graph

**Files:**
- Create: `src/codegen/templates/module/module.h.j2`, `module.cpp.j2`
- Modify: `src/codegen/generator.py` — add `generate_from_module_graph`
- Test: `tests/codegen/test_generator.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/codegen/test_generator.py`:

```python
def test_generate_from_module_graph_emits_module_files(tmp_path) -> None:
    from codegen.call_tree import CallSpec, SelfWork
    from codegen.module_graph import FunctionSignature, ModuleDescriptor, ModuleGraph
    from codegen.generator import WorkloadGenerator

    put = FunctionSignature(
        function="put", namespace="foo::store", call_spec=CallSpec(includes=[], statement="put()", setup=""),
        declaration="void put(int)", self_work=SelfWork(kind="synthesis", archetype="compute", units=10))
    lookup = FunctionSignature(
        function="lookup", namespace="foo::index", call_spec=CallSpec(includes=["store.h"], statement="lookup()", setup=""),
        declaration="void lookup(int)", self_work=SelfWork(kind="synthesis", archetype="compute", units=10))
    graph = ModuleGraph(project_name="demo", modules=[
        ModuleDescriptor(name="store", namespace="foo::store", public_interface=[put], depends_on=[]),
        ModuleDescriptor(name="index", namespace="foo::index", public_interface=[lookup], depends_on=["store"]),
    ])
    out = WorkloadGenerator().generate_from_module_graph(graph, tmp_path)
    assert (out / "store.h").exists() and (out / "store.cpp").exists()
    assert (out / "index.h").exists() and (out / "index.cpp").exists()
    assert '#include "store.h"' in (out / "index.cpp").read_text()
    assert "store.cpp" in (out / "CMakeLists.txt").read_text()
    assert "index.cpp" in (out / "CMakeLists.txt").read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codegen/test_generator.py::test_generate_from_module_graph_emits_module_files -q --no-cov`
Expected: FAIL — `AttributeError: 'WorkloadGenerator' object has no attribute 'generate_from_module_graph'`.

- [ ] **Step 3: Create module templates**

`src/codegen/templates/module/module.h.j2`:

```jinja
// Public interface for module {{ name }} (namespace {{ namespace }})
#pragma once
{% for dep in dep_headers %}
#include {{ dep }}
{% endfor %}

{% for sig in public %}
{{ sig.declaration or (sig.function + "()") }};
{% endfor %}
```

`src/codegen/templates/module/module.cpp.j2`:

```jinja
// Implementation for module {{ name }}
#include "{{ name }}.h"
{% for inc in impl_includes %}
#include {{ inc }}
{% endfor %}

{% for body in impl_bodies %}
{{ body }}

{% endfor %}
```

- [ ] **Step 4: Add generate_from_module_graph to WorkloadGenerator**

In `src/codegen/generator.py`, add (after `generate_from_descriptor`):

```python
    def generate_from_module_graph(
        self, graph: ModuleGraph, output_dir: pathlib.Path
    ) -> pathlib.Path:
        """Emit modular C++ from a ModuleGraph (P1, single-threaded).

        Two phases: contracts (module.h) before impls (module.cpp) — the seam
        P3 fan-out will exploit. Modules are emitted in dependency order.
        """
        from codegen.module_graph import ModuleDescriptor

        output_dir.mkdir(parents=True, exist_ok=True)
        ordered = self._topo_order(graph)
        impl_files: list[str] = []

        # Phase 1: contracts.
        for mod in ordered:
            content = self._module_env().get_template("module/module.h.j2").render(
                name=mod.name,
                namespace=mod.namespace,
                dep_headers=[f'"{m}.h"' for m in mod.depends_on],
                public=mod.public_interface,
            )
            (output_dir / f"{mod.name}.h").write_text(content)

        # Phase 2: impls.
        for mod in ordered:
            bodies = [
                self.behavior._render_for_module(sig, self._module_env())
                for sig in mod.public_interface + mod.internal_functions
            ]
            impl_includes = sorted({inc for sig in mod.public_interface + mod.internal_functions
                                    for inc in sig.call_spec.includes})
            content = self._module_env().get_template("module/module.cpp.j2").render(
                name=mod.name, impl_includes=impl_includes, impl_bodies=bodies,
            )
            (output_dir / f"{mod.name}.cpp").write_text(content)
            impl_files.append(f"{mod.name}.cpp")

        scaffold_context = {
            "project_name": graph.project_name,
            "compile_flags": "-O2 -march=armv8.2-a -fno-inline-small-functions",
            "dependencies": [],
            "dep_headers": [],
            "stages": [],
            "extra_sources": impl_files,
            "config": graph.config,
        }
        self.scaffold.generate(scaffold_context, output_dir)
        self.knob.generate_config(graph.config, output_dir / "config.json")
        return output_dir

    def _topo_order(self, graph: ModuleGraph) -> list[ModuleDescriptor]:
        by_name = {m.name: m for m in graph.modules}
        order: list[ModuleDescriptor] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            for dep in by_name[name].depends_on:
                visit(dep)
            order.append(by_name[name])

        for m in graph.modules:
            visit(m.name)
        return order

    def _module_env(self) -> jinja2.Environment:
        import jinja2
        template_dir = pathlib.Path(__file__).parent / "templates"
        return jinja2.Environment(loader=jinja2.FileSystemLoader(str(template_dir)),
                                 keep_trailing_newline=True)
```

Also add `from codegen.module_graph import ModuleGraph` to the imports at the top of `generator.py` (under the existing `codegen` imports). Add a helper on `BehaviorGenerator`:

```python
# src/codegen/behavior_gen.py — add method to BehaviorGenerator:
    def _render_for_module(self, sig, env) -> str:
        """Render a function's implementation body for module.cpp."""
        stage = {"stage_name": sig.function,
                 "strategies": [{"strategy": sig.self_work.kind or "compute_synthesis",
                                 "synthesis_config": sig.self_work.config}]}
        try:
            return StrategyRegistry.get(sig.self_work.kind).render_def(stage, env) if sig.self_work.kind in StrategyRegistry.available() else ""
        except KeyError:
            return sig.call_spec.statement or f"// {sig.function} body"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/codegen/test_generator.py::test_generate_from_module_graph_emits_module_files -q --no-cov`
Expected: PASS.

- [ ] **Step 6: Run full suite + mypy**

Run: `python -m pytest -q`
Expected: all pass (160 + new). Then `python -m pre_commit run mypy --files src/codegen/generator.py src/codegen/behavior_gen.py` — fix any `--strict` errors (annotate params/returns; avoid `Any` where a concrete type exists).

- [ ] **Step 7: Commit**

```bash
python -m ruff format src/codegen/generator.py src/codegen/behavior_gen.py src/codegen/templates/module/ tests/codegen/test_generator.py
python -m ruff check --fix src/codegen/generator.py src/codegen/behavior_gen.py tests/codegen/test_generator.py
git add src/codegen/generator.py src/codegen/behavior_gen.py src/codegen/templates/module/ tests/codegen/test_generator.py
git commit -m "feat(codegen): emit modular C++ from ModuleGraph (P1)"
```

---

## Self-Review

**1. Spec coverage** — §3.1 IR → Task 1. §3.2 signature reuse → Task 2. §3.3 deterministic recovery + cycle fail-loud → Task 3. §3.4 strategy split (render_decl/render_def) → Tasks 4–5. §3.4 modular codegen + topo build + CMake wiring → Task 6. §3.5 thread_pool passthrough → Task 1 (field present, default None). §5 regression (render split concatenation) → Task 4 test. §6 testing (recovery / IR / codegen / strategy split) → Tasks 1–6. Non-goals (no P2/P3/LLM/compaction/legacy) honored — no code touches the agent-dict `generate()` path or `SkeletonDescriptor`.

**2. Placeholder scan** — Task 3 step 3 contains an `if False else` dead branch that the note instructs the implementer to collapse to the `_make_sig` call only; the implementer must delete the dead expression. This is flagged inline, not hidden. No other TBD/TODO.

**3. Type consistency** — `FunctionSignature` fields (`function`, `namespace`, `call_spec`, `declaration`, `self_work`, `thread_pool`) used consistently in Tasks 1, 3, 6. `ModuleDescriptor` fields (`name`, `namespace`, `public_interface`, `internal_functions`, `depends_on`) consistent across Tasks 1, 3, 6. `render_decl(stage, env) -> str` / `render_def(stage, env) -> str` signatures consistent across Tasks 4, 5, 6. `ModuleGraphBuilder.build(profile, project_name)` consistent across Task 3 test + Task 6 (not directly used there).

Gaps found during review: none requiring a new task. Task 6's `_render_for_module` keys the strategy off `sig.self_work.kind` ("synthesis"/"real_call"/"self_budget"), which is NOT a strategy registry name (the registry holds `compute_synthesis`/`memory_synthesis`/`direct_call`/`mixed`). **Fix inline:** map `sig.self_work.kind` + `archetype` to a strategy name:
- `kind == "real_call"` → `"direct_call"`
- `kind == "synthesis"` and `archetype == "memory"` → `"memory_synthesis"`
- `kind == "synthesis"` otherwise → `"compute_synthesis"`
- fallback → empty body.

This mapping belongs in `_render_for_module`; the implementer must implement it as written above, not call `StrategyRegistry.get(sig.self_work.kind)`.
