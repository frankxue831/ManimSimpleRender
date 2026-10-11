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

from explainer.plan.anchors import assign
from explainer.plan.evidence import WeakCiteError, extract_evidence
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


def _ascii(text, where):
    for source, replacement in _TRANSLITERATIONS.items():
        text = text.replace(source, replacement)
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExpandError(f"{where} contains non-ASCII text that cannot be transliterated: {exc}")
    return text


def _claims_for(beat, factsheet):
    cite = beat.caption_cite
    if cite is None:
        return []
    target = factsheet.targets[cite.focus_index]
    tokens = extract_evidence(target, cite.start, cite.end)
    return [
        {
            "cite": format_cite(target.path, cite.start, cite.end),
            "evidence": list(tokens),
        }
    ]


def _code_beat(beat, factsheet):
    cite = beat.cite
    target = factsheet.targets[cite.focus_index]
    tokens = extract_evidence(target, cite.start, cite.end)
    out = {
        "id": beat.id,
        "canvas": {"kind": "code", "spec": {}},
        "narration": _ascii(beat.narration, f"beat {beat.id!r} narration"),
        "code_callout": {
            "display": _ascii(beat.display, f"beat {beat.id!r} display"),
            "language": target.language or "text",
            "cite": format_cite(target.path, cite.start, cite.end),
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
    return out


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
                out = _diagram_beat(beat)
            else:
                out = _code_beat(beat, factsheet)
            claims = _claims_for(beat, factsheet)
            if claims:
                out["claims"] = claims
            if beat.caption:
                out["caption"] = _ascii(beat.caption, f"beat {beat.id!r} caption")
        except WeakCiteError as exc:
            dropped.append(DroppedBeat(beat_id=beat.id, reason=str(exc)))
            continue
        beats.append(out)

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
