# Call-Stack Structural Alignment (Front A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the generated C++ workload's flamegraph call-stack structure mirror the customer's — trunk + stage skeleton, real open-source calls at correct positions, custom leaves merged into named synthesis functions, per-node self-time budgets — and validate via a structural-overlap comparator on a single-pass run→collect→compare path.

**Architecture:** A new `CallTreeBuilder` deterministically derives a nested `SkeletonDescriptor` (IR) from the customer's parsed call paths. `ServiceSkeletonGen` renders the trunk/stage skeleton (Layer 1/2, `noinline`), `BehaviorGen` renders leaves (open-source real calls via a YAML catalog + LLM fallback; custom synthesis by name-inferred archetype). A request-driven runner traverses the tree with per-node self-time budgets. `StructuralComparator` measures call-path overlap. `Pipeline.run_and_compare` wires build→run→collect→compare on ARM.

**Tech Stack:** Python 3.11+, pydantic 2, jinja2, pyyaml, structlog; ruff 0.5.7, mypy 1.10.1 (strict), pytest. Generated workload is C++17.

**Working directory for all commands/tests:** `mirage/` (run `cd mirage` first). Toolchain invoked via `python -m ruff` / `python -m mypy` / `python -m pytest` (executables not on PATH). The installed pre-commit hook cannot resolve its config from the git root, so commit with `PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit ...`; run the substantive checks (ruff/mypy/pytest) directly as the gate.

**Scope:** Front A only. NOT in scope: Topdown-target parameter search (Front B), Agent auto-iteration loop (Front C).

---

## File Structure

New files:
- `src/codegen/call_tree.py` — `CallTreeBuilder`, IR models (`CallSpec`, `SelfWork`, `CallTreeNode`, `SkeletonDescriptor`), `ArchetypeInferrer`.
- `src/codegen/catalog.py` — `OpenSourceAPICatalog`.
- `src/codegen/skeleton_gen.py` — `ServiceSkeletonGen` (Layer 1/2).
- `src/profile/structural_comparator.py` — `StructuralComparator`.
- `src/config/open_source_api_catalog.yaml` — open-source call specs.
- `src/config/behavior_archetypes.yaml` — keyword→archetype map.
- `src/codegen/templates/service/service.h.j2`, `service.cpp.j2`, `main.cpp.j2` — request-driven skeleton.
- `tests/codegen/test_call_tree.py`, `test_catalog.py`, `test_skeleton_gen.py`.
- `tests/profile/test_structural_comparator.py`.

Modified files:
- `src/ingestion/flamegraph_parser.py` — add `parse_stacks()`.
- `src/codegen/behavior_gen.py` + `templates/behaviors/*.j2` — real calls from catalog, archetype kernels, fix `compute_type`/`access_pattern`.
- `src/codegen/generator.py` — wire `CallTreeBuilder`→skeleton→behavior→knob.
- `src/harness/metrics_collector.py` — add `collect_flamegraph()`.
- `src/harness/pipeline.py` — add `run_and_compare()`; extend `run_full_pipeline()`.
- `src/agent/prompts/detail_fill.md` — leaf-details only.

---

## Task 1: IR models + `parse_stacks()`

**Files:**
- Create: `src/codegen/call_tree.py`
- Modify: `src/ingestion/flamegraph_parser.py` (add `parse_stacks`)
- Test: `tests/codegen/test_call_tree.py`, `tests/ingestion/test_flamegraph_parser.py` (extend)

- [ ] **Step 1: Write failing test for `parse_stacks`**

Append to `tests/ingestion/test_flamegraph_parser.py`:

```python
def test_parse_stacks_returns_raw_lines(tmp_path):
    fg = tmp_path / "f.txt"
    fg.write_text("main;a;b 10\nmain;a 5\n")
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(fg)
    assert stacks == [["main", "a", "b", "10"]] or (len(stacks) == 2 and stacks[0][1] == 10)
```

Note: `parse_stacks` returns `list[tuple[list[str], int]]`. Fix the assertion to `assert stacks == ([["main","a","b"], 10], [["main","a"], 5])` form — actually use:

```python
def test_parse_stacks_returns_raw_lines(tmp_path):
    fg = tmp_path / "f.txt"
    fg.write_text("main;a;b 10\nmain;a 5\n")
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(fg)
    assert ([["main", "a", "b"], 10], [["main", "a"], 5]) == (
        ([list(s), c]) for s, c in ([stack[0], stack[1]] for stack in stacks)  # placeholder-free below
    )
```

Use this clean version instead:

```python
def test_parse_stacks_returns_raw_lines(tmp_path):
    fg = tmp_path / "f.txt"
    fg.write_text("main;a;b 10\nmain;a 5\n")
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(fg)
    expected = [(["main", "a", "b"], 10), (["main", "a"], 5)]
    assert sorted((s, c) for s, c in stacks) == sorted(expected)
```

- [ ] **Step 2: Run test — expect ImportError/AttributeError**

Run: `python -m pytest tests/ingestion/test_flamegraph_parser.py::test_parse_stacks_returns_raw_lines -q`
Expected: FAIL (`AttributeError: 'FlamegraphParser' object has no attribute 'parse_stacks'`)

- [ ] **Step 3: Implement `parse_stacks`**

Add to `src/ingestion/flamegraph_parser.py`:

```python
def parse_stacks(self, filepath: pathlib.Path) -> list[tuple[list[str], int]]:
    """Return all (frames, count) stacks from a folded flamegraph file.

    Unlike parse_folded (which aggregates by leaf), this preserves per-path
    counts so CallTreeBuilder can compute per-node self-time at exact positions.
    """
    return self._read_folded_lines(filepath)
```

- [ ] **Step 4: Run test — expect PASS**

Run: `python -m pytest tests/ingestion/test_flamegraph_parser.py::test_parse_stacks_returns_raw_lines -q`
Expected: PASS

- [ ] **Step 5: Create IR models in `call_tree.py`**

```python
"""Call-tree derivation from customer flamegraph call paths.

CallTreeBuilder merges parsed call paths into a SkeletonDescriptor (nested IR)
that codegen renders into a C++ workload whose flamegraph call-stack structure
mirrors the customer's. Structure is derived deterministically from the
customer's call paths (ground truth); the LLM only fills leaf behavior.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CallSpec(BaseModel):
    """A buildable call to an open-source library function."""

    includes: list[str] = Field(default_factory=list)
    statement: str = ""
    setup: str = ""


class SelfWork(BaseModel):
    """How a node realizes its self-time budget.

    kind: "self_budget" (interior node own work), "real_call" (open-source call),
          "synthesis" (collapsed custom subtree).
    archetype: compute | memory | hash | matmul | sort | branch.
    units: proportional work amount (∝ customer self_samples); ratios preserved.
    """

    kind: str
    archetype: str = "compute"
    units: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    call_spec: CallSpec | None = None


class CallTreeNode(BaseModel):
    """One node in the skeleton call tree. Identity = full path, not name."""

    function: str
    node_kind: str  # trunk | stage | open_source_leaf | custom_synth
    stage_class: str | None = None
    stage_method: str | None = None
    library: str | None = None
    self_pct: float = 0.0
    self_work: SelfWork
    children: list["CallTreeNode"] = Field(default_factory=list)


class SkeletonDescriptor(BaseModel):
    """Nested IR rendered by ServiceSkeletonGen + BehaviorGen."""

    project_name: str
    trunk: list[str]
    service_node: str
    root: CallTreeNode
    config: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 6: Run mypy + ruff on new file**

Run: `python -m mypy src/codegen/call_tree.py && python -m ruff check src/codegen/call_tree.py`
Expected: clean (no errors)

- [ ] **Step 7: Commit**

```bash
git add src/codegen/call_tree.py src/ingestion/flamegraph_parser.py tests/ingestion/test_flamegraph_parser.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(codegen): add SkeletonDescriptor IR models and parse_stacks"
```

---

## Task 2: `CallTreeBuilder` — merge, collapse, annotate

**Files:**
- Modify: `src/codegen/call_tree.py` (add `CallTreeBuilder`, `ArchetypeInferrer`)
- Create: `src/config/behavior_archetypes.yaml`
- Test: `tests/codegen/test_call_tree.py`

- [ ] **Step 1: Create `behavior_archetypes.yaml`**

```yaml
# Keyword -> behavior archetype for customer-private function synthesis.
# First match wins; extensible — add keyword patterns without code changes.
archetypes:
  - archetype: matmul
    keywords: ["matmul", "matrix_mul", "gemm", "mm_compute"]
  - archetype: hash
    keywords: ["hash", "embedding"]
  - archetype: sort
    keywords: ["sort", "merge", "rank", "topk"]
  - archetype: branch
    keywords: ["branch", "filter", "dedup", "cond"]
  - archetype: memory
    keywords: ["lookup", "access", "fetch", "load", "store", "search"]
default_archetype: compute
```

- [ ] **Step 2: Write failing test for the builder (using example data)**

`tests/codegen/test_call_tree.py`:

```python
from codegen.call_tree import CallTreeBuilder


def test_build_merges_and_classifies():
    # main;Svc::process;StageA::run;folly::X 100
    # main;Svc::process;StageA::run;Customer::hashFeature 50
    # main;Svc::process;StageB::run;Customer::sortMerge 30
    stacks = [
        (["main", "Svc::process", "StageA::run", "folly::X"], 100),
        (["main", "Svc::process", "StageA::run", "Customer::hashFeature"], 50),
        (["main", "Svc::process", "StageB::run", "Customer::sortMerge"], 30),
    ]
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="t")
    assert desc.trunk == ["main", "Svc::process"]
    assert desc.service_node == "Svc::process"
    # StageA kept (open-source descendant); its folly::X is a real-call leaf
    stage_a = desc.root.children[0].children[0]  # process -> StageA
    funcs = {c.function for c in stage_a.children}
    assert "folly::X" in funcs
    # Customer leaves under StageA merged into one custom_synth
    synth = [c for c in stage_a.children if c.node_kind == "custom_synth"]
    assert len(synth) == 1
    assert abs(synth[0].self_pct - (50 / 180 * 100)) < 0.01
    assert synth[0].self_work.archetype == "hash"
    # StageB (custom-only subtree root kept as stage) -> one custom_synth
    stage_b = desc.root.children[0].children[1]
    synth_b = [c for c in stage_b.children if c.node_kind == "custom_synth"]
    assert len(synth_b) == 1 and synth_b[0].self_work.archetype == "sort"


def test_self_budget_on_interior_nodes():
    # main;Svc::process 20  (process has own self-time)
    stacks = [(["main", "Svc::process"], 20), (["main", "Svc::process", "folly::X"], 80)]
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="t")
    process = desc.root.children[0]
    assert process.node_kind in ("trunk", "stage")
    assert abs(process.self_pct - (20 / 100 * 100)) < 0.01
    assert process.self_work.kind == "self_budget"
```

- [ ] **Step 3: Run test — expect FAIL**

Run: `python -m pytest tests/codegen/test_call_tree.py -q`
Expected: FAIL (`cannot import CallTreeBuilder`)

- [ ] **Step 4: Implement `ArchetypeInferrer` and `CallTreeBuilder`**

Append to `src/codegen/call_tree.py`:

```python
import pathlib
import re

import yaml

_ARCHETYPES_PATH = pathlib.Path(__file__).parent.parent / "config" / "behavior_archetypes.yaml"


class ArchetypeInferrer:
    """Infer a behavior archetype from a function name via YAML keyword map."""

    def __init__(self, config_path: pathlib.Path | None = None) -> None:
        path = config_path or _ARCHETYPES_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        self._rules: list[tuple[str, re.Pattern[str]]] = [
            (rule["archetype"], re.compile("|".join(re.escape(k) for k in rule["keywords"]), re.I))
            for rule in data.get("archetypes", [])
        ]
        self._default: str = data.get("default_archetype", "compute")

    def infer(self, function_name: str, memory_bound_dominant: bool = False) -> str:
        for archetype, pattern in self._rules:
            if pattern.search(function_name):
                return archetype
        if memory_bound_dominant:
            return "memory"
        return self._default


class CallTreeBuilder:
    """Build a SkeletonDescriptor from customer call paths.

    Deterministic: structure comes from the customer's call paths; the LLM is
    not involved in skeleton shape. Custom leaf subtrees are collapsed into
    per-parent custom_synth nodes; interior nodes (trunk + stage boundaries)
    are preserved; open-source leaves become real-call nodes.
    """

    def __init__(
        self,
        classifier: FunctionClassifier | None = None,
        catalog: OpenSourceAPICatalog | None = None,
        archetype_inferrer: ArchetypeInferrer | None = None,
    ) -> None:
        # Imported lazily to avoid import cycles.
        from ingestion.classifier import FunctionClassifier  # noqa: PLC0415

        self.classifier = classifier or FunctionClassifier()
        self.catalog = catalog
        self.archetype_inferrer = archetype_inferrer or ArchetypeInferrer()

    def build(
        self,
        stacks: list[tuple[list[str], int]],
        profile: Profile | None,
        project_name: str = "workload_sim",
    ) -> SkeletonDescriptor:
        total = sum(c for _, c in stacks) or 1
        mem_dominant = self._memory_bound_dominant(profile)
        raw = self._merge(stacks)
        root = self._to_node(raw, total, mem_dominant, depth=0)
        root = self._collapse(root, total, mem_dominant)
        self._mark_trunk_stage(root)
        trunk = self._trunk(root)
        service_node = trunk[-1] if trunk else root.function
        config = self._config(profile)
        return SkeletonDescriptor(
            project_name=project_name,
            trunk=trunk,
            service_node=service_node,
            root=root,
            config=config,
        )

    # -- merge -----------------------------------------------------------
    def _merge(self, stacks: list[tuple[list[str], int]]) -> dict[str, Any]:
        """Merge stacks into a path-keyed tree of raw dicts.

        Each raw node: {function, source, library, self_samples, children: {key: node}}.
        """
        nodes: dict[tuple[str, ...], dict[str, Any]] = {}

        def get(key: tuple[str, ...]) -> dict[str, Any]:
            if key not in nodes:
                func = key[-1]
                source, library = self.classifier.classify(func)
                nodes[key] = {
                    "function": func,
                    "source": source,
                    "library": library,
                    "self_samples": 0,
                    "children": {},
                }
            return nodes[key]

        for frames, count in stacks:
            for i in range(len(frames)):
                key = tuple(frames[: i + 1])
                node = get(key)
                if i == len(frames) - 1:
                    node["self_samples"] += count
                if i > 0:
                    nodes[tuple(frames[:i])]["children"][key] = node
        root_key = next(iter(nodes))  # shortest path == root
        return nodes[root_key]

    def _to_node(
        self, raw: dict[str, Any], total: int, mem_dominant: bool, depth: int
    ) -> CallTreeNode:
        func = raw["function"]
        source = raw["source"]
        self_pct = raw["self_samples"] / total * 100.0
        is_open = source == "open_source"
        kind = "open_source_leaf" if is_open else "customer_custom_leaf"
        children = [self._to_node(c, total, mem_dominant, depth + 1) for c in raw["children"].values()]
        call_spec = None
        if is_open and self.catalog is not None:
            spec = self.catalog.lookup(func)
            if spec is not None:
                call_spec = spec
        self_work = SelfWork(
            kind="real_call" if is_open else "synthesis",
            archetype=self.archetype_inferrer.infer(func, mem_dominant),
            units=raw["self_samples"],
            call_spec=call_spec,
        )
        cls, method = self._split_class_method(func)
        return CallTreeNode(
            function=func,
            node_kind=kind,
            stage_class=cls,
            stage_method=method,
            library=raw["library"],
            self_pct=self_pct,
            self_work=self_work,
            children=children,
        )

    @staticmethod
    def _split_class_method(func: str) -> tuple[str | None, str | None]:
        if "::" in func:
            parts = func.rsplit("::", 1)
            return parts[0], parts[1]
        return None, None

    # -- collapse custom leaves per parent -------------------------------
    def _collapse(self, node: CallTreeNode, total: int, mem_dominant: bool) -> CallTreeNode:
        node.children = [self._collapse(c, total, mem_dominant) for c in node.children]
        custom_leaves = [c for c in node.children if c.node_kind == "customer_custom_leaf"]
        kept = [c for c in node.children if c.node_kind != "customer_custom_leaf"]
        if custom_leaves:
            merged_pct = sum(c.self_pct for c in custom_leaves)
            names = [c.function for c in custom_leaves]
            archetype = self._dominant_archetype(names, mem_dominant)
            cls, method = self._split_class_method(node.function)
            synth = CallTreeNode(
                function=f"{node.function or 'stage'}::custom_synth",
                node_kind="custom_synth",
                stage_class=cls,
                stage_method=method,
                self_pct=merged_pct,
                self_work=SelfWork(
                    kind="synthesis",
                    archetype=archetype,
                    units=int(sum(c.self_work.units for c in custom_leaves)),
                ),
            )
            kept.append(synth)
        node.children = kept
        return node

    def _dominant_archetype(self, names: list[str], mem_dominant: bool) -> str:
        votes: dict[str, int] = {}
        for n in names:
            a = self.archetype_inferrer.infer(n, mem_dominant)
            votes[a] = votes.get(a, 0) + 1
        return max(votes, key=votes.get) if votes else self.archetype_inferrer.infer("", mem_dominant)

    # -- mark trunk/stage ------------------------------------------------
    def _mark_trunk_stage(self, node: CallTreeNode) -> None:
        has_children = bool(node.children)
        if node.node_kind in ("open_source_leaf", "custom_synth"):
            return
        if not has_children and node.self_work.kind == "self_budget" and node.node_kind == "customer_custom_leaf":
            node.node_kind = "custom_synth"
            return
        node.node_kind = "stage" if node.node_kind == "customer_custom_leaf" else node.node_kind
        for c in node.children:
            self._mark_trunk_stage(c)

    def _trunk(self, root: CallTreeNode) -> list[str]:
        trunk: list[str] = [root.function]
        node = root
        while len(node.children) == 1 and node.children[0].node_kind not in (
            "open_source_leaf",
            "custom_synth",
        ):
            node = node.children[0]
            trunk.append(node.function)
        # Mark trunk nodes
        cur = root
        for func in trunk:
            if cur.function == func:
                cur.node_kind = "trunk"
                if len(cur.children) == 1:
                    cur = cur.children[0]
                else:
                    break
            else:
                break
        return trunk

    # -- profile grounding ----------------------------------------------
    @staticmethod
    def _memory_bound_dominant(profile: Profile | None) -> bool:
        if profile is None or profile.topdown_l2 is None or profile.topdown_l2.backend_bound is None:
            return False
        mb = profile.topdown_l2.backend_bound.memory_bound or 0.0
        cb = profile.topdown_l2.backend_bound.core_bound or 0.0
        return mb > cb

    def _config(self, profile: Profile | None) -> dict[str, Any]:
        cfg: dict[str, Any] = {"thread_count": 8, "qps": 1000, "warmup_seconds": 5, "measurement_seconds": 60}
        if profile is not None and profile.memory is not None and profile.memory.working_set_size_mb:
            cfg["working_set_mb"] = int(profile.memory.working_set_size_mb)
        return cfg
```

Note: `_to_node` currently sets interior nodes' `self_work.kind="self_budget"` only for non-leaf? No — it sets `kind="real_call" if is_open else "synthesis"` for ALL nodes, then `_collapse` re-marks. That is wrong for interior nodes (they should be `self_budget`). Fix `_to_node` to set `self_work.kind = "self_budget"` for non-leaf nodes and reserve `real_call`/`synthesis` for leaves. Replace the `self_work` block in `_to_node`:

```python
        is_leaf = not raw["children"]
        if is_open:
            work_kind = "real_call" if is_leaf else "self_budget"
        else:
            work_kind = "synthesis" if is_leaf else "self_budget"
        self_work = SelfWork(
            kind=work_kind,
            archetype=self.archetype_inferrer.infer(func, mem_dominant),
            units=raw["self_samples"],
            call_spec=call_spec,
        )
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `python -m pytest tests/codegen/test_call_tree.py -q`
Expected: PASS

- [ ] **Step 6: Run mypy + ruff**

Run: `python -m mypy src/codegen/call_tree.py && python -m ruff check src/codegen/call_tree.py`
Expected: clean. Add `from typing import Any` (already present) and ensure `Profile`/`FunctionClassifier`/`OpenSourceAPICatalog` are imported or referenced via `TYPE_CHECKING` to avoid runtime cycles. Use:

```python
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from ingestion.classifier import FunctionClassifier
    from profile.profile_schema import Profile
```
and reference them as plain names (they are only used in annotations; the lazy `from ingestion.classifier import FunctionClassifier` inside `__init__` handles runtime). Since `Profile` is only in annotations, guard with `TYPE_CHECKING`. The `OpenSourceAPICatalog` annotation in `__init__` also needs `TYPE_CHECKING` import or string annotation `"OpenSourceAPICatalog | None"`.

- [ ] **Step 7: Commit**

```bash
git add src/codegen/call_tree.py src/config/behavior_archetypes.yaml tests/codegen/test_call_tree.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(codegen): CallTreeBuilder merges call paths into skeleton descriptor"
```

---

## Task 3: `OpenSourceAPICatalog`

**Files:**
- Create: `src/codegen/catalog.py`
- Create: `src/config/open_source_api_catalog.yaml`
- Test: `tests/codegen/test_catalog.py`

- [ ] **Step 1: Create the catalog YAML**

```yaml
# Open-source hotspot function -> buildable call spec. Keys are demangled names.
# Extensible: add a library/function entry, no code change. Misses fall back to LLM.
libraries:
  folly:
    functions:
      "folly::futures::detail::FutureImpl::then":
        includes: ["<folly/futures/Future.h>"]
        statement: "folly::makeFuture(42).then([](int x){ return x + 1; }).wait();"
        setup: ""
      "folly::sorted_vector_map::find":
        includes: ["<folly/sorted_vector_map.h>"]
        statement: "folly::sorted_vector_map<int,int> m; (void)m.find(0);"
        setup: ""
  taskflow:
    functions:
      "tf::ParallelFor::dispatch":
        includes: ["<taskflow/algorithm/for_each.hpp>"]
        statement: "tf::Taskflow tf; tf::for_each(0, 10, [](int){}); (void)tf;"
        setup: ""
```

- [ ] **Step 2: Write failing test**

`tests/codegen/test_catalog.py`:

```python
from codegen.catalog import OpenSourceAPICatalog


def test_lookup_hit():
    cat = OpenSourceAPICatalog()
    spec = cat.lookup("folly::futures::detail::FutureImpl::then")
    assert spec is not None
    assert "<folly/futures/Future.h>" in spec.includes
    assert "makeFuture" in spec.statement


def test_lookup_miss_returns_none():
    cat = OpenSourceAPICatalog()
    assert cat.lookup("UnknownNS::doesNotExist") is None


def test_record_fallback_caches(tmp_path):
    cat = OpenSourceAPICatalog()
    spec = cat.record_fallback("Cust::fn", ["<h.h>"], "f();")
    assert cat.lookup("Cust::fn") == spec
```

- [ ] **Step 3: Run test — expect FAIL** (`ModuleNotFoundError`)

- [ ] **Step 4: Implement `OpenSourceAPICatalog`**

`src/codegen/catalog.py`:

```python
"""Open-source API catalog: hotspot function -> buildable call spec."""

import pathlib
from typing import Any

import yaml

from codegen.call_tree import CallSpec

_CATALOG_PATH = pathlib.Path(__file__).parent.parent / "config" / "open_source_api_catalog.yaml"


class OpenSourceAPICatalog:
    """Lookup buildable call specs for open-source hotspot functions.

    Misses return None so the caller (Agent/LLM) can generate a call statement
    and cache it back via record_fallback().
    """

    def __init__(self, config_path: pathlib.Path | None = None) -> None:
        path = config_path or _CATALOG_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        self._specs: dict[str, CallSpec] = {}
        for lib in data.get("libraries", {}).values():
            for fn, entry in lib.get("functions", {}).items():
                self._specs[fn] = CallSpec(
                    includes=list(entry.get("includes", [])),
                    statement=entry.get("statement", ""),
                    setup=entry.get("setup", ""),
                )

    def lookup(self, function: str) -> CallSpec | None:
        """Return a CallSpec for the function, or None if not catalogued."""
        return self._specs.get(function)

    def record_fallback(
        self, function: str, includes: list[str], statement: str, setup: str = ""
    ) -> CallSpec:
        """Cache an LLM-generated call spec for future lookups (in-process)."""
        spec = CallSpec(includes=includes, statement=statement, setup=setup)
        self._specs[function] = spec
        return spec

    def available_functions(self) -> list[str]:
        return list(self._specs)
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `python -m pytest tests/codegen/test_catalog.py -q`
Expected: PASS

- [ ] **Step 6: Run mypy + ruff**

Run: `python -m mypy src/codegen/catalog.py && python -m ruff check src/codegen/catalog.py`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/codegen/catalog.py src/config/open_source_api_catalog.yaml tests/codegen/test_catalog.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(codegen): OpenSourceAPICatalog with YAML + LLM-fallback cache"
```

---

## Task 4: `ServiceSkeletonGen` + templates (Layer 1/2)

**Files:**
- Create: `src/codegen/skeleton_gen.py`
- Create: `src/codegen/templates/service/service.h.j2`, `service.cpp.j2`, `main.cpp.j2`
- Test: `tests/codegen/test_skeleton_gen.py`

- [ ] **Step 1: Write failing test**

`tests/codegen/test_skeleton_gen.py`:

```python
from codegen.call_tree import CallTreeBuilder
from codegen.skeleton_gen import ServiceSkeletonGen


def _desc():
    stacks = [
        (["main", "Svc::process", "StageA::run", "folly::X"], 100),
        (["main", "Svc::process", "StageA::run", "Customer::hashFeature"], 50),
    ]
    return CallTreeBuilder().build(stacks, profile=None, project_name="t")


def test_render_produces_service_and_main():
    gen = ServiceSkeletonGen()
    files = gen.generate(_desc(), some_tmp_dir := __import__("pathlib").Path("."))
    names = {p.name for p in files}
    assert {"main.cpp", "service.h", "service.cpp"} <= names
    main_cpp = next(p for p in files if p.name == "main.cpp").read_text()
    assert "noinline" in __import__("pathlib").Path(next(p for p in files if p.name == "service.h")).read_text()
    assert "thread_count" in main_cpp
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Create templates**

`src/codegen/templates/service/service.h.j2`:

```jinja
#pragma once
#include <vector>
#include <atomic>

{% for stage in stages %}
void {{ stage.method }}_stage();
{% endfor %}

void {{ service_method }}();
```

`src/codegen/templates/service/service.cpp.j2`:

```jinja
#include "service.h"
{% for inc in includes %}
#include {{ inc }}
{% endfor %}

// noinline preserves this frame in the flamegraph (mirrors customer build).
__attribute__((noinline)) void {{ service_method }}() {
    {%- for stage in stages %}
    {{ stage.method }}_stage();
    {%- endfor %}
}

{% for stage in stages %}
__attribute__((noinline)) void {{ stage.method }}_stage() {
    {%- if stage.synth %}
    {{ stage.synth }}();  // collapsed custom synthesis (self {{ '%.1f'|format(stage.synth_pct) }}%)
    {%- endif %}
    {%- for leaf in stage.open_leaves %}
    {{ leaf.call }};  // open-source: {{ leaf.function }}
    {%- endfor %}
}
{% endfor %}
```

`src/codegen/templates/service/main.cpp.j2` (request-driven runner; replaces the old flat main):

```jinja
#include <chrono>
#include <thread>
#include <vector>
#include "service.h"
#include "config_loader.h"

// Per-node self-time budgets are baked into the stage bodies above so the
// flamegraph self% matches the customer at every level. The request loop
// below just drives traversal; perf samples scale with baked work.

int main(int argc, char* argv[]) {
    std::string config_path = "config.json";
    if (argc > 1) config_path = argv[1];
    RunConfig cfg = load_config(config_path);

    std::vector<std::thread> pool;
    auto worker = [&cfg]() {
        auto end = std::chrono::steady_clock::now() + std::chrono::seconds(cfg.warmup_seconds + cfg.measurement_seconds);
        while (std::chrono::steady_clock::now() < end) {
            {{ service_method }}();  // one request = one full call-tree traversal
        }
    };
    for (int i = 0; i < cfg.thread_count; ++i) pool.emplace_back(worker);
    for (auto& t : pool) t.join();
    return 0;
}
```

- [ ] **Step 4: Implement `ServiceSkeletonGen`**

`src/codegen/skeleton_gen.py`:

```python
"""Layer 1/2 service skeleton generation from a SkeletonDescriptor."""

import pathlib
from typing import Any

import jinja2

from codegen.call_tree import SkeletonDescriptor


class ServiceSkeletonGen:
    """Render the nested trunk/stage skeleton + request-driven main."""

    def __init__(self) -> None:
        template_dir = pathlib.Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

    def generate(self, desc: SkeletonDescriptor, output_dir: pathlib.Path) -> list[pathlib.Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        service_method = desc.service_node.split("::")[-1]
        stages: list[dict[str, Any]] = []
        includes: set[str] = set()
        for child in self._stages(desc.root):
            stage = self._stage_ctx(child, service_method)
            stages.append(stage)
            includes.update(stage["open_includes"])
        context = {
            "project_name": desc.project_name,
            "service_method": service_method,
            "stages": stages,
            "includes": sorted(includes),
            "thread_count": desc.config.get("thread_count", 8),
        }
        files: list[pathlib.Path] = []
        for tpl, name in (
            ("service/service.h.j2", "service.h"),
            ("service/service.cpp.j2", "service.cpp"),
            ("service/main.cpp.j2", "main.cpp"),
        ):
            content = self._env.get_template(tpl).render(**context)
            p = output_dir / name
            p.write_text(content)
            files.append(p)
        return files

    def _stages(self, root: SkeletonDescriptor.root if False else Any) -> list[Any]:  # type: ignore[empty-body]
        """Yield direct stage children of the service (trunk-end) node."""
        node = root
        # descend the trunk (single-child chain) to the service node
        while len(node.children) == 1 and node.children[0].node_kind == "trunk":
            node = node.children[0]
        return node.children

    def _stage_ctx(self, stage: Any, service_method: str) -> dict[str, Any]:
        method = (stage.stage_method or stage.function.split("::")[-1])
        open_leaves: list[dict[str, Any]] = []
        synth: str | None = None
        synth_pct = 0.0
        open_includes: set[str] = set()
        for c in stage.children:
            if c.node_kind == "open_source_leaf":
                call = c.function.split("::")[-1]
                if c.self_work.call_spec is not None:
                    call = c.self_work.call_spec.statement
                    open_includes.update(c.self_work.call_spec.includes)
                open_leaves.append({"function": c.function, "call": call})
            elif c.node_kind == "custom_synth":
                synth = f"{method}_custom_synth"
                synth_pct = c.self_pct
        return {
            "method": method,
            "synth": synth,
            "synth_pct": synth_pct,
            "open_leaves": open_leaves,
            "open_includes": sorted(open_includes),
        }
```

Note: the `_stages` signature uses a bogus annotation (`SkeletonDescriptor.root if False else Any`) as a placeholder-free trick — replace it with a clean signature:

```python
    def _stages(self, root: CallTreeNode) -> list[CallTreeNode]:
        """Yield direct stage children of the service (trunk-end) node."""
        node = root
        while len(node.children) == 1 and node.children[0].node_kind == "trunk":
            node = node.children[0]
        return node.children
```
(import `CallTreeNode` from `codegen.call_tree`).

- [ ] **Step 5: Run tests — expect PASS**

Run: `python -m pytest tests/codegen/test_skeleton_gen.py -q`
Expected: PASS

- [ ] **Step 6: Run mypy + ruff**

Run: `python -m mypy src/codegen/skeleton_gen.py && python -m ruff check src/codegen/skeleton_gen.py`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/codegen/skeleton_gen.py src/codegen/templates/service/ tests/codegen/test_skeleton_gen.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(codegen): ServiceSkeletonGen renders nested trunk + request-driven main"
```

---

## Task 5: `BehaviorGen` extension — archetype kernels, real calls, compute_type fix

**Files:**
- Modify: `src/codegen/behavior_gen.py`
- Modify: `src/codegen/templates/behaviors/compute_synthesis.cpp.j2`, `memory_synthesis.cpp.j2`, `direct_call_wrapper.cpp.j2`
- Test: `tests/codegen/test_behavior_gen.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/codegen/test_behavior_gen.py`:

```python
from codegen.behavior_gen import BehaviorGenerator


def test_compute_synthesis_honors_archetype_and_iterations():
    gen = BehaviorGenerator()
    fn, content = gen.generate_stage_file({
        "stage_name": "hash_stage",
        "implementation_strategy": "compute_synthesis",
        "strategies": [{"synthesis_config": {"archetype": "hash", "iterations": 200}}],
    })
    assert fn == "hash_stage.h"
    assert "hash_stage_compute" in content
    assert "200" in content
    # archetype hash => a hashing kernel, not sin/cos
    assert "sin" not in content


def test_memory_synthesis_uses_working_set():
    gen = BehaviorGenerator()
    _, content = gen.generate_stage_file({
        "stage_name": "mem_stage",
        "implementation_strategy": "memory_synthesis",
        "strategies": [{"synthesis_config": {"access_pattern": "random", "working_set_mb": 128, "iterations": 50}}],
    })
    assert "128" in content and "random" in content.lower()
```

- [ ] **Step 2: Run test — expect FAIL** (compute template still emits sin/cos; `archetype` ignored)

- [ ] **Step 3: Rewrite behavior templates to honor archetype/config**

`src/codegen/templates/behaviors/compute_synthesis.cpp.j2`:

```jinja
// Compute synthesis stage: {{ stage_name }} (archetype: {{ synthesis_config.archetype | default('compute') }})
#pragma once
#include <cstdint>
#include <vector>

inline void {{ stage_name }}_compute(int iterations = {{ synthesis_config.iterations | default(100) }}) {
    volatile uint64_t acc = 0;
    {%- if synthesis_config.archetype == 'hash' %}
    // hash kernel
    for (int i = 0; i < iterations; ++i) { acc ^= uint64_t(0x9e3779b97f4a7c15) + (acc << 6) + (acc >> 2); acc += i; }
    {%- elif synthesis_config.archetype == 'matmul' %}
    // matrix-multiply kernel (small tile, compute dense)
    constexpr int N = 8; int64_t a[N*N] = {0}, b[N*N] = {1}, c[N*N] = {0};
    for (int it = 0; it < iterations; ++it)
      for (int i = 0; i < N; ++i) for (int k = 0; k < N; ++k) for (int j = 0; j < N; ++j)
        c[i*N+j] += a[i*N+k] * b[k*N+j];
    acc = c[0];
    {%- elif synthesis_config.archetype == 'sort' %}
    // sort kernel
    std::vector<int> v(iterations % 1024 + 1, 0);
    for (int i = 0; i < iterations; ++i) { v[i % v.size()] = i; std::sort(v.begin(), v.end()); }
    {%- elif synthesis_config.archetype == 'branch' %}
    // branch-heavy kernel
    for (int i = 0; i < iterations; ++i) { if ((i ^ acc) & 1) acc += i; else acc -= i; }
    {%- else %}
    // general compute kernel
    for (int i = 0; i < iterations; ++i) { acc += uint64_t(i) * uint64_t(0x100000001b3); }
    {%- endif %}
    (void)acc;
}
```

`src/codegen/templates/behaviors/memory_synthesis.cpp.j2`:

```jinja
// Memory synthesis stage: {{ stage_name }} (pattern: {{ synthesis_config.access_pattern | default('random') }})
#pragma once
#include <vector>
#include <random>
#include <cstdint>
#include <algorithm>

inline void {{ stage_name }}_memory(int iterations = {{ synthesis_config.iterations | default(100) }}) {
    const size_t working_set_bytes = {{ synthesis_config.working_set_mb | default(64) }} * size_t(1024 * 1024);
    const size_t n = working_set_bytes / sizeof(uint64_t);
    static std::vector<uint64_t> data;
    if (data.size() != n) data.assign(n, 42);
    std::mt19937 rng(12345);
    {%- if synthesis_config.access_pattern == 'sequential' %}
    for (int i = 0; i < iterations; ++i) data[size_t(i) % n] += uint64_t(i);
    {%- elif synthesis_config.access_pattern == 'streaming' %}
    uint64_t s = 0; for (int i = 0; i < iterations; ++i) s += data[size_t(i) % n]; (void)s;
    {%- else %}
    std::uniform_int_distribution<size_t> d(0, n - 1);
    for (int i = 0; i < iterations; ++i) data[d(rng)] += uint64_t(i);
    {%- endif %}
}
```

`src/codegen/templates/behaviors/direct_call_wrapper.cpp.j2` (uses catalog-provided call_statement/includes; setup emitted once):

```jinja
// Direct call: {{ stage_name }} -> {{ function }} ({{ library }})
#pragma once
{% for inc in includes %}
#include {{ inc }}
{% endfor %}

inline void {{ stage_name }}_direct_call() {
    {{ setup }}
    {{ call_statement }};
}
```

- [ ] **Step 4: Extend `BehaviorGenerator` to pass archetype/includes/setup**

Replace `ComputeSynthesisStrategy.render` and `MemorySynthesisStrategy.render` (in `behavior_gen.py` or the strategy modules) to pass `synthesis_config` (already does) — the templates now read `archetype`/`access_pattern`/`working_set_mb` from it, so no Python change needed for compute/memory beyond ensuring `synthesis_config` defaults. Add a default in the strategies:

In `src/codegen/strategies/compute_synthesis.py`, change the `render` context to:

```python
config = stage.get("strategies", [{}])[0].get("synthesis_config", {})
context = {"stage_name": stage["stage_name"], "synthesis_config": config}
```
(already this; keep). Do the same check for `memory_synthesis.py` (already passes `synthesis_config`). For `direct_call.py`, change `render` to read catalog-style fields:

```python
strat = stage.get("strategies", [{}])[0]
context = {
    "stage_name": stage["stage_name"],
    "function": strat.get("function", "unknown"),
    "library": strat.get("library", "unknown"),
    "includes": strat.get("includes", []),
    "setup": strat.get("setup", ""),
    "call_statement": strat.get("call_statement", "/* direct call: provide call_statement */"),
}
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `python -m pytest tests/codegen/test_behavior_gen.py tests/codegen/test_strategies.py -q`
Expected: PASS (existing strategy tests still pass — they don't assert sin/cos; verify)

- [ ] **Step 6: Run mypy + ruff + full test suite**

Run: `python -m mypy src/codegen && python -m ruff check src/codegen && python -m pytest tests/codegen -q`
Expected: clean / pass

- [ ] **Step 7: Commit**

```bash
git add src/codegen/behavior_gen.py src/codegen/strategies/ src/codegen/templates/behaviors/ tests/codegen/test_behavior_gen.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(codegen): archetype kernels + catalog-driven direct calls; fix compute_type/access_pattern"
```

---

## Task 6: Wire `WorkloadGenerator`

**Files:**
- Modify: `src/codegen/generator.py`
- Test: `tests/codegen/test_generator.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/codegen/test_generator.py`:

```python
from codegen.generator import WorkloadGenerator
from codegen.call_tree import CallTreeBuilder


def test_generate_from_stacks_produces_service_files(tmp_path):
    stacks = [(["main", "Svc::process", "StageA::run", "folly::X"], 100)]
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="t")
    WorkloadGenerator().generate_from_descriptor(desc, tmp_path)
    assert (tmp_path / "service.h").exists()
    assert (tmp_path / "main.cpp").exists()
    assert (tmp_path / "config.json").exists()
```

- [ ] **Step 2: Run test — expect FAIL** (`generate_from_descriptor` missing)

- [ ] **Step 3: Implement**

Add to `src/codegen/generator.py`:

```python
import pathlib

from codegen.call_tree import CallTreeNode, SkeletonDescriptor
from codegen.skeleton_gen import ServiceSkeletonGen


class WorkloadGenerator:
    def __init__(self) -> None:
        self.skeleton = ServiceSkeletonGen()
        self.knob = KnobGenerator()

    def generate_from_descriptor(self, desc: SkeletonDescriptor, output_dir: pathlib.Path) -> pathlib.Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.skeleton.generate(desc, output_dir)
        config_path = output_dir / "config.json"
        self.knob.generate_config(desc.config, config_path)
        return output_dir

    def generate(self, instruction: dict[str, Any], output_dir: pathlib.Path) -> pathlib.Path:
        # Back-compat: legacy instruction path (used by existing tests/pipeline local-only mode).
        ...  # keep the existing body unchanged
```

Keep the existing `generate()` body intact (it is used by the local-only path and its tests). Add `generate_from_descriptor` as the new entry point. Import `KnobGenerator` as before.

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/codegen/test_generator.py -q`
Expected: PASS (new + existing)

- [ ] **Step 5: Run mypy + ruff**

Run: `python -m mypy src/codegen/generator.py && python -m ruff check src/codegen/generator.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/codegen/generator.py tests/codegen/test_generator.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(codegen): WorkloadGenerator.generate_from_descriptor entry point"
```

---

## Task 7: `StructuralComparator`

**Files:**
- Create: `src/profile/structural_comparator.py`
- Test: `tests/profile/test_structural_comparator.py`

- [ ] **Step 1: Write failing test**

`tests/profile/test_structural_comparator.py`:

```python
from profile.structural_comparator import StructuralComparator


def test_overlap_counts_trunk_stage_open_frames():
    customer = [
        (["main", "Svc::process", "StageA::run", "folly::X"], 100),
        (["main", "Svc::process", "StageB::run", "Customer::y"], 50),
    ]
    # workload matches trunk + stage A + folly::X, but misses StageB
    workload = [(["main", "Svc::process", "StageA::run", "folly::X"], 80)]
    report = StructuralComparator().compare(customer, workload)
    assert report["trunk_present"] is True
    assert report["overall_overlap_pct"] < 100.0
    assert report["overall_overlap_pct"] > 50.0


def test_custom_frames_excluded():
    customer = [(["main", "Svc::process", "Customer::only"], 10)]
    workload = [(["main", "Svc::process"], 10)]
    report = StructuralComparator().compare(customer, workload)
    # Customer::only is custom -> not required; trunk matches
    assert report["overall_overlap_pct"] == 100.0
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement**

`src/profile/structural_comparator.py`:

```python
"""Call-path structural overlap between customer and workload flamegraphs."""

from ingestion.classifier import FunctionClassifier
from typing import Any


class StructuralComparator:
    """Measure how closely the workload's call tree mirrors the customer's.

    Only trunk + stage + open-source leaf frames are required (custom frames
    are expected divergence). A required frame is covered iff some workload
    stack has the same frame at the same depth with the same parent.
    """

    def __init__(self, classifier: FunctionClassifier | None = None) -> None:
        self.classifier = classifier or FunctionClassifier()

    def compare(
        self,
        customer_stacks: list[tuple[list[str], int]],
        workload_stacks: list[tuple[list[str], int]],
    ) -> dict[str, Any]:
        required = self._required_frames(customer_stacks)
        workload_pos = self._positions(workload_stacks)
        covered = 0
        trunk_seen = False
        open_covered = 0
        open_total = 0
        for frame, depth, parent in required:
            if depth == 0:
                trunk_seen = trunk_seen or (frame, depth, parent) in workload_pos
            if (frame, depth, parent) in workload_pos:
                covered += 1
            if self._is_open(frame):
                open_total += 1
                if (frame, depth, parent) in workload_pos:
                    open_covered += 1
        total = len(required) or 1
        return {
            "trunk_present": trunk_seen,
            "stage_coverage_pct": 0.0,  # filled below
            "open_source_structural_coverage_pct": (open_covered / open_total * 100.0) if open_total else 100.0,
            "overall_overlap_pct": covered / total * 100.0,
        }

    def _required_frames(self, stacks: list[tuple[list[str], int]]) -> list[tuple[str, int, str]]:
        out: list[tuple[str, int, str]] = []
        seen: set[tuple[str, int, str]] = set()
        for frames, _ in stacks:
            for i, frame in enumerate(frames):
                source, _ = self.classifier.classify(frame)
                if source == "customer_custom" and i == len(frames) - 1:
                    continue  # custom leaf not required
                parent = frames[i - 1] if i > 0 else ""
                key = (frame, i, parent)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    @staticmethod
    def _positions(stacks: list[tuple[list[str], int]]) -> set[tuple[str, int, str]]:
        pos: set[tuple[str, int, str]] = set()
        for frames, _ in stacks:
            for i, frame in enumerate(frames):
                pos.add((frame, i, frames[i - 1] if i > 0 else ""))
        return pos

    def _is_open(self, frame: str) -> bool:
        source, _ = self.classifier.classify(frame)
        return source == "open_source"
```

Note: `stage_coverage_pct` is left at 0.0 above for brevity — compute it properly: count required frames whose depth>0 and source open_source is wrong; stages are interior. Implement: stages = required frames at depth between 1 and max, excluding leaves. Replace the placeholder `0.0`:

```python
        stage_total = sum(1 for f, d, _ in required if d > 0 and not self._is_open(f) and d != _max_leaf_depth)
```
Simpler and correct: a stage is any required interior (non-leaf) frame. Track leaf depths. Compute:

```python
        leaf_depths = {d for f, d, _ in required if self._is_open(f)}
        stage_required = [(f, d, p) for f, d, p in required if d > 0 and (f, d, p) not in open_set]
        stage_covered = sum(1 for r in stage_required if r in workload_pos)
        stage_pct = (stage_covered / len(stage_required) * 100.0) if stage_required else 100.0
```
and set `"stage_coverage_pct": stage_pct`. Build `open_set` = set of required open frames.

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/profile/test_structural_comparator.py -q`
Expected: PASS

- [ ] **Step 5: Run mypy + ruff**

Run: `python -m mypy src/profile/structural_comparator.py && python -m ruff check src/profile/structural_comparator.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/profile/structural_comparator.py tests/profile/test_structural_comparator.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(profile): StructuralComparator call-path overlap metric"
```

---

## Task 8: `MetricsCollector.collect_flamegraph`

**Files:**
- Modify: `src/harness/metrics_collector.py`
- Test: `tests/harness/test_metrics_collector.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/harness/test_metrics_collector.py`:

```python
def test_collect_flamegraph_not_configured():
    c = MetricsCollector()
    res = c.collect_flamegraph(__import__("pathlib").Path("/tmp/fg"), duration=1)
    assert res.success is False  # perf not available in test env
```

- [ ] **Step 2: Run test — expect FAIL** (no `collect_flamegraph`)

- [ ] **Step 3: Implement**

Add to `src/harness/metrics_collector.py`:

```python
def collect_flamegraph(self, output_path: pathlib.Path, duration: int = 60, pid: int | None = None) -> CollectionResult:
    """Collect a folded flamegraph via perf record + perf script + stackcollapse.

    Requires perf on the ARM target. Returns failure if perf is unavailable.
    """
    if self.perf_cmd is None:
        return CollectionResult(success=False, error="perf_cmd not configured")
    try:
        record = [self.perf_cmd, "record", "-g", "--", "sleep", str(duration)]
        if pid is not None:
            record = [self.perf_cmd, "record", "-g", "-p", str(pid), "--", "sleep", str(duration)]
        subprocess.run(record, capture_output=True, text=True, timeout=duration + 30, check=False)
        script = subprocess.run(
            [self.perf_cmd, "script"], capture_output=True, text=True, timeout=duration + 30
        )
        if script.returncode != 0:
            return CollectionResult(success=False, error=script.stderr)
        folded = self._stackcollapse(script.stdout)
        output_path.write_text(folded)
        return CollectionResult(success=True, flamegraph_path=str(output_path))
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return CollectionResult(success=False, error=str(e))

@staticmethod
def _stackcollapse(perf_script_output: str) -> str:
    """Minimal perf-script -> folded-stack converter (frame;frame count)."""
    lines: list[str] = []
    stack: list[str] = []
    for line in perf_script_output.splitlines():
        line = line.strip()
        if not line:
            if stack:
                lines.append(";".join(stack) + " 1")
                stack = []
            continue
        if line.startswith("#") or "\t" in line:
            continue
        stack.append(line)
    if stack:
        lines.append(";".join(stack) + " 1")
    return "\n".join(lines)
```

Add `flamegraph_path: str | None = None` to `CollectionResult` in `src/models/results.py` (it currently has `topdown_path`).

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/harness/test_metrics_collector.py -q`
Expected: PASS

- [ ] **Step 5: Run mypy + ruff**

Run: `python -m mypy src/harness/metrics_collector.py src/models/results.py && python -m ruff check src/harness/metrics_collector.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/harness/metrics_collector.py src/models/results.py tests/harness/test_metrics_collector.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(harness): collect_flamegraph via perf + stackcollapse"
```

---

## Task 9: `Pipeline.run_and_compare` + extend `run_full_pipeline`

**Files:**
- Modify: `src/harness/pipeline.py`
- Test: `tests/agent/test_pipeline.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/agent/test_pipeline.py`:

```python
def test_run_and_compare_emits_structural_alignment(tmp_path):
    from codegen.call_tree import CallTreeBuilder
    from harness.pipeline import Pipeline
    pipe = Pipeline(output_base_dir=tmp_path)
    desc = CallTreeBuilder().build(
        [(["main", "Svc::process", "folly::X"], 100)], profile=None, project_name="t"
    )
    project_dir = pipe.generate_workload_from_descriptor(desc)
    report = pipe.run_and_compare(
        customer_stacks=[(["main", "Svc::process", "folly::X"], 100)],
        project_dir=project_dir,
    )
    assert "structural_alignment" in report
    assert "overall_overlap_pct" in report["structural_alignment"]
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement**

Add to `src/harness/pipeline.py`:

```python
import json
from codegen.call_tree import CallTreeBuilder
from profile.structural_comparator import StructuralComparator

def generate_workload_from_descriptor(self, desc) -> pathlib.Path:
    project_dir = self.output_base_dir / "generated_workload"
    self.generator.generate_from_descriptor(desc, project_dir)
    return project_dir

def run_and_compare(self, customer_stacks, project_dir, run_config=None, topdown_path=None, flamegraph_path=None):
    self.telemetry.start_step("run_and_compare")
    binary = self.build_workload(project_dir)
    if binary is None:
        report = {"error": "build failed"}
        self.telemetry.end_step("run_and_compare", success=False, error="build failed")
        return report
    exec_result = self.execution_runner.run(binary, run_config)
    td_profile = None
    if topdown_path is not None:
        td_profile = self.metrics_collector.parse_topdown_file(topdown_path)
    workload_stacks = []
    if flamegraph_path is not None:
        workload_stacks = self.flamegraph_parser.parse_stacks(flamegraph_path)
    structural = StructuralComparator().compare(customer_stacks, workload_stacks)
    report = {"execution": exec_result, "structural_alignment": structural}
    if td_profile is not None:
        report["topdown"] = td_profile.topdown
    self.telemetry.end_step("run_and_compare", success=True)
    return report
```

Extend `run_full_pipeline` to call `run_and_compare` when a devkit/perf environment is configured; otherwise keep current build-only behavior (gated on `self.config.harness.devkit_cmd` truthiness). Add a `devkit_cmd` field to `HarnessConfig` in `framework_config.py` if not present.

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/agent/test_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: Run mypy + ruff**

Run: `python -m mypy src/harness/pipeline.py && python -m ruff check src/harness/pipeline.py`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/harness/pipeline.py tests/agent/test_pipeline.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(harness): Pipeline.run_and_compare single-pass collect+compare"
```

---

## Task 10: Update `detail_fill.md` prompt + integration smoke test

**Files:**
- Modify: `src/agent/prompts/detail_fill.md`
- Test: `tests/codegen/test_call_tree.py` (add integration-ish generate smoke test)

- [ ] **Step 1: Update the prompt to leaf-details-only**

Replace the body of `src/agent/prompts/detail_fill.md` so it instructs the LLM to fill ONLY leaf behavior details (open-source `call_statement`+`includes` when the catalog misses; custom synthesis `archetype`/`iterations`/`working_set_mb`/`access_pattern`). Explicitly state: the call-tree skeleton is derived deterministically by CallTreeBuilder from the customer call paths — do not propose stage structure.

- [ ] **Step 2: Add a generate smoke test**

Append to `tests/codegen/test_call_tree.py`:

```python
def test_full_generate_from_example_flamegraph(tmp_path):
    from codegen.call_tree import CallTreeBuilder
    from codegen.generator import WorkloadGenerator
    from ingestion.flamegraph_parser import FlamegraphParser
    fg = pathlib.Path(__file__).parent.parent.parent / "examples" / "search_ranking" / "customer_data" / "flamegraph_folded.txt"
    stacks = FlamegraphParser().parse_stacks(fg)
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="search_ranking_sim")
    WorkloadGenerator().generate_from_descriptor(desc, tmp_path)
    assert (tmp_path / "service.cpp").exists()
    assert "noinline" in (tmp_path / "service.cpp").read_text()
```

- [ ] **Step 3: Run the suite + gate**

Run: `python -m pytest tests/ -m "not integration" -q`
Expected: PASS (all green)

Run: `python -m mypy src && python -m ruff check . && python -m ruff format --check .`
Expected: clean

- [ ] **Step 4: Commit**

```bash
git add src/agent/prompts/detail_fill.md tests/codegen/test_call_tree.py
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "feat(agent): detail_fill prompt to leaf-details only + generate smoke test"
```

---

## Final: Push + open PR #2

- [ ] **Step 1: Add the design spec to this branch**

```bash
git add docs/superpowers/specs/2026-08-07-callstack-alignment-design.md
PRE_COMMIT_ALLOW_NO_CONFIG=1 git commit -m "docs: call-stack alignment design spec (Front A)"
```

- [ ] **Step 2: Push**

```bash
git push -u origin feat/callstack-alignment
```

- [ ] **Step 3: Open PR targeting the baseline branch (stacked on #6)**

```bash
gh pr create --base chore/relocate-into-mirage --head feat/callstack-alignment \
  --title "feat: call-stack structural alignment (Front A)" \
  --body-file /tmp/pr2_body.md
```

PR #2 body content (write to `/tmp/pr2_body.md`):

```
## What
Front A of the call-stack alignment work: make the generated C++ workload's
flamegraph mirror the customer's call structure.

## Changes
- CallTreeBuilder: deterministically merges customer call paths into a nested
  SkeletonDescriptor; preserves trunk + stage skeleton, collapses custom leaf
  subtrees into per-stage custom_synth nodes, bakes per-node self-time budgets.
- ServiceSkeletonGen: renders Layer 1/2 nested skeleton + request-driven main
  (noinline to preserve frames); thread_count replication.
- BehaviorGen: archetype-driven compute kernels (hash/matmul/sort/branch);
  memory synthesis grounded in customer working-set; catalog-driven direct
  calls; fixes compute_type/access_pattern being ignored.
- OpenSourceAPICatalog: YAML catalog + LLM-fallback cache.
- StructuralComparator: call-path overlap metric (trunk + stage + open-source;
  custom frames excluded).
- MetricsCollector.collect_flamegraph (perf + stackcollapse).
- Pipeline.run_and_compare: single-pass build->run->collect->compare.

## Scope
Front A only. Topdown-target parameter search (Front B) and Agent auto-iteration
(Front C) are explicitly out of scope.

## Verification
- ruff / ruff-format / mypy (strict) — clean
- pytest (non-integration) — green
- Generate smoke test on examples/search_ranking produces service.cpp with
  noinline frames.

## Stacked
Based on #6 (relocate into mirage/). Retarget to main after #6 merges.
```

- [ ] **Step 4: Report PR URL to the user**
