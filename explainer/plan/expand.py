"""Turn a plan IR into a storyboard dict, supplying everything the model did not.

This is where the trust guarantee is actually implemented: evidence is read
out of the real source here, never taken from the model, and diagram anchors
are assigned here, never chosen by the model. A beat whose cite cannot
support a claim is DROPPED rather than shipped with weak evidence -- silently
weakening it is the one outcome that would make a green citation gate
meaningless.
"""

import os
from dataclasses import asdict, dataclass

from explainer.cite.verifier import match_evidence
from explainer.plan.anchors import assign
from explainer.plan.evidence import WeakCiteError, candidate_lines, extract_evidence
from explainer.storyboard.cites import format_cite
from explainer.storyboard.model import Theme

# A draft with fewer surviving beats than this has no arc left to narrate,
# even when every remaining beat is individually well-cited (design spec
# "Beat survival rules").
MIN_BEATS = 2

# Unicode that a writing model reaches for by habit, mapped to the ASCII the
# gTTS narration path and this repo's ASCII rule both require. Spelled with
# escapes so this source file itself stays pure ASCII.
_TRANSLITERATIONS = {
    "\u2014": "--",  # em dash
    "\u2013": "-",  # en dash
    "\u2212": "-",  # minus sign
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote
    "\u201c": '"',  # left double quote
    "\u201d": '"',  # right double quote
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",  # non-breaking space
    "\u00d7": "x",  # multiplication sign
    "\u2192": "->",  # right arrow
    "\u2190": "<-",  # left arrow
}

# The Theme dataclass's own defaults, pinned into the emitted document so a
# generated storyboard is explicit about what it renders with. Derived rather
# than re-spelled, so a changed default in the model cannot silently diverge
# from what new storyboards stamp. Empty fields (background) are omitted.
DEFAULT_THEME = {key: value for key, value in asdict(Theme()).items() if value}


class ExpandError(Exception):
    """The plan cannot be turned into a usable storyboard."""


@dataclass(frozen=True)
class DroppedBeat:
    beat_id: str
    reason: str

    def __str__(self):
        return f"dropped beat {self.beat_id!r}: {self.reason}"


def _spans(numbers):
    """Ascending line numbers as runs: ``(1, 2, 3, 7)`` reads ``"1-3, 7"``."""
    runs = []
    for number in numbers:
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return ", ".join(str(first) if first == last else f"{first}-{last}" for first, last in runs)


@dataclass(frozen=True)
class DroppedLines:
    """Lines that trimming cut from a cite although they could hold evidence.

    The beat still ships, with the trimmed cite, so its on-screen label covers
    less than the display may show.
    """

    beat_id: str
    cite: str  # as the plan asked for it
    kept: str  # as written: trimmed to the lines the evidence reaches
    lines: tuple  # the cut lines that hold an evidence candidate

    def __str__(self):
        noun, pronoun = ("line", "it") if len(self.lines) == 1 else ("lines", "them")
        return (
            f"dropped {noun} {_spans(self.lines)} from a cite in beat {self.beat_id!r}: "
            f"no evidence token reaches {pronoun}, so {self.cite} is written as {self.kept}"
        )


def _ascii(text, where):
    for source, replacement in _TRANSLITERATIONS.items():
        text = text.replace(source, replacement)
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExpandError(f"{where} contains non-ASCII text that cannot be transliterated: {exc}")
    return text


def _evidence_range(target, start, end, tokens):
    """The part of ``start``..``end`` from the first to the last line *tokens* hold.

    The citation gate fails a cite whose first or last line holds no
    evidence, because such a range (one ending on a closing brace, say) can
    shift by a line with every token still inside it. Citing this range
    instead keeps a generated storyboard passing its gate by construction. It
    is measured with the gate's own matcher, so the two cannot disagree.
    extract_evidence keeps a token on the first and last lines that can hold
    one, so the trim cuts only lines that cannot; _cited reports any cut that
    reaches a line that could, since the label then covers less than the
    display shows.
    """
    offset = start - target.start
    lines = target.text.split("\n")[offset : offset + (end - start) + 1]
    first, last = match_evidence(lines, tokens).span
    return start + first, start + last


def _cited(beat_id, target, start, end):
    """Evidence for ``target``'s lines ``start``..``end``, and the cite trimmed to it.

    Returns ``(cite, tokens, cut)``. ``cut`` holds a :class:`DroppedLines` when
    the trim cut a line that could hold evidence, so that cut is reported
    instead of silent; it is empty when only blank lines, braces and the like
    were cut.
    """
    tokens = extract_evidence(target, start, end)
    first, last = _evidence_range(target, start, end, tokens)
    kept = format_cite(target.path, first, last)
    lines = tuple(n for n in candidate_lines(target, start, end) if not first <= n <= last)
    if not lines:
        return kept, tokens, ()
    return kept, tokens, (DroppedLines(beat_id, format_cite(target.path, start, end), kept, lines),)


def _claims_for(beat, factsheet):
    cite = beat.caption_cite
    if cite is None:
        return [], ()
    target = factsheet.targets[cite.focus_index]
    kept, tokens, cut = _cited(beat.id, target, cite.start, cite.end)
    return [{"cite": kept, "evidence": list(tokens)}], cut


def _code_beat(beat, factsheet):
    cite = beat.cite
    target = factsheet.targets[cite.focus_index]
    kept, tokens, cut = _cited(beat.id, target, cite.start, cite.end)
    out = {
        "id": beat.id,
        "canvas": {"kind": "code", "spec": {}},
        "narration": _ascii(beat.narration, f"beat {beat.id!r} narration"),
        "code_callout": {
            "display": _ascii(beat.display, f"beat {beat.id!r} display"),
            "language": target.language or "text",
            "cite": kept,
            "evidence": list(tokens),
        },
    }
    regions = [
        {
            "lines": list(region.lines),
            "emphasis": "normal",
            "narration": _ascii(region.narration, f"beat {beat.id!r} region"),
        }
        for region in beat.regions
        if region.lines
    ]
    if regions:
        out["code_callout"]["regions"] = regions
    return out, cut


def _diagram_beat(beat):
    placed = assign(beat.diagram.nodes, beat.diagram.edges)
    nodes = []
    for node in beat.diagram.nodes:
        entry = {"id": node.id, "shape": node.shape, "at": placed[node.id]}
        if node.label:
            entry["label"] = _ascii(node.label, f"beat {beat.id!r} node label")
        if node.color:
            entry["color"] = node.color
        nodes.append(entry)
    edges = []
    for edge in beat.diagram.edges:
        entry = {"kind": edge.kind, "from": {"node": edge.frm}, "to": {"node": edge.to}}
        if edge.label:
            entry["label"] = _ascii(edge.label, f"beat {beat.id!r} edge label")
        if edge.color:
            entry["color"] = edge.color
        if edge.narration:
            entry["narration"] = _ascii(edge.narration, f"beat {beat.id!r} edge narration")
        edges.append(entry)
    return {
        "id": beat.id,
        "canvas": {"kind": "diagram", "spec": {"nodes": nodes, "edges": edges}},
        "narration": _ascii(beat.narration, f"beat {beat.id!r} narration"),
    }


def expand(plan, factsheet, output_dir):
    """Build the storyboard dict for *plan*.

    Returns ``(document, dropped)``. ``output_dir`` is the directory the
    storyboard will be written to: ``meta.source.repo_root`` is stored relative
    to it, matching how ``load_storyboard`` resolves a relative root.

    ``dropped`` lists, in beat order, what the storyboard leaves out of the
    plan: a :class:`DroppedBeat` for each beat dropped, and a
    :class:`DroppedLines` for each cite of a shipped beat whose trim cut a
    line that could hold evidence.

    A beat's cite (its own, or a caption_cite naming a separate claim) can
    fail evidence extraction on either a code or a diagram beat -- a diagram
    beat's caption is prose like any other and gets the same treatment. When
    that happens the WHOLE beat is dropped rather than shipped with a caption
    that implies a citation it cannot back.
    """
    beats, dropped = [], []
    for beat in plan.beats:
        try:
            if beat.canvas_kind == "diagram":
                out, cut = _diagram_beat(beat), ()
            else:
                out, cut = _code_beat(beat, factsheet)
            claims, claim_cut = _claims_for(beat, factsheet)
            if claims:
                out["claims"] = claims
            if beat.caption:
                out["caption"] = _ascii(beat.caption, f"beat {beat.id!r} caption")
        except WeakCiteError as exc:
            dropped.append(DroppedBeat(beat_id=beat.id, reason=str(exc)))
            continue
        beats.append(out)
        dropped.extend(cut + claim_cut)

    if len(beats) < MIN_BEATS:
        raise ExpandError(
            f"only {len(beats)} beat(s) survived evidence extraction (need {MIN_BEATS}); "
            f"try wider --focus ranges"
        )
    if not any(b["canvas"]["kind"] == "code" for b in beats):
        raise ExpandError(
            "no code beat survived, so the storyboard would cite nothing; a "
            "diagram-only draft cannot prove anything"
        )

    return (
        {
            "version": 1,
            "meta": {
                "title": _ascii(plan.title, "title"),
                "subtitle": _ascii(plan.subtitle, "subtitle"),
                "source": {
                    "repo_root": os.path.relpath(factsheet.repo_root, os.path.abspath(output_dir)),
                    "commit": factsheet.commit,
                    "allow_dirty": factsheet.dirty,
                },
                "voice": "gtts",
            },
            "theme": dict(DEFAULT_THEME),
            "beats": beats,
        },
        tuple(dropped),
    )
