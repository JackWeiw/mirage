"""End-to-end pipeline orchestration for Phase 1.

Phase 1: Manual-driven, no auto-iteration. Each step callable individually
or the full pipeline run in sequence. Agent is optional — works in local-only mode.
"""

import pathlib
from typing import Any

from agent.agent_core import AgentCore
from agent.strategy import decide_iteration_priority
from codegen.generator import WorkloadGenerator
from config.framework_config import FrameworkConfig
from harness.build_runner import BuildRunner
from harness.execution_runner import ExecutionRunner
from harness.metrics_collector import MetricsCollector
from harness.run_config import RunConfig
from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.topdown_parser import TopdownParser
from models.results import PipelineResult
from observability.iteration_history import IterationHistory, IterationRecord
from observability.logging import get_logger
from observability.telemetry import PipelineTelemetry
from profile.comparator import ProfileComparator
from profile.profile_schema import Profile, ProfileMetadata
from profile.profile_store import ProfileStore

logger = get_logger("pipeline")


class Pipeline:
    """End-to-end pipeline: data ingestion -> agent analysis -> code gen -> build -> run -> compare.

    Args:
        output_base_dir: Base directory for all generated output.
        config: FrameworkConfig. If None, uses defaults.
        agent: AgentCore instance. If None, creates from config. Set to None explicitly for local-only mode.
    """

    def __init__(
        self,
        output_base_dir: pathlib.Path,
        config: FrameworkConfig | None = None,
        agent: AgentCore | None = None,
    ) -> None:
        self.output_base_dir = output_base_dir
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or FrameworkConfig.defaults()

        self.profile_store = ProfileStore(base_dir=output_base_dir / "profiles")
        self.flamegraph_parser = FlamegraphParser()
        self.topdown_parser = TopdownParser()
        self.comparator = ProfileComparator(config=self.config.comparison)
        self.agent = agent or AgentCore(config=self.config.agent)
        self.generator = WorkloadGenerator()
        self.build_runner = BuildRunner(
            cmake_path=self.config.harness.cmake_path,
            make_path=self.config.harness.make_path,
            build_dir_suffix=self.config.harness.build_dir_suffix,
        )
        self.execution_runner = ExecutionRunner()
        self.metrics_collector = MetricsCollector()
        self.telemetry = PipelineTelemetry(pipeline_id="workload_sim")
        self.history = IterationHistory(customer_name="unknown")

    def ingest_customer_data(
        self,
        flamegraph_path: pathlib.Path | None = None,
        topdown_path: pathlib.Path | None = None,
        customer_name: str = "customer",
        metadata: dict[str, Any] | None = None,
    ) -> Profile:
        """Ingest customer data files into a structured Profile."""
        self.telemetry.start_step("ingesting")

        hotspots = []
        if flamegraph_path is not None:
            hotspots = self.flamegraph_parser.parse_folded(flamegraph_path)
            logger.info("parsed_hotspots", count=len(hotspots))

        topdown_profile: Profile | None = None
        if topdown_path is not None:
            if topdown_path.suffix == ".json":
                topdown_profile = self.topdown_parser.parse_json(topdown_path)
            elif topdown_path.suffix == ".csv":
                topdown_profile = self.topdown_parser.parse_csv(topdown_path)
            logger.info("parsed_topdown", suffix=topdown_path.suffix)

        meta = metadata or {}
        profile = Profile(
            metadata=ProfileMetadata(
                customer=customer_name,
                date=meta.get("date", "unknown"),
                platform=meta.get("platform", "arm64"),
                neoverse_core=meta.get("neoverse_core"),
            ),
            hotspots=hotspots,
            topdown=topdown_profile.topdown if topdown_profile else None,
            topdown_l2=topdown_profile.topdown_l2 if topdown_profile else None,
            memory=topdown_profile.memory if topdown_profile else None,
            business_logic=meta.get("business_logic"),
        )

        self.profile_store.save(profile, name=f"{customer_name}_profile")
        self.history.customer_name = customer_name
        logger.info("customer_profile_saved", name=customer_name)

        self.telemetry.end_step("ingesting", success=True)
        return profile

    def generate_workload(
        self, customer_profile: Profile, instruction: dict[str, Any] | None = None
    ) -> pathlib.Path:
        """Generate workload code from customer Profile.

        If instruction is provided (local-only mode), use it directly.
        If instruction is None, use Agent to produce it (requires API key).
        """
        self.telemetry.start_step("generating")

        if instruction is not None:
            logger.info("using_manual_instruction")
        elif self.agent.is_available():
            logger.info("running_agent_chain")
            profile_json = customer_profile.model_dump_json()
            instruction = self.agent.run_full_chain(profile_json)
        else:
            raise RuntimeError(
                "No instruction provided and Agent not available. "
                "Either provide an instruction dict or set ANTHROPIC_API_KEY."
            )

        if instruction is None:
            raise RuntimeError("No generation instruction available.")

        logger.info("instruction_stages", count=len(instruction.get("stages", [])))
        project_dir = self.output_base_dir / "generated_workload"
        self.generator.generate(instruction, project_dir)
        logger.info("workload_generated", dir=str(project_dir))

        self.telemetry.end_step("generating", success=True)
        return project_dir

    def build_workload(self, project_dir: pathlib.Path) -> str | None:
        """Build the generated workload project."""
        self.telemetry.start_step("building")
        result = self.build_runner.build(project_dir)

        if not result.success:
            logger.error("build_failed", error=result.stderr)
            self.telemetry.end_step("building", success=False, error=result.stderr)
            return None

        logger.info("build_succeeded", binary=result.binary_path)
        self.telemetry.end_step("building", success=True)
        return result.binary_path

    def run_workload(self, binary_path: str, run_config: RunConfig | None = None) -> dict[str, Any]:
        """Run the workload binary."""
        self.telemetry.start_step("running")
        if run_config is None:
            run_config = RunConfig()
        result = self.execution_runner.run(binary_path, run_config)

        if result.success:
            logger.info("execution_succeeded")
        else:
            logger.error("execution_failed", error=result.stderr)

        self.telemetry.end_step(
            "running",
            success=result.success,
            error=result.stderr if not result.success else None,
        )
        return result.model_dump()

    def compare_results(
        self,
        customer_profile: Profile,
        workload_profile: Profile,
        iteration: int = 0,
    ) -> dict[str, Any]:
        self.telemetry.start_step("comparing")
        report = self.comparator.compare(customer_profile, workload_profile, iteration)

        # Add to iteration history
        td_diffs = {k: v["diff_pct"] for k, v in report.get("topdown_l1", {}).items()}
        bw_diff = report.get("memory", {}).get("bandwidth_gbps", {}).get("diff_pct", 0.0)
        coverage = report.get("hotspot_coverage", {}).get("coverage_pct", 0.0)
        priority = decide_iteration_priority(report, config=self.config.comparison)

        record = IterationRecord(
            iteration=iteration,
            converged=report["convergence"]["converged"],
            topdown_diffs=td_diffs,
            memory_diff_pct=bw_diff,
            coverage_pct=coverage,
            strategy_priority=priority,
        )
        self.history.add_record(record)

        logger.info(
            "comparison_done",
            converged=report["convergence"]["converged"],
            reason=report["convergence"]["reason"],
        )
        self.telemetry.end_step("comparing", success=True)
        return report

    def run_full_pipeline(
        self,
        flamegraph_path: pathlib.Path | None = None,
        topdown_path: pathlib.Path | None = None,
        customer_name: str = "customer",
        metadata: dict[str, Any] | None = None,
        instruction: dict[str, Any] | None = None,
    ) -> PipelineResult:
        """Run the full end-to-end pipeline.

        Args:
            flamegraph_path: Path to customer flamegraph file.
            topdown_path: Path to customer Topdown data.
            customer_name: Customer identifier.
            metadata: Additional metadata dict.
            instruction: Generation instruction for local-only mode. If None, uses Agent.

        Returns:
            PipelineResult.
        """
        try:
            customer_profile = self.ingest_customer_data(
                flamegraph_path=flamegraph_path,
                topdown_path=topdown_path,
                customer_name=customer_name,
                metadata=metadata,
            )

            project_dir = self.generate_workload(customer_profile, instruction=instruction)

            binary_path = self.build_workload(project_dir)

            if binary_path is None:
                return PipelineResult(
                    success=False,
                    customer_profile_json=customer_profile.model_dump_json(),
                    project_dir=str(project_dir),
                    error="Build failed",
                )

            # Save iteration history
            self.history.save(self.output_base_dir / "history.json")

            return PipelineResult(
                success=True,
                customer_profile_json=customer_profile.model_dump_json(),
                project_dir=str(project_dir),
            )

        except Exception as e:
            logger.error("pipeline_failed", error=str(e))
            return PipelineResult(success=False, error=str(e))
