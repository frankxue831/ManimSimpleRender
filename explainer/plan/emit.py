"""Serialize, self-check, and atomically write the generated storyboard.

Generated output is validated by the SAME load_storyboard() and
verify_storyboard() a hand-authored file goes through -- there is deliberately
no second validation path here that could drift from explainer/storyboard/
model.py as the schema grows.

The check runs on a temp file in the DESTINATION directory, because
load_storyboard resolves a relative meta.source.repo_root against the
storyboard file's own directory; validating elsewhere would resolve a
different root. os.replace then makes the real write atomic, so a run that
fails validation cannot truncate a storyboard that was already there.

That destination directory can be inside the very repo being explained, which
means the citation gate's own dirty-tree check could otherwise see this
module's ".staging"/".rejected" scratch files as uncommitted changes and fail
on the write itself. write_storyboard passes both paths to verify_storyboard
as ignore_paths for exactly this reason -- a real uncommitted edit anywhere
else in the repo is still caught.
"""

import os

import yaml

from explainer.cite.verifier import verify_storyboard
from explainer.storyboard.model import StoryboardError, load_storyboard


class EmitError(Exception):
    """Generated output failed its own checks, or could not be produced at all.

    ``kind`` is ``"schema"`` (the document could not be serialized to YAML in
    the first place, or the loader rejected it -- including by raising
    something other than StoryboardError; see write_storyboard), ``"citation"``
    (the citation gate rejected it), or ``"io"`` (a filesystem operation --
    creating the output directory, writing the staging file, or one of the
    atomic replaces -- raised OSError; see validate_output_location and the
    outer OSError handling in write_storyboard). ``rejected_path`` is where
    the offending document was kept, staged next to ``output_path``; it is
    ``None`` when nothing was ever written, which happens when the document
    could not be serialized at all, and always for an ``"io"`` failure, since
    by definition nothing could be reliably written or renamed there.
    """

    def __init__(self, message, kind, rejected_path=None):
        super().__init__(message)
        self.kind = kind
        self.rejected_path = rejected_path


def _dump(document):
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=False, width=88)


def companion_paths(output_path):
    """The ``.staging``/``.rejected`` scratch paths for *output_path*.

    The single spelling of the suffix scheme: write_storyboard() creates these
    files, and cli.py must name the SAME paths in its pre-flight dirty-check
    ignore list (F1) -- deriving them in two places is how the two would
    drift. Pure string arithmetic on the absolute path; nothing here touches
    the filesystem.
    """
    output_path = os.path.abspath(output_path)
    return output_path + ".staging", output_path + ".rejected"


def validate_output_location(output_path):
    """Confirm *output_path* can plausibly be written, without writing it.

    Called from cli.py BEFORE the LLM client is constructed, so a bad output
    location -- most concretely, a read-only parent directory -- is reported
    at zero API cost instead of after drafting a plan nobody can save. This
    creates the destination directory as a side effect on success (the same
    thing write_storyboard's own os.makedirs does), including when a later,
    unrelated step of the run goes on to fail for its own reason; that is a
    real, minor side effect of checking this early, not a claim that the run
    as a whole succeeded.

    This is a point-in-time probe, not a guarantee: the directory can still
    become unwritable between this call and the real write, which is why
    write_storyboard() below has its own OSError handling too.
    """
    output_path = os.path.abspath(output_path)
    directory = os.path.dirname(output_path)
    probe = output_path + ".writetest"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(probe, "w", encoding="utf-8"):
            pass
    except OSError as exc:
        raise EmitError(f"cannot write to output directory {directory!r}: {exc}", "io") from exc
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass  # never created (the makedirs/open above already failed), or already gone


def write_storyboard(document, output_path):
    """Validate *document*, then write it to *output_path*. Returns the path.

    Every failure out of this function -- serialization, schema, citation, or
    a filesystem error -- surfaces as :class:`EmitError`, and none of them
    leaves a ``.staging`` file behind: a rejected document is renamed to
    ``.rejected`` instead (so it stays available for debugging), and a
    document that could not even be serialized, or a run that failed on a
    filesystem error, never has a ``.staging`` file left for it.
    """
    output_path = os.path.abspath(output_path)
    directory = os.path.dirname(output_path)
    staging, rejected = companion_paths(output_path)

    try:
        os.makedirs(directory, exist_ok=True)

        try:
            text = _dump(document)
        except Exception as exc:
            # yaml.safe_dump can refuse *document* outright (no representer
            # for some value in it) before any file is touched. Computing
            # the text BEFORE opening `staging` (rather than writing
            # straight into an open handle) means that failure never leaves
            # an empty .staging file behind -- there is nothing to clean up
            # because nothing was ever created.
            raise EmitError(
                f"the generated storyboard could not be serialized to YAML: "
                f"{type(exc).__name__}: {exc}",
                "schema",
            ) from exc

        with open(staging, "w", encoding="utf-8") as handle:
            handle.write(text)

        try:
            storyboard = load_storyboard(staging)
        except Exception as exc:
            # load_storyboard is documented to raise only StoryboardError,
            # but a document that yaml.safe_dump serializes without
            # complaint (a python `set` round-trips through the `!!set`
            # tag; a bare string stands in for a mapping just fine at the
            # YAML level) can still reach a bare `.get()` on a non-mapping
            # deep inside it -- e.g. _parse_theme or _parse_source -- and
            # raise something else entirely: AttributeError, TypeError, ....
            # That must not leak past this boundary as a surprise exception
            # type any more than it may orphan the staging file, so EVERY
            # failure here gets the schema-rejection treatment; the
            # exception type is named only when it is not the documented one.
            os.replace(staging, rejected)
            detail = (
                str(exc) if isinstance(exc, StoryboardError) else f"{type(exc).__name__}: {exc}"
            )
            raise EmitError(
                f"the generated storyboard is not valid: {detail}", "schema", rejected
            ) from exc

        # ignore_paths=(staging, rejected): the citation gate's dirty-tree
        # check must not see the staging file THIS call just wrote (or a
        # stale .rejected left by a previous failed run at this exact output
        # path) as an uncommitted change -- otherwise writing a storyboard
        # into the very repo it cites would always fail on the write itself
        # (F1). A real uncommitted edit anywhere else is still caught.
        failures = verify_storyboard(storyboard, ignore_paths=(staging, rejected))
        if failures:
            os.replace(staging, rejected)
            detail = "; ".join(str(f) for f in failures)
            raise EmitError(
                f"the generated storyboard failed its own citation gate: {detail}",
                "citation",
                rejected,
            )

        os.replace(staging, output_path)
        return output_path
    except OSError as exc:
        # Anything above -- os.makedirs, the staging write, or any of the
        # os.replace calls, including one raised while handling a
        # StoryboardError/citation rejection above -- can fail this way if
        # the directory becomes unwritable between validate_output_location
        # (cli.py's up-front check) and this call. Never leave a partial
        # .staging file behind; there is nothing meaningful to keep as
        # "rejected" here (unlike the schema/citation cases above, this
        # is not a rejection of the document's content).
        try:
            os.remove(staging)
        except OSError:
            pass
        raise EmitError(f"cannot write storyboard to {output_path!r}: {exc}", "io") from exc
