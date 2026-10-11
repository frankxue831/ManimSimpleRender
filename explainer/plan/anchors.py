"""Place diagram nodes on named anchors that are guaranteed not to collide.

The storyboard loader runs check_layout() over every diagram beat and rejects
one where two anchors sit closer than MIN_SEP. Rather than let the model pick
anchors and retry on rejection, the model supplies topology only and this
module supplies the geometry -- so a layout rejection is structurally
impossible instead of merely unlikely.

Of the 15 anchors in diagram_layout.py, exactly two pairs are closer than
MIN_SEP: top/upper-center and bottom/lower-center (0.4 apart; every other pair
is at least 1.2). Dropping top and bottom leaves 13 anchors in which any
distinct selection is automatically legal. tests/explainer_tests/
test_plan_anchors.py proves that exhaustively against the real check_layout
rather than trusting this paragraph.

Chains place left-to-right on the horizontal anchors and stars center their hub;
every other topology keeps the role-hint placement.
"""

from explainer.storyboard.diagram_layout import ANCHORS

# Ordered by visual preference, not by name: the left/right axis reads as flow,
# so it is spent first. Excludes "top" and "bottom" -- see the module docstring.
SAFE_ANCHORS = (
    "left",
    "right",
    "center",
    "upper-center",
    "lower-center",
    "upper-left",
    "upper-right",
    "lower-left",
    "lower-right",
    "center-left",
    "center-right",
    "far-left",
    "far-right",
)

MAX_NODES = len(SAFE_ANCHORS)

# A module-level fact, checked once at import rather than on every assign()
# call; the exhaustive test proves the geometry, this line pins membership.
assert all(name in ANCHORS for name in SAFE_ANCHORS), "SAFE_ANCHORS drifted from ANCHORS"

_ROLE_ANCHOR = {"source": "left", "sink": "right"}

# Horizontal anchors ordered by strictly increasing x (see diagram_layout
# ANCHORS: -6.2, -5.0, -3.0, 0.0, 3.0, 5.0, 6.2). A chain placed on a
# contiguous slice of this reads in flow order, left to right.
_H_ORDER = (
    "far-left",
    "left",
    "center-left",
    "center",
    "center-right",
    "right",
    "far-right",
)


class AnchorError(Exception):
    """Raised when a topology cannot be placed on the safe anchor set."""


def _chain_order(nodes, edges):
    """Node ids in path order when *edges* form ONE simple directed path
    covering every node, else None. Empty edges are never a chain, so
    edge-free calls keep the shipped placement exactly."""
    if not edges:
        return None
    ids = [n.id for n in nodes]
    succ, pred = {}, {}
    for edge in edges:
        if edge.frm == edge.to:
            return None  # self-loop
        if edge.frm in succ or edge.to in pred:
            return None  # branching, duplicate, or bidirectional edge
        succ[edge.frm] = edge.to
        pred[edge.to] = edge.frm
    if len(succ) != len(ids) - 1:
        return None  # too few edges (disconnected) or too many (cycle)
    starts = [i for i in ids if i not in pred]
    if len(starts) != 1:
        return None
    order = [starts[0]]
    while order[-1] in succ and len(order) <= len(ids):
        order.append(succ[order[-1]])
    if len(order) != len(ids) or set(order) != set(ids):
        return None  # a lobe the walk never reached, or a ghost endpoint
    return order


def _star_hub(nodes, edges):
    """The hub id when *edges* form a star over every node, else None.
    Runs after the chain check, so a 3-node path (whose middle node touches
    every edge) is already taken."""
    if len(nodes) < 3 or not edges:
        return None
    ids = {n.id for n in nodes}
    endpoints = set()
    for edge in edges:
        if edge.frm == edge.to:
            return None
        endpoints.add(edge.frm)
        endpoints.add(edge.to)
    if endpoints != ids:
        return None  # an untouched or ghost node breaks the star
    hubs = [i for i in ids if all(i in (e.frm, e.to) for e in edges)]
    if len(hubs) != 1:
        return None
    return hubs[0]


def assign(nodes, edges=()):
    """Map each node id to a distinct safe anchor, honouring topology first
    and role hints second.

    ``nodes`` is any sequence of objects exposing ``.id`` and ``.role``;
    ``edges`` any sequence exposing ``.frm`` and ``.to``. A single directed
    chain places in path order, left to right, so flow reads as flow; a star
    puts its hub at center. Every other shape -- cycles, disconnected
    graphs, duplicate or bidirectional edges, self-loops, chains longer than
    the horizontal band, or no edges at all -- falls back to the role-hint
    placement, so no topology places worse than before edges were consulted.
    Deterministic: the same topology always yields the same placement, so a
    regenerated storyboard diffs cleanly.
    """
    ids = [n.id for n in nodes]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise AnchorError(f"duplicate node id(s): {duplicates}")
    if len(nodes) > MAX_NODES:
        raise AnchorError(f"{len(nodes)} nodes exceeds the {MAX_NODES}-anchor budget")

    chain = _chain_order(nodes, edges)
    if chain is not None and len(chain) <= len(_H_ORDER):
        start = (len(_H_ORDER) - len(chain)) // 2
        return dict(zip(chain, _H_ORDER[start : start + len(chain)]))

    hub = _star_hub(nodes, edges)
    if hub is not None:
        placed = {hub: "center"}
        spokes = [i for i in ids if i != hub]
        free = [anchor for anchor in SAFE_ANCHORS if anchor != "center"]
        placed.update(zip(spokes, free))
        return placed

    placed = {}
    used = set()
    for node in nodes:  # role hints first, so flow reads left-to-right
        anchor = _ROLE_ANCHOR.get(node.role)
        if anchor is not None and anchor not in used:
            placed[node.id] = anchor
            used.add(anchor)
    for node in nodes:  # everyone else fills the preference order
        if node.id in placed:
            continue
        for anchor in SAFE_ANCHORS:
            if anchor not in used:
                placed[node.id] = anchor
                used.add(anchor)
                break
    return placed
