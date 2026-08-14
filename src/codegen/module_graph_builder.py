"""Deterministic recovery of a ModuleGraph from a customer Profile.

No LLM. Stacks come from ``Profile.hotspots[].call_path`` (lists of demangled
frame strings). Frames are clustered by namespace (via signature parsing).
Call-path parent->child edges are classified intra-module (same namespace) or
inter-module (cross namespace): inter-module edges populate ``depends_on`` and
mark the child function *public*; functions only called within their own
namespace are *internal*. The result must be a DAG -- cycles fail loud.

Caller tracking is keyed by (namespace, name) -- a bare function name can recur
across namespaces, so per-name tracking would misclassify public/internal.
"""

from itertools import pairwise
from typing import Any

from codegen.call_tree import CallSpec, SelfWork
from codegen.module_graph import FunctionSignature, ModuleDescriptor, ModuleGraph
from codegen.signature import parse_signature
from profile.profile_schema import Profile


class ModuleGraphBuilder:
    """Build a ModuleGraph from a Profile's call paths."""

    def __init__(self, classifier: Any | None = None) -> None:
        # classifier is accepted to mirror CallTreeBuilder's constructor shape;
        # P1 recovery uses signature parsing only. Reserved for future
        # library->namespace hints.
        self.classifier = classifier

    def build(self, profile: Profile, project_name: str = "workload_sim") -> ModuleGraph:
        """Merge profile call paths into a ModuleGraph ready for codegen."""
        sigs: dict[tuple[str, str], FunctionSignature] = {}
        caller_ns: dict[tuple[str, str], set[str]] = {}
        modules: dict[str, ModuleDescriptor] = {}
        edges: set[tuple[str, str]] = set()

        def identity(frame: str) -> tuple[str, str]:
            ps = parse_signature(frame)
            return ps.namespace or "", ps.name

        for hotspot in profile.hotspots:
            if not hotspot.call_path:
                continue
            frames = hotspot.call_path
            for frame in frames:
                ns, name = identity(frame)
                if (ns, name) not in sigs:
                    ps = parse_signature(frame)
                    sigs[(ns, name)] = FunctionSignature(
                        function=name,
                        namespace=ns,
                        call_spec=CallSpec(includes=[], statement=f"{name}()", setup=""),
                        declaration=ps.declaration,
                        self_work=SelfWork(
                            kind="synthesis",
                            archetype="compute",
                            units=max(1, int(hotspot.self_pct)),
                        ),
                    )
                if ns not in modules:
                    modules[ns] = ModuleDescriptor(name=_module_name(ns), namespace=ns)
            for parent, child in pairwise(frames):
                parent_ns, _ = identity(parent)
                child_ns, child_name = identity(child)
                caller_ns.setdefault((child_ns, child_name), set()).add(parent_ns)
                if parent_ns != child_ns:
                    edges.add((parent_ns, child_ns))

        # Classify public vs internal and wire depends_on.
        for (ns, _name), sig in sigs.items():
            mod = modules[ns]
            callers = caller_ns.get((ns, sig.function), set())
            cross_namespace = any(caller != ns for caller in callers)
            target = mod.public_interface if cross_namespace else mod.internal_functions
            target.append(sig)
        for caller, callee in edges:
            modules[caller].depends_on.append(_module_name(callee))

        graph = ModuleGraph(project_name=project_name, modules=list(modules.values()))
        self._fail_on_name_collision(graph)
        self._fail_on_cycle(graph)
        return graph

    def _fail_on_name_collision(self, graph: ModuleGraph) -> None:
        """Two distinct namespaces can share a last segment (``foo::store`` and
        ``bar::store`` both collapse to ``store``), which would silently drop a
        module from codegen (same output filename). P1 fails loud; P2 will
        disambiguate names instead.
        """
        names = [m.name for m in graph.modules]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(
                f"module name collision (ambiguous last namespace segment, "
                f"needs P2 disambiguation): {sorted(dupes)}"
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
    if namespace == "":
        return "main"
    return namespace.rsplit("::", 1)[-1].lower() or "mod"
