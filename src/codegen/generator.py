"""Orchestrates the full code generation pipeline: scaffold -> behavior -> knob."""

import pathlib

from codegen.behavior_gen import BehaviorGenerator
from codegen.knob_gen import KnobGenerator
from codegen.scaffold_gen import ScaffoldGenerator


class WorkloadGenerator:
    """Orchestrate full workload project generation from generation instruction.

    Combines:
    - ScaffoldGenerator (Layer 0-1: project structure)
    - BehaviorGenerator (Layer 3: stage implementations)
    - KnobGenerator (Layer 4: runtime config)
    """

    def __init__(self) -> None:
        self.scaffold = ScaffoldGenerator()
        self.behavior = BehaviorGenerator()
        self.knob = KnobGenerator()

    def generate(self, instruction: dict[str, object], output_dir: pathlib.Path) -> pathlib.Path:
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
        stage_contexts: list[dict[str, object]] = []
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
