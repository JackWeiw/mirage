"""Synthesis plan IR: the architect's output, consumed by the codegen
orchestrator (Phase C) and the surgical-iteration tier (Phase D).

A SynthesisTask wraps an existing FunctionSignature (reused, not duplicated) +
the slice of customer signature relevant to synthesizing that function's body.
The SynthesisPlan carries the (possibly LLM-refined) ModuleGraph + one task per
function the orchestrator must materialize. This is the stable contract between
architect and codegen, replacing the prose instruction dict.
"""

from pydantic import BaseModel, Field

from codegen.module_graph import FunctionSignature, ModuleGraph


class SynthesisTask(BaseModel):
    """One unit of synthesis work: produce the .cpp body for one function."""

    module: str
    signature: FunctionSignature
    role: str  # "body_synthesis" (LLM) | "deterministic" (real_call, skips LLM)
    signature_slice: dict[str, object] = Field(default_factory=dict)
    status: str = "pending"  # "pending" | "synthesized" | "failed"
    result: str | None = None  # synthesized .cpp body

    @classmethod
    def from_signature(cls, sig: FunctionSignature, module: str) -> "SynthesisTask":
        """Deterministic task envelope: role inferred from self_work.kind."""
        role = "deterministic" if sig.self_work.kind == "real_call" else "body_synthesis"
        return cls(module=module, signature=sig, role=role)


class SynthesisPlan(BaseModel):
    """The architect's output: a buildable ModuleGraph + per-function tasks."""

    module_graph: ModuleGraph
    tasks: list[SynthesisTask] = Field(default_factory=list)
    source: str = "deterministic"  # "deterministic" | "llm"
    notes: str | None = None
    synthesized_bodies: dict[str, str] = Field(
        default_factory=dict
    )  # module_name -> cached .cpp body (surgical re-synthesis state)

    @classmethod
    def from_graph(cls, graph: ModuleGraph, source: str = "deterministic") -> "SynthesisPlan":
        """Deterministic plan: one task per function across all modules."""
        tasks: list[SynthesisTask] = []
        for mod in graph.modules:
            for sig in [*mod.public_interface, *mod.internal_functions]:
                tasks.append(SynthesisTask.from_signature(sig, mod.name))
        return cls(module_graph=graph, tasks=tasks, source=source)
