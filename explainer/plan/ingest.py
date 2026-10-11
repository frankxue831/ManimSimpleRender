"""Read the focus ranges a planner run is allowed to see, and stamp provenance.

Every failure here happens BEFORE the LLM call, so a typo in --focus costs
nothing. The commit is stamped from the target repo's real HEAD and a dirty
tree is refused by default, because the rendered video shows a
"verified @ <commit>" badge that must not overstate what was checked.
"""

import os

from explainer.cite.verifier import repo_dirty_lines, run_git
from explainer.plan.facts import FactSheet, FocusTarget
from explainer.storyboard.cites import parse_cite

# Extension -> (language hint, kind). Kind selects the evidence extractor:
# prose ranges yield verbatim phrases, code ranges yield literals/identifiers.
_LANGUAGES = {
    ".py": ("python", "code"),
    ".rs": ("rust", "code"),
    ".js": ("javascript", "code"),
    ".ts": ("typescript", "code"),
    ".go": ("go", "code"),
    ".java": ("java", "code"),
    ".c": ("c", "code"),
    ".h": ("c", "code"),
    ".cpp": ("cpp", "code"),
    ".sh": ("bash", "code"),
    ".yaml": ("yaml", "code"),
    ".yml": ("yaml", "code"),
    ".toml": ("toml", "code"),
    ".json": ("json", "code"),
    ".md": ("markdown", "prose"),
    ".markdown": ("markdown", "prose"),
    ".rst": ("rst", "prose"),
    ".txt": ("text", "prose"),
}


class IngestError(Exception):
    """Raised when a focus target cannot be resolved, or the tree is dirty."""


def parse_focus(spec):
    """Split ``path``/``path:line``/``path:start-end`` into ``(path, start, end)``.

    ``start``/``end`` are ``None`` for a bare path, meaning "the whole file".
    The ``path:lineref`` form IS the cite grammar -- parse_cite owns it, so a
    focus spec and the cite it produces can never be spelled differently.
    """
    if ":" not in spec:
        return spec, None, None
    try:
        path, start, end = parse_cite(spec)
    except ValueError:
        raise IngestError(f"malformed focus {spec!r} (expected path, path:line, or path:a-b)")
    if start < 1 or end < start:
        raise IngestError(f"bad line range in focus {spec!r} (need 1 <= start <= end)")
    return path, start, end


def _source_state(repo_root, allow_dirty, ignore_paths=()):
    head = run_git(repo_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise IngestError(
            f"cannot resolve HEAD of {repo_root}: {head.stderr.strip() or 'not a git repo?'}"
        )
    # ignore_paths excludes the CURRENT run's own would-be .staging/.rejected
    # output companions (see cli.py) -- a previous run's leftover .rejected
    # must not make an otherwise-clean tree read as dirty for THIS run.
    # repo_dirty_lines is the same probe verify_source_state applies at write
    # time, so the two dirty checks can never disagree about what counts as
    # the planner's own mess versus a real uncommitted change.
    lines = repo_dirty_lines(repo_root, ignore_paths)
    dirty = bool(lines)
    if dirty and not allow_dirty:
        raise IngestError(
            f"{len(lines)} uncommitted change(s) in {repo_root}; "
            f"cited lines may not match HEAD (pass --allow-dirty to accept)"
        )
    return head.stdout.strip(), dirty


def _read_target(repo_root, spec):
    path, start, end = parse_focus(spec)
    full = os.path.join(repo_root, path)
    real_root = os.path.realpath(repo_root)
    real_full = os.path.realpath(full)
    try:
        contained = os.path.commonpath([real_full, real_root]) == real_root
    except ValueError:
        # Paths on different drives (Windows) can't share a common path,
        # which by definition means the target is not inside repo_root.
        contained = False
    if not contained:
        raise IngestError(f"focus target {path!r} resolves outside repo root {repo_root!r}")
    if os.path.isabs(path):
        # facts.py's FocusTarget.path docstring promises "repo-relative,
        # spelled the way a cite spells it" -- recording the machine-specific
        # absolute form here would make the cite expand.py builds from it
        # (and the storyboard it ends up in) verifiable only on the machine
        # that ran this planner run. Re-derive it from the realpaths the
        # containment check above already computed, so it names exactly the
        # target that check just approved.
        path = os.path.relpath(real_full, real_root)
    try:
        with open(full, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except UnicodeDecodeError as exc:
        # Not an OSError subclass (it is a ValueError), so it needs its own
        # clause: a non-UTF-8 focus file (wrong encoding, or genuinely
        # binary) must fail closed here rather than escape as a raw
        # traceback past the CLI's `except IngestError`.
        raise IngestError(f"focus target {path!r} is not valid UTF-8 text: {exc}")
    except OSError as exc:
        raise IngestError(f"cannot read focus target {path!r}: {exc}")
    if not lines:
        raise IngestError(f"focus target {path!r} is empty")
    if start is None:
        start, end = 1, len(lines)
    if end > len(lines):
        raise IngestError(f"focus {spec!r} runs past end of file ({len(lines)} lines)")
    language, kind = _LANGUAGES.get(os.path.splitext(path)[1].lower(), ("", "code"))
    return FocusTarget(
        path=path,
        start=start,
        end=end,
        text="\n".join(lines[start - 1 : end]),
        language=language,
        kind=kind,
    )


def build_factsheet(repo_root, focus_specs, topic, beat_budget, allow_dirty, ignore_paths=()):
    """Resolve every focus spec against *repo_root* and stamp the repo's git state.

    ``ignore_paths`` (absolute filesystem paths) are excluded from the dirty
    check -- cli.py passes the CURRENT run's would-be ``.staging``/
    ``.rejected`` output companions here, so a previous run's leftover
    ``.rejected`` file cannot make a clean repo look dirty (see F1).
    """
    if not focus_specs:
        raise IngestError("at least one --focus target is required")
    repo_root = os.path.abspath(repo_root)
    commit, dirty = _source_state(repo_root, allow_dirty, ignore_paths)
    targets = tuple(_read_target(repo_root, spec) for spec in focus_specs)
    return FactSheet(
        repo_root=repo_root,
        commit=commit,
        dirty=dirty,
        topic=topic,
        targets=targets,
        beat_budget=beat_budget,
    )
