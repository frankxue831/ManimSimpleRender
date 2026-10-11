"""The narrow JSON the model emits, and the checks its schema cannot express.

The model never writes YAML or the storyboard schema. It writes this: beat
intents, prose, cite line numbers, and diagram topology. Keeping its output
surface small is what makes most schema violations impossible rather than
merely caught -- anchors, evidence, region geometry, and the storyboard shape
are all code's job.

output_config.format supports a restricted JSON Schema dialect: no recursion,
no numeric or string bounds, and additionalProperties must be false with every
property listed in required. So PLAN_SCHEMA stays flat and enum-driven, and
everything else is re-validated in parse() against the FactSheet the model was
shown.
"""

from dataclasses import dataclass

from explainer.plan.anchors import MAX_NODES
from explainer.storyboard.model import (
    CONNECTOR_KINDS,
    ID_RE,
    NODE_SHAPES,
    PALETTE,
    display_line_count,
)

# Derived from the storyboard model's own vocabulary so the two can never
# drift; sorted because a JSON Schema enum needs a deterministic order.
# "" is the plan IR's spelling of "no colour".
_COLORS = [""] + sorted(PALETTE)
_SHAPES = sorted(NODE_SHAPES)
# Connector kinds only -- the vbar mark is anchored by `at` rather than
# from/to, which would need a second edge shape in the schema (spec section 4).
_EDGE_KINDS = sorted(CONNECTOR_KINDS)
_ROLES = ["source", "sink", "middle"]

_CITE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["focus_index", "start", "end"],
    "properties": {
        "focus_index": {"type": "integer"},
        "start": {"type": "integer"},
        "end": {"type": "integer"},
    },
}

_REGION = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines", "narration"],
    "properties": {
        "lines": {"type": "array", "items": {"type": "integer"}},
        "narration": {"type": "string"},
    },
}

_NODE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "shape", "label", "color", "role"],
    "properties": {
        "id": {"type": "string"},
        "shape": {"enum": _SHAPES},
        "label": {"type": "string"},
        "color": {"enum": _COLORS},
        "role": {"enum": _ROLES},
    },
}

_EDGE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "from", "to", "label", "narration", "color"],
    "properties": {
        "kind": {"enum": _EDGE_KINDS},
        "from": {"type": "string"},
        "to": {"type": "string"},
        "label": {"type": "string"},
        "narration": {"type": "string"},
        "color": {"enum": _COLORS},
    },
}

_DIAGRAM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "edges"],
    "properties": {
        "nodes": {"type": "array", "items": _NODE},
        "edges": {"type": "array", "items": _EDGE},
    },
}

_BEAT = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id",
        "canvas_kind",
        "focus_index",
        "cite_start",
        "cite_end",
        "display",
        "narration",
        "caption",
        "caption_cite",
        "regions",
        "diagram",
    ],
    "properties": {
        "id": {"type": "string"},
        "canvas_kind": {"enum": ["code", "diagram"]},
        "focus_index": {"type": "integer"},
        "cite_start": {"type": "integer"},
        "cite_end": {"type": "integer"},
        "display": {"type": "string"},
        "narration": {"type": "string"},
        "caption": {"type": "string"},
        # anyOf rather than a type array: the dialect documents anyOf, and a
        # nullable field has no other supported spelling.
        "caption_cite": {"anyOf": [{"type": "null"}, _CITE]},
        "regions": {"type": "array", "items": _REGION},
        "diagram": {"anyOf": [{"type": "null"}, _DIAGRAM]},
    },
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "subtitle", "beats"],
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "beats": {"type": "array", "items": _BEAT},
    },
}


class PlanError(Exception):
    """The model's plan IR is not usable against the fact sheet it was shown."""


@dataclass(frozen=True)
class PlanCite:
    focus_index: int
    start: int
    end: int


@dataclass(frozen=True)
class PlanRegion:
    lines: tuple
    narration: str


@dataclass(frozen=True)
class PlanNode:
    id: str
    shape: str
    label: str
    color: str
    role: str


@dataclass(frozen=True)
class PlanEdge:
    kind: str
    frm: str
    to: str
    label: str
    narration: str
    color: str


@dataclass(frozen=True)
class PlanDiagram:
    nodes: tuple
    edges: tuple


@dataclass(frozen=True)
class PlanBeat:
    id: str
    canvas_kind: str
    cite: "PlanCite | None"  # None exactly on diagram beats, like caption_cite
    display: str
    narration: str
    caption: str
    caption_cite: "PlanCite | None"
    regions: tuple
    diagram: "PlanDiagram | None"


@dataclass(frozen=True)
class Plan:
    title: str
    subtitle: str
    beats: tuple


def _require(condition, message):
    if not condition:
        raise PlanError(message)


def _is_int(value):
    """True for a real int -- not the bool subclass JSON true/false decodes to."""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_cite(raw, sheet, where):
    if raw is None:
        return None
    _require(isinstance(raw, dict), f"{where} must be a mapping")
    index = raw.get("focus_index")
    _require(
        _is_int(index) and 0 <= index < len(sheet.targets),
        f"{where} focus_index {index!r} is not one of the {len(sheet.targets)} focus targets",
    )
    target = sheet.targets[index]
    start, end = raw.get("start"), raw.get("end")
    _require(_is_int(start) and _is_int(end), f"{where} needs integer start/end")
    _require(
        target.start <= start <= end <= target.end,
        f"{where} lines {start}-{end} fall outside focus target "
        f"{target.path}:{target.start}-{target.end}",
    )
    return PlanCite(focus_index=index, start=start, end=end)


def _parse_diagram(raw, where):
    _require(isinstance(raw, dict), f"{where} diagram must be a mapping")

    # A missing/null key defaults to [], but any other falsy-but-present
    # value (0, "", {}) must still hit the list-type check below rather than
    # being coerced to [] before the check ever runs.
    nodes_raw = raw.get("nodes")
    if nodes_raw is None:
        nodes_raw = []
    _require(isinstance(nodes_raw, list), f"{where} diagram nodes must be a list")
    nodes = []
    for n in nodes_raw:
        _require(isinstance(n, dict), f"{where} diagram node must be a mapping")
        nodes.append(
            PlanNode(
                id=str(n.get("id", "") or ""),
                shape=str(n.get("shape", "") or ""),
                label=str(n.get("label", "") or ""),
                color=str(n.get("color", "") or ""),
                role=str(n.get("role", "middle") or "middle"),
            )
        )
    nodes = tuple(nodes)
    _require(nodes, f"{where} diagram needs at least one node")
    _require(
        len(nodes) <= MAX_NODES,
        f"{where} has {len(nodes)} nodes, above the {MAX_NODES}-anchor budget",
    )
    for node in nodes:
        _require(ID_RE.match(node.id), f"{where} node id {node.id!r} is not a usable identifier")
        _require(node.shape in _SHAPES, f"{where} node {node.id!r} has bad shape {node.shape!r}")
        _require(node.color in _COLORS, f"{where} node {node.id!r} has bad color {node.color!r}")
        _require(node.role in _ROLES, f"{where} node {node.id!r} has bad role {node.role!r}")
    known = {n.id for n in nodes}
    _require(len(known) == len(nodes), f"{where} has duplicate node ids")

    edges_raw = raw.get("edges")
    if edges_raw is None:
        edges_raw = []
    _require(isinstance(edges_raw, list), f"{where} diagram edges must be a list")
    edges = []
    for e in edges_raw:
        _require(isinstance(e, dict), f"{where} diagram edge must be a mapping")
        edges.append(
            PlanEdge(
                # `or ""` so a JSON null normalizes to "" like a missing key,
                # instead of stringifying to the word "None" -- mirrors the
                # guard every other field here already has.
                kind=str(e.get("kind", "") or ""),
                # Same guard, for a different reason: a null endpoint must
                # not silently resolve against a node genuinely named "None".
                frm=str(e.get("from", "") or ""),
                to=str(e.get("to", "") or ""),
                label=str(e.get("label", "") or ""),
                narration=str(e.get("narration", "") or "").strip(),
                color=str(e.get("color", "") or ""),
            )
        )
    edges = tuple(edges)
    for edge in edges:
        _require(edge.kind in _EDGE_KINDS, f"{where} edge has bad kind {edge.kind!r}")
        _require(edge.color in _COLORS, f"{where} edge has bad color {edge.color!r}")
        for endpoint in (edge.frm, edge.to):
            _require(
                endpoint in known,
                f"{where} edge references unknown node {endpoint!r}",
            )
    return PlanDiagram(nodes=nodes, edges=edges)


def _parse_beat(raw, sheet, index):
    where = f"beat #{index}"
    _require(isinstance(raw, dict), f"{where} must be a mapping")
    beat_id = str(raw.get("id", "") or "").strip()
    _require(beat_id, f"{where} needs an id")
    where = f"beat {beat_id!r}"

    kind = raw.get("canvas_kind")
    _require(kind in ("code", "diagram"), f"{where} has unknown canvas_kind {kind!r}")

    narration = str(raw.get("narration", "") or "").strip()
    _require(narration, f"{where} needs narration")

    display = str(raw.get("display", "") or "")
    diagram_raw = raw.get("diagram")

    if kind == "diagram":
        _require(not display.strip(), f"{where} is a diagram beat and must not carry a display")
        display = ""  # a diagram beat shows topology only; no display text is kept
        _require(diagram_raw is not None, f"{where} is a diagram beat but has no topology")
        diagram = _parse_diagram(diagram_raw, where)
        cite = None
    else:
        _require(display.strip(), f"{where} is a code beat and needs display text")
        _require(diagram_raw is None, f"{where} is a code beat and must not carry topology")
        diagram = None
        cite = _parse_cite(
            {
                "focus_index": raw.get("focus_index"),
                "start": raw.get("cite_start"),
                "end": raw.get("cite_end"),
            },
            sheet,
            f"{where} cite",
        )

    regions_raw = raw.get("regions")
    if regions_raw is None:
        regions_raw = []
    _require(isinstance(regions_raw, list), f"{where} regions must be a list")
    if kind == "diagram":
        # A diagram beat has no display for a region to highlight lines
        # within. Checked BEFORE the per-region parse so the repair loop is
        # told the real rule, not a bounds error against a 0-line display.
        _require(not regions_raw, f"{where} is a diagram beat and must not carry regions")
    regions = []
    n_lines = display_line_count(display) if display.strip() else 0
    for region in regions_raw:
        _require(isinstance(region, dict), f"{where} region must be a mapping")
        lines_raw = region.get("lines")
        if lines_raw is None:
            lines_raw = []
        _require(isinstance(lines_raw, list), f"{where} region lines must be a list")
        lines = []
        for raw_line in lines_raw:
            _require(_is_int(raw_line), f"{where} region line {raw_line!r} is not an integer")
            lines.append(raw_line)
        lines = tuple(lines)
        for line in lines:
            _require(
                1 <= line <= n_lines,
                f"{where} region line {line} is outside the {n_lines}-line display",
            )
        regions.append(
            PlanRegion(lines=lines, narration=str(region.get("narration", "") or "").strip())
        )

    caption = str(raw.get("caption", "") or "").strip()
    caption_cite = _parse_cite(raw.get("caption_cite"), sheet, f"{where} caption_cite")
    # A caption_cite is a verified claim about the SOURCE; caption is the
    # on-screen text that claim is supposed to be proving. One without the
    # other is a contradiction -- expand.py has no display to attach an
    # orphan claim to, and no basis to invent a caption for an unclaimed one
    # -- so it is rejected here, at the trust boundary, rather than reaching
    # expand.py as a strange draft (see F5).
    _require(
        caption or caption_cite is None,
        f"{where} has a caption_cite but no caption -- a verified claim needs "
        f"on-screen text to attach to",
    )

    return PlanBeat(
        id=beat_id,
        canvas_kind=kind,
        cite=cite,
        display=display,
        narration=narration,
        caption=caption,
        caption_cite=caption_cite,
        regions=tuple(regions),
        diagram=diagram,
    )


def parse(raw, factsheet):
    """Validate the model's JSON against *factsheet* and return a :class:`Plan`."""
    _require(isinstance(raw, dict), "plan must be a JSON object")
    beats_raw = raw.get("beats")
    _require(isinstance(beats_raw, list) and beats_raw, "plan needs a non-empty beats list")
    title = str(raw.get("title", "") or "").strip()
    _require(title, "plan needs a title")
    return Plan(
        title=title,
        subtitle=str(raw.get("subtitle", "") or "").strip(),
        beats=tuple(_parse_beat(b, factsheet, i) for i, b in enumerate(beats_raw)),
    )
