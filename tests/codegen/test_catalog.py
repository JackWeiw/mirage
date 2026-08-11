"""Tests for OpenSourceAPICatalog."""

from codegen.catalog import OpenSourceAPICatalog


def test_lookup_hit_returns_call_spec() -> None:
    cat = OpenSourceAPICatalog()
    spec = cat.lookup("folly::futures::detail::FutureImpl::then")
    assert spec is not None
    assert "<folly/futures/Future.h>" in spec.includes
    assert "makeFuture" in spec.statement


def test_lookup_miss_returns_none() -> None:
    cat = OpenSourceAPICatalog()
    assert cat.lookup("UnknownNS::doesNotExist") is None


def test_record_fallback_caches_for_future_lookup() -> None:
    cat = OpenSourceAPICatalog()
    spec = cat.record_fallback("Cust::fn", ["<h.h>"], "f();")
    assert cat.lookup("Cust::fn") == spec
    cached = cat.lookup("Cust::fn")
    assert cached is not None
    assert cached.statement == "f();"
