"""Best-effort private identity of the bundled implementation observed on disk.

This is diagnostic evidence, not loaded-code attestation or a reproducible-build
claim. It never inspects a caller's scene, environment, Git remote or credentials.
"""

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path

from app.render.diagnostics import exception_type, utc_timestamp

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES = ("app", "explainer", "scene_production")
_RESOURCES = ("app/config/config.v2.json", "app/ui/theme.qss")


def _raise_walk_error(error):
    raise error


def _source_identity():
    """Hash packaged Python and fixed resources, excluding scenes and user data."""
    files = []
    for package in _PACKAGES:
        root = _SOURCE_ROOT / package
        if root.is_symlink() or not (root / "__init__.py").is_file():
            raise OSError("bundled source unavailable")
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=_raise_walk_error):
            dirs[:] = sorted(name for name in dirs if name != "__pycache__")
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise OSError("bundled source symlink")
            files.extend(Path(directory) / name for name in names if name.endswith(".py"))
    files.extend(_SOURCE_ROOT / name for name in _RESOURCES)
    hashes = {}
    for path in sorted(files):
        if path.is_symlink():
            raise OSError("bundled source symlink")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        hashes[path.relative_to(_SOURCE_ROOT).as_posix()] = digest
    # Canonical JSON makes the aggregate independent of directory iteration.
    encoded = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "status": "observed",
        "algorithm": "sha256-of-canonical-file-hashes-v1",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "files": hashes,
    }


def implementation_identity():
    """Return an explicit unknown marker when a source observation is unavailable."""
    started = utc_timestamp()
    try:
        package_version = version("manim-simple-render")
    except (PackageNotFoundError, OSError, ValueError):
        package_version = "unknown"
    try:
        source = _source_identity()
    except (OSError, ValueError) as exc:
        source = {"status": "unknown", "reason": exception_type(exc)}
    return {
        "distribution": "manim-simple-render",
        "package_version": package_version,
        "observation_started_at": started,
        "observation_finished_at": utc_timestamp(),
        "source": source,
    }


def write_implementation_manifest(path, *, operation, run_id):
    """Create a 0600 sidecar; return False on storage failure without changing a run.

    Callers supply fixed operation labels and their existing run ID, and handle
    a False return through their own warning channel. An existing file is never
    overwritten. No fsync or concurrent source-edit consistency is promised.
    """
    path = Path(path)
    created = False
    try:
        value = {
            "schema_version": 1,
            "operation": operation,
            "run_id": run_id,
            "implementation": implementation_identity(),
        }
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        try:
            stream = os.fdopen(descriptor, "w", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
            stream.write("\n")
        return True
    except (OSError, ValueError, TypeError):
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        return False
