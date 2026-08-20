"""evaluate_criteria evaluates the four success criteria from a history's
topdown_diffs + stop_reason. This is the judgment the driver prints PASS/FAIL on,
so it is unit-tested in isolation (no real loop run)."""

import run_loop_demo  # type: ignore[import-not-found]

from observability.iteration_history import (
    IterationHistory,
    IterationRecord,
)


def _hist(
    diffs: list[dict[str, float]],
    converged: bool = False,
    priorities: list[int] | None = None,
) -> IterationHistory:
    """Build a history with per-iteration topdown_diffs (dominant=backend_bound)."""
    pr = priorities or [2] * len(diffs)
    h = IterationHistory(customer_name="t")
    for i, d in enumerate(diffs):
        h.add_record(
            IterationRecord(
                iteration=i,
                converged=(converged and i == len(diffs) - 1),
                topdown_diffs=d,
                strategy_priority=pr[i],
            )
        )
    h.total_iterations = len(diffs)
    return h


def test_pass_when_steered_monotone_and_converged() -> None:
    # seed gap 35 -> 22 -> 8, one structural iteration, converged.
    h = _hist(
        [
            {"backend_bound": -35.0},
            {"backend_bound": -22.0},
            {"backend_bound": 8.0},
        ],
        converged=True,
    )
    res = run_loop_demo.evaluate_criteria(h, "converged", "backend_bound", threshold=10.0)
    assert res.verdict == "PASS"
    assert res.criteria == [True, True, True, True]


def test_fail_when_no_structural_iteration() -> None:
    # only runtime (priority 1) iterations -> steering not triggered.
    h = _hist([{"backend_bound": -35.0}, {"backend_bound": -33.0}], priorities=[1, 1])
    res = run_loop_demo.evaluate_criteria(h, "max_iter", "backend_bound", threshold=10.0)
    assert res.verdict == "FAIL"
    assert res.criteria[0] is False  # steering triggered = False


def test_fail_on_two_bounces() -> None:
    # 35 -> 22 (better) -> 30 (bounce) -> 18 (better) -> 25 (second bounce) = FAIL monotonic
    h = _hist(
        [
            {"backend_bound": -35.0},
            {"backend_bound": -22.0},
            {"backend_bound": -30.0},
            {"backend_bound": -18.0},
            {"backend_bound": -25.0},
        ]
    )
    res = run_loop_demo.evaluate_criteria(h, "max_iter", "backend_bound", threshold=10.0)
    assert res.criteria[1] is False  # monotonic violated (two bounces)


def test_fail_when_non_dominant_exceeds_20pp_at_terminal() -> None:
    # dominant converges but bad_speculation is 25pp off -> criterion 4 fails.
    h = _hist(
        [
            {"backend_bound": -35.0},
            {"backend_bound": 5.0, "bad_speculation": -25.0},
        ],
        converged=True,
    )
    res = run_loop_demo.evaluate_criteria(h, "converged", "backend_bound", threshold=10.0)
    assert res.criteria[3] is False
    assert res.verdict == "FAIL"


def test_max_iter_pass_when_dominant_narrowed_to_under_10pp() -> None:
    # not converged, but final dominant gap 6pp and monotone -> pass terminal.
    h = _hist([{"backend_bound": -35.0}, {"backend_bound": -12.0}, {"backend_bound": -6.0}])
    res = run_loop_demo.evaluate_criteria(h, "max_iter", "backend_bound", threshold=10.0)
    assert res.criteria[2] is True
    assert res.verdict == "PASS"
