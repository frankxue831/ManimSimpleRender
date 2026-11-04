"""Explicit, bounded project transfer. Inspection never imports project code.

An unsigned inventory proves consistency, not authenticity or code safety.
Review received sources before explicitly invoking rendering or other commands.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile

from . import pairs

MAX_FILES = 128
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 72 * 1024 * 1024
MAX_DIRECTORY_BYTES = 1024 * 1024
ROLES = {"source", "subtitle", "asset", "config", "documentation"}
RESERVED = {"handoff.json", "project.json"}


def _json(raw):
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("JSON exceeds 2 MiB")
    return json.loads(raw, object_pairs_hook=pairs)


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError("expected a relative file path of at most 200 characters")
    p = PurePosixPath(value)
    parts = value.split("/")
    blocked = {"__pycache__", "node_modules", "venv", "cache", "caches", "partial_movie_files"}
    if (
        str(p) != value
        or p.is_absolute()
        or any(c in value for c in "\\:")
        or any(ord(c) < 32 for c in value)
        or any(s in {"", ".", ".."} or s.startswith(".") for s in parts)
        or any(s.casefold() in blocked or s.endswith((" ", ".")) for s in parts)
        or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", s) for s in parts)
        or p.suffix.casefold() in {".pem", ".key", ".p12", ".pfx", ".pyc", ".env"}
    ):
        raise ValueError("unsafe, secret or cache path: " + value)
    return value


def _paths(values):
    seen = set()
    normalized = []
    for value in values:
        _path(value)
        alias = unicodedata.normalize("NFC", value).casefold()
        if alias in seen:
            raise ValueError("duplicate or portable-path alias: " + value)
        seen.add(alias)
        normalized.append(alias)
    for value in normalized:
        if any(
            parent in seen for parent in map(str, PurePosixPath(value).parents) if parent != "."
        ):
            raise ValueError("file path also used as a directory")


def _requirements(req):
    if not isinstance(req, dict) or set(req) != {
        "python_min",
        "python_max",
        "packages",
        "tools",
        "fonts",
    }:
        raise ValueError("requirements need python_min/python_max, packages, tools and fonts")
    for name in ("python_min", "python_max"):
        v = req[name]
        if not isinstance(v, list) or len(v) != 2 or any(type(n) is not int or n < 0 for n in v):
            raise ValueError("Python bounds must be [major, minor]")
    if req["python_min"] >= req["python_max"]:
        raise ValueError("Python range is empty")
    if not isinstance(req["packages"], dict) or len(req["packages"]) > 64:
        raise ValueError("packages must map distribution names to exact versions")
    for name, version in req["packages"].items():
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name)
            or not isinstance(version, str)
            or not re.fullmatch(r"[A-Za-z0-9_.+!-]{1,64}", version)
        ):
            raise ValueError("invalid package name or exact version")
    for name in ("tools", "fonts"):
        if (
            not isinstance(req[name], list)
            or len(req[name]) > 32
            or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in req[name])
        ):
            raise ValueError("tools/fonts must be lists of names")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in req["tools"]):
        raise ValueError("tools are executable names, not paths or commands")


def _project(spec):
    if not isinstance(spec, dict) or set(spec) != {
        "version",
        "name",
        "manifest",
        "files",
        "requirements",
    }:
        raise ValueError("project needs version, name, manifest, files and requirements")
    if type(spec["version"]) is not int or spec["version"] != 1:
        raise ValueError("unsupported project version")
    if not isinstance(spec["name"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", spec["name"]):
        raise ValueError("project name must be a short lowercase identifier")
    _path(spec["manifest"])
    files = spec["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("select 1 to 128 input files explicitly")
    for f in files:
        if not isinstance(f, dict) or set(f) != {"path", "role", "license"}:
            raise ValueError("each file needs path, role and license/permission note")
        _path(f["path"])
        if (
            f["path"].casefold() in RESERVED
            or not isinstance(f["role"], str)
            or f["role"] not in ROLES
        ):
            raise ValueError("reserved name or unknown file role")
        if not isinstance(f["license"], str) or not f["license"].strip() or len(f["license"]) > 200:
            raise ValueError("record redistribution permission for every selected file")
    _paths([f["path"] for f in files] + sorted(RESERVED))
    if spec["manifest"] not in [f["path"] for f in files]:
        raise ValueError("select the revision manifest as an input")
    _requirements(spec["requirements"])


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _provenance(handoff):
    for name in ("producer", "tool"):
        if not isinstance(handoff.get(name), dict):
            raise ValueError("invalid handoff provenance: " + name)
    if any(
        not isinstance(handoff["producer"].get(k), str) for k in ("python", "system", "machine")
    ):
        raise ValueError("invalid producer provenance")
    if not isinstance(handoff["tool"].get("portable_module_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", handoff["tool"]["portable_module_sha256"]
    ):
        raise ValueError("invalid tool provenance")


def _content(raw):
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("selected file exceeds 16 MiB")
    if re.search(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----", raw):
        raise ValueError("private-key content is not exportable")


def _closure(spec, contents):
    entry = _json(contents[spec["manifest"]])
    if not isinstance(entry, dict):
        raise ValueError("revision manifest must be an object")
    deps = entry.get("dependencies")
    if not isinstance(deps, list):
        raise ValueError("revision manifest must declare dependencies")
    base = PurePosixPath(spec["manifest"]).parent
    for relative in [entry.get("source"), entry.get("timeline"), *deps]:
        _path(relative)
        selected = str(base / relative)
        if selected not in contents:
            raise ValueError("missing selected revision input: " + selected)
    return entry


def export_project(spec_path, output):
    spec_path = Path(spec_path)
    if spec_path.is_symlink():
        raise ValueError("project specification must not be a link")
    root = spec_path.resolve().parent
    with spec_path.open("rb") as handle:
        spec = _json(handle.read(2 * 1024 * 1024 + 1))
    _project(spec)
    contents, inventory, total = {}, [], 0
    for f in spec["files"]:
        current = root
        for part in PurePosixPath(f["path"]).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("selected paths must not traverse links")
        if not current.is_file() or not stat.S_ISREG(current.stat().st_mode):
            raise ValueError("missing selected regular file: " + f["path"])
        if total + current.stat().st_size > MAX_TOTAL_BYTES:
            raise ValueError("aggregate selected inputs exceed 64 MiB")
        raw = _local_bytes(root, f["path"], min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total))
        total += len(raw)
        contents[f["path"]] = raw
        inventory.append({**f, "bytes": len(raw), "sha256": _sha(raw)})
    if sum(map(len, contents.values())) > MAX_TOTAL_BYTES:
        raise ValueError("selected inputs exceed 64 MiB")
    entry = _closure(spec, contents)
    handoff = {
        "version": 1,
        "project": spec,
        "files": inventory,
        "producer": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "tool": {
            "name": "manim-simple-render",
            "portable_module_sha256": _sha(Path(__file__).read_bytes()),
        },
        "commands": {
            "receiving_launcher": {
                "file_from_trusted_product": "scene_production/launcher.py",
                "argv_placeholder": "<TRUSTED_LAUNCHER>",
                "required_python_option": "-I",
                "notice": (
                    "Resolve <TRUSTED_LAUNCHER> from the trusted product before using recipes; "
                    "never use an imported project script."
                ),
            },
            "render": [
                "python",
                "-I",
                "<TRUSTED_LAUNCHER>",
                "render",
                "--scene-file",
                str(PurePosixPath(spec["manifest"]).parent / entry["source"]),
                "--scene",
                entry.get("scene", "Scene"),
                "--timeline",
                str(PurePosixPath(spec["manifest"]).parent / entry["timeline"]),
                "--output-dir",
                "runs",
                "--quality",
                "qm",
                "--record-scene-frames",
            ],
            "revision_manifest": spec["manifest"],
            "outputs": {
                "receipt": "runs/scene-*/result.json",
                "video": "receipt.video",
                "inspection_frames": "receipt.frames",
            },
        },
        "notice": (
            "Unsigned inventory; inspect sources before trusted rendering. "
            "No sandbox, semantic proof or automatic installation."
        ),
    }
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
        archive.writestr("project.json", json.dumps(spec, ensure_ascii=False, indent=2))
        archive.writestr("handoff.json", json.dumps(handoff, ensure_ascii=False, indent=2))
    return handoff


def _archive_budget(path):
    """Bound ZIP metadata work before ZipFile constructs its entry objects.

    This small-project format needs one ordinary central directory, not split
    volumes, ZIP64 end records, archive comments, prefixes or trailing material.
    An uncommented final record uses the same ZipFile fast-path selection.
    """
    path = Path(path)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("archive container exceeds regular-file / 72 MiB budget")
    with path.open("rb") as source:
        if source.read(4) != b"PK\x03\x04":
            raise ValueError("archive requires an initial local header; prefixes are refused")
        size = metadata.st_size
        source.seek(max(0, size - 42))
        footer = source.read(42)
        if len(footer) >= 42 and footer[:4] == b"PK\x06\x07":
            raise ValueError("ZIP64 end-directory locators are outside this project format")
        end = footer[-22:]
        if len(end) != 22 or end[:4] != b"PK\x05\x06":
            raise ValueError("project archive requires an ordinary uncommented ZIP end record")
        record = struct.unpack("<4s4H2LH", end)
        if record[-1] != 0:
            raise ValueError("project archive requires an uncommented ZIP end record")
        _, disk, directory_disk, disk_count, count, length, offset, _ = record
        if count > MAX_FILES + 2 or length > MAX_DIRECTORY_BYTES:
            raise ValueError("archive directory exceeds preparse count/size budget")
        absolute_end = size - 22
        if disk or directory_disk or disk_count != count or offset + length != absolute_end:
            raise ValueError("unsupported archive end-directory layout")
        source.seek(offset)
        directory = source.read(length)
    cursor = entries = 0
    first_local_header = None
    while cursor < len(directory):
        if directory[cursor : cursor + 4] != b"PK\x01\x02" or cursor + 46 > len(directory):
            raise ValueError("invalid archive central directory")
        local_header = struct.unpack_from("<L", directory, cursor + 42)[0]
        first_local_header = (
            local_header if first_local_header is None else min(first_local_header, local_header)
        )
        name, extra, comment = struct.unpack_from("<HHH", directory, cursor + 28)
        cursor += 46 + name + extra + comment
        entries += 1
        if entries > MAX_FILES + 2:
            raise ValueError("archive directory exceeds preparse entry budget")
    if cursor != len(directory) or entries != count:
        raise ValueError("archive directory length/count mismatch")
    if first_local_header != 0:
        raise ValueError("first referenced local header must start at zero; prefixes are refused")


def _archive(path):
    _archive_budget(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if (
                len(infos) > MAX_FILES + 2
                or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES + 4 * 1024 * 1024
            ):
                raise ValueError("archive exceeds input count/size budget")
            _paths([i.filename for i in infos])
            for i in infos:
                kind = stat.S_IFMT(i.external_attr >> 16)
                if (
                    i.is_dir()
                    or kind not in (0, stat.S_IFREG)
                    or i.flag_bits & 1
                    or i.file_size > MAX_FILE_BYTES
                ):
                    raise ValueError("encrypted, linked, special or oversized archive entry")
            if "handoff.json" not in archive.namelist() or "project.json" not in archive.namelist():
                raise ValueError("missing handoff/project inventory")
            if any(archive.getinfo(n).file_size > 2 * 1024 * 1024 for n in RESERVED):
                raise ValueError("handoff/project JSON exceeds 2 MiB")
            handoff = _json(archive.read("handoff.json"))
            spec = _json(archive.read("project.json"))
            _project(spec)
            if (
                not isinstance(handoff, dict)
                or handoff.get("version") != 1
                or handoff.get("project") != spec
            ):
                raise ValueError("handoff version/project mismatch")
            _provenance(handoff)
            if not isinstance(handoff.get("files"), list) or len(handoff["files"]) != len(
                spec["files"]
            ):
                raise ValueError("invalid file inventory")
            expected = {f["path"] for f in spec["files"]} | RESERVED
            if set(archive.namelist()) != expected:
                raise ValueError("unlisted or missing archive input")
            if sum(i.file_size for i in infos if i.filename not in RESERVED) > MAX_TOTAL_BYTES:
                raise ValueError("aggregate selected inputs exceed 64 MiB")
            contents = {}
            for selected, f in zip(spec["files"], handoff["files"]):
                if not isinstance(f, dict) or {k: f.get(k) for k in selected} != selected:
                    raise ValueError("inventory role/path/permission mismatch")
                raw = archive.read(selected["path"])
                _content(raw)
                if (
                    type(f.get("bytes")) is not int
                    or f["bytes"] != len(raw)
                    or f.get("sha256") != _sha(raw)
                ):
                    raise ValueError("input size/hash mismatch: " + selected["path"])
                contents[selected["path"]] = raw
            _closure(spec, contents)
            contents["project.json"] = archive.read("project.json")
            contents["handoff.json"] = archive.read("handoff.json")
            return handoff, contents
    except (zipfile.BadZipFile, KeyError, TypeError, UnicodeError, NotImplementedError) as exc:
        raise ValueError("invalid project archive: " + str(exc)) from exc


def inspect_bundle(path):
    return _archive(path)[0]


def unpack_bundle(path, destination):
    handoff, contents = _archive(path)
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise ValueError("destination already exists; choose a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".handoff-", dir=destination.parent))
    try:
        for relative, raw in contents.items():
            p = stage / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        if os.path.lexists(destination):
            raise ValueError("destination appeared; refusing overwrite")
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return handoff


def _local_bytes(root, relative, max_bytes=MAX_FILE_BYTES):
    _path(relative)
    p = root
    for part in PurePosixPath(relative).parts:
        p = p / part
        if p.is_symlink():
            raise ValueError("project files must not traverse links: " + relative)
    if not p.is_file() or not stat.S_ISREG(p.stat().st_mode):
        raise ValueError("missing regular project file: " + relative)
    with p.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("file or aggregate input budget exceeded")
    _content(raw)
    return raw


def probe_environment(requirements):
    """Probe installed distributions/tools/fonts only; never import project sources."""
    versions = {}
    for name in requirements["packages"]:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    fonts, font_error = [], None
    if requirements["fonts"]:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import json,manimpango; print(json.dumps(manimpango.list_fonts()))",
                ],
                cwd=tempfile.gettempdir(),
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode:
                font_error = (
                    "Font inventory unavailable; use an environment with manimpango installed."
                )
            else:
                fonts = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            font_error = "Font inventory unavailable; check installed manimpango/Pango."
    return {
        "python": list(sys.version_info[:2]),
        "packages": versions,
        "tools": [t for t in requirements["tools"] if shutil.which(t)],
        "fonts": fonts,
        "font_error": font_error,
        "system": platform.system(),
    }


def preflight(root, environment=None):
    """Read-only integrity/static/environment checks; ready does not authorize code."""
    root = Path(root).resolve()
    report = {
        "ready": False,
        "errors": [],
        "warnings": [],
        "notice": (
            "No project code executed. Ready checks declared prerequisites, "
            "not safety or semantic correctness."
        ),
    }
    try:
        spec = _json(_local_bytes(root, "project.json"))
        _project(spec)
        handoff = _json(_local_bytes(root, "handoff.json"))
        if (
            not isinstance(handoff, dict)
            or handoff.get("version") != 1
            or handoff.get("project") != spec
        ):
            raise ValueError("handoff/project mismatch")
        _provenance(handoff)
        inventory = handoff.get("files")
        if not isinstance(inventory, list) or len(inventory) != len(spec["files"]):
            raise ValueError("invalid file inventory")
        total, contents = 0, {}
        for selected, item in zip(spec["files"], inventory):
            if not isinstance(item, dict) or {k: item.get(k) for k in selected} != selected:
                raise ValueError("inventory role/path/permission mismatch")
            raw = _local_bytes(root, selected["path"], min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total))
            total += len(raw)
            if len(raw) != item.get("bytes") or _sha(raw) != item.get("sha256"):
                raise ValueError(
                    "input hash mismatch: "
                    + selected["path"]
                    + "; retain baseline and export a new revision after editing"
                )
            contents[selected["path"]] = raw
        if total > MAX_TOTAL_BYTES:
            raise ValueError("project inputs exceed 64 MiB")
        _closure(spec, contents)
        from .revision import manifest

        m, _, _, _, _ = manifest(root / spec["manifest"])
        report["project"] = spec["name"]
        report["scene_ids"] = [s["id"] for s in m["segments"]]
        report["requirements"] = spec["requirements"]
    except (ValueError, OSError, KeyError, TypeError) as exc:
        report["errors"].append({"code": "inputs", "message": str(exc)})
        return report
    req = spec["requirements"]
    env = probe_environment(req) if environment is None else environment
    report["environment"] = env
    if not req["python_min"] <= env["python"] < req["python_max"]:
        report["errors"].append(
            {
                "code": "python",
                "message": (
                    f"Use Python >= {req['python_min']} and < {req['python_max']}; "
                    f"found {env['python']}"
                ),
            }
        )
    for name, version in req["packages"].items():
        actual = env["packages"].get(name)
        if actual != version:
            report["errors"].append(
                {
                    "code": "package",
                    "message": (
                        f"Use an environment with {name}=={version}; "
                        f"found {actual or 'not installed'}. No installation performed."
                    ),
                }
            )
    for name in req["tools"]:
        if name not in env["tools"]:
            report["errors"].append(
                {
                    "code": "tool",
                    "message": (
                        f"Required executable {name} is missing from PATH; "
                        "select an existing render environment."
                    ),
                }
            )
    for name in req["fonts"]:
        if name not in env["fonts"]:
            report["errors"].append(
                {
                    "code": "font",
                    "message": (
                        f"Required font {name} is unavailable. Use an installed licensed font "
                        "and explicitly update/re-export the project; pixels may change."
                    ),
                }
            )
    if env.get("font_error"):
        report["errors"].append({"code": "font_probe", "message": env["font_error"]})
    if handoff.get("producer", {}).get("system") != env["system"]:
        report["warnings"].append(
            "Producer/receiver operating systems differ; "
            "no cross-platform pixel equality guarantee."
        )
    if handoff.get("tool", {}).get("portable_module_sha256") != _sha(Path(__file__).read_bytes()):
        report["warnings"].append(
            "Portable tool implementation differs from producer; review version/provenance."
        )
    report["warnings"].append(
        "Font names do not prove glyph coverage; native Cairo/Pango/FFmpeg versions "
        "and rendering remain to be checked. Review source before rendering."
    )
    report["ready"] = not report["errors"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Export only explicitly selected authorised inputs")
    export.add_argument("--project", required=True)
    export.add_argument("--output", required=True)
    inspect = sub.add_parser("inspect", help="Validate ZIP without executing project code")
    inspect.add_argument("bundle")
    unpack = sub.add_parser("unpack", help="Validate and unpack to a new directory")
    unpack.add_argument("bundle")
    unpack.add_argument("destination")
    check = sub.add_parser("preflight", help="Check received inputs and installed prerequisites")
    check.add_argument("directory")
    args = parser.parse_args()
    try:
        if args.command == "export":
            report = export_project(args.project, args.output)
        elif args.command == "inspect":
            report = inspect_bundle(args.bundle)
        elif args.command == "unpack":
            report = unpack_bundle(args.bundle, args.destination)
        else:
            report = preflight(args.directory)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if args.command == "preflight" and not report["ready"] else 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
