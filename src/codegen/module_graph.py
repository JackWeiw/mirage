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
