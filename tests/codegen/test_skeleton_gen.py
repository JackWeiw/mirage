"""Tests for ServiceSkeletonGen and identifier sanitization."""

from codegen.call_tree import CallTreeBuilder
from codegen.catalog import OpenSourceAPICatalog
from codegen.skeleton_gen import ServiceSkeletonGen, sanitize_identifier


def test_sanitize_identifier_strips_namespace_and_invalid_chars() -> None:
    assert (
        sanitize_identifier("folly::futures::detail::FutureImpl::then")
        == "folly_futures_detail_FutureImpl_then"
    )
    assert sanitize_identifier("StageA::run") == "StageA_run"


def test_render_produces_service_and_main(tmp_path) -> None:  # type: ignore[no-untyped-def]
    stacks = [
        (
            ["main", "Svc::process", "StageA::run", "folly::futures::detail::FutureImpl::then"],
            100,
        ),
        (["main", "Svc::process", "StageA::run", "Customer::hashFeature"], 50),
    ]
    desc = CallTreeBuilder(catalog=OpenSourceAPICatalog()).build(
        stacks, profile=None, project_name="t"
    )
    files = ServiceSkeletonGen().generate(desc, tmp_path, synth_files=["StageA_run_synth.h"])
    names = {p.name for p in files}
    assert {"service.h", "service.cpp", "main.cpp"} <= names

    service_h = (tmp_path / "service.h").read_text()
    assert "noinline" in service_h

    service_cpp = (tmp_path / "service.cpp").read_text()
    assert "Svc_process" in service_cpp  # sanitized service method
    assert "StageA_run_stage" in service_cpp
    assert "StageA_run_custom_synth" in service_cpp  # synth call emitted
    assert "makeFuture" in service_cpp  # catalog open-source call statement
    assert "<folly/futures/Future.h>" in service_cpp  # catalog include
    assert '#include "StageA_run_synth.h"' in service_cpp  # synth header included

    main_cpp = (tmp_path / "main.cpp").read_text()
    assert "std::thread" in main_cpp
    assert "Svc_process()" in main_cpp
