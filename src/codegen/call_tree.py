"""Call-tree derivation from customer flamegraph call paths.

CallTreeBuilder merges parsed call paths into a SkeletonDescriptor (nested IR)
that codegen renders into a C++ workload whose flamegraph call-stack structure
mirrors the customer's. Structure is derived deterministically from the
customer's call paths (ground truth); the LLM only fills leaf behavior.

Node identity is the full call path, so the same function under different
parents is distinct. Custom leaf subtrees are collapsed into per-parent
custom_synth nodes; interior nodes (trunk + stage boundaries) are preserved;
open-source leaves become real-call nodes.
"""

from __future__ import annotations

import pathlib
import re
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from codegen.catalog import OpenSourceAPICatalog
    from ingestion.classifier import FunctionClassifier
    from profile.profile_schema import Profile

_ARCHETYPES_PATH = pathlib.Path(__file__).parent.parent / "config" / "behavior_archetypes.yaml"


class CallSpec(BaseModel):
    """A buildable call to an open-source library function."""

    includes: list[str] = Field(default_factory=list)
    statement: str = ""
    setup: str = ""


class SelfWork(BaseModel):
    """How a node realizes its self-time budget.

    kind:
        self_budget - interior node own work (small calibrated loop).
        real_call    - open-source leaf, calls the real library function.
        synthesis    - collapsed custom subtree, synthesized kernel.
    archetype: compute | memory | hash | matmul | sort | branch.
    units: proportional work amount (proportional to customer self_samples);
           ratios are preserved across nodes so flamegraph self% matches.
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
    children: list[CallTreeNode] = Field(default_factory=list)


class SkeletonDescriptor(BaseModel):
    """Nested IR rendered by ServiceSkeletonGen + BehaviorGen."""

    project_name: str
    trunk: list[str]
    service_node: str
    root: CallTreeNode
    config: dict[str, Any] = Field(default_factory=dict)


class ArchetypeInferrer:
    """Infer a behavior archetype from a function name via a YAML keyword map."""

    def __init__(self, config_path: pathlib.Path | None = None) -> None:
        path = config_path or _ARCHETYPES_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        self._rules: list[tuple[str, re.Pattern[str]]] = [
            (
                rule["archetype"],
                re.compile("|".join(re.escape(k) for k in rule["keywords"]), re.IGNORECASE),
            )
            for rule in data.get("archetypes", [])
        ]
        self._default: str = data.get("default_archetype", "compute")

    def infer(self, function_name: str, memory_bound_dominant: bool = False) -> str:
        """Return the archetype for a function name, or a Topdown-grounded default."""
        for archetype, pattern in self._rules:
            if pattern.search(function_name):
                return archetype
        if memory_bound_dominant:
            return "memory"
        return self._default


def _is_stage(node: CallTreeNode) -> bool:
    """A stage is a node whose children are all leaves (open-source / custom synth)."""
    return bool(node.children) and all(
        c.node_kind in ("open_source_leaf", "custom_synth") for c in node.children
    )


def service_node_of(root: CallTreeNode) -> CallTreeNode:
    """Return the node whose children are the business stages (the service entry).

    Descend the single-child trunk chain while the child is neither a leaf nor a
    stage; stop at the divergence point (multiple children) or when the child is
    a stage/leaf. This preserves the service frame even with a single stage.
    """
    node = root
    while True:
        if len(node.children) != 1:
            return node
        child = node.children[0]
        if child.node_kind in ("open_source_leaf", "custom_synth"):
            return node
        if _is_stage(child):
            return node
        node = child


class CallTreeBuilder:
    """Build a SkeletonDescriptor from customer call paths.

    The skeleton shape comes entirely from the customer's call paths; the LLM
    is not involved in structure. Custom leaf subtrees collapse into per-parent
    custom_synth nodes; interior nodes (trunk + stages) are preserved; open-
    source leaves become real-call nodes whose internal subtrees come for free
    from the actual library call.
    """

    def __init__(
        self,
        classifier: FunctionClassifier | None = None,
        catalog: OpenSourceAPICatalog | None = None,
        archetype_inferrer: ArchetypeInferrer | None = None,
    ) -> None:
        from ingestion.classifier import FunctionClassifier

        self.classifier = classifier or FunctionClassifier()
        self.catalog = catalog
        self.archetype_inferrer = archetype_inferrer or ArchetypeInferrer()

    def build(
        self,
        stacks: list[tuple[list[str], int]],
        profile: Profile | None,
        project_name: str = "workload_sim",
    ) -> SkeletonDescriptor:
        """Merge stacks into a SkeletonDescriptor ready for codegen."""
        total = sum(count for _, count in stacks) or 1
        mem_dominant = self._memory_bound_dominant(profile)
        raw_root = self._merge(stacks)
        root = self._to_node(raw_root, total, mem_dominant)
        root = self._collapse(root, total, mem_dominant)
        service = service_node_of(root)
        self._mark_trunk(root, service)
        trunk = self._trunk_list(root, service)
        return SkeletonDescriptor(
            project_name=project_name,
            trunk=trunk,
            service_node=service.function,
            root=root,
            config=self._config(profile),
        )

    # -- merge stacks into a path-keyed raw tree --------------------------
    def _merge(self, stacks: list[tuple[list[str], int]]) -> dict[str, Any]:
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
        # The root is the shortest path (depth 0, single frame).
        return nodes[min(nodes, key=len)]

    def _to_node(self, raw: dict[str, Any], total: int, mem_dominant: bool) -> CallTreeNode:
        func = raw["function"]
        source = raw["source"]
        is_open = source == "open_source"
        is_leaf = not raw["children"]
        self_pct = raw["self_samples"] / total * 100.0
        children = [self._to_node(child, total, mem_dominant) for child in raw["children"].values()]
        call_spec = None
        if is_open and is_leaf and self.catalog is not None:
            spec = self.catalog.lookup(func)
            if spec is not None:
                call_spec = spec
        work_kind = (
            "real_call"
            if (is_open and is_leaf)
            else ("synthesis" if (not is_open and is_leaf) else "self_budget")
        )
        cls, method = self._split_class_method(func)
        kind = (
            "open_source_leaf"
            if (is_open and is_leaf)
            else "stage"
            if not is_leaf
            else "customer_custom_leaf"
        )
        return CallTreeNode(
            function=func,
            node_kind=kind,
            stage_class=cls,
            stage_method=method,
            library=raw["library"],
            self_pct=self_pct,
            self_work=SelfWork(
                kind=work_kind,
                archetype=self.archetype_inferrer.infer(func, mem_dominant),
                units=raw["self_samples"],
                call_spec=call_spec,
            ),
            children=children,
        )

    @staticmethod
    def _split_class_method(func: str) -> tuple[str | None, str | None]:
        if "::" in func:
            head, tail = func.rsplit("::", 1)
            return head, tail
        return None, None

    # -- collapse custom leaf subtrees per parent --------------------------
    def _collapse(self, node: CallTreeNode, total: int, mem_dominant: bool) -> CallTreeNode:
        node.children = [self._collapse(child, total, mem_dominant) for child in node.children]
        custom_leaves = [c for c in node.children if c.node_kind == "customer_custom_leaf"]
        kept = [c for c in node.children if c.node_kind != "customer_custom_leaf"]
        if custom_leaves:
            cls, method = self._split_class_method(node.function)
            synth = CallTreeNode(
                function=f"{node.function or 'stage'}::custom_synth",
                node_kind="custom_synth",
                stage_class=cls,
                stage_method=method,
                self_pct=sum(c.self_pct for c in custom_leaves),
                self_work=SelfWork(
                    kind="synthesis",
                    archetype=self._dominant_archetype(
                        [c.function for c in custom_leaves], mem_dominant
                    ),
                    units=int(sum(c.self_work.units for c in custom_leaves)),
                ),
            )
            kept.append(synth)
        node.children = kept
        return node

    def _dominant_archetype(self, names: list[str], mem_dominant: bool) -> str:
        votes: dict[str, int] = {}
        for name in names:
            arch = self.archetype_inferrer.infer(name, mem_dominant)
            votes[arch] = votes.get(arch, 0) + 1
        if not votes:
            return self.archetype_inferrer.infer("", mem_dominant)
        return max(votes, key=lambda k: votes[k])

    # -- mark the trunk chain (root .. service) ---------------------------
    @staticmethod
    def _mark_trunk(root: CallTreeNode, service: CallTreeNode) -> None:
        """Mark the single-child chain from root to the service node as trunk."""
        node = root
        while True:
            node.node_kind = "trunk"
            if node is service:
                break
            node = node.children[0]

    @staticmethod
    def _trunk_list(root: CallTreeNode, service: CallTreeNode) -> list[str]:
        trunk: list[str] = []
        node = root
        while True:
            trunk.append(node.function)
            if node is service:
                break
            node = node.children[0]
        return trunk

    # -- profile grounding ------------------------------------------------
    @staticmethod
    def _memory_bound_dominant(profile: Profile | None) -> bool:
        if (
            profile is None
            or profile.topdown_l2 is None
            or profile.topdown_l2.backend_bound is None
        ):
            return False
        memory_bound = profile.topdown_l2.backend_bound.memory_bound or 0.0
        core_bound = profile.topdown_l2.backend_bound.core_bound or 0.0
        return memory_bound > core_bound

    @staticmethod
    def _config(profile: Profile | None) -> dict[str, Any]:
        config: dict[str, Any] = {
            "thread_count": 8,
            "qps": 1000,
            "warmup_seconds": 5,
            "measurement_seconds": 60,
        }
        if (
            profile is not None
            and profile.memory is not None
            and profile.memory.working_set_size_mb is not None
        ):
            config["working_set_mb"] = int(profile.memory.working_set_size_mb)
        return config
