#!/usr/bin/env python3
"""Reference capture for the memory_bound scenario (marker-gated, numactl-pinned,
LLC-miss gate). The body lives in collect_common.run_reference_capture (DRY);
this entry point just pins the scenario dir so its own collection.yaml loads."""

import argparse
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import collect_common  # type: ignore[import-not-found]  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--out-dir", default=str(_HERE))
    ap.add_argument("--devkit-cmd", default=None)
    args = ap.parse_args()
    return collect_common.run_reference_capture(  # type: ignore[no-any-return]
        binary=args.binary,
        scenario_dir=pathlib.Path(args.out_dir),
        devkit_cmd=args.devkit_cmd,
    )


if __name__ == "__main__":
    raise SystemExit(main())
