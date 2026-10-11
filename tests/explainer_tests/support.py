"""Shared fixtures for the planner test files.

One definition of the temp-git-repo builder and the write-permission probe:
a fix to either (a new git config knob, a different porcelain flag, a better
probe) lands here once instead of once per test file.
"""

import os
import subprocess
import tempfile

# The host's global git config must not leak into fixtures (commit signing,
# templates, hooks would all break the canned init/commit sequence).
_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}


def make_git_repo(files, commit=True):
    """Create a temp git repo containing *files* (a {relpath: text} mapping).

    With ``commit=False`` the repo is initialized but has no HEAD -- the
    fixture for "inside a git repo that cannot resolve a commit".
    """
    root = tempfile.mkdtemp()
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    if commit:
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "x")
    return root


def git(root, *args):
    """Run a git command in *root* (check=True), hermetic global config."""
    return subprocess.run(
        ["git", "-C", root, *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    )


def git_head(root):
    """The repo's full HEAD commit hash."""
    return git(root, "rev-parse", "HEAD").stdout.strip()


def confirm_write_is_blocked(case, directory):
    """Skip a permission-based test if *directory* turns out writable anyway.

    Some environments (root, certain CI containers/filesystems) do not
    enforce the read-only permission bit a test just set. Probing first,
    rather than trusting chmod blindly, keeps the test honest instead of
    silently testing nothing.
    """
    probe = os.path.join(directory, "_write_probe")
    try:
        with open(probe, "w", encoding="utf-8"):
            pass
    except OSError:
        return
    os.remove(probe)
    case.skipTest("permissions do not block writes in this environment (e.g. running as root)")
