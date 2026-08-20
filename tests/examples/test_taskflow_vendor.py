"""Vendored taskflow is traceable: header set present, LICENSE is MIT, README records
tag + URL + manifest, and the manifest still matches the files on disk."""

import pathlib
import subprocess

_VENDOR = pathlib.Path(__file__).resolve().parents[2] / "examples" / "third_party" / "taskflow"


def test_master_include_present() -> None:
    assert (_VENDOR / "taskflow" / "taskflow.hpp").is_file()


def test_license_is_mit() -> None:
    text = (_VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert "MIT" in text and "permission" in text.lower()


def test_readme_records_tag_url_manifest() -> None:
    text = (_VENDOR / "README.md").read_text(encoding="utf-8")
    assert "v3.9.0" in text
    assert "github.com/taskflow/taskflow" in text
    assert "SHA-256" in text


def test_manifest_matches_files_on_disk() -> None:
    manifest = _VENDOR / "manifest.sha256"
    assert manifest.is_file()
    result = subprocess.run(
        ["sha256sum", "-c", "manifest.sha256"],
        cwd=str(_VENDOR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
