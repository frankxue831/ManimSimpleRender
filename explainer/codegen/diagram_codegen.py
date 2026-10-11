"""Deterministic diagram codegen: a typed Diagram -> Manim setup lines + reveal
steps. Pure string-building; the only imports are the shared codegen helpers and
the storyboard-side layout math.
"""

from explainer.codegen.common import (
    INDENT,
    LABEL_FONT,
    PALETTE,
    TEXT_COLOR,
    _s,
    badge_lines,
    cite_basename,
)
from explainer.codegen.manim_codegen import RevealStep
from explainer.storyboard.diagram_layout import bow_control_points, resolve_anchor


def _point(x, y):
    return f"[{x}, {y}, 0.0]"


def emit_diagram(beat, storyboard):
    """Return (setup_lines, [RevealStep]). Emits nodes, edges, path, motion, and
    a provenance badge when any diagram claim is present."""
    dia = beat.canvas.diagram
    theme = storyboard.theme

    def resolve_color(name, default):
        if name == "confirmed":
            return theme.accent_confirmed
        if name == "contradicted":
            return theme.accent_contradicted
        return PALETTE.get(name, default) if name else default

    setup = []
    pos = {}  # node id -> (x, y)
    stage_plays, narrated_steps = [], []

    def _route(narration, plays):
        # A glyph with its own narration becomes a step; otherwise it joins the
        # stage (revealed under the beat narration).
        if narration:
            narrated_steps.append(RevealStep(narration=narration, plays=plays))
        else:
            stage_plays.extend(plays)

    for i, node in enumerate(dia.nodes):
        var = f"node{i}"
        x, y = resolve_anchor(node.at)
        pos[node.id] = (x, y)
        color = resolve_color(node.color, "#FFFFFF")
        if node.shape == "dot":
            setup.append(f"{INDENT}{var} = Dot({_point(x, y)}, radius=0.12, color={_s(color)})")
        elif node.shape == "ring":
            setup.append(
                f"{INDENT}{var} = Circle(radius=0.34, color={_s(color)}).move_to({_point(x, y)})"
            )
        else:  # box
            setup.append(
                f"{INDENT}{var} = RoundedRectangle(width=1.4, height=0.7, corner_radius=0.1, "
                f"color={_s(color)}).move_to({_point(x, y)})"
            )
        plays = [f"FadeIn({var})"]
        if node.label:
            lvar = f"{var}_label"
            setup.append(
                f"{INDENT}{lvar} = Text({_s(node.label)}, font={_s(LABEL_FONT)}, font_size=22, "
                f"color={_s(color if node.color else TEXT_COLOR)}, disable_ligatures=True)"
                f".next_to({var}, DOWN, buff=0.18)"
            )
            # FadeIn (not Write): partial Write frames read as typos mid-voiceover.
            plays.append(f"FadeIn({lvar})")
        _route(node.narration, plays)

    def _endpoint_xy(ep):
        return pos[ep.node] if ep.node else resolve_anchor(ep.anchor)

    for j, edge in enumerate(dia.edges):
        var = f"edge{j}"
        color = resolve_color(edge.color, "#9AA0A6")
        if edge.kind == "vbar":
            x, y = resolve_anchor(edge.at)
            h = edge.height / 2.0
            setup.append(
                f"{INDENT}{var} = Line({_point(x, y - h)}, {_point(x, y + h)}, "
                f"color={_s(color)}, stroke_width=6)"
            )
        else:
            sx, sy = _endpoint_xy(edge.frm)
            ex, ey = _endpoint_xy(edge.to)
            if edge.kind == "arrow":
                setup.append(
                    f"{INDENT}{var} = Arrow({_point(sx, sy)}, {_point(ex, ey)}, "
                    f"buff=0.15, color={_s(color)}, stroke_width=4)"
                )
            elif edge.kind == "line":
                setup.append(
                    f"{INDENT}{var} = Line({_point(sx, sy)}, {_point(ex, ey)}, "
                    f"color={_s(color)}, stroke_width=4)"
                )
            elif edge.kind == "dashed":
                setup.append(
                    f"{INDENT}{var} = DashedVMobject(Arrow({_point(sx, sy)}, "
                    f"{_point(ex, ey)}, buff=0.1, color={_s(color)}, stroke_width=4))"
                )
            else:  # curve
                setup.append(
                    f"{INDENT}{var} = CurvedArrow({_point(sx, sy)}, {_point(ex, ey)}, "
                    f"color={_s(color)}, stroke_width=4)"
                )
        plays = [f"Create({var})"]
        if edge.label:
            lvar = f"{var}_label"
            setup.append(
                f"{INDENT}{lvar} = Text({_s(edge.label)}, font={_s(LABEL_FONT)}, font_size=20, "
                f"color={_s(color)}, disable_ligatures=True).next_to({var}, UP, buff=0.12)"
            )
            plays.append(f"FadeIn({lvar})")
        _route(edge.narration, plays)

    has_motion = dia.path is not None and dia.motion is not None
    more_after_stage = bool(narrated_steps) or has_motion or bool(dia.claims)
    steps = [
        RevealStep(
            narration=beat.narration,
            plays=stage_plays or ["Wait(0.1)"],
            post_wait=0.35 if more_after_stage else 0.0,
        )
    ]

    # Provenance as soon as the stage is up (before edge-by-edge VO and motion),
    # so the badge is a continuous product signature, not a late decoration.
    if dia.claims:
        commit = storyboard.source.commit
        badge = f"verified @ {commit}" if commit else "verified"
        setup.extend(
            badge_lines(
                "provenance",
                cite_basename(dia.claims[0].cite),
                badge,
                theme.accent_confirmed,
            )
        )
        setup.append(f"{INDENT}provenance.to_edge(DOWN, buff=0.25)")
        steps.append(RevealStep(narration="", plays=["FadeIn(provenance)"], run_time="0.5"))

    steps.extend(narrated_steps)

    if has_motion:
        p0 = _endpoint_xy(dia.path.frm)
        p3 = _endpoint_xy(dia.path.to)
        c1, c2 = bow_control_points(p0, p3, dia.path.bow)
        pcolor = resolve_color(dia.path.color, "#2DD4BF")
        setup.append(
            f"{INDENT}path = CubicBezier({_point(*p0)}, {_point(*c1)}, "
            f"{_point(*c2)}, {_point(*p3)}).set_stroke(color={_s(pcolor)}, width=5)"
        )
        move_idx = [n.id for n in dia.nodes].index(dia.motion.node)
        node_var = f"node{move_idx}"
        prep_plays = []
        # Dim the non-moving glyphs. Use set_stroke for stroke-only shapes
        # (ring/box/edges): set_opacity would also set fill opacity, ballooning a
        # ring into a disc or a curved arrow into a filled blob. A filled dot dims
        # via opacity. Labels (Text) dim via opacity safely.
        for i, n in enumerate(dia.nodes):
            if i == move_idx:
                continue
            v = f"node{i}"
            prep_plays.append(
                f"{v}.animate.set_opacity(0.3)"
                if n.shape == "dot"
                else f"{v}.animate.set_stroke(opacity=0.3)"
            )
            if n.label:
                prep_plays.append(f"node{i}_label.animate.set_opacity(0.3)")
        for j, e in enumerate(dia.edges):
            prep_plays.append(f"edge{j}.animate.set_stroke(opacity=0.3)")
            if e.label:
                prep_plays.append(f"edge{j}_label.animate.set_opacity(0.3)")
        # Fade the moving node's own label so it does not orphan at the start point.
        if dia.nodes[move_idx].label:
            prep_plays.append(f"FadeOut(node{move_idx}_label)")
        prep_plays.append("Create(path)")
        steps.append(RevealStep(narration="", plays=prep_plays, run_time="0.8"))
        steps.append(
            RevealStep(
                narration=dia.motion.narration,
                plays=[f"MoveAlongPath({node_var}, path)"],
                post_wait=0.4,
            )
        )

    return setup, steps
