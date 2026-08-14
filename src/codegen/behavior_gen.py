"""Generate behavior implementation code using strategy registry."""

import importlib
import pathlib
import pkgutil
from typing import Any

import jinja2

from codegen.call_tree import SkeletonDescriptor, service_node_of
from codegen.module_graph import FunctionSignature
from codegen.skeleton_gen import sanitize_identifier
from codegen.strategies.base import StrategyRegistry


def discover_strategies(package: Any) -> None:
    """Import every submodule of ``package`` to trigger self-registration.

    Skips ``base`` and any ``_``-prefixed private modules. A submodule that
    fails to import propagates the error (fail loud): a broken strategy must
    surface at startup, not as a runtime ``KeyError``.
    """
    for module_info in pkgutil.iter_modules(package.__path__):
        name = module_info.name
        if name == "base" or name.startswith("_"):
            continue
        importlib.import_module(f"{package.__name__}.{name}")


def auto_register() -> None:
    """Import every strategy submodule to trigger its self-registration.

    Discovers submodules of the ``codegen.strategies`` package automatically,
    so adding a strategy is just dropping a module into the package — no import
    line to maintain here.
    """
    import codegen.strategies as _strategies

    discover_strategies(_strategies)


class BehaviorGenerator:
    """Generate Layer 3 behavior implementation code from Behavior Profiles.

    Uses StrategyRegistry to dispatch to the correct strategy implementation.
    New strategies can be added without modifying this class.
    """

    def __init__(self) -> None:
        auto_register()
        template_dir = pathlib.Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

    def generate_stage_file(self, stage: dict[str, Any]) -> tuple[str, str]:
        """Generate a C++ header file for one workflow stage using the registry.

        Raises:
            KeyError: If implementation_strategy is not registered.
        """
        strategy_name = stage["implementation_strategy"]
        strategy = StrategyRegistry.get(strategy_name)
        return strategy.render(stage, self._env)

    def generate_synth_header(
        self, stage_name: str, archetype: str, units: int, working_set_mb: int
    ) -> tuple[str, str]:
        """Render a noinline custom_synth() header for one collapsed custom subtree."""
        template = self._env.get_template("behaviors/custom_synth.cpp.j2")
        content = template.render(
            stage_name=stage_name,
            archetype=archetype,
            iterations=max(1, units or 100),
            working_set_mb=working_set_mb or 64,
        )
        return f"{stage_name}_synth.h", content

    def generate_synth_headers(
        self, desc: SkeletonDescriptor, output_dir: pathlib.Path
    ) -> list[str]:
        """Write a custom_synth header per stage that has a collapsed custom subtree."""
        files: list[str] = []
        service = service_node_of(desc.root)
        working_set_mb = int(desc.config.get("working_set_mb", 64))
        for stage in service.children:
            if stage.node_kind == "custom_synth":
                # A collapsed custom leaf sitting directly under the service.
                method = sanitize_identifier(stage.function)
                filename, content = self.generate_synth_header(
                    method,
                    stage.self_work.archetype,
                    stage.self_work.units,
                    working_set_mb,
                )
                (output_dir / filename).write_text(content)
                files.append(filename)
                continue
            synth = next((c for c in stage.children if c.node_kind == "custom_synth"), None)
            if synth is None:
                continue
            method = sanitize_identifier(stage.function)
            filename, content = self.generate_synth_header(
                method, synth.self_work.archetype, synth.self_work.units, working_set_mb
            )
            (output_dir / filename).write_text(content)
            files.append(filename)
        return files

    def _render_for_module(self, sig: FunctionSignature, env: jinja2.Environment) -> str:
        """Render one function's implementation body for module.cpp.

        Maps the signature's ``self_work`` (kind + archetype) to a strategy
        registry name — *not* ``self_work.kind`` directly, since the registry
        holds ``compute_synthesis``/``memory_synthesis``/``direct_call``/
        ``mixed`` while ``kind`` is ``synthesis``/``real_call``/``self_budget``.
        """
        strategy_name = _strategy_for_sig(sig)
        if strategy_name is None:
            return sig.call_spec.statement or f"// {sig.function} body"
        stage = _stage_for_sig(sig, strategy_name)
        return StrategyRegistry.get(strategy_name).render_def(stage, env)

    def _render_decl_for_module(self, sig: FunctionSignature, env: jinja2.Environment) -> str:
        """Render one public function's prototype for module.h.

        The prototype must match the definition ``_render_for_module`` emits
        (same strategy, same synthesized name) so header and cpp link. We use
        the strategy's ``render_decl`` rather than ``sig.declaration``: the
        latter is the customer's demangled (often namespace-qualified)
        prototype, which is neither the function we synthesize nor valid at
        file scope.
        """
        strategy_name = _strategy_for_sig(sig)
        if strategy_name is None:
            return f"void {sig.function}();"
        stage = _stage_for_sig(sig, strategy_name)
        return StrategyRegistry.get(strategy_name).render_decl(stage, env)


def _strategy_for_sig(sig: FunctionSignature) -> str | None:
    """Map a signature's self_work to a strategy registry name, or None."""
    kind = sig.self_work.kind
    if kind == "real_call":
        return "direct_call"
    if kind == "synthesis":
        if sig.self_work.archetype == "memory":
            return "memory_synthesis"
        return "compute_synthesis"
    return None  # self_budget / unknown -> fall back to a stub body


def _stage_for_sig(sig: FunctionSignature, strategy_name: str) -> dict[str, object]:
    """Build a render-stage dict for a module function."""
    config: dict[str, object] = {
        **sig.self_work.config,
        "archetype": sig.self_work.archetype,
        "iterations": sig.self_work.units,
    }
    stage: dict[str, object] = {
        "stage_name": sig.function,
        "strategies": [{"strategy": strategy_name, "synthesis_config": config}],
    }
    if strategy_name == "direct_call":
        first_strategy = stage["strategies"]
        assert isinstance(first_strategy, list)
        first_strategy[0]["function"] = sig.function
        first_strategy[0]["library"] = ""
        stage["dep_headers"] = list(sig.call_spec.includes)
        stage["call_statement"] = sig.call_spec.statement or f"{sig.function}();"
    return stage
