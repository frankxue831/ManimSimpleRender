"""Explainer Engine CLI: ``plan``, ``verify``, and ``render`` a storyboard.

    explainer plan --repo R --focus F --topic T -o story.yaml   # draft from code
    explainer verify story.yaml                 # citation gate only
    explainer render story.yaml -o out.mp4      # codegen -> verify -> render

Exit codes: 0 ok, 2 validation/schema error, 3 citation miss, 4 render failure,
5 planner LLM failure, 6 planner produced nothing usable.
"""

import argparse
import os
import sys

from app.render.quality import known_quality_flags

from explainer.cite.verifier import verify_storyboard
from explainer.render.adapter import (
    CitationGateError,
    RenderError,
    render_storyboard,
    resolution_to_quality,
)
from explainer.storyboard.model import StoryboardError, iter_citation_targets, load_storyboard
from explainer.plan.client import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    AnthropicClient,
    ClientError,
    GrokCliClient,
    MaxTokensExhausted,
)
from explainer.plan.critique import MAX_REVISIONS_LIMIT, draft_plan_ir, revise_plan
from explainer.plan.emit import (
    EmitError,
    companion_paths,
    validate_output_location,
    write_storyboard,
)
from explainer.plan.expand import ExpandError, expand
from explainer.plan.ingest import IngestError, build_factsheet
from explainer.plan.plan_ir import PlanError
from explainer.plan.prompt import build_user_prompt

# Each unit of --max-repairs costs one additional real LLM call: _draft_plan
# below loops `range(max_repairs + 1)` times, calling client.draft() on every
# pass. Left unbounded, a mistyped flag or a persistently bad model could
# burn an unlimited number of paid API calls before the CLI gives up. 5
# repairs (6 attempts total, against a shipped default of 1) is generous
# headroom for iterating past a flaky schema mismatch while keeping the
# worst case small and predictable.
MAX_REPAIRS_LIMIT = 5

# --beats is only a hint rendered into the prompt as "Plan about N beats."
# (prompt.py's _BEAT_GUIDANCE) -- nothing downstream caps the number of
# beats a draft can actually contain, so an over-large value cannot itself
# multiply API calls the way --max-repairs can. What it CAN do is ask the
# model to plan an arc this format was never designed to carry in one JSON
# response (risking a MaxTokensExhausted burn on a single ill-posed request),
# or -- at the other end, 0 or negative -- render a nonsensical instruction
# that contradicts the system prompt's own "at least one beat MUST be a code
# beat." Real storyboards written so far top out at 4 beats (the shipped
# examples/evidence-budget.story.yaml has 3); 20 is roughly 5x that,
# generous headroom for a genuinely long topic while still catching a
# fat-fingered flag before any API spend.
MAX_BEATS_LIMIT = 20


def _quality_flag(value):
    """argparse type: normalize a human quality spelling to a manim flag.

    Accepts ``ql``/``qm``/``qh``/``qp``/``qk`` with or without the leading dash,
    or a resolution height like ``1080``/``1080p``/``1440p60`` (parsed by the
    same ``resolution_to_quality`` that handles ``theme.resolution``). The
    natural spelling ``--quality -ql`` is eaten by argparse (the dash starts a
    new option), so the bare forms are the recommended ones.
    """
    raw = str(value).strip()
    flag = raw if raw.startswith("-") else f"-{raw}"
    if flag in known_quality_flags():
        return flag
    from_height = resolution_to_quality(raw, fallback=None)
    if from_height:
        return from_height
    raise argparse.ArgumentTypeError(
        f"unknown quality {value!r} (accepted: ql/qm/qh/qp/qk, with or without "
        f"a leading dash, or a height like 480/720p/1080p30/1440/2160)"
    )


def _verify(args):
    try:
        storyboard = load_storyboard(args.storyboard)
    except StoryboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    failures = verify_storyboard(storyboard)
    if failures:
        print("citation gate FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 3
    print(f"citation gate OK: every cited fact verified against {storyboard.source.repo_root}")
    if storyboard.source.commit:
        # The gate just confirmed HEAD matches this claim (verify_source_state),
        # so the on-screen "verified @ <commit>" badge is substantiated.
        print(f"  commit: {storyboard.source.commit}  OK (repo HEAD matches)")
    for beat in storyboard.beats:
        for cite, evidence in iter_citation_targets(beat):
            print(f"  [{beat.id}] {cite}  OK ({len(evidence)} evidence tokens)")
    return 0


def _render(args):
    try:
        out = render_storyboard(
            args.storyboard, args.output, quality=args.quality, on_log=lambda line: print(line)
        )
    except StoryboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except CitationGateError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except RenderError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    print(f"rendered: {out}")
    return 0


def _build_client(args):
    """Construct the drafting client for ``--provider``.

    Provider defaults resolve HERE rather than in argparse because they are
    not interchangeable: ``--model`` and ``--effort`` parse to None so that
    "unset" stays distinguishable from "explicitly chosen". Handing grok
    Anthropic's defaults would send it a claude-* model id, and a
    ``--reasoning-effort`` that grok's default model rejects outright.

    Both clients satisfy the same one-method protocol, and everything
    downstream of ``draft()`` is provider-independent -- evidence tokens are
    extracted from real source either way, so the citation gate is exactly as
    strong whichever model drafted the plan.
    """
    if args.provider == "grok":
        return GrokCliClient(model=args.model, effort=args.effort)
    return AnthropicClient(
        model=args.model or DEFAULT_MODEL,
        effort=args.effort or DEFAULT_EFFORT,
        max_tokens=args.max_tokens,
    )


def _draft_plan(client, factsheet, max_repairs):
    """Draft and validate a plan IR, allowing *max_repairs* corrective retries.

    Returns ``(raw, plan)`` -- the raw dict is kept because the revision
    pass re-sends it to the model verbatim.
    """
    return draft_plan_ir(client, factsheet, build_user_prompt(factsheet), max_repairs)


def _plan(args):
    # Argument validation first and free: catch a nonsensical --max-repairs
    # or --beats before doing any work at all, same reasoning as the
    # ingest/output checks below -- see MAX_REPAIRS_LIMIT's and
    # MAX_BEATS_LIMIT's own comments for what each caps.
    if args.max_repairs < 0 or args.max_repairs > MAX_REPAIRS_LIMIT:
        print(
            f"error: --max-repairs must be between 0 and {MAX_REPAIRS_LIMIT} "
            f"(got {args.max_repairs})",
            file=sys.stderr,
        )
        return 2

    if args.beats < 1 or args.beats > MAX_BEATS_LIMIT:
        print(
            f"error: --beats must be between 1 and {MAX_BEATS_LIMIT} (got {args.beats})",
            file=sys.stderr,
        )
        return 2

    if args.revisions < 0 or args.revisions > MAX_REVISIONS_LIMIT:
        print(
            f"error: --revisions must be between 0 and {MAX_REVISIONS_LIMIT} "
            f"(got {args.revisions})",
            file=sys.stderr,
        )
        return 2

    # Ingest next and on its own: a bad focus target must cost nothing, so
    # every input error is reported before the client is ever constructed.
    # ignore_paths names THIS run's own would-be ".staging"/".rejected"
    # output companions -- the same paths emit.py itself will use, from the
    # same function -- so a previous run's leftover ".rejected" (plausible
    # when -o lands inside the repo being explained) cannot make an
    # otherwise-clean tree look dirty here (F1). companion_paths is pure
    # string arithmetic, so no filesystem access happens before
    # validate_output_location below.
    ignore_paths = companion_paths(args.output)
    try:
        factsheet = build_factsheet(
            args.repo, args.focus, args.topic, args.beats, args.allow_dirty, ignore_paths
        )
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(build_user_prompt(factsheet))
        return 0

    # See _build_client below for why the provider defaults resolve here
    # rather than in argparse.
    #
    # Also validated before the client is constructed, same reasoning as
    # ingest above: a bad output location (e.g. a read-only parent
    # directory) must be caught before any API spend, not after
    # write_storyboard() runs at the very end of a run that already paid for
    # a completion. write_storyboard() still has its own OSError handling
    # for the rarer case where the directory becomes unwritable in between.
    try:
        validate_output_location(args.output)
    except EmitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    client = _build_client(args)
    try:
        raw, plan = _draft_plan(client, factsheet, args.max_repairs)
    except MaxTokensExhausted as exc:
        # Checked before the (broader) ClientError below: a MaxTokensExhausted
        # IS a ClientError, but this is the one ClientError kind the design
        # spec's exit ladder puts under 6 ("no usable draft") rather than 5
        # ("LLM failure") -- the request reached the model and made progress.
        print(f"error: {exc}", file=sys.stderr)
        return 6
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 6

    # Quality pass: strictly never-worse. A failed revision is discarded
    # with a note; the run keeps the draft it already validated.
    original_plan = plan
    for _ in range(args.revisions):
        revised = revise_plan(client, factsheet, raw, args.max_repairs)
        if revised is None:
            print("note: revision pass failed; keeping the previous draft", file=sys.stderr)
            break
        raw, plan = revised

    output_dir = os.path.dirname(os.path.abspath(args.output))
    try:
        document, dropped = expand(plan, factsheet, output_dir)
    except ExpandError as exc:
        if plan is not original_plan:
            # The revision passed parse() but cannot expand (e.g. it went
            # diagram-only, or its cites yield no evidence). Never-worse:
            # fall back to the draft that was already accepted.
            print(
                f"note: revised plan failed expansion ({exc}); "
                f"falling back to the original draft",
                file=sys.stderr,
            )
            try:
                document, dropped = expand(original_plan, factsheet, output_dir)
            except ExpandError as original_exc:
                print(f"error: {original_exc}", file=sys.stderr)
                return 6
            # The fallback already re-expanded the draft; mark it consumed so
            # the beat-granularity comparison below does not re-run against a
            # `plan` that was never reassigned to `original_plan`.
            plan = original_plan
        else:
            print(f"error: {exc}", file=sys.stderr)
            return 6

    if plan is not original_plan and dropped:
        # Never-worse, at beat granularity: a revision that loses more beats
        # to weak evidence than the validated draft did is a regression, not
        # a revision. expand() is pure, so re-expanding the draft costs only
        # CPU and only on the already-rare dropped-beat path.
        try:
            original_document, original_dropped = expand(original_plan, factsheet, output_dir)
        except ExpandError:
            pass  # the draft cannot expand at all; the revision's result stands
        else:
            if len(dropped) > len(original_dropped):
                print(
                    f"note: the revision dropped {len(dropped)} beat(s) where the "
                    f"draft dropped {len(original_dropped)}; keeping the draft's expansion",
                    file=sys.stderr,
                )
                document, dropped = original_document, original_dropped

    for drop in dropped:
        print(f"dropped beat {drop.beat_id!r}: {drop.reason}", file=sys.stderr)

    try:
        written = write_storyboard(document, args.output)
    except EmitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        # rejected_path is None exactly when nothing was ever written (an
        # undumpable document, or an "io" filesystem failure) -- printing
        # the literal string "None" there would claim a file was kept when
        # none was.
        if exc.rejected_path is not None:
            print(f"  rejected draft kept at: {exc.rejected_path}", file=sys.stderr)
        return 3 if exc.kind == "citation" else 6

    print(f"planned: {written}")
    print(f"  {len(document['beats'])} beat(s), verified against {factsheet.repo_root}")
    print(f"  next: explainer render {written} -o out.mp4")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="explainer", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    verify = sub.add_parser("verify", help="run the citation gate against the source repo")
    verify.add_argument("storyboard")
    verify.set_defaults(fn=_verify)

    render = sub.add_parser("render", help="generate, verify, and render to an mp4")
    render.add_argument("storyboard")
    render.add_argument("-o", "--output", required=True)
    render.add_argument(
        "--quality",
        default=None,
        type=_quality_flag,
        help="render quality: ql/qm/qh/qp/qk (dash optional; use --quality=-ql for the "
        "dashed form) or a height like 1080p; default derives from theme.resolution",
    )
    render.set_defaults(fn=_render)

    plan_cmd = sub.add_parser("plan", help="draft a storyboard from real code with an LLM")
    plan_cmd.add_argument("--repo", required=True, help="root of the repo being explained")
    plan_cmd.add_argument(
        "--focus",
        required=True,
        action="append",
        metavar="PATH[:START-END]",
        help="a source range the planner may cite; repeatable",
    )
    plan_cmd.add_argument("--topic", required=True, help="what the video should explain")
    plan_cmd.add_argument("-o", "--output", required=True)
    plan_cmd.add_argument("--beats", type=int, default=4, help="beat budget hint (default 4)")
    plan_cmd.add_argument(
        "--provider",
        default="grok",
        choices=["anthropic", "grok"],
        help="which model drafts the plan (default grok, which is free via the "
        "grok CLI's own session; use anthropic for publish-bound runs)",
    )
    # --model and --effort default to None rather than to Anthropic's values
    # so that "unset" stays distinguishable from "explicitly chosen": the
    # per-provider default is resolved in _build_client, because handing grok
    # a claude-* model id -- or an --effort its default model rejects -- would
    # break every grok run. See _build_client.
    plan_cmd.add_argument(
        "--model", default=None, help="provider-specific model id (default: the provider's own)"
    )
    plan_cmd.add_argument(
        "--effort",
        default=None,
        choices=["low", "medium", "high", "xhigh", "max"],
    )
    plan_cmd.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="anthropic only; the grok CLI exposes no token budget",
    )
    plan_cmd.add_argument(
        "--max-repairs", type=int, default=1, help="corrective retries on a bad plan (default 1)"
    )
    plan_cmd.add_argument(
        "--revisions",
        type=int,
        default=1,
        help="quality revision passes after a valid draft, one model call each "
        "(default 1; 0 disables)",
    )
    plan_cmd.add_argument(
        "--allow-dirty",
        action="store_true",
        help="accept a dirty tree; recorded as allow_dirty in the storyboard",
    )
    plan_cmd.add_argument(
        "--dry-run", action="store_true", help="print the prompt and exit without calling the API"
    )
    plan_cmd.set_defaults(fn=_plan)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
