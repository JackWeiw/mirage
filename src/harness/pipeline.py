"""End-to-end pipeline orchestration for Phase 1 + Phase 2 auto-iteration loop.

Phase 1: Manual-driven, no auto-iteration. Each step callable individually
or the full pipeline run in sequence. Agent is optional -- works in local-only mode.

Phase 2: run_iteration_loop is the two-tier closed-loop controller
(collect -> compare -> decide priority -> tier -> gate -> apply -> record),
with termination on convergence / max_iter / oscillation / no-improvement /
run-failure-streak / build-failure-streak / degraded-stall.
"""

import math
import pathlib
import subprocess
import time
from collections.abc import Callable
from typing import Any

from agent.adjustment import (
    apply_adjustments,
    deterministic_revise,
    validate_adjustments,
)
from agent.agent_core import AgentCore, LLMError
from agent.strategy import decide_iteration_priority
from codegen.call_tree import SkeletonDescriptor
from codegen.generator import WorkloadGenerator
from config.framework_config import FrameworkConfig
from harness.build_runner import BuildRunner
from harness.config_writer import write_config_json_atomic
from harness.execution_runner import ExecutionRunner
from harness.metrics_collector import MetricsCollector
from harness.run_config import RunConfig
from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.topdown_parser import TopdownParser
from models.results import BuildResult, PipelineResult, RunFailure
from observability.iteration_history import (
    IterationHistory,
    IterationRecord,
)
from observability.logging import get_logger
from observability.telemetry import PipelineTelemetry
from profile.comparator import ProfileComparator
from profile.profile_schema import Profile, ProfileMetadata
from profile.profile_store import ProfileStore
from profile.structural_comparator import StructuralComparator

logger = get_logger("pipeline")

_WORKLOAD_PAD_S = 3  # spike's _WORKLOAD_MEASUREMENT_PAD_S; slack for proc.wait


class Pipeline:
    """End-to-end pipeline: data ingestion -> agent analysis -> code gen -> build -> run -> compare.

    Args:
        output_base_dir: Base directory for all generated output.
        config: FrameworkConfig. If None, loads via from_env (defaults +
            MIRAGE_AGENT_* env overrides) so the gateway is configurable without
            editing default_config.yaml. Pass FrameworkConfig.defaults() for an
            env-free, offline test config.
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
        self.config = config or FrameworkConfig.from_env()

        self.profile_store = ProfileStore(base_dir=output_base_dir / "profiles")
        self.flamegraph_parser = FlamegraphParser()
        self.topdown_parser = TopdownParser()
        self.comparator = ProfileComparator(config=self.config.comparison)
        # Respect an explicit agent=None (local-only / degraded mode).
        self.agent: AgentCore | None = (
            agent if agent is not None else AgentCore(config=self.config.agent)
        )
        self.generator = WorkloadGenerator()
        self.build_runner = BuildRunner(
            cmake_path=self.config.harness.cmake_path,
            make_path=self.config.harness.make_path,
            build_dir_suffix=self.config.harness.build_dir_suffix,
        )
        self.execution_runner = ExecutionRunner()
        self.metrics_collector = MetricsCollector(devkit_cmd=self.config.devkit.devkit_cmd)
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
            elif topdown_path.suffix in (".txt", ".text"):
                topdown_profile = self.topdown_parser.parse_text(topdown_path)
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
        elif self.agent is not None and self.agent.is_available():
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

    def generate_workload_from_descriptor(self, desc: SkeletonDescriptor) -> pathlib.Path:
        """Generate a workload project from a SkeletonDescriptor (call-tree path)."""
        self.telemetry.start_step("generating")
        project_dir = self.output_base_dir / "generated_workload"
        self.generator.generate_from_descriptor(desc, project_dir)
        logger.info("workload_generated_from_descriptor", dir=str(project_dir))
        self.telemetry.end_step("generating", success=True)
        return project_dir

    def run_and_compare(
        self,
        customer_stacks: list[tuple[list[str], int]],
        project_dir: pathlib.Path | None = None,
        binary_path: str | None = None,
        run_config: RunConfig | None = None,
        flamegraph_path: pathlib.Path | None = None,
        workload_stacks: list[tuple[list[str], int]] | None = None,
    ) -> dict[str, Any]:
        """Single-pass build -> run -> collect -> structural compare.

        On a dev machine without perf/cmake, pass workload_stacks directly to
        exercise the structural comparison without collection. On ARM, pass
        flamegraph_path (collected via perf) instead.
        """
        self.telemetry.start_step("run_and_compare")
        report: dict[str, Any] = {}
        if binary_path is None and project_dir is not None:
            binary_path = self.build_workload(project_dir)
        if binary_path is not None:
            report["execution"] = self.execution_runner.run(binary_path, run_config)
        if workload_stacks is None and flamegraph_path is not None:
            workload_stacks = self.flamegraph_parser.parse_stacks(flamegraph_path)
        if workload_stacks is None:
            workload_stacks = []
        report["structural_alignment"] = StructuralComparator().compare(
            customer_stacks, workload_stacks
        )
        self.telemetry.end_step("run_and_compare", success=True)
        return report

    def build_workload_result(self, project_dir: pathlib.Path) -> BuildResult:
        """Build the generated workload project, returning the full BuildResult.

        Preserves compiler stdout/stderr so the loop's build-failure
        self-correction path can surface the real error to the LLM (#3b-fu1).
        """
        self.telemetry.start_step("building")
        result = self.build_runner.build(project_dir)

        if not result.success:
            logger.error("build_failed", error=result.stderr)
            self.telemetry.end_step("building", success=False, error=result.stderr)
        else:
            logger.info("build_succeeded", binary=result.binary_path)
            self.telemetry.end_step("building", success=True)
        return result

    def build_workload(self, project_dir: pathlib.Path) -> str | None:
        """Build the generated workload project (binary path, or None).

        Thin wrapper over `build_workload_result` for Phase-1 callers that
        only need the binary path. Loop callers should use
        `build_workload_result` to keep compiler stderr.
        """
        return self.build_workload_result(project_dir).binary_path

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

    def run_and_collect(
        self,
        binary_path: str,
        project_dir: pathlib.Path,
        warmup_seconds: int,
        measurement_seconds: int,
    ) -> Profile | RunFailure:
        """Run the existing binary, collect topdown during measurement, parse.

        Mirrors the spike's run_one_point run/collect leg: taskset-pin (if
        cpu_range configured), warm up, crash-check, collect_topdown(-p pid),
        wait for exit, parse. Returns a workload Profile or a RunFailure
        (crash/timeout/collect_fail). Crashes are NOT retried (deterministic);
        timeouts and collect-failures retry up to collect_retry.
        """
        config_path = str((project_dir / "config.json").resolve())
        binary = str(pathlib.Path(binary_path).resolve())
        cpu_range = self.config.devkit.cpu_range
        launch_cmd: list[str] = (
            ["taskset", "-c", cpu_range, binary, config_path]
            if cpu_range
            else [binary, config_path]
        )
        try:
            proc = subprocess.Popen(
                launch_cmd,
                cwd=str(project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            return RunFailure(reason=f"binary_launch_failed: {exc}", kind="crash")

        time.sleep(warmup_seconds)
        # Crash during warmup (e.g. a stage segfaulted): no retry.
        if proc.poll() is not None:
            try:
                out, err_out = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err_out = "<communicate-timeout>", "<communicate-timeout>"
            return RunFailure(
                reason=f"workload_exited_during_warmup rc={proc.returncode}",
                kind="crash",
                stdout=out or "",
                stderr=err_out or "",
            )

        interval = self.config.devkit.interval_seconds
        pid = proc.pid if self.config.devkit.collect_pid else None
        td_path = project_dir / "topdown.txt"
        retries = self.config.comparison.collect_retry
        last_err = ""
        for _ in range(retries + 1):
            coll = self.metrics_collector.collect_topdown(
                td_path,
                duration=measurement_seconds,
                interval=interval,
                pid=pid,
            )
            if coll.success and coll.topdown_path is not None:
                # Wait for the workload to exit (measurement + pad).
                try:
                    proc.wait(timeout=measurement_seconds + _WORKLOAD_PAD_S + 30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    return RunFailure(reason="workload_hang", kind="timeout")
                try:
                    prof = self.metrics_collector.parse_topdown_file(
                        pathlib.Path(coll.topdown_path)
                    )
                except (ValueError, OSError) as exc:
                    last_err = f"parse_failed: {exc}"
                    continue
                if prof.topdown is None:
                    last_err = "no_topdown_l1_lines"
                    continue
                return prof
            last_err = coll.error or "collect_failed"
        # Exhausted retries: reap the process, then return a failure.
        try:
            proc.wait(timeout=_WORKLOAD_PAD_S + 30)
        except subprocess.TimeoutExpired:
            proc.kill()
        kind = (
            "timeout"
            if "timeout" in last_err.lower() or "hang" in last_err.lower()
            else "collect_fail"
        )
        return RunFailure(reason=last_err, kind=kind)

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

    # ------------------------------------------------------------------
    # Phase 2 -- auto-iteration loop driver (two-tier)
    # ------------------------------------------------------------------

    def _build_instruction(
        self,
        instruction: dict[str, Any],
        build: Callable[[dict[str, Any]], BuildResult],
    ) -> BuildResult:
        """Build the initial binary via the caller-supplied build callable."""
        return build(instruction)

    @staticmethod
    def _make_record(
        i: int,
        report: dict[str, Any],
        priority: int,
        adjustments: list[dict[str, Any]],
        applied_moves: list[dict[str, Any]] | None = None,
    ) -> IterationRecord:
        """Build an IterationRecord from a comparison report.

        Leaves score=None so IterationHistory.add_record computes it via
        compute_score (the normalized multi-dim score).
        """
        td_diffs = {k: v["diff_pct"] for k, v in report.get("topdown_l1", {}).items()}
        bw_diff = report.get("memory", {}).get("bandwidth_gbps", {}).get("diff_pct", 0.0)
        coverage = report.get("hotspot_coverage", {}).get("coverage_pct", 0.0)
        return IterationRecord(
            iteration=i,
            converged=report.get("convergence", {}).get("converged", False),
            topdown_diffs=td_diffs,
            memory_diff_pct=bw_diff,
            coverage_pct=coverage,
            strategy_priority=priority,
            adjustments=adjustments,
            applied_moves=applied_moves or [],
        )

    @staticmethod
    def _applied_moves(
        accepted: list[dict[str, Any]],
        instruction: dict[str, Any],
        tier: str,
    ) -> list[dict[str, Any]]:
        """Compute per-accepted adjustment {knob, tier, sign} for oscillation tracking.

        sign = sign(to - actual_current).  Reads actual_current from the
        instruction BEFORE apply_adjustments reassigns it.
        """
        from agent.adjustment import RUNTIME_KNOBS

        moves: list[dict[str, Any]] = []
        for adj in accepted:
            knob = adj["knob"]
            to_val = adj["to"]
            if knob in RUNTIME_KNOBS:
                actual = instruction.get("config", {}).get(knob)
            else:
                stage_name = adj.get("stage", "")
                stage = next(
                    (s for s in instruction.get("stages", []) if s.get("stage_name") == stage_name),
                    None,
                )
                actual = (
                    stage.get("strategies", [{}])[0].get("synthesis_config", {}).get(knob)
                    if stage is not None
                    else None
                )
            if (
                isinstance(to_val, int | float)
                and isinstance(actual, int | float)
                and not isinstance(to_val, bool)
                and not isinstance(actual, bool)
            ):
                delta = float(to_val) - float(actual)
                sign = int(math.copysign(1, delta)) if delta != 0 else 0
            else:
                sign = 0
            moves.append({"knob": knob, "tier": tier, "sign": sign})
        return moves

    @staticmethod
    def _attribute_observed_effects(
        prev_record: IterationRecord,
        last_report: dict[str, Any],
        report: dict[str, Any],
    ) -> None:
        """Attribute observed metric deltas to the PREVIOUS record's adjustments.

        Mutates prev_record.observed_effects in place. Single-adjustment
        round -> per-knob attribution to that adjustment's expected_metric
        (only if the metric is in the new report). Multi-adjustment (LLM
        batch) -> overall deltas across every topdown L1 metric (no false
        per-knob causality). Called from both the converged break-path and
        the loop tail -- the single source of truth for attribution.
        """
        last_td = last_report.get("topdown_l1", {})
        new_td = report.get("topdown_l1", {})
        if len(prev_record.adjustments) == 1:
            # Single-adjustment round: per-knob attribution.
            metric = str(prev_record.adjustments[0].get("expected_metric", ""))
            if metric and metric in new_td:
                old_diff = last_td.get(metric, {}).get("diff_pct", 0.0)
                new_diff = new_td.get(metric, {}).get("diff_pct", 0.0)
                prev_record.observed_effects[metric] = new_diff - old_diff
        elif len(prev_record.adjustments) > 1:
            # Multi-adjustment (LLM batch): overall deltas only.
            for metric in new_td:
                old_diff = last_td.get(metric, {}).get("diff_pct", 0.0)
                new_diff = new_td.get(metric, {}).get("diff_pct", 0.0)
                prev_record.observed_effects[metric] = new_diff - old_diff

    def _loop_result(self, history: IterationHistory, stop_reason: str) -> PipelineResult:
        """Persist history to disk and build the PipelineResult."""
        history_path = history.save(self.output_base_dir / "history.json")
        return PipelineResult(
            success=(stop_reason == "converged"),
            best_iteration=history.best_iteration,
            degraded=history.degraded,
            stop_reason=stop_reason,
            history_path=str(history_path),
        )

    def run_iteration_loop(
        self,
        customer_profile: Profile,
        seed_instruction: dict[str, Any] | None = None,
        sensitivity: dict[str, dict[str, Any]] | None = None,
        max_iter: int = 10,
        collect: (Callable[[str, dict[str, Any]], Profile | RunFailure] | None) = None,
        build: Callable[[dict[str, Any]], BuildResult] | None = None,
    ) -> PipelineResult:
        """Two-tier auto-iteration loop driver (spec 'Two-tier loop driver').

        The loop runs collect -> compare -> decide priority -> tier
        (runtime = deterministic no-rebuild / structural = LLM
        regenerate+rebuild) -> gate adjustments -> apply -> record, with
        termination on convergence, max_iter, oscillation, no-improvement,
        run-failure-streak, build-failure-streak, or degraded-stall.

        Args:
            customer_profile: Target customer Profile.
            seed_instruction: Fallback instruction when agent is unavailable.
            sensitivity: Per-knob sensitivity table for the deterministic leg.
            max_iter: Maximum iterations.
            collect: Injectable collect(binary_path, instruction) -> Profile | RunFailure.
            build: Injectable build(instruction) -> BuildResult (carries stderr).

        Returns:
            PipelineResult with best_iteration, stop_reason, degraded, history_path.
        """
        cmp_cfg = self.config.comparison
        sens: dict[str, dict[str, Any]] = sensitivity or {}

        # Resolve callables (defaults wrap the real Pipeline methods).
        if build is None:

            def _default_build(instr: dict[str, Any]) -> BuildResult:
                pdir = self.output_base_dir / "generated_workload"
                self.generator.generate(instr, pdir)
                return self.build_workload_result(pdir)

            build_fn: Callable[[dict[str, Any]], BuildResult] = _default_build
        else:
            build_fn = build

        if collect is None:

            def _default_collect(binary: str, instr: dict[str, Any]) -> Profile | RunFailure:
                pdir = pathlib.Path(binary).parent
                ws = instr.get("config", {}).get(
                    "warmup_seconds", self.config.run_defaults.warmup_seconds
                )
                ms = instr.get("config", {}).get(
                    "measurement_seconds",
                    self.config.run_defaults.measurement_seconds,
                )
                return self.run_and_collect(binary, pdir, ws, ms)

            collect_fn: Callable[[str, dict[str, Any]], Profile | RunFailure] = _default_collect
        else:
            collect_fn = collect

        # --- Instruction acquisition ---
        agent_available = self.agent is not None and self.agent.is_available()
        instruction: dict[str, Any] | None = None
        if agent_available and seed_instruction is None:
            assert self.agent is not None  # for mypy
            instruction = self.agent.run_full_chain(customer_profile.model_dump_json())
        else:
            instruction = seed_instruction
        if instruction is None:
            return PipelineResult(
                success=False,
                error="no_instruction_available",
                stop_reason="no_instruction",
            )

        # --- Initial build (seed must compile; no recovery) ---
        seed_build = self._build_instruction(instruction, build_fn)
        if not seed_build.success:
            return PipelineResult(
                success=False,
                error=f"seed_build_failed: {seed_build.stderr}".rstrip(": "),
                stop_reason="seed_build_failed",
            )
        binary: str | None = seed_build.binary_path
        if binary is None:
            # success but no binary path -- malformed BuildResult; treat as
            # a seed build failure (defensive, should not happen).
            return PipelineResult(
                success=False,
                error="seed_build_failed: no binary path",
                stop_reason="seed_build_failed",
            )

        # --- Loop state ---
        history = IterationHistory(customer_name=customer_profile.metadata.customer)
        self.history = history  # wire back to the Pipeline for inspection
        run_fail_streak = 0
        build_fail_streak = 0
        pending_build_fix = False
        # When True, this iteration must apply the LLM's *revised instruction*
        # (a code-level fix) and rebuild from it, instead of gating/applying
        # knob adjustments (knobs cannot fix a codegen compile error). Only
        # the LLM can self-correct a build failure; set when entering the
        # pending_build_fix branch with an agent available.
        apply_revised = False
        last_report: dict[str, Any] | None = None
        prev_record: IterationRecord | None = None
        stop_reason = "max_iter"  # default if the for loop completes without break

        for i in range(max_iter):
            agent_avail_now = self.agent is not None and self.agent.is_available()
            # Per-iteration reset of the apply_revised flag. It is set only in
            # the pending_build_fix branch (when the agent is available) and
            # consumed in the apply section below; resetting here guarantees a
            # stale True can never leak into a later iteration even if the
            # candidate-gen branch that assigns _revised is skipped.
            apply_revised = False
            # ---- Collect phase ----
            if pending_build_fix:
                # The last structural revision did not compile.  Don't run
                # the (dead) binary; revise straight from the last good
                # report, with the compiler stderr now in history so the
                # LLM can self-correct.
                report = last_report
                if agent_avail_now:
                    # Ask the LLM for a *revised instruction* (code fix) and
                    # rebuild from it this iteration (see apply section).
                    apply_revised = True
                    pending_build_fix = False
                else:
                    # Agent went away mid-recovery: runtime knobs cannot fix a
                    # codegen build failure. Keep skipping the dead binary and
                    # let the build-failure streak terminate.
                    history.add_record(
                        IterationRecord(
                            iteration=i,
                            converged=False,
                            build_failed=True,
                        )
                    )
                    build_fail_streak += 1
                    if build_fail_streak >= cmp_cfg.build_failure_stop:
                        stop_reason = "build_failure_streak"
                        break
                    continue
            else:
                # binary is set by the initial build and only replaced after
                # a successful rebuild; it is never None at a collect site.
                assert binary is not None
                collect_result = collect_fn(binary, instruction)
                if isinstance(collect_result, RunFailure):
                    run_fail_streak += 1
                    history.add_record(
                        IterationRecord(
                            iteration=i,
                            converged=False,
                            failed=True,
                            failure_reason=collect_result.reason,
                        )
                    )
                    if run_fail_streak >= cmp_cfg.run_failure_stop:
                        stop_reason = "run_failure_streak"
                        break
                    continue  # skip this round, no revise
                run_fail_streak = 0
                report = self.comparator.compare(customer_profile, collect_result, iteration=i)

            if report is None:
                # Defensive: pending_build_fix on the very first iter with
                # no prior report (should not happen -- seed build failure
                # is caught above).
                stop_reason = "no_report_available"
                break

            # ---- Priority + tier ----
            priority = decide_iteration_priority(report, config=cmp_cfg)

            if priority == 0:
                # Converged -- record and exit.
                record = self._make_record(i, report, priority, [])
                history.add_record(record)
                # Run attribution for the previous record before breaking.
                if prev_record is not None and last_report is not None:
                    self._attribute_observed_effects(prev_record, last_report, report)
                stop_reason = "converged"
                break

            tier = "runtime" if priority == 1 else "structural"

            # ---- Candidate generation ----
            # agent_avail_now computed at the top of the loop iter.
            runtime_candidates_empty = False
            # _revised: the LLM's code-level instruction rewrite. Only the
            # structural+agent branch assigns it; on the pending_build_fix
            # path we apply it (rebuild) instead of knob adjustments.
            _revised: dict[str, Any] | None = None
            cand: list[dict[str, Any]]

            if priority == 1:
                # Runtime tier: deterministic fast path.
                cand = deterministic_revise(
                    instruction,
                    report,
                    sens,
                    history,
                    oscillation_window=cmp_cfg.oscillation_window,
                    topdown_threshold_pct=cmp_cfg.topdown_threshold_pct,
                )
                runtime_candidates_empty = len(cand) == 0
            elif agent_avail_now:
                # Structural tier, agent available -- LLM leg.
                # BUG-FIX #2: use [1] (the adjustments), NOT [0].
                assert self.agent is not None  # for mypy
                try:
                    _revised, cand = self.agent.revise_instruction(
                        instruction, report, sens, history
                    )
                except LLMError as exc:
                    # A failed LLM call (reasoning-model truncation, transient
                    # gateway error, malformed JSON) must NOT crash the run --
                    # degrade this iteration to the deterministic path and
                    # continue, exactly like agent-unavailable. Reasoning models
                    # (GLM-4.7/deepseek) truncate often at low max_tokens; raising
                    # max_tokens (MIRAGE_AGENT_MAX_TOKENS) is the real fix -- this
                    # is the safety net so one bad call doesn't kill the loop.
                    logger.warning(
                        "structural_llm_failed_degrade iter=%d kind=%s err=%s",
                        i,
                        type(exc).__name__,
                        exc,
                    )
                    history.degraded = True
                    tier = "runtime"
                    _revised = None
                    cand = deterministic_revise(
                        instruction,
                        report,
                        sens,
                        history,
                        oscillation_window=cmp_cfg.oscillation_window,
                        topdown_threshold_pct=cmp_cfg.topdown_threshold_pct,
                    )
                    runtime_candidates_empty = len(cand) == 0
            else:
                # Degraded mode: priority >= 2, agent unavailable.
                # Force runtime-tier execution (deterministic).
                history.degraded = True
                tier = "runtime"  # CRITICAL: force runtime tier in degraded mode
                cand = deterministic_revise(
                    instruction,
                    report,
                    sens,
                    history,
                    oscillation_window=cmp_cfg.oscillation_window,
                    topdown_threshold_pct=cmp_cfg.topdown_threshold_pct,
                )
                runtime_candidates_empty = len(cand) == 0

            # ---- Gate + apply ----
            build_failed_this_iter = False
            if apply_revised and _revised is not None:
                # pending_build_fix path: the LLM revised the instruction to
                # fix a compile error. Apply the revised instruction directly
                # -- the gate validates *knob* direction/scope, which does not
                # apply to a code-level rewrite -- and rebuild from it.
                apply_revised = False
                record = self._make_record(i, report, priority, [], [])
                instruction = _revised
                new_res = self._build_instruction(instruction, build_fn)
                if not new_res.success:
                    build_fail_streak += 1
                    record.build_failed = True
                    record.build_stderr = new_res.stderr
                    build_failed_this_iter = True
                    pending_build_fix = True
                else:
                    binary = new_res.binary_path
                    build_fail_streak = 0
            else:
                accepted, rejected = validate_adjustments(
                    cand,
                    instruction,
                    report,
                    sens,
                    tier,
                    topdown_threshold_pct=cmp_cfg.topdown_threshold_pct,
                )
                if rejected:
                    logger.info(
                        "adjustments_rejected",
                        iteration=i,
                        count=len(rejected),
                        reasons=[r.get("reason") for r in rejected],
                    )

                # ---- Record + apply ----
                applied = self._applied_moves(accepted, instruction, tier) if accepted else []
                record = self._make_record(i, report, priority, accepted, applied)

                if accepted:
                    # BUG-FIX #1: reassign -- apply_adjustments returns a NEW dict.
                    instruction = apply_adjustments(instruction, accepted)
                    if tier == "structural":
                        new_res = self._build_instruction(instruction, build_fn)
                        if not new_res.success:
                            build_fail_streak += 1
                            record.build_failed = True
                            record.build_stderr = new_res.stderr
                            build_failed_this_iter = True
                            pending_build_fix = True
                        else:
                            binary = new_res.binary_path
                            build_fail_streak = 0
                    else:
                        # Runtime tier: atomic rewrite of project config.json.
                        # Best-effort -- the test stub may not create a real
                        # project directory.
                        assert binary is not None  # set after a successful build
                        project_dir = pathlib.Path(binary).parent
                        config_path = project_dir / "config.json"
                        if project_dir.is_dir():
                            try:
                                write_config_json_atomic(
                                    config_path,
                                    instruction.get("config", {}),
                                )
                            except OSError as exc:
                                logger.warning("config_write_failed", error=str(exc))

            history.add_record(record)

            # ---- observed_effects attribution (for the PREVIOUS record) ----
            if prev_record is not None and last_report is not None:
                self._attribute_observed_effects(prev_record, last_report, report)
            prev_record = record
            last_report = report  # capture for next iteration's attribution

            # ---- Termination checks ----
            if build_failed_this_iter:
                if build_fail_streak >= cmp_cfg.build_failure_stop:
                    stop_reason = "build_failure_streak"
                    break
                continue

            if history.is_oscillating(cmp_cfg.oscillation_window):
                stop_reason = "oscillation"
                break

            if history.no_improvement_for(cmp_cfg.no_improvement_stop):
                stop_reason = "no_improvement_stop"
                break

            # Degraded-mode: runtime tier exhausted (all runtime knobs
            # skip-blocked or boundary-exhausted).  deterministic_revise
            # returned [] during this iteration's candidate generation.
            if (
                history.degraded
                and runtime_candidates_empty
                and any(
                    not v.get("within_threshold", True)
                    for v in report.get("topdown_l1", {}).values()
                )
            ):
                stop_reason = "runtime_tier_exhausted_agent_unavailable"
                break

        return self._loop_result(history, stop_reason)
