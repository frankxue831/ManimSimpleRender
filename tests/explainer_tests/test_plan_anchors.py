"""Tests for explainer.plan.anchors.

The exhaustive test is the point: rather than trusting a hand-argument about
anchor geometry, it runs every assignment the assigner can produce through the
REAL check_layout that the storyboard loader uses.
"""

import itertools
import unittest

from explainer.plan.anchors import MAX_NODES, SAFE_ANCHORS, AnchorError, assign
from explainer.storyboard.diagram_layout import ANCHORS, MIN_SEP, check_layout, resolve_anchor


class _Node:
    def __init__(self, node_id, role="middle"):
        self.id = node_id
        self.role = role


class _Edge:
    def __init__(self, frm, to):
        self.frm = frm
        self.to = to


class SafeAnchorSetTests(unittest.TestCase):
    def test_safe_anchors_are_real_anchors(self):
        for name in SAFE_ANCHORS:
            self.assertIn(name, ANCHORS)

    def test_safe_anchors_are_pairwise_separated(self):
        for a, b in itertools.combinations(SAFE_ANCHORS, 2):
            (ax, ay), (bx, by) = resolve_anchor(a), resolve_anchor(b)
            distance = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            self.assertGreaterEqual(distance, MIN_SEP, f"{a} vs {b}")

    def test_max_nodes_matches_the_safe_set(self):
        self.assertEqual(MAX_NODES, len(SAFE_ANCHORS))
        self.assertEqual(MAX_NODES, 13)


class AssignTests(unittest.TestCase):
    def test_source_goes_left_and_sink_goes_right(self):
        placed = assign([_Node("a", "source"), _Node("b", "sink")])
        self.assertEqual(placed["a"], "left")
        self.assertEqual(placed["b"], "right")

    def test_assignment_is_deterministic(self):
        nodes = [_Node("a", "source"), _Node("b"), _Node("c", "sink")]
        self.assertEqual(assign(nodes), assign(nodes))

    def test_every_assignment_passes_the_real_check_layout(self):
        # Exhaustive over sizes; roles varied so the role-first branch is
        # exercised at every size too.
        roles = ("source", "sink", "middle")
        for count in range(1, MAX_NODES + 1):
            nodes = [_Node("n%d" % i, roles[i % len(roles)]) for i in range(count)]
            with self.subTest(count=count):
                placed = assign(nodes)
                self.assertEqual(len(set(placed.values())), count)
                check_layout([(nid, resolve_anchor(a)) for nid, a in placed.items()])

    def test_too_many_nodes_raises(self):
        with self.assertRaises(AnchorError):
            assign([_Node("n%d" % i) for i in range(MAX_NODES + 1)])

    def test_duplicate_node_ids_raise(self):
        with self.assertRaises(AnchorError):
            assign([_Node("a"), _Node("a")])


class TopologyPlacementTests(unittest.TestCase):
    def _xs(self, placed, order):
        return [resolve_anchor(placed[node_id])[0] for node_id in order]

    def test_chain_reads_left_to_right(self):
        nodes = [_Node("a"), _Node("b"), _Node("c"), _Node("d"), _Node("e")]
        edges = [_Edge("a", "b"), _Edge("b", "c"), _Edge("c", "d"), _Edge("d", "e")]
        placed = assign(nodes, edges)
        xs = self._xs(placed, ["a", "b", "c", "d", "e"])
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(set(xs)), 5)

    def test_two_node_chain_reads_left_to_right(self):
        nodes = [_Node("b"), _Node("a")]  # node order must not matter
        placed = assign(nodes, [_Edge("a", "b")])
        xs = self._xs(placed, ["a", "b"])
        self.assertEqual(xs, sorted(xs))

    def test_seven_node_chain_fills_the_horizontal_band(self):
        ids = ["n%d" % i for i in range(7)]
        nodes = [_Node(i) for i in ids]
        edges = [_Edge(ids[i], ids[i + 1]) for i in range(6)]
        xs = self._xs(assign(nodes, edges), ids)
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(set(xs)), 7)

    def test_star_puts_the_hub_at_center(self):
        nodes = [_Node("s1"), _Node("hub"), _Node("s2"), _Node("s3")]
        edges = [_Edge("hub", "s1"), _Edge("hub", "s2"), _Edge("s3", "hub")]
        placed = assign(nodes, edges)
        self.assertEqual(placed["hub"], "center")
        self.assertEqual(len(set(placed.values())), 4)

    def test_topology_placement_is_deterministic(self):
        nodes = [_Node("a"), _Node("b"), _Node("c")]
        edges = [_Edge("a", "b"), _Edge("b", "c")]
        self.assertEqual(assign(nodes, edges), assign(nodes, edges))

    def test_degenerate_topologies_fall_back_to_edge_free_placement(self):
        # Every degenerate shape must place exactly as if no edges were
        # given -- "no topology regresses below current placement".
        nodes = [_Node("a"), _Node("b"), _Node("c")]
        baseline = assign(nodes)
        cases = {
            "cycle": [_Edge("a", "b"), _Edge("b", "c"), _Edge("c", "a")],
            "self-loop": [_Edge("a", "a"), _Edge("a", "b"), _Edge("b", "c")],
            "bidirectional": [_Edge("a", "b"), _Edge("b", "a")],
            "duplicate": [_Edge("a", "b"), _Edge("a", "b")],
            "disconnected": [_Edge("a", "b")],  # c is untouched by any edge
            "ghost-endpoint": [_Edge("a", "b"), _Edge("b", "zz")],
        }
        for name, edges in cases.items():
            with self.subTest(topology=name):
                self.assertEqual(assign(nodes, edges), baseline)

    def test_chain_longer_than_the_horizontal_band_falls_back(self):
        ids = ["n%d" % i for i in range(8)]
        nodes = [_Node(i) for i in ids]
        edges = [_Edge(ids[i], ids[i + 1]) for i in range(7)]
        self.assertEqual(assign(nodes, edges), assign(nodes))

    def test_no_edges_keeps_the_shipped_placement(self):
        nodes = [_Node("a"), _Node("b", "source")]
        self.assertEqual(assign(nodes, ()), assign(nodes))


if __name__ == "__main__":
    unittest.main()
