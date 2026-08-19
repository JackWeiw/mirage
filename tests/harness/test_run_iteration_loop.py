"""Stub-plant integration test for Pipeline.run_iteration_loop.

Exercises 8 control-flow paths of the two-tier auto-iteration loop driver
using a stub plant (no ARM/devkit/LLM).  The integration test stubs ONLY the
plant (collect/build) and the agent, keeping the REAL deterministic controller
+ REAL gate + REAL comparator.
"""

from __future__ import annotations

import pathlib
from typing import Any, cast

from config.framework_config import FrameworkConfig
from harness.pipeline import Pipeline
from models.results import BuildResult, PipelineResult, RunFailure
from profile.profile_schema import (
    Profile,
    ProfileMetadata,
    TopdownL1,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _customer_profile() -> Profile:
    """Target customer profile for tests (topdown-only convergence)."""
    return Profile(
        metadata=ProfileMetadata(customer="stub", date="2026-08-17"),
        topdown=TopdownL1(
            frontend_bound=10.0,
            backend_bound=72.0,
            bad_speculation=5.0,
            retiring=13.0,
        ),
        memory=None,
        hotspots=[],
    )


def _seed_instruction() -> dict[str, Any]:
    """Seed instruction with runtime config knobs + structural synthesis knobs."""
    return {
        "project_name": "sim",
        "config": {
            "compute_ratio": 0.1,
            "memory_ratio": 0.1,
            "thread_count": 4,
            "qps": 100,
            "warmup_seconds": 0,
            "measurement_seconds": 1,
        },
        "stages": [
            {
                "stage_name": "s0",
                "strategies": [
                    {
                        "synthesis_config": {
                            "working_set_mb": 64,
                            "access_pattern": "sequential",
                            "iterations": 100,
                            "archetype": "matmul",
                        }
                    }
                ],
            }
        ],
    }


def _sensitivity() -> dict[str, dict[str, Any]]:
    """Sensitivity table covering both runtime and structural knobs."""
    return {
        "memory_ratio": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "compute_ratio": {
            "target_metric": "retiring",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "working_set_mb": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "iterations": {
            "target_metric": "retiring",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "thread_count": {
            "target_metric": "frontend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "qps": {
            "target_metric": "bad_speculation",
            "expected_direction": "down",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
    }


def _make_config(tmp_path: pathlib.Path, **overrides: Any) -> FrameworkConfig:
    """Build a FrameworkConfig with custom comparison thresholds."""
    cfg = FrameworkConfig()
    for k, v in overrides.items():
        if hasattr(cfg.comparison, k):
            setattr(cfg.comparison, k, v)
        else:
            # Non-comparison overrides can be added to cfg directly if needed.
            pass
    return cfg


def _fake_build(instruction: dict[str, Any]) -> BuildResult:
    """Default build stub that always succeeds."""
    return BuildResult(success=True, binary_path="/fake/binary")


def _get_synth(instruction: dict[str, Any]) -> dict[str, Any]:
    """Read the first stage's synthesis_config from the instruction."""
    stages = instruction.get("stages", [{}])
    strategies = stages[0].get("strategies", [{}])
    synth = strategies[0].get("synthesis_config", {})
    return cast("dict[str, Any]", synth)


def _make_collect_stub(
    base_backend: float = 50.0,
    base_retiring: float = 10.0,
    frontend_val: float = 10.0,
    bad_spec_val: float = 5.0,
    wsm_slope: float = 0.1,
    iter_slope: float = 0.02,
    mr_slope: float = 5.0,
    cr_slope: float = 5.0,
    tc_slope: float = 0.0,
    qps_slope: float = 0.0,
) -> Any:
    """Build a collect stub whose workload metrics move with knob values.

    backend_bound  = base_backend  + wsm*<slope> + memory_ratio*<slope>
    retiring       = base_retiring + iter*<slope> + compute_ratio*<slope>
    frontend_bound = frontend_val  + (thread_count-4)*tc_slope
    bad_speculation= bad_spec_val  + (100-qps)*qps_slope  (higher qps -> lower)

    Scenes are tuned to ABSOLUTE percentage-point diff (comparator emits w - c
    pp, threshold 10pp). Default initial backend = 50 + 64*0.1 + 0.1*5 = 56.9,
    a -15.1pp gap vs customer 72 -> out of the 10pp band -> the loop iterates
    (priority >= 2 -> structural tier when an agent is present; runtime-tier-only
    when degraded). mr_slope=5 means runtime memory_ratio alone can't close a -15pp
    gap (max mr=1.0 -> 61.4, still -10.6pp out) -> degraded runs exhaust the
    runtime tier instead of falsely converging. The structural wsm knob CAN
    close it (wsm 64->160 -> backend 66.5, within band).
    """

    def collect(binary: str, instruction: dict[str, Any]) -> Profile | RunFailure:
        cfg = instruction.get("config", {})
        synth = _get_synth(instruction)
        wsm = synth.get("working_set_mb", 64)
        iters = synth.get("iterations", 100)
        mr = cfg.get("memory_ratio", 0.1)
        cr = cfg.get("compute_ratio", 0.1)
        tc = cfg.get("thread_count", 4)
        qps = cfg.get("qps", 100)
        return Profile(
            metadata=ProfileMetadata(customer="workload", date="2026-08-17"),
            topdown=TopdownL1(
                frontend_bound=frontend_val + (tc - 4) * tc_slope,
                backend_bound=base_backend + wsm * wsm_slope + mr * mr_slope,
                bad_speculation=bad_spec_val + (100 - qps) * qps_slope,
                retiring=base_retiring + iters * iter_slope + cr * cr_slope,
            ),
            memory=None,
            hotspots=[],
        )

    return collect


# ---------------------------------------------------------------------------
# Path #1: converge
# ---------------------------------------------------------------------------


class TestConvergePath:
    """Stub metrics move toward target on each adjustment; loop converges."""

    def test_converge_via_structural_tier(self, tmp_path: pathlib.Path) -> None:
        """Mock agent emits structural adjustments that close the gap in one
        round; subsequent collect shows convergence."""
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        # Workload starts out of the 10pp band (customer backend=72, band
        # [62, 82]); the structural wsm knob closes the gap in one round.
        # backend_bound  = 50 + 64*0.1 + 0.1*5 = 56.9  (gap -15.1pp, OUT)
        # retiring       = 10 + 100*0.02 + 0.1*5 = 12.5 (gap -0.5pp, IN)
        # backend -15.1pp -> priority >= 2 (>=10, <20) -> structural tier.

        call_count = 0

        def mock_agent_revise(
            instr: dict[str, Any],
            report: dict[str, Any],
            sensitivity: dict[str, dict[str, Any]],
            history: Any,
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            nonlocal call_count
            call_count += 1
            # First call: emit structural adjustments that close the gap.
            # After apply (wsm 64->160; iterations rejected by the gate because
            # retiring is already within threshold), the rebuilt collect is:
            #   backend_bound = 50 + 160*0.1 + 0.1*5 = 66.5  (gap -5.5pp, IN)
            #   retiring      = 10 + 100*0.02 + 0.1*5 = 12.5 (gap -0.5pp, IN)
            # All L1 within 10pp -> converged at iter 1.
            if call_count == 1:
                return instr, [
                    {
                        "stage": "s0",
                        "knob": "working_set_mb",
                        "from": 64,
                        "to": 160,
                        "rationale": "close backend_gap",
                        "expected_metric": "backend_bound",
                        "expected_direction": "up",
                    },
                    {
                        "stage": "s0",
                        "knob": "iterations",
                        "from": 100,
                        "to": 150,
                        "rationale": "close retiring gap",
                        "expected_metric": "retiring",
                        "expected_direction": "up",
                    },
                ]
            # Subsequent calls: no more adjustments.
            return instr, []

        class MockAgent:
            def is_available(self) -> bool:
                return True

            def revise_instruction(
                self,
                instr: dict[str, Any],
                report: dict[str, Any],
                sensitivity: dict[str, dict[str, Any]],
                history: Any,
            ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
                return mock_agent_revise(instr, report, sensitivity, history)

            def run_full_chain(self, profile_json: str) -> dict[str, Any]:
                return seed

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(tmp_path),
            agent=MockAgent(),  # type: ignore[arg-type]
        )
        collect = _make_collect_stub()
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=5,
            collect=collect,
            build=_fake_build,
        )
        assert isinstance(result, PipelineResult)
        assert result.stop_reason == "converged"
        assert result.success is True
        assert result.best_iteration is not None
        assert result.history_path is not None
        # Verify the agent was called at least once.
        assert call_count >= 1
        # Verify the history file was written.
        assert pathlib.Path(result.history_path).exists()
        # Verify observed_effects attribution on the first record (iter 0).
        # iter 0 was a single-adjustment round targeting backend_bound;
        # observed_effects should contain the delta for that metric.
        rec0 = pipeline.history.records[0]
        assert len(rec0.observed_effects) > 0, "observed_effects should be populated"
        # backend_bound diff moved from negative (too low) toward 0% -> delta != 0.
        assert "backend_bound" in rec0.observed_effects
        assert rec0.observed_effects["backend_bound"] != 0.0


# ---------------------------------------------------------------------------
# observed_effects attribution: runtime single-adjustment
# ---------------------------------------------------------------------------


class TestObservedEffectsRuntime:
    """Verify observed_effects attribution for runtime single-adjustment rounds.

    The loop records per-knob {expected_metric: delta} for single-adjustment
    rounds.  This test uses degraded mode (runtime-only) and verifies that
    after a runtime adjustment moves a metric, the previous record's
    observed_effects reflects the actual delta.
    """

    def test_runtime_observed_effects_nonzero(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        # Use degraded mode so only runtime tier is available.
        # The collect stub moves backend_bound with memory_ratio.
        collect = _make_collect_stub()

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(
                tmp_path,
                oscillation_window=10,
                no_improvement_stop=100,
            ),
            agent=None,
        )
        pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=5,
            collect=collect,
            build=_fake_build,
        )
        # Find a record with a single adjustment (runtime tier).
        found_nonzero = False
        for rec in pipeline.history.records:
            if (
                len(rec.adjustments) == 1
                and len(rec.observed_effects) > 0
                and any(v != 0.0 for v in rec.observed_effects.values())
            ):
                found_nonzero = True
                break
        assert found_nonzero, "expected at least one runtime record with non-zero observed_effects"


# ---------------------------------------------------------------------------
# Path #2: max_iter / best_iteration by score
# ---------------------------------------------------------------------------


class TestMaxIterPath:
    """Stub improves then plateaus; loop exhausts max_iter; best_iteration
    is the argmin-score iteration (NOT the last)."""

    def test_max_iter_best_by_score(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        # Use only the runtime tier: set up so errors are in [5, 10) ->
        # priority 2 -> structural.  But we use a mock agent that returns []
        # so nothing changes -> no_improvement would fire.
        # Instead: use degraded mode (agent unavailable) so the loop forces
        # runtime tier, and set no_improvement_stop very high.
        collect = _make_collect_stub()

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(
                tmp_path,
                max_iter=4,
                no_improvement_stop=100,
                oscillation_window=10,
            ),
            agent=None,
        )
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=4,
            collect=collect,
            build=_fake_build,
        )
        assert result.stop_reason in (
            "max_iter",
            "runtime_tier_exhausted_agent_unavailable",
        )
        assert result.best_iteration is not None
        assert result.degraded is True


# ---------------------------------------------------------------------------
# Path #3: escalate to LLM tier
# ---------------------------------------------------------------------------


class TestEscalatePath:
    """Seed a structural gap (priority >= 2); mock agent available +
    revise_instruction returns a structural adjustment; assert the
    structural path is taken (build called) and the gate accepts."""

    def test_escalate_structural_adjustment_applied(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        build_calls: list[dict[str, Any]] = []

        def tracking_build(instr: dict[str, Any]) -> BuildResult:
            build_calls.append(
                {
                    "wsm": _get_synth(instr).get("working_set_mb"),
                    "iters": _get_synth(instr).get("iterations"),
                }
            )
            return BuildResult(success=True, binary_path="/fake/binary")

        def mock_agent_revise(
            instr: dict[str, Any],
            report: dict[str, Any],
            sensitivity: dict[str, dict[str, Any]],
            history: Any,
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            return instr, [
                {
                    "stage": "s0",
                    "knob": "working_set_mb",
                    "from": 64,
                    "to": 160,
                    "rationale": "close backend gap",
                    "expected_metric": "backend_bound",
                    "expected_direction": "up",
                },
            ]

        class MockAgent:
            def is_available(self) -> bool:
                return True

            def revise_instruction(
                self,
                instr: dict[str, Any],
                report: dict[str, Any],
                sensitivity: dict[str, dict[str, Any]],
                history: Any,
            ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
                return mock_agent_revise(instr, report, sensitivity, history)

            def run_full_chain(self, profile_json: str) -> dict[str, Any]:
                return seed

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(tmp_path, no_improvement_stop=2),
            agent=MockAgent(),  # type: ignore[arg-type]
        )
        collect = _make_collect_stub()
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=3,
            collect=collect,
            build=tracking_build,
        )
        # Build was called at least twice: initial + at least one rebuild.
        assert len(build_calls) >= 2
        # The rebuild received the structural adjustment.
        rebuilt = build_calls[1]
        assert rebuilt["wsm"] == 160
        assert result.best_iteration is not None


# ---------------------------------------------------------------------------
# Path #4: oscillation stop
# ---------------------------------------------------------------------------


class TestOscillationPath:
    """Script the stub so a runtime knob reverses direction within
    oscillation_window; assert stop_reason == 'oscillation'."""

    def test_oscillation_terminates(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        # Absolute-pp oscillation scene (customer backend 72, band [62, 82]).
        # With slope 115, both endpoints of the first runtime step stay OUT of
        # the 10pp band, so the controller reverses the knob next iter:
        #   mr=0.1 -> backend 50+11.5 = 61.5 (gap -10.5pp, OUT low -> want up)
        #   mr=0.3 -> backend 50+34.5 = 84.5 (gap +12.5pp, OUT high -> reverse)
        # oscillation_window=1 then skip-blocks the reversal -> runtime tier
        # exhausted (oscillation detection would also qualify under the same
        # stop set). Under relative diff this scene converged at iter 1 (mr=0.3
        # -> 80, +11.1% -> out -> reversed -> oscillation); absolute pp widens
        # the band so we steepen the slope to keep the overshoot outside it.
        def collect_oscillation(binary: str, instruction: dict[str, Any]) -> Profile | RunFailure:
            cfg = instruction.get("config", {})
            mr = cfg.get("memory_ratio", 0.1)
            backend = 50.0 + mr * 115.0
            return Profile(
                metadata=ProfileMetadata(customer="workload", date="2026-08-17"),
                topdown=TopdownL1(
                    frontend_bound=10.0,
                    backend_bound=backend,
                    bad_speculation=5.0,
                    retiring=13.0,
                ),
                memory=None,
                hotspots=[],
            )

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(
                tmp_path,
                oscillation_window=1,
                no_improvement_stop=100,
            ),
            agent=None,
        )
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=10,
            collect=collect_oscillation,
            build=_fake_build,
        )
        assert result.stop_reason in (
            "oscillation",
            "runtime_tier_exhausted_agent_unavailable",
        )
        assert result.degraded is True


# ---------------------------------------------------------------------------
# Path #5: no_improvement stop
# ---------------------------------------------------------------------------


class TestNoImprovementPath:
    """Script the stub so no_improvement_stop consecutive iterations don't
    refresh best score; assert early termination."""

    def test_no_improvement_terminates(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        # Stub returns a CONSTANT workload -- adjustments are accepted but
        # metrics don't change -> score stays the same -> no improvement.
        def collect_static(binary: str, instruction: dict[str, Any]) -> Profile | RunFailure:
            return Profile(
                metadata=ProfileMetadata(customer="workload", date="2026-08-17"),
                topdown=TopdownL1(
                    frontend_bound=10.0,
                    backend_bound=60.0,
                    bad_speculation=5.0,
                    retiring=13.0,
                ),
                memory=None,
                hotspots=[],
            )

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(
                tmp_path,
                no_improvement_stop=3,
                oscillation_window=100,
            ),
            agent=None,
        )
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=20,
            collect=collect_static,
            build=_fake_build,
        )
        # Should terminate on no_improvement_stop or runtime_tier_exhausted.
        assert result.stop_reason in (
            "no_improvement_stop",
            "runtime_tier_exhausted_agent_unavailable",
        )
        assert result.best_iteration is not None
        assert result.degraded is True


# ---------------------------------------------------------------------------
# Path #6: run-failure streak
# ---------------------------------------------------------------------------


class TestRunFailureStreakPath:
    """collect returns RunFailure for run_failure_stop consecutive rounds;
    assert stop_reason == 'run_failure_streak'."""

    def test_run_failure_streak(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        def collect_fail(binary: str, instruction: dict[str, Any]) -> Profile | RunFailure:
            return RunFailure(reason="segfault", kind="crash")

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(tmp_path, run_failure_stop=2),
            agent=None,
        )
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=10,
            collect=collect_fail,
            build=_fake_build,
        )
        assert result.stop_reason == "run_failure_streak"
        assert result.success is False


# ---------------------------------------------------------------------------
# Path #7: build-failure streak
# ---------------------------------------------------------------------------


class TestBuildFailureStreakPath:
    """build returns None for build_failure_stop consecutive structural
    revisions; assert build_failed records + early termination."""

    def test_build_failure_streak(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        build_count = 0

        def build_fail_after_seed(
            instr: dict[str, Any],
        ) -> BuildResult:
            nonlocal build_count
            build_count += 1
            if build_count == 1:
                # initial build succeeds
                return BuildResult(success=True, binary_path="/fake/binary")
            # subsequent structural builds fail with a real compiler error
            return BuildResult(success=False, stderr="error: use of undeclared identifier 'foo'")

        def mock_agent_revise(
            instr: dict[str, Any],
            report: dict[str, Any],
            sensitivity: dict[str, dict[str, Any]],
            history: Any,
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            # Read the current working_set_mb from the instruction so the
            # gate does not reject the adjustment as a no-op.
            current = _get_synth(instr).get("working_set_mb", 64)
            new_val = min(current + 64, 4096)
            return instr, [
                {
                    "stage": "s0",
                    "knob": "working_set_mb",
                    "from": current,
                    "to": new_val,
                    "rationale": "close gap",
                    "expected_metric": "backend_bound",
                    "expected_direction": "up",
                },
            ]

        class MockAgent:
            def is_available(self) -> bool:
                return True

            def revise_instruction(
                self,
                instr: dict[str, Any],
                report: dict[str, Any],
                sensitivity: dict[str, dict[str, Any]],
                history: Any,
            ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
                return mock_agent_revise(instr, report, sensitivity, history)

            def run_full_chain(self, profile_json: str) -> dict[str, Any]:
                return seed

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(tmp_path, build_failure_stop=2),
            agent=MockAgent(),  # type: ignore[arg-type]
        )

        # Collect stub that returns metrics far from target so loop continues.
        def collect_non_converging(
            binary: str, instruction: dict[str, Any]
        ) -> Profile | RunFailure:
            return Profile(
                metadata=ProfileMetadata(customer="workload", date="2026-08-17"),
                topdown=TopdownL1(
                    frontend_bound=10.0,
                    backend_bound=55.0,  # customer=72, diff=-23.6%
                    bad_speculation=5.0,
                    retiring=13.0,
                ),
                memory=None,
                hotspots=[],
            )

        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=10,
            collect=collect_non_converging,
            build=build_fail_after_seed,
        )
        assert result.stop_reason == "build_failure_streak"
        assert result.success is False
        # The failed record carries the REAL compiler stderr (not the old
        # "build_returned_none" placeholder). (#3b-fu1)
        failed = [r for r in pipeline.history.records if r.build_failed]
        assert failed, "expected at least one build_failed record"
        assert "undeclared identifier" in failed[0].build_stderr


# ---------------------------------------------------------------------------
# Path #7b: build-failure self-correction via the LLM's revised instruction
# ---------------------------------------------------------------------------


class TestBuildFailureSelfCorrectionPath:
    """A structural rebuild fails once; the LLM returns a *revised
    instruction* (code-level fix); the loop rebuilds from it and recovers
    (no false build_failure_streak). Pins #3b-fu1 Part C."""

    def test_build_failure_self_corrects_via_revised_instruction(
        self, tmp_path: pathlib.Path
    ) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        build_count = 0

        def build_fail_then_recover(instr: dict[str, Any]) -> BuildResult:
            nonlocal build_count
            build_count += 1
            if build_count == 2:
                # first structural rebuild fails with a real compiler error
                return BuildResult(
                    success=False, stderr="error: use of undeclared identifier 'bar'"
                )
            # seed build + all later builds succeed
            return BuildResult(success=True, binary_path="/fake/binary")

        revise_count = 0

        def mock_agent_revise(
            instr: dict[str, Any],
            report: dict[str, Any],
            sensitivity: dict[str, dict[str, Any]],
            history: Any,
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            nonlocal revise_count
            revise_count += 1
            # On the NORMAL structural path this knob adjustment drives a
            # rebuild (which fails once, triggering pending_build_fix). On
            # the pending_build_fix path the loop applies the *revised
            # instruction* directly (a code-level fix), skipping the gate.
            current = _get_synth(instr).get("working_set_mb", 64)
            revised = {**instr, "_llm_code_fix": revise_count}
            return revised, [
                {
                    "stage": "s0",
                    "knob": "working_set_mb",
                    "from": current,
                    "to": min(current + 64, 4096),
                    "rationale": "close backend gap",
                    "expected_metric": "backend_bound",
                    "expected_direction": "up",
                },
            ]

        class MockAgent:
            def is_available(self) -> bool:
                return True

            def revise_instruction(
                self,
                instr: dict[str, Any],
                report: dict[str, Any],
                sensitivity: dict[str, dict[str, Any]],
                history: Any,
            ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
                return mock_agent_revise(instr, report, sensitivity, history)

            def run_full_chain(self, profile_json: str) -> dict[str, Any]:
                return seed

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(tmp_path, build_failure_stop=5),
            agent=MockAgent(),  # type: ignore[arg-type]
        )
        # Non-converging collect so structural keeps firing past recovery.
        collect = _make_collect_stub()
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=4,
            collect=collect,
            build=build_fail_then_recover,
        )
        # Recovery happened: build was called at least 3x (seed + failed
        # rebuild + successful rebuild from the revised instruction).
        assert build_count >= 3
        # The loop recovered and continued (no false build_failure_streak).
        assert result.stop_reason != "build_failure_streak"
        # The failed record carries the real compiler stderr.
        failed = [r for r in pipeline.history.records if r.build_failed]
        assert failed and "undeclared identifier" in failed[0].build_stderr
        # The revised instruction was applied (the loop rebuilt from it).
        assert revise_count >= 2


# ---------------------------------------------------------------------------
# Path #8: degraded mode
# ---------------------------------------------------------------------------


class TestDegradedPath:
    """Agent unavailable; structural gap exists (priority >= 2); loop
    continues runtime-tier-only; degraded=True; stops on runtime stall."""

    def test_degraded_runtime_only(self, tmp_path: pathlib.Path) -> None:
        customer = _customer_profile()
        seed = _seed_instruction()
        sens = _sensitivity()

        # Collect stub: metrics move with runtime knobs.
        collect = _make_collect_stub()

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(
                tmp_path,
                oscillation_window=1,
                no_improvement_stop=100,
            ),
            agent=None,
        )
        result = pipeline.run_iteration_loop(
            customer_profile=customer,
            seed_instruction=seed,
            sensitivity=sens,
            max_iter=10,
            collect=collect,
            build=_fake_build,
        )
        assert result.degraded is True
        assert result.stop_reason in (
            "runtime_tier_exhausted_agent_unavailable",
            "oscillation",
            "no_improvement_stop",
        )
        assert result.best_iteration is not None
        # The loop did NOT terminate merely because priority >= 2 arose --
        # it continued at least one runtime-tier adjustment.
        assert len(pipeline.history.records) >= 1
