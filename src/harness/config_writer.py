"""Crash-safe atomic JSON config writer for the runtime iteration tier."""

import contextlib
import json
import os
import pathlib
import tempfile
from typing import Any

from observability.logging import get_logger

logger = get_logger("config_writer")


def write_config_json_atomic(path: pathlib.Path, config: dict[str, Any]) -> None:
    """Write ``config`` as JSON to ``path`` atomically (crash-safe).

    The runtime iteration tier overwrites ``project/config.json`` every runtime
    pass and re-runs the existing binary. On a weak/embedded ARM filesystem a
    write interrupted by crash, power loss, or signal would leave a truncated
    ``config.json`` and poison every subsequent iteration. This writes to a temp
    file in the SAME directory as the target (so the final move is a
    same-filesystem ``rename(2)``, atomic on POSIX -- a cross-device move would
    degrade to a non-atomic copy), ``fsync``s the temp, then ``os.replace``s it
    onto the target. The target is never opened in-place for writing.

    On any failure the temp file is unlinked and the target is left untouched
    (still the previous config).
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(config, indent=2) + "\n"
    # Same-dir temp so os.replace is an atomic same-filesystem rename.
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        # Clean up the orphaned temp; the target is untouched (still the prior
        # config). Swallow unlink errors -- the original failure is the real
        # error to propagate.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
