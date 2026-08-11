"""Layer 1/2 service skeleton generation from a SkeletonDescriptor.

Renders the nested trunk/stage skeleton (noinline to preserve frames in the
flamegraph) and a request-driven main. Per-node self-time budgets are baked
into the stage bodies (real open-source calls + custom synthesis) so the
flamegraph self% mirrors the customer at every level.
"""

from __future__ import annotations

import pathlib
import re
from typing import TYPE_CHECKING, Any

import jinja2

from codegen.call_tree import service_node_of

if TYPE_CHECKING:
    from codegen.call_tree import CallTreeNode, SkeletonDescriptor

_TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"
_IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_]")


def sanitize_identifier(name: str) -> str:
    """Turn a (possibly namespace-qualified) function name into a valid C++ identifier."""
    return _IDENTIFIER_RE.sub("_", name.replace("::", "_"))


class ServiceSkeletonGen:
    """Render the nested trunk/stage skeleton + request-driven main."""

    def __init__(self) -> None:
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
            keep_trailing_newline=True,
        )

    def generate(
        self,
        desc: SkeletonDescriptor,
        output_dir: pathlib.Path,
        synth_files: list[str] | None = None,
    ) -> list[pathlib.Path]:
        """Write service.h, service.cpp, and main.cpp from the descriptor."""
        output_dir.mkdir(parents=True, exist_ok=True)
        service = service_node_of(desc.root)
        service_method = sanitize_identifier(desc.service_node)
        stages: list[dict[str, Any]] = []
        includes: set[str] = set()
        for child in service.children:
            stage_ctx = self._stage_ctx(child)
            stages.append(stage_ctx)
            includes.update(stage_ctx["open_includes"])
        context = {
            "project_name": desc.project_name,
            "service_method": service_method,
            "stages": stages,
            "includes": sorted(includes),
            "synth_files": synth_files or [],
            "thread_count": desc.config.get("thread_count", 8),
        }
        files: list[pathlib.Path] = []
        for template_name, output_name in (
            ("service/service.h.j2", "service.h"),
            ("service/service.cpp.j2", "service.cpp"),
            ("service/main.cpp.j2", "main.cpp"),
        ):
            content = self._env.get_template(template_name).render(**context)
            path = output_dir / output_name
            path.write_text(content)
            files.append(path)
        return files

    def _stage_ctx(self, stage: CallTreeNode) -> dict[str, Any]:
        method = sanitize_identifier(stage.function)
        open_leaves: list[dict[str, Any]] = []
        open_includes: set[str] = set()
        synth: str | None = None
        # An open-source leaf sitting directly under the service (no customer
        # wrapper stage) is itself a "stage": emit its real call directly.
        if stage.node_kind == "open_source_leaf" and stage.self_work.call_spec is not None:
            open_leaves.append(
                {"function": stage.function, "call": stage.self_work.call_spec.statement}
            )
            open_includes.update(stage.self_work.call_spec.includes)
        # A collapsed custom leaf sitting directly under the service is itself
        # a "stage": emit a call to its synthesis function.
        if stage.node_kind == "custom_synth":
            synth = f"{method}_custom_synth"
        for child in stage.children:
            if child.node_kind == "open_source_leaf":
                call = f"/* no call_spec for {child.function} */"
                if child.self_work.call_spec is not None:
                    call = child.self_work.call_spec.statement
                    open_includes.update(child.self_work.call_spec.includes)
                open_leaves.append({"function": child.function, "call": call})
            elif child.node_kind == "custom_synth":
                synth = f"{method}_custom_synth"
        return {
            "method": method,
            "synth": synth,
            "self_pct": stage.self_pct,
            "open_leaves": open_leaves,
            "open_includes": sorted(open_includes),
        }
