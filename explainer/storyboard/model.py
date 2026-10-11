"""Storyboard IR: parse, validate, and version ``story.yaml``.

The IR's unit is a ``Beat`` (a narrated step) with a typed ``Canvas`` and an
optional cited ``CodeCallout`` plus prose ``Claim``s. Only v1 fields are
modeled; unknown extra keys are ignored so additive schema growth never breaks
an older file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

from explainer.storyboard.cites import parse_cite
from explainer.storyboard.diagram_layout import (
    ANCHORS,
    LayoutError,
    check_layout,
    resolve_anchor,
)

SUPPORTED_VERSION = 1
# Supported canvas kinds. 'plot' draws Axes + curves; 'code' centers a large code
# callout with no plot, for code/structure topics that don't map to a curve. A
# 'code' beat must carry a code_callout -- codegen errors on an empty one rather
# than emitting a silently blank scene.
V1_CANVAS_KINDS = frozenset({"plot", "code", "diagram"})
SUPPORTED_VOICES = frozenset({"gtts"})


class StoryboardError(Exception):
    """Raised when a story.yaml is malformed or fails validation."""


def display_line_count(display):
    """Line count of a callout display, as codegen/code_compat will see it.

    The ONE definition of the display's line arithmetic: model validation and
    codegen's defense-in-depth region check must never disagree about it.
    """
    return len(str(display).rstrip().split("\n"))


@dataclass(frozen=True)
class Source:
    """Where cited code lives, for the citation verifier."""

    repo_root: str
    repo_url: str = ""
    commit: str = ""
    allow_dirty: bool = False


@dataclass(frozen=True)
class Theme:
    syntax_style: str = "monokai"
    resolution: str = "1080p30"
    accent_confirmed: str = "#3FB950"
    accent_contradicted: str = "#F85149"
    background: str = ""


@dataclass(frozen=True)
class Region:
    """An ordered highlight within a code callout, with its own narration line."""

    lines: tuple
    emphasis: str = "normal"
    narration: str = ""


@dataclass(frozen=True)
class CodeCallout:
    """Cited code shown on screen. ``display`` may be a readability simplification;
    truth is guaranteed by ``evidence`` (tokens that must be present at ``cite``)."""

    display: str
    language: str
    cite: str
    evidence: tuple = ()
    regions: tuple = ()


@dataclass(frozen=True)
class Claim:
    """A cited code fact that appears in prose (caption/narration), not a block."""

    cite: str
    evidence: tuple = ()


# Public: the planner's IR schema (explainer/plan/plan_ir.py) derives its
# enum lists from these, so the diagram vocabulary has one source of truth.
NODE_SHAPES = frozenset({"dot", "ring", "box"})
CONNECTOR_KINDS = frozenset({"arrow", "curve", "line", "dashed"})
_MARK_KINDS = frozenset({"vbar"})
_BOWS = frozenset({"up", "down", "none"})
PALETTE = frozenset(
    {
        "blue",
        "red",
        "green",
        "yellow",
        "purple",
        "teal",
        "gold",
        "white",
        "grey",
        "confirmed",
        "contradicted",
    }
)
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

_NODE_KEYS = {"id", "shape", "at", "label", "color", "narration", "cite", "evidence"}
_EDGE_KEYS = {
    "kind",
    "from",
    "to",
    "at",
    "height",
    "id",
    "label",
    "color",
    "narration",
    "cite",
    "evidence",
}
_PATH_KEYS = {"from", "to", "bow", "color"}
_MOTION_KEYS = {"node", "narration"}


def _reject_unknown(data, allowed, beat_id, where):
    if not isinstance(data, dict):
        raise StoryboardError(f"beat {beat_id!r}: {where} must be a mapping")
    extra = set(data) - allowed
    if extra:
        raise StoryboardError(f"beat {beat_id!r}: unknown key(s) in {where}: {sorted(extra)}")


def _require_evidence(cite, evidence, beat_id, where):
    if cite and not evidence:
        raise StoryboardError(
            f"beat {beat_id!r}: {where} has a cite but no evidence -- a 'verified' "
            f"badge must prove something"
        )


def _validate_cite_format(cite, beat_id, where):
    if not cite:
        return
    try:
        parse_cite(cite)
    except ValueError as exc:
        raise StoryboardError(f"beat {beat_id!r}: {where} {exc}")


@dataclass(frozen=True)
class Endpoint:
    node: str = ""
    anchor: str = ""


@dataclass(frozen=True)
class DiaNode:
    id: str
    shape: str
    at: str
    label: str = ""
    color: str = ""
    narration: str = ""
    cite: str = ""
    evidence: tuple = ()


@dataclass(frozen=True)
class DiaEdge:
    kind: str
    frm: "Endpoint | None" = None
    to: "Endpoint | None" = None
    at: str = ""
    height: float = 2.0
    id: str = ""
    label: str = ""
    color: str = ""
    narration: str = ""
    cite: str = ""
    evidence: tuple = ()


@dataclass(frozen=True)
class DiaPath:
    frm: Endpoint
    to: Endpoint
    bow: str = "none"
    color: str = ""


@dataclass(frozen=True)
class DiaMotion:
    node: str
    narration: str = ""


@dataclass(frozen=True)
class Diagram:
    nodes: tuple = ()
    edges: tuple = ()
    path: "DiaPath | None" = None
    motion: "DiaMotion | None" = None
    claims: tuple = ()


@dataclass(frozen=True)
class Canvas:
    kind: str
    spec: dict = field(default_factory=dict)
    diagram: "Diagram | None" = None


@dataclass(frozen=True)
class Beat:
    id: str
    canvas: Canvas
    narration: str
    code_callout: CodeCallout | None = None
    caption: str = ""
    claims: tuple = ()


@dataclass(frozen=True)
class Storyboard:
    version: int
    title: str
    source: Source
    beats: tuple
    theme: Theme = field(default_factory=Theme)
    subtitle: str = ""
    target_runtime_s: int = 0
    voice: str = "gtts"


def load_storyboard(path) -> Storyboard:
    """Parse and validate a story.yaml file into a :class:`Storyboard`.

    Raises :class:`StoryboardError` on any schema/validation problem.
    """
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise StoryboardError("storyboard must be a YAML mapping")

    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        raise StoryboardError(
            f"unsupported version {version!r} (this build supports {SUPPORTED_VERSION})"
        )

    meta = raw.get("meta") or {}
    title = meta.get("title")
    if not title:
        raise StoryboardError("meta.title is required")

    beats_raw = raw.get("beats")
    if not isinstance(beats_raw, list) or not beats_raw:
        raise StoryboardError("beats must be a non-empty list")

    voice = meta.get("voice", "gtts") or "gtts"
    if voice not in SUPPORTED_VOICES:
        raise StoryboardError(
            f"unsupported voice {voice!r} (v1 supports: {sorted(SUPPORTED_VOICES)})"
        )

    base_dir = os.path.dirname(os.path.abspath(path))
    return Storyboard(
        version=version,
        title=str(title),
        subtitle=meta.get("subtitle", "") or "",
        source=_parse_source(meta.get("source") or {}, base_dir),
        theme=_parse_theme(raw.get("theme") or {}),
        beats=tuple(_parse_beat(b, i) for i, b in enumerate(beats_raw)),
        target_runtime_s=int(meta.get("target_runtime_s", 0) or 0),
        voice=voice,
    )


def _parse_source(data, base_dir="") -> Source:
    repo_root = data.get("repo_root")
    if not repo_root:
        raise StoryboardError("meta.source.repo_root is required")
    repo_root = str(repo_root)
    # Resolve a relative repo_root against the storyboard file's directory so the
    # citation gate works regardless of the current working directory (a bare
    # relative path would otherwise resolve against CWD and break off the repo root).
    if base_dir and not os.path.isabs(repo_root):
        repo_root = os.path.normpath(os.path.join(base_dir, repo_root))
    return Source(
        repo_root=repo_root,
        repo_url=data.get("repo_url", "") or "",
        commit=str(data.get("commit", "") or "").strip(),
        allow_dirty=bool(data.get("allow_dirty", False)),
    )


def _parse_theme(data) -> Theme:
    return Theme(
        syntax_style=data.get("syntax_style", "monokai"),
        resolution=data.get("resolution", "1080p30"),
        accent_confirmed=data.get("accent_confirmed", "#3FB950"),
        accent_contradicted=data.get("accent_contradicted", "#F85149"),
        background=data.get("background", ""),
    )


def _parse_beat(data, index) -> Beat:
    if not isinstance(data, dict):
        raise StoryboardError(f"beat #{index} must be a mapping")
    beat_id = data.get("id") or f"beat-{index}"
    narration = data.get("narration")
    if not narration or not str(narration).strip():
        raise StoryboardError(f"beat {beat_id!r} is missing narration")
    callout = data.get("code_callout")
    canvas = _parse_canvas(data.get("canvas"), beat_id)
    if canvas.kind == "diagram" and callout:
        raise StoryboardError(
            f"beat {beat_id!r}: a diagram beat must not also carry a code_callout"
        )
    return Beat(
        id=str(beat_id),
        canvas=canvas,
        narration=str(narration).strip(),
        code_callout=_parse_callout(callout, beat_id) if callout else None,
        caption=data.get("caption", "") or "",
        claims=tuple(_parse_claim(c, beat_id) for c in (data.get("claims") or [])),
    )


def _parse_canvas(data, beat_id) -> Canvas:
    if not isinstance(data, dict):
        raise StoryboardError(f"beat {beat_id!r} is missing a canvas")
    kind = data.get("kind")
    if kind not in V1_CANVAS_KINDS:
        supported = sorted(V1_CANVAS_KINDS)
        raise StoryboardError(f"beat {beat_id!r}: unknown canvas kind {kind!r} (v1: {supported})")
    diagram = _parse_diagram(data.get("spec") or {}, beat_id) if kind == "diagram" else None
    # spec is kept as a raw dict alongside the typed diagram; downstream codegen uses canvas.diagram
    return Canvas(kind=kind, spec=data.get("spec") or {}, diagram=diagram)


def _parse_callout(data, beat_id) -> CodeCallout:
    if not isinstance(data, dict):
        raise StoryboardError(f"beat {beat_id!r}: code_callout must be a mapping")
    cite = data.get("cite")
    if not cite:
        raise StoryboardError(f"beat {beat_id!r}: code_callout requires a cite")
    display = data.get("display")
    if display is None:
        raise StoryboardError(f"beat {beat_id!r}: code_callout requires display text")
    display = str(display)
    regions = tuple(_parse_region(r, beat_id) for r in (data.get("regions") or []))
    # Validate region highlights against the display HERE, so `explainer verify`
    # rejects an out-of-range line before a render (and its TTS calls) is paid for.
    n_lines = display_line_count(display)
    for region in regions:
        for line in region.lines:
            if line < 1 or line > n_lines:
                raise StoryboardError(
                    f"beat {beat_id!r}: region line {line} out of range "
                    f"(display has {n_lines} lines)"
                )
    evidence = tuple(str(e) for e in (data.get("evidence") or []))
    _require_evidence(cite, evidence, beat_id, "code_callout")
    return CodeCallout(
        display=display,
        language=data.get("language", "text"),
        cite=str(cite),
        evidence=evidence,
        regions=regions,
    )


def _parse_region(data, beat_id) -> Region:
    if not isinstance(data, dict):
        raise StoryboardError(f"beat {beat_id!r}: region must be a mapping")
    return Region(
        lines=tuple(int(n) for n in (data.get("lines") or [])),
        emphasis=data.get("emphasis", "normal"),
        # Stripped so a whitespace-only narration takes the silent-highlight
        # path in codegen instead of reaching gTTS as "speakable" text.
        narration=str(data.get("narration", "") or "").strip(),
    )


def _parse_claim(data, beat_id) -> Claim:
    if not isinstance(data, dict):
        raise StoryboardError(f"beat {beat_id!r}: claim must be a mapping")
    cite = data.get("cite")
    if not cite:
        raise StoryboardError(f"beat {beat_id!r}: claim requires a cite")
    evidence = tuple(str(e) for e in (data.get("evidence") or []))
    _require_evidence(cite, evidence, beat_id, "claim")
    return Claim(cite=str(cite), evidence=evidence)


def _parse_endpoint(data, beat_id, where):
    if not isinstance(data, dict) or ("node" in data) == ("anchor" in data):
        raise StoryboardError(
            f"beat {beat_id!r}: {where} must be exactly one of {{node: id}} / {{anchor: name}}"
        )
    anchor = str(data.get("anchor", ""))
    if "anchor" in data and anchor not in ANCHORS:
        raise StoryboardError(f"beat {beat_id!r}: {where} unknown anchor {anchor!r}")
    return Endpoint(node=str(data.get("node", "")), anchor=anchor)


def _parse_node(data, beat_id):
    _reject_unknown(data, _NODE_KEYS, beat_id, "node")
    node = DiaNode(
        id=str(data.get("id", "")),
        shape=str(data.get("shape", "")),
        at=str(data.get("at", "")),
        label=str(data.get("label", "") or ""),
        color=str(data.get("color", "") or ""),
        narration=str(data.get("narration", "") or "").strip(),
        cite=str(data.get("cite", "") or ""),
        evidence=tuple(str(e) for e in (data.get("evidence") or [])),
    )
    if not ID_RE.match(node.id):
        raise StoryboardError(f"beat {beat_id!r}: node id {node.id!r} must match {ID_RE.pattern}")
    if node.shape not in NODE_SHAPES:
        raise StoryboardError(f"beat {beat_id!r}: node {node.id!r} bad shape {node.shape!r}")
    if node.at not in ANCHORS:
        raise StoryboardError(f"beat {beat_id!r}: node {node.id!r} bad anchor {node.at!r}")
    if node.color and node.color not in PALETTE:
        raise StoryboardError(f"beat {beat_id!r}: node {node.id!r} bad color {node.color!r}")
    _require_evidence(node.cite, node.evidence, beat_id, f"node {node.id!r}")
    _validate_cite_format(node.cite, beat_id, f"node {node.id!r}")
    return node


def _parse_edge(data, beat_id):
    _reject_unknown(data, _EDGE_KEYS, beat_id, "edge")
    kind = str(data.get("kind", ""))
    is_mark = kind in _MARK_KINDS
    if not is_mark and kind not in CONNECTOR_KINDS:
        raise StoryboardError(f"beat {beat_id!r}: bad edge kind {kind!r}")
    if is_mark:
        at = str(data.get("at", ""))
        if at not in ANCHORS:
            raise StoryboardError(f"beat {beat_id!r}: mark {kind!r} bad/missing anchor {at!r}")
        frm = to = None
    else:
        if "from" not in data or "to" not in data:
            raise StoryboardError(f"beat {beat_id!r}: connector {kind!r} needs both from and to")
        frm = _parse_endpoint(data["from"], beat_id, "edge from")
        to = _parse_endpoint(data["to"], beat_id, "edge to")
        at = ""
    color = str(data.get("color", "") or "")
    if color and color not in PALETTE:
        raise StoryboardError(f"beat {beat_id!r}: edge bad color {color!r}")
    edge = DiaEdge(
        kind=kind,
        frm=frm,
        to=to,
        at=at,
        height=float(data.get("height", 2.0)),
        id=str(data.get("id", "") or ""),
        label=str(data.get("label", "") or ""),
        color=color,
        narration=str(data.get("narration", "") or "").strip(),
        cite=str(data.get("cite", "") or ""),
        evidence=tuple(str(e) for e in (data.get("evidence") or [])),
    )
    _require_evidence(edge.cite, edge.evidence, beat_id, f"edge {edge.label or edge.kind!r}")
    _validate_cite_format(edge.cite, beat_id, f"edge {edge.label or edge.kind!r}")
    return edge


def _parse_diagram(spec, beat_id) -> Diagram:
    if not isinstance(spec, dict):
        raise StoryboardError(f"beat {beat_id!r}: diagram spec must be a mapping")
    _reject_unknown(spec, {"nodes", "edges", "path", "motion"}, beat_id, "diagram spec")
    nodes = tuple(_parse_node(n, beat_id) for n in (spec.get("nodes") or []))
    ids = [n.id for n in nodes]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise StoryboardError(f"beat {beat_id!r}: duplicate node id(s) {sorted(dupes)}")
    by_id = {n.id: n for n in nodes}
    edges = tuple(_parse_edge(e, beat_id) for e in (spec.get("edges") or []))

    def _check_endpoint(ep, where):
        if ep.node and ep.node not in by_id:
            raise StoryboardError(f"beat {beat_id!r}: {where} references unknown node {ep.node!r}")

    for e in edges:
        for ep, where in ((e.frm, "edge from"), (e.to, "edge to")):
            if ep is not None:
                _check_endpoint(ep, where)

    path = None
    if spec.get("path"):
        p = spec["path"]
        _reject_unknown(p, _PATH_KEYS, beat_id, "path")
        bow = str(p.get("bow", "none"))
        if bow not in _BOWS:
            raise StoryboardError(f"beat {beat_id!r}: path bad bow {bow!r}")
        path = DiaPath(
            frm=_parse_endpoint(p["from"], beat_id, "path from"),
            to=_parse_endpoint(p["to"], beat_id, "path to"),
            bow=bow,
            color=str(p.get("color", "") or ""),
        )
        _check_endpoint(path.frm, "path from")
        _check_endpoint(path.to, "path to")

    motion = None
    if spec.get("motion"):
        m = spec["motion"]
        _reject_unknown(m, _MOTION_KEYS, beat_id, "motion")
        node_id = str(m.get("node", ""))
        if node_id not in by_id:
            raise StoryboardError(f"beat {beat_id!r}: motion node {node_id!r} is not a node")
        if by_id[node_id].shape != "dot":
            raise StoryboardError(f"beat {beat_id!r}: motion node {node_id!r} must be a dot")
        if path is None:
            raise StoryboardError(f"beat {beat_id!r}: motion needs a path")
        motion = DiaMotion(node=node_id, narration=str(m.get("narration", "") or "").strip())

    points = [(n.id, resolve_anchor(n.at)) for n in nodes]
    points += [(e.id or e.kind, resolve_anchor(e.at)) for e in edges if e.kind in _MARK_KINDS]
    try:
        check_layout(points)
    except LayoutError as exc:
        raise StoryboardError(f"beat {beat_id!r}: {exc}")

    # Cited labels become Claims from the already-parsed typed structs (their
    # cite/evidence are normalized and validated), not a second pass over raw YAML.
    claims = tuple(Claim(cite=x.cite, evidence=x.evidence) for x in (*nodes, *edges) if x.cite)
    return Diagram(nodes=nodes, edges=edges, path=path, motion=motion, claims=claims)


def iter_citation_targets(beat):
    """Yield every ``(cite, evidence)`` the citation gate must verify for ``beat``:
    the code callout, each prose claim, and each cited diagram label."""
    if beat.code_callout is not None:
        yield beat.code_callout.cite, beat.code_callout.evidence
    for claim in beat.claims:
        yield claim.cite, claim.evidence
    if beat.canvas.diagram is not None:
        for claim in beat.canvas.diagram.claims:
            yield claim.cite, claim.evidence
