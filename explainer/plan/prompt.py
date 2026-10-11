"""Render a FactSheet into the prompt, and house the editorial instructions.

Line numbers shown here are ABSOLUTE file lines, because that is what a cite
means and what plan_ir validates against; showing excerpt-relative numbers
would invite off-by-N cites that fail validation for a reason the model cannot
see. Everything structural (anchors, evidence, YAML) is deliberately absent
from these instructions -- code owns it, so the prompt does not describe it.

topic, path, kind, and language come from the caller (topic) or the target
repo's filesystem/ingest.py (the rest), and the repo being explained may not
be trustworthy, so all four are passed through _single_line() before being
interpolated: a value that embeds a line break could otherwise start a bare
line that reads as one of this module's own "--- focus_index: N ---" /
"path: ..." / "lines: ..." headers, ahead of (or instead of) the real one.
kind and language sit on the same "lines: ..." header line as the range
itself, so a break embedded in either forges a second header block exactly
like one embedded in path would -- they get the identical treatment.

`text` needs a different defense, because unlike the other four fields its
line COUNT is itself meaningful: the "lines: start-end" header promises
exactly (end - start + 1) numbered lines, and plan_ir.parse() validates a
cite against that same range, so under- or over-counting is not merely
cosmetic. _split_lines(text) splits on every character str.splitlines()
treats as a break -- not just a literal "\n" -- so none of them can survive
embedded inside a rendered line, same as the other four fields. Where it
deliberately disagrees with str.splitlines() is a *trailing* break:
str.splitlines() silently drops the blank line that implies ("a\n".splitlines()
== ["a"], not ["a", ""]), and _split_lines restores it as a trailing "" line.
That matters because FocusTarget.text is, per its own docstring, start..end's
lines joined with "\n" and nothing appended after -- so a target whose last
line is blank (an ordinary PEP8 trailing separator; this is universal in real
source) is indistinguishable, in `text`, from one that simply has one fewer
line, unless the trailing break is counted. Silently under-counting desyncs
the header from what actually gets a numeric prefix and lets plan_ir accept a
cite for a line the model was never shown -- see _split_lines's own docstring
for the mechanics.
"""

import json

from explainer.plan.facts import FactSheet, FocusTarget


def _single_line(text):
    """Flatten *text* to one physical line.

    Joins the pieces str.splitlines() would treat as separate lines with a
    single space, so no character Python's own line-breaking logic
    recognizes -- not just "\\n" -- can survive as an embedded break. topic
    and a filesystem path are single-line by construction, so this is a
    no-op for every legitimate value of either; it only changes a value that
    smuggles an embedded line break.
    """
    return " ".join(text.splitlines())


def _split_lines(text):
    r"""Split *text* into the physical lines a focus target's excerpt renders as.

    Same boundary set as str.splitlines() -- \n, \r, \r\n, \v, \f, \x1c-\x1e,
    \x85, \u2028, \u2029 -- so every character it recognizes as a break is
    still consumed as a boundary here too, never left embedded inside a
    returned piece (the property that defeats header forgery via `text`). The
    one deliberate difference: a *trailing* break yields a trailing "" line
    instead of being dropped, which is what keeps the count in sync with the
    "lines: start-end" header -- see the module docstring for why that count
    matters and how a trailing blank line previously desynced it.

    The boundary set itself is not hardcoded to get this: appending a
    sentinel character that is never a line break can only ever add ONE new
    trailing element compared to splitting *text* alone, and only when
    *text* already ended on a boundary -- otherwise the sentinel just extends
    the final element in place. Comparing the two counts answers "did text
    end on a break" using str.splitlines()'s own definition of "break",
    rather than a second, hand-maintained copy of it that could drift out of
    sync (for example if a future Python version recognized one more
    character). The sentinel must not be "\n" itself: a text ending in a
    lone "\r" would combine with an appended "\n" into the single
    two-character "\r\n" terminator and hide the very break being tested for.
    """
    lines = text.splitlines()
    if len(lines) != len((text + "#").splitlines()):
        lines.append("")
    return lines


_BEAT_GUIDANCE = """\
Plan about {budget} beats. Fewer is fine if the topic is small.
"""


def build_user_prompt(factsheet):
    """Render the fact sheet the model plans from. Deterministic."""
    topic = _single_line(factsheet.topic)
    blocks = [f"TOPIC: {topic}", "", _BEAT_GUIDANCE.format(budget=factsheet.beat_budget)]
    for index, target in enumerate(factsheet.targets):
        blocks.append(f"--- focus_index: {index} ---")
        blocks.append(f"path: {_single_line(target.path)}")
        kind = _single_line(target.kind)
        language = _single_line(target.language)
        blocks.append(f"lines: {target.start}-{target.end} ({kind}, {language})")
        # _split_lines(), not str.splitlines() or a literal "\n" split: see
        # the module and _split_lines docstrings. Every character
        # splitlines() treats as a break is still consumed as a boundary
        # here, but a *trailing* one also gets its dropped blank line back,
        # so the numbered line count always matches the "lines: start-end"
        # header above -- no matter what `text` embeds.
        numbered = "\n".join(
            f"{target.start + offset}: {line}"
            for offset, line in enumerate(_split_lines(target.text))
        )
        blocks.append(numbered)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def repair_suffix(error):
    """Appended to the prompt for the one repair attempt after a parse failure."""
    return (
        "\n\nYour previous plan was rejected by validation with this error:\n"
        f"  {error}\n"
        "Return a corrected plan. Change only what the error requires.\n"
    )


_EDITORIAL = """\
You plan short, narrated explainer videos about real source code.

You will be given a topic and a set of numbered focus targets: exact excerpts
of real files, with absolute line numbers. Plan a beat arc that teaches the
topic to a competent engineer who has never seen this code.

THE EDITORIAL STANDARD -- what separates a watchable plan from a flat one:

- Pose ONE question the video answers, and make the subtitle carry that
  question or its answer. Every beat must advance toward the answer. A plan
  that merely inventories the code, beat after beat, is a failed plan.
- Every beat must earn its place: after it, the viewer believes something
  they did not believe before it. Cut any beat that restates a neighbor in a
  different costume. Do not alternate diagram and code beats mechanically;
  pair a concept with code only when each half adds something the other
  cannot.
- Narration argues, it does not describe. Name the concrete stakes -- what
  breaks, what it costs, what would surprise the reader -- instead of reading
  the screen aloud. Spell numbers the way they should be spoken.
- Fewer, stronger beats beat more, thinner ones. The beat budget is
  a ceiling, not a quota.

THE RULES (mechanics):

- Beats come in two kinds. A "diagram" beat carries the concept: a few named
  nodes and the edges between them, no code. A "code" beat carries the proof:
  a short excerpt with a cite pointing at the exact lines that make it true.
- At least one beat MUST be a code beat. A plan made only of diagrams proves
  nothing.
- cite_start/cite_end must lie inside the focus target you name with
  focus_index, and should be the NARROWEST range that carries the idea -- the
  line with the constant or the branch, not the whole function.
- "display" is what appears on screen. It may simplify the source for
  readability; correctness is established separately from the cited lines, so
  prefer clarity over transcription.
- "regions" highlight lines WITHIN display (1-based) and each carries its own
  narration sentence. Use them to walk the viewer through a multi-line callout
  one idea at a time rather than highlighting everything at once.
- "narration" is spoken aloud. Write plain spoken prose, not bullet points,
  and keep every string ASCII -- no smart quotes, em dashes, or ellipsis
  characters.
- Set "caption_cite" only when the caption states a fact that the source
  proves; otherwise null.
- Do not invent file paths, line numbers, or code that is not in the excerpts.
"""

# The worked example the v1 design specified but never shipped. It is
# executable documentation: tests parse and expand it through the real
# validators, so the prompt can never teach a shape the pipeline rejects.
# The focus target is deliberately fictional and small so no real input
# resembles it (spec section 7, anchoring risk).
_EXAMPLE_SOURCE = '''\
"""Retry with exponential backoff and a hard cap."""

BASE_DELAY_S = 0.2
MAX_DELAY_S = 30.0


def backoff_delay(attempt):
    """Delay before retry number *attempt* (0-based), doubling each time."""
    delay = BASE_DELAY_S * (2 ** attempt)
    return min(delay, MAX_DELAY_S)'''

EXAMPLE_SHEET = FactSheet(
    repo_root="/example",
    commit="",
    dirty=False,
    topic="why retries back off exponentially instead of hammering the server",
    targets=(
        FocusTarget(
            path="retry.py",
            start=1,
            end=10,
            text=_EXAMPLE_SOURCE,
            language="python",
            kind="code",
        ),
    ),
    beat_budget=3,
)

EXAMPLE_PLAN = {
    "title": "Why retries wait longer each time",
    "subtitle": "What stops a retry storm from finishing the outage?",
    "beats": [
        {
            "id": "storm",
            "canvas_kind": "diagram",
            "focus_index": 0,
            "cite_start": 0,
            "cite_end": 0,
            "display": "",
            "narration": (
                "When a service starts failing, every client that retries "
                "immediately becomes part of the problem: the service is now "
                "serving the outage instead of recovering from it. The fix "
                "is not fewer retries. It is retries that get out of the way."
            ),
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": {
                "nodes": [
                    {
                        "id": "clients",
                        "shape": "box",
                        "label": "retrying clients",
                        "color": "red",
                        "role": "source",
                    },
                    {
                        "id": "service",
                        "shape": "ring",
                        "label": "failing service",
                        "color": "grey",
                        "role": "sink",
                    },
                ],
                "edges": [
                    {
                        "kind": "arrow",
                        "from": "clients",
                        "to": "service",
                        "label": "retry storm",
                        "narration": (
                            "Each immediate retry lands on a service that " "is already down."
                        ),
                        "color": "red",
                    }
                ],
            },
        },
        {
            "id": "doubling",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 9,
            "cite_end": 9,
            "display": "delay = BASE_DELAY_S * (2 ** attempt)",
            "narration": (
                "One line answers the question: the wait doubles with every "
                "attempt. The first retry waits a fifth of a second; by the "
                "seventh it is waiting most of half a minute. Load on the "
                "failing service falls away exponentially, which is exactly "
                "the room it needs to recover."
            ),
            "caption": "",
            "caption_cite": None,
            "regions": [
                {
                    "lines": [1],
                    "narration": (
                        "Two to the power of the attempt number is the " "entire mechanism."
                    ),
                }
            ],
            "diagram": None,
        },
        {
            "id": "cap",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 10,
            "cite_end": 10,
            "display": "return min(delay, MAX_DELAY_S)",
            "narration": (
                "Doubling forever would be its own bug: by attempt twenty "
                "the client would wait for years. The min against the cap "
                "means nobody ever waits more than thirty seconds, so "
                "backoff protects the service without abandoning the user."
            ),
            "caption": "Waits double, but never past thirty seconds",
            "caption_cite": {"focus_index": 0, "start": 3, "end": 4},
            "regions": [],
            "diagram": None,
        },
    ],
}

SYSTEM_PROMPT = (
    _EDITORIAL
    + "\nWORKED EXAMPLE -- this shows the standard, not a structure to copy."
    + " Your plan\nmust fit its own topic and code, never imitate this"
    + " shape.\n\nEXAMPLE INPUT:\n\n"
    + build_user_prompt(EXAMPLE_SHEET)
    + "\nEXAMPLE PLAN (meets the standard):\n\n"
    + json.dumps(EXAMPLE_PLAN, indent=1)
    + "\n"
)
