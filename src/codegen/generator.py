"""Orchestrates the full code generation pipeline: scaffold -> behavior -> knob."""

import pathlib
from typing import Any

from codegen.behavior_gen import BehaviorGenerator
from codegen.call_tree import CallTreeNode, SkeletonDescriptor
from codegen.catalog import OpenSourceAPICatalog
from codegen.knob_gen import KnobGenerator
from codegen.scaffold_gen import ScaffoldGenerator
from codegen.skeleton_gen import ServiceSkeletonGen


class WorkloadGenerator:
    """Orchestrate full workload project generation from generation instruction.

    Combines:
    - ScaffoldGenerator (Layer 0-1: project structure)
    - BehaviorGenerator (Layer 3: stage implementations)
    - KnobGenerator (Layer 4: runtime config)
    """

    def __init__(self) -> None:
        self.scaffold = ScaffoldGenerator()
        self.skeleton = ServiceSkeletonGen()
        self.behavior = BehaviorGenerator()
        self.knob = KnobGenerator()
        self.catalog = OpenSourceAPICatalog()

    def generate_from_descriptor(
        self, desc: SkeletonDescriptor, output_dir: pathlib.Path
    ) -> pathlib.Path:
        """Generate a project from a SkeletonDescriptor (call-tree-driven path).

        Renders behavior synth headers, then the scaffold (CMakeLists,
        config_loader, config.json, flat main), then the service skeleton
        (service.h, service.cpp, request-driven main overwriting the flat
        one), then overrides config.json with knob defaults.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        synth_files = self.behavior.generate_synth_headers(desc, output_dir)
        deps, dep_headers = self._dependencies(desc)
        scaffold_context = {
            "project_name": desc.project_name,
            "compile_flags": "-O2 -march=armv8.2-a -fno-inline-small-functions",
            "dependencies": deps,
            "dep_headers": dep_headers,
            "stages": [],
            "extra_sources": ["service.cpp"],
            "config": desc.config,
        }
        self.scaffold.generate(scaffold_context, output_dir)
        self.skeleton.generate(desc, output_dir, synth_files=synth_files)
        self.knob.generate_config(desc.config, output_dir / "config.json")
        return output_dir

    def _dependencies(self, desc: SkeletonDescriptor) -> tuple[list[dict[str, str]], list[str]]:
        """Derive CMake dependencies + headers from open-source leaves."""
        libs: set[str] = set()
        headers: set[str] = set()
        for node in _walk(desc.root):
            if node.node_kind == "open_source_leaf":
                if node.library:
                    libs.add(node.library)
                if node.self_work.call_spec is not None:
                    headers.update(node.self_work.call_spec.includes)
        specs = self.catalog.library_specs()
        deps = [
            {
                "name": specs.get(lib, {}).get("cmake_name", lib),
                "version": specs.get(lib, {}).get("version", "0"),
            }
            for lib in sorted(libs)
        ]
        return deps, sorted(headers)

    def generate(self, instruction: dict[str, Any], output_dir: pathlib.Path) -> pathlib.Path:
        """Generate a complete workload project from a generation instruction.

        Args:
            instruction: Structured generation instruction dict with keys:
                project_name, compile_flags, dependencies, dep_headers,
                stages (list of behavior profiles), config (knob dict).
            output_dir: Directory to generate the project in.

        Returns:
            Path to the generated project directory.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate behavior stage files
        stage_files: list[str] = []
        stage_contexts: list[dict[str, Any]] = []
        for stage in instruction.get("stages", []):
            filename, content = self.behavior.generate_stage_file(stage)
            filepath = output_dir / filename
            filepath.write_text(content)
            stage_files.append(filename)

            # Build stage context for main.cpp template
            impl = stage["implementation_strategy"]
            loop_call = (
                f"{stage['stage_name']}_compute();"
                if impl != "memory_synthesis"
                else f"{stage['stage_name']}_memory();"
            )
            stage_ctx = {
                "include_statement": f'#include "{filename}"',
                "warmup_call": loop_call,
                "loop_call": loop_call,
                "measure_call": f"// {stage['stage_name']} measurement start",
            }
            stage_contexts.append(stage_ctx)

        # Build scaffold context
        scaffold_context = {
            "project_name": instruction.get("project_name", "workload_sim"),
            "compile_flags": instruction.get("compile_flags", "-O2"),
            "dependencies": instruction.get("dependencies", []),
            "dep_headers": instruction.get("dep_headers", []),
            "stages": stage_contexts,
            "extra_sources": stage_files,
            "config": instruction.get("config", {}),
        }

        # Generate scaffold (CMakeLists.txt, main.cpp, config_loader.h, config.json)
        self.scaffold.generate(scaffold_context, output_dir)

        # Override config.json with knob generator for precise control
        config_path = output_dir / "config.json"
        self.knob.generate_config(instruction.get("config", {}), config_path)

        return output_dir


def _walk(node: CallTreeNode) -> list[CallTreeNode]:
    """Pre-order traversal of a call tree."""
    out: list[CallTreeNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.children)
    return out
