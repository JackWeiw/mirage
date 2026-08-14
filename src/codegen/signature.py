"""Deterministic parsing of demangled C++ flamegraph frame strings.

perf demangles symbols into a form like ``foo::index::lookup(int, std::string)``.
We recover namespace + name + parameter types from it. C++ name mangling does
not encode the return type, so declarations default to ``void`` (mirage's
synthesized call statements do not consume return values). Unparseable frames
— mangled (Itanium ``_Z`` prefix) or otherwise not matching the demangled
grammar — fall back to the raw string as the name with no namespace; this is
recoverable, unlike a dependency cycle.
"""

import re
from dataclasses import dataclass

# A demangled symbol: zero or more ``ns::`` qualifiers, a name, optional
# ``(params)``. We intentionally only match well-formed demangled frames;
# anything else (mangled, truncated, non-identifier) falls through to the raw
# fallback.
_SYMBOL_RE = re.compile(
    r"^(?P<qualifier>(?:[A-Za-z_]\w*(?:<[^>]*>)?::)*)"
    r"(?P<name>[A-Za-z_]\w*(?:<[^>]*>)?)"
    r"(?:\((?P<params>.*)\))?$"
)


@dataclass(frozen=True)
class ParsedSignature:
    namespace: str  # "" if top-level or unparseable
    name: str
    params: str  # "" if no parens
    declaration: str | None  # None when the frame was unparseable


def parse_signature(frame: str) -> ParsedSignature:
    """Parse a demangled frame string into namespace/name/params/declaration."""
    if frame.startswith("_Z"):
        # Itanium-mangled: not demangled, no readable namespace/signature.
        return ParsedSignature(namespace="", name=frame, params="", declaration=None)
    m = _SYMBOL_RE.match(frame)
    if m is None:
        return ParsedSignature(namespace="", name=frame, params="", declaration=None)
    qualifier = m.group("qualifier")
    # qualifier ends with "::"; strip the trailing "::" to get the namespace.
    namespace = qualifier[:-2] if qualifier.endswith("::") else ""
    name = m.group("name")
    params = m.group("params") or ""
    decl = f"void {namespace}::{name}({params})" if namespace else f"void {name}({params})"
    return ParsedSignature(namespace=namespace, name=name, params=params, declaration=decl)
