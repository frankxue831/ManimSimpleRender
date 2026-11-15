"""Read-only, Qt-free runtime preflight for ordinary Scene agents."""

import argparse
import json
import shutil
import sys

from app.environment import doctor
from app.environment.models import CheckStatus


def inspect_environment(
    require_latex=False,
    which=shutil.which,
    run=doctor._default_run,
    find_spec=doctor.importlib.util.find_spec,
):
    report = doctor.check_environment(find_spec=find_spec, which=which, run=run, frozen=False)
    checks = []
    for check in report.checks:
        if check.name == "LaTeX":
            continue  # Default Manim Tex needs latex + dvisvgm, not pdflatex alone.
        status = check.status.value
        if check.name == "ffmpeg" and check.status is not CheckStatus.OK:
            status = "failed"
        checks.append(
            {
                "name": check.name,
                "status": status,
                "detail": check.detail.replace(" (pip install manim)", ""),
            }
        )
    for name, flag, required in (
        ("ffprobe", "-version", True),
        ("latex", "--version", require_latex),
        ("dvisvgm", "--version", require_latex),
    ):
        path = which(name)
        valid = path and doctor._tool_version([path, flag], run, 15) is not None
        checks.append(
            {
                "name": name,
                "status": "ok" if valid else ("failed" if required else "warning"),
                "detail": path if valid else f"{name} missing or not runnable on PATH",
            }
        )
    build = []
    pkg = which("pkg-config")
    for package in ("cairo", "pangocairo"):
        valid = pkg and doctor._tool_version([pkg, "--modversion", package], run, 15) is not None
        build.append(
            {
                "name": package,
                "status": "ok" if valid else "warning",
                "detail": (
                    "build metadata available"
                    if valid
                    else (
                        "build metadata unavailable; may block installation, "
                        "not an existing runtime"
                    )
                ),
            }
        )
    blockers = [c["name"] for c in checks if c["status"] == "failed"]
    return {
        "schema_version": 1,
        "workflow": "ordinary_scene",
        "interpreter": sys.executable,
        "python_version": sys.version.split()[0],
        "ok": not blockers,
        "blockers": blockers,
        "checks": checks,
        "build_diagnostics": build,
        "render_smoke_test": "not_run",
        "recovery": {
            "manim_install_argv": [sys.executable, "-m", "pip", "install", "manim==0.22.1"],
            "guidance": (
                "Use the intended virtual environment. Install OS prerequisites "
                "from README before pip if needed. Ensure ffmpeg and ffprobe "
                "are runnable on PATH. For default Tex install latex and dvisvgm. "
                "No installation is performed by this command."
            ),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit structured report")
    parser.add_argument("--require-latex", action="store_true", help="Require default Tex tools")
    args = parser.parse_args(argv)
    result = inspect_environment(require_latex=args.require_latex)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Interpreter: {result['interpreter']}")
        for check in result["checks"]:
            print(f"[{check['status']}] {check['name']}: {check['detail']}")
        print(result["recovery"]["guidance"])
        print("Manim installation argv: " + json.dumps(result["recovery"]["manim_install_argv"]))
        print("Render smoke test: not run. Run the agent quickstart control next.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
