"""Environment preflight: can the CURRENT interpreter actually render?

Qt-free. Mirrors how a render is launched (``sys.executable -m manim ...``): manim
is not merely located but EXERCISED -- the doctor runs ``-m manim --version`` (or
the external ``manim`` for a frozen app) so a broken/un-runnable install (missing
pycairo, dead interpreter symlink) is caught instead of reported OK (BL-07). ffmpeg
and LaTeX are probed on PATH and their ``--version`` is run too, so a present-but-
broken tool is a WARNING with the captured error (BL-34). Every probe is injectable
so the doctor is unit-testable headlessly with no real subprocess / no tools.
"""

import importlib.util
import re
import shutil
import subprocess
import sys

from app.environment.models import CheckStatus, EnvironmentCheck, EnvironmentReport

# Bounded so a hung/slow tool can never freeze the (button-initiated) check.
_VERSION_TIMEOUT = 15
# stderr markers that mean the interpreter could start manim but its import blew up.
_ERROR_MARKERS = ("Traceback (most recent call", "ImportError", "ModuleNotFoundError")


def _default_run(argv, timeout):
    """Run ``argv`` capturing output as text. Wrapper so callers/tests can inject."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _parse_version(text):
    """Extract the first ``X.Y[.Z]`` version token from tool output, or ""."""
    match = re.search(r"\d+\.\d+(?:\.\d+)?", text or "")
    return match.group(0) if match else ""


def _looks_like_error(text):
    return any(marker in (text or "") for marker in _ERROR_MARKERS)


def check_python(frozen=None):
    """Report the interpreter. In a frozen app sys.executable is the GUI binary (not
    the render interpreter, which is an external manim), so label it as such (BL-34)."""
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    version = sys.version.split()[0]
    if frozen:
        return EnvironmentCheck(
            "Python", CheckStatus.OK, f"{version} (app runtime; render uses external manim on PATH)"
        )
    return EnvironmentCheck("Python", CheckStatus.OK, f"{version} at {sys.executable}")


def check_manim(
    find_spec=importlib.util.find_spec,
    which=shutil.which,
    frozen=None,
    run=_default_run,
    timeout=_VERSION_TIMEOUT,
):
    """manim must actually RUN for the render. Normal: ``[sys.executable, -m, manim,
    --version]`` in THIS interpreter. Frozen app: the external ``manim`` on PATH. A
    non-zero exit or an ImportError/Traceback in stderr is FAILED (BL-07)."""
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    if frozen:
        path = which("manim")
        if not path:
            return EnvironmentCheck(
                "manim", CheckStatus.FAILED, "manim not found on PATH (install manim and retry)"
            )
        argv = [path, "--version"]
    else:
        try:
            present = find_spec("manim") is not None
        except (ImportError, ValueError):
            present = False
        if not present:
            return EnvironmentCheck(
                "manim",
                CheckStatus.FAILED,
                "not installed in this Python environment (pip install manim)",
            )
        argv = [sys.executable, "-m", "manim", "--version"]
    # Exercise it -- a locatable-but-broken manim is the exact failure the doctor exists
    # to catch.
    try:
        result = run(argv, timeout)
    except FileNotFoundError:
        return EnvironmentCheck("manim", CheckStatus.FAILED, "manim executable not found")
    except subprocess.TimeoutExpired:
        return EnvironmentCheck("manim", CheckStatus.FAILED, "manim --version timed out")
    except OSError as exc:
        return EnvironmentCheck("manim", CheckStatus.FAILED, f"manim could not run: {exc}")
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0 or _looks_like_error(stderr):
        lines = (stderr or stdout).splitlines()
        first = lines[0] if lines else f"exit {result.returncode}"
        return EnvironmentCheck(
            "manim", CheckStatus.FAILED, f"installed but failed to run: {first}"
        )
    version = _parse_version(stdout)
    return EnvironmentCheck(
        "manim", CheckStatus.OK, f"version {version}" if version else "installed"
    )


def _tool_version(argv, run, timeout):
    """Return the parsed version of a tool, "" if it ran but no version was found, or
    None if it could not run (so the caller can warn about a present-but-broken tool)."""
    try:
        result = run(argv, timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_version(f"{result.stdout or ''} {result.stderr or ''}")


def check_ffmpeg(which=shutil.which, run=_default_run, timeout=_VERSION_TIMEOUT):
    """ffmpeg must be on PATH and runnable -- manim shells out to it to encode video."""
    path = which("ffmpeg")
    if not path:
        return EnvironmentCheck(
            "ffmpeg",
            CheckStatus.FAILED,
            "ffmpeg not found on PATH (manim needs it to encode video)",
        )
    version = _tool_version([path, "-version"], run, timeout)
    if version is None:
        return EnvironmentCheck(
            "ffmpeg", CheckStatus.WARNING, f"{path} (present but '-version' failed to run)"
        )
    return EnvironmentCheck("ffmpeg", CheckStatus.OK, f"{path} ({version})" if version else path)


def check_latex(which=shutil.which, run=_default_run, timeout=_VERSION_TIMEOUT):
    """LaTeX is optional -- only Tex/MathTex scenes need it -- so absence (or a broken
    install) is a WARNING, not a failure."""
    path = which("latex") or which("pdflatex")
    if not path:
        return EnvironmentCheck(
            "LaTeX", CheckStatus.WARNING, "not found (only needed for Tex/MathTex scenes)"
        )
    version = _tool_version([path, "--version"], run, timeout)
    if version is None:
        return EnvironmentCheck(
            "LaTeX", CheckStatus.WARNING, f"{path} (present but '--version' failed to run)"
        )
    return EnvironmentCheck("LaTeX", CheckStatus.OK, f"{path} ({version})" if version else path)


def check_environment(
    find_spec=importlib.util.find_spec,
    which=shutil.which,
    run=_default_run,
    timeout=_VERSION_TIMEOUT,
    frozen=None,
):
    """Run every preflight check and return an :class:`EnvironmentReport`."""
    return EnvironmentReport(
        [
            check_python(frozen=frozen),
            check_manim(find_spec=find_spec, which=which, frozen=frozen, run=run, timeout=timeout),
            check_ffmpeg(which=which, run=run, timeout=timeout),
            check_latex(which=which, run=run, timeout=timeout),
        ]
    )
