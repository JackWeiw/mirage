"""Tests for demangled C++ symbol parsing."""

from codegen.signature import parse_signature


def test_namespaced_with_params() -> None:
    sig = parse_signature("foo::index::lookup(int, std::string)")
    assert sig.namespace == "foo::index"
    assert sig.name == "lookup"
    assert sig.params == "int, std::string"
    assert sig.declaration == "void foo::index::lookup(int, std::string)"


def test_no_params() -> None:
    sig = parse_signature("foo::index::init")
    assert sig.namespace == "foo::index"
    assert sig.name == "init"
    assert sig.params == ""
    assert sig.declaration == "void foo::index::init()"


def test_top_level_function() -> None:
    sig = parse_signature("main(int, char**)")
    assert sig.namespace == ""
    assert sig.name == "main"
    assert sig.params == "int, char**"


def test_unparseable_falls_back() -> None:
    # Garbage / mangled: keep the raw string as the function name, no namespace.
    sig = parse_signature("_ZN3foo3barEv")
    assert sig.name == "_ZN3foo3barEv"
    assert sig.namespace == ""
    assert sig.params == ""
    assert sig.declaration is None
