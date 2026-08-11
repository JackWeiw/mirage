"""Open-source API catalog: hotspot function -> buildable call spec."""

import pathlib

import yaml

from codegen.call_tree import CallSpec

_CATALOG_PATH = pathlib.Path(__file__).parent.parent / "config" / "open_source_api_catalog.yaml"


class OpenSourceAPICatalog:
    """Lookup buildable call specs for open-source hotspot functions.

    Misses return None so the caller (Agent/LLM) can generate a call statement
    and cache it back via record_fallback() (in-process; persistence is a
    follow-up). This keeps the catalog extensible without code changes.
    """

    def __init__(self, config_path: pathlib.Path | None = None) -> None:
        path = config_path or _CATALOG_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        self._specs: dict[str, CallSpec] = {}
        self._libraries: dict[str, dict[str, str]] = {}
        for name, library in data.get("libraries", {}).items():
            self._libraries[name] = {
                "version": library.get("version", "0"),
                "cmake_name": library.get("cmake_name", name),
            }
            for fn, entry in library.get("functions", {}).items():
                self._specs[fn] = CallSpec(
                    includes=list(entry.get("includes", [])),
                    statement=entry.get("statement", ""),
                    setup=entry.get("setup", ""),
                )

    def library_specs(self) -> dict[str, dict[str, str]]:
        """Return per-library CMake/version metadata for dependency generation."""
        return self._libraries

    def lookup(self, function: str) -> CallSpec | None:
        """Return a CallSpec for the function, or None if not catalogued."""
        return self._specs.get(function)

    def record_fallback(
        self, function: str, includes: list[str], statement: str, setup: str = ""
    ) -> CallSpec:
        """Cache an LLM-generated call spec for future lookups (in-process)."""
        spec = CallSpec(includes=includes, statement=statement, setup=setup)
        self._specs[function] = spec
        return spec

    def available_functions(self) -> list[str]:
        """Return the demangled function names currently catalogued."""
        return list(self._specs)
