"""Citation gate: assert every on-screen code fact's ``evidence`` is present at
the cited source lines, in the storyboard's source repo.

This is the Explainer Engine's trust guarantee and its differentiator. A
callout's ``display`` text is never trusted (it may be a teaching
simplification); only the ``evidence`` tokens are checked against the real
source. A wrong constant or a renamed symbol fails the gate.
"""

import os
import re
import subprocess
from dataclasses import dataclass

from explainer.storyboard.cites import is_regex_token, parse_cite
from explainer.storyboard.model import iter_citation_targets

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class CiteFailure:
    beat_id: str
    cite: str
    missing: tuple
    reason: str = "evidence_not_found"

    def __str__(self):
        return f"[{self.beat_id}] {self.cite}: {self.reason}: missing {list(self.missing)}"


@dataclass(frozen=True)
class SourceStateFailure:
    """The repo's git state contradicts what the storyboard claims about it."""

    reason: str  # commit_mismatch | commit_invalid | dirty_tree | git_unavailable
    detail: str

    def __str__(self):
        return f"[source] {self.reason}: {self.detail}"


def _normalize(text):
    return _WS.sub(" ", text).strip()


def _dirty_lines(status_stdout, repo_root, ignore_paths=()):
    """``git status --porcelain`` output lines, minus any naming a path in
    ``ignore_paths``.

    ``ignore_paths`` are absolute filesystem paths a caller knows are its own
    in-flight temporary files -- concretely, emit.py's ``.staging``/
    ``.rejected`` companions of a storyboard being written INTO the repo it
    cites. Without this, the mere act of writing (or having previously
    failed to write) a storyboard would make an otherwise-clean tree read as
    dirty, purely as a side effect of checking it. This is a positional,
    exact-path match, never a name/suffix heuristic, so it cannot hide an
    unrelated real change that happens to share a suffix.
    """
    if not ignore_paths:
        return status_stdout.strip().splitlines()
    ignore_abs = {os.path.abspath(p) for p in ignore_paths}
    kept = []
    for line in status_stdout.splitlines():
        if not line:
            continue
        # Porcelain v1: two status chars, a space, then the path (or, for a
        # rename/copy, "old -> new" -- the working tree now has NEW).
        path_part = line[3:]
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        path_part = path_part.strip()
        if len(path_part) >= 2 and path_part[0] == path_part[-1] == '"':
            path_part = path_part[1:-1]
        if os.path.abspath(os.path.join(repo_root, path_part)) in ignore_abs:
            continue
        kept.append(line)
    return kept


def _read_cited(repo_root, cite):
    path, start, end = parse_cite(cite)
    with open(os.path.join(repo_root, path), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    if start < 1 or end > len(lines) or start > end:
        raise IndexError(f"cite range {start}-{end} out of bounds (file has {len(lines)} lines)")
    return "\n".join(lines[start - 1 : end])


def _missing_evidence(text, evidence):
    """Return evidence tokens NOT found in ``text``. A ``/pattern/`` token is a
    regex; everything else is a whitespace-normalized substring match."""
    normalized = _normalize(text)
    missing = []
    for token in evidence:
        token = str(token)
        if is_regex_token(token):
            try:
                found = re.search(token[1:-1], text) is not None
            except re.error:
                found = False  # a malformed pattern cannot be verified -> count as missing
            if not found:
                missing.append(token)
        elif _normalize(token) not in normalized:
            missing.append(token)
    return missing


_HEX = frozenset("0123456789abcdef")


def run_git(repo_root, *args):
    """Run git in *repo_root*, never raising: a missing/unrunnable git binary
    reports as a synthetic returncode-127 result instead of an OSError, so
    every caller has exactly one failure shape to check. Public because
    explainer/plan/ingest.py probes the same repo state at plan time."""
    try:
        return subprocess.run(
            ["git", "-C", repo_root, *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:  # git not installed / not executable
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=127, stdout="", stderr=str(exc)
        )


def repo_dirty_lines(repo_root, ignore_paths=()):
    """Uncommitted-change lines of ``git status --porcelain`` for *repo_root*,
    minus any naming a path in ``ignore_paths`` (see :func:`_dirty_lines`).

    The ONE spelling of "is this tree dirty" -- verify_source_state applies it
    at write/verify time and ingest applies it at plan time, so the two can
    never disagree about what counts as the planner's own scratch files versus
    a real uncommitted change. A repo whose status cannot be read reports as
    clean here; resolving HEAD is the caller's separate, louder check.
    """
    status = run_git(repo_root, "status", "--porcelain")
    if status.returncode != 0:
        return []
    return _dirty_lines(status.stdout, repo_root, ignore_paths)


def verify_source_state(source, repo_root=None, ignore_paths=()):
    """Check the repo's git state against the storyboard's claims about it.

    The on-screen badge says "verified @ <commit>", so when ``source.commit``
    is set it must be substantiated: the repo's HEAD must start with it, and a
    dirty working tree fails unless ``allow_dirty`` (cited lines are read from
    disk, so uncommitted edits could diverge from the claimed commit). With no
    commit claimed, git is never consulted -- non-git source roots stay valid.

    ``ignore_paths`` (see :func:`_dirty_lines`) excludes specific absolute
    paths from the dirty check -- emit.py passes its own ``.staging``/
    ``.rejected`` files here so writing a storyboard into the repo it cites
    does not fail on the write itself.
    """
    if not source.commit:
        return []
    root = repo_root or source.repo_root
    claimed = source.commit.strip().lower()
    # A too-short or non-hex prefix would make startswith() vacuous (a 1-char
    # claim matches 1 in 16 repos; an empty one matches anything) -- the badge
    # must only ever show a claim that can actually fail. Git's own minimum
    # abbreviation is 4 hex chars.
    if len(claimed) < 4 or not set(claimed) <= _HEX:
        return [
            SourceStateFailure(
                "commit_invalid",
                f"meta.source.commit {source.commit!r} is not a usable commit prefix "
                f"(need at least 4 hex characters)",
            )
        ]
    head = run_git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return [
            SourceStateFailure(
                "git_unavailable",
                f"storyboard claims commit {source.commit!r} but HEAD of {root} "
                f"cannot be resolved: {head.stderr.strip() or 'not a git repo?'}",
            )
        ]
    failures = []
    actual = head.stdout.strip()
    if not actual.lower().startswith(claimed):
        failures.append(
            SourceStateFailure(
                "commit_mismatch",
                f"storyboard claims commit {source.commit!r} but repo HEAD is "
                f"{actual[:12]} -- the badge would overstate; update meta.source.commit",
            )
        )
    if not source.allow_dirty:
        lines = repo_dirty_lines(root, ignore_paths)
        if lines:
            changes = len(lines)
            failures.append(
                SourceStateFailure(
                    "dirty_tree",
                    f"{changes} uncommitted change(s) in {root}; cited lines may not "
                    f"match commit {source.commit!r} (set allow_dirty: true to accept)",
                )
            )
    return failures


def verify_storyboard(storyboard, repo_root=None, ignore_paths=()):
    """Verify every cited code fact in ``storyboard``.

    Returns a list of :class:`CiteFailure` / :class:`SourceStateFailure`; an
    empty list means the source state matches what the storyboard claims AND
    every callout and claim's evidence was found at its cited lines.
    ``repo_root`` overrides ``storyboard.source.repo_root`` (useful for tests
    and pinned checkouts). ``ignore_paths`` is forwarded to
    :func:`verify_source_state`.
    """
    root = repo_root or storyboard.source.repo_root
    failures = list(
        verify_source_state(storyboard.source, repo_root=root, ignore_paths=ignore_paths)
    )
    for beat in storyboard.beats:
        for cite, evidence in iter_citation_targets(beat):
            try:
                text = _read_cited(root, cite)
            except (OSError, ValueError, IndexError) as exc:
                failures.append(
                    CiteFailure(beat.id, cite, tuple(evidence), reason=type(exc).__name__)
                )
                continue
            missing = _missing_evidence(text, evidence)
            if missing:
                failures.append(CiteFailure(beat.id, cite, tuple(missing)))
    return failures
