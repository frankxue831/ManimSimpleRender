"""Tests for explainer.storyboard.model.load_storyboard (IR parse + validation)."""

import os
import tempfile
import textwrap
import unittest

from explainer.storyboard.model import (
    Storyboard,
    StoryboardError,
    Theme,
    _parse_theme,
    load_storyboard,
)

MINIMAL = textwrap.dedent("""
    version: 1
    meta:
      title: "T"
      source: { repo_root: "/tmp/repo" }
    theme: {}
    beats:
      - id: b1
        canvas: { kind: plot, spec: {} }
        narration: "hi"
    """)


def _write(text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    handle.write(text)
    handle.close()
    return handle.name


def _load(text):
    path = _write(text)
    try:
        return load_storyboard(path)
    finally:
        os.unlink(path)


class LoadStoryboardTests(unittest.TestCase):
    def _load(self, text):
        return _load(text)

    def test_loads_minimal_storyboard(self):
        sb = self._load(MINIMAL)
        self.assertIsInstance(sb, Storyboard)
        self.assertEqual(sb.version, 1)
        self.assertEqual(sb.title, "T")
        self.assertEqual(sb.source.repo_root, "/tmp/repo")
        self.assertEqual(len(sb.beats), 1)
        self.assertEqual(sb.beats[0].canvas.kind, "plot")
        self.assertEqual(sb.beats[0].narration, "hi")

    def test_relative_repo_root_resolved_against_storyboard_dir(self):
        # A relative repo_root must resolve against the storyboard FILE's directory,
        # not the process CWD, so the citation gate works from any working directory.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.story.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(MINIMAL.replace('repo_root: "/tmp/repo"', 'repo_root: "../sibling/repo"'))
            sb = load_storyboard(path)
        self.assertTrue(os.path.isabs(sb.source.repo_root))
        self.assertEqual(sb.source.repo_root, os.path.normpath(os.path.join(d, "../sibling/repo")))

    def test_absolute_repo_root_left_unchanged(self):
        sb = self._load(MINIMAL)  # repo_root: /tmp/repo (absolute)
        self.assertEqual(sb.source.repo_root, "/tmp/repo")

    def test_unsupported_version_raises(self):
        with self.assertRaises(StoryboardError):
            self._load(MINIMAL.replace("version: 1", "version: 2"))

    def test_unknown_canvas_kind_raises(self):
        with self.assertRaises(StoryboardError):
            self._load(MINIMAL.replace("kind: plot", "kind: hologram"))

    def test_code_canvas_kind_accepted(self):
        # 'code' canvas is now supported for code-centric topics (no forced plot).
        # Load accepts it; codegen separately requires a code_callout to be present.
        sb = self._load(MINIMAL.replace("kind: plot", "kind: code"))
        self.assertEqual(sb.beats[0].canvas.kind, "code")

    def test_unsupported_voice_rejected(self):
        bad = textwrap.dedent("""
            version: 1
            meta:
              title: "T"
              voice: elevenlabs
              source: { repo_root: "/tmp/repo" }
            beats:
              - id: b1
                canvas: { kind: plot, spec: {} }
                narration: "hi"
            """)
        with self.assertRaises(StoryboardError):
            self._load(bad)

    def test_gtts_voice_accepted(self):
        ok = textwrap.dedent("""
            version: 1
            meta:
              title: "T"
              voice: gtts
              source: { repo_root: "/tmp/repo" }
            beats:
              - id: b1
                canvas: { kind: plot, spec: {} }
                narration: "hi"
            """)
        self.assertEqual(self._load(ok).voice, "gtts")

    def test_missing_narration_raises(self):
        bad = textwrap.dedent("""
            version: 1
            meta: { title: "T", source: { repo_root: "/tmp/repo" } }
            beats:
              - id: b1
                canvas: { kind: plot, spec: {} }
            """)
        with self.assertRaises(StoryboardError):
            self._load(bad)

    def test_missing_repo_root_raises(self):
        bad = MINIMAL.replace('source: { repo_root: "/tmp/repo" }', "source: {}")
        with self.assertRaises(StoryboardError):
            self._load(bad)

    def test_parses_code_callout_and_claims(self):
        text = textwrap.dedent("""
            version: 1
            meta: { title: "T", source: { repo_root: "/tmp/repo" } }
            beats:
              - id: b1
                canvas: { kind: plot, spec: {} }
                narration: "n"
                code_callout:
                  display: "x"
                  language: rust
                  cite: "a.rs:1-2"
                  evidence: ["foo", "0.4"]
                  regions:
                    - { lines: [1], emphasis: strong, narration: "r1" }
                claims:
                  - { cite: "a.rs:5", evidence: ["bar"] }
            """)
        sb = self._load(text)
        beat = sb.beats[0]
        self.assertEqual(beat.code_callout.cite, "a.rs:1-2")
        self.assertEqual(beat.code_callout.evidence, ("foo", "0.4"))
        self.assertEqual(beat.code_callout.regions[0].emphasis, "strong")
        self.assertEqual(beat.claims[0].cite, "a.rs:5")
        self.assertEqual(beat.claims[0].evidence, ("bar",))

    # A cite with no evidence proves nothing, yet the citation gate used to pass
    # it: it looked for each listed token, so an empty list could never report a
    # miss. The gate now fails it too (no_evidence), but the loader refuses it
    # first, with a message that says why.
    # Diagram nodes/edges were already guarded; callouts and prose claims were not.

    def test_code_callout_cite_without_evidence_rejected(self):
        for evidence in ("evidence: []", ""):
            with self.subTest(evidence=evidence or "<omitted>"):
                text = textwrap.dedent("""
                    version: 1
                    meta: { title: "T", source: { repo_root: "/tmp/repo" } }
                    beats:
                      - id: b1
                        canvas: { kind: code, spec: {} }
                        narration: "n"
                        code_callout:
                          display: "x"
                          language: rust
                          cite: "a.rs:1"
                          %s
                    """ % evidence)
                with self.assertRaises(StoryboardError):
                    self._load(text)

    def test_claim_cite_without_evidence_rejected(self):
        for evidence in ("evidence: []", ""):
            with self.subTest(evidence=evidence or "<omitted>"):
                text = textwrap.dedent("""
                    version: 1
                    meta: { title: "T", source: { repo_root: "/tmp/repo" } }
                    beats:
                      - id: b1
                        canvas: { kind: plot, spec: {} }
                        narration: "n"
                        claims:
                          - { cite: "a.rs:5"%s }
                    """ % (", " + evidence if evidence else ""))
                with self.assertRaises(StoryboardError):
                    self._load(text)

    # The same hole one level down: a token that matches ANY text proves
    # nothing about the cited lines. A blank token normalizes to "", which is
    # a substring of everything, and a /pattern/ that matches the empty string
    # ("//", "/a*/") is found in any text, so the gate passed both.

    def _with_evidence(self, block):
        return textwrap.dedent("""
            version: 1
            meta: { title: "T", source: { repo_root: "/tmp/repo" } }
            beats:
              - id: b1
                canvas: { kind: plot, spec: {} }
                narration: "n"
                %s
            """) % block

    def test_token_that_matches_any_text_rejected(self):
        blocks = {
            "callout": 'code_callout: { display: "x", language: rust, cite: "a.rs:1", '
            'evidence: ["foo", %s] }',
            "claim": 'claims: [{ cite: "a.rs:5", evidence: ["foo", %s] }]',
        }
        for where, block in blocks.items():
            for token in ('""', '"   "', '"//"', '"/a*/"'):
                with self.subTest(where=where, token=token):
                    with self.assertRaises(StoryboardError) as cm:
                        self._load(self._with_evidence(block % token))
                    self.assertIn("prove something", str(cm.exception))

    def test_token_that_needs_real_text_still_loads(self):
        # The check must not overshoot: surrounding spaces are normalized away
        # by the gate, and a regex that cannot match empty text is real evidence.
        block = 'claims: [{ cite: "a.rs:5", evidence: ["  foo  ", "/fo+/", "/(?=bar)/"] }]'
        sb = self._load(self._with_evidence(block))
        self.assertEqual(sb.beats[0].claims[0].evidence, ("  foo  ", "/fo+/", "/(?=bar)/"))

    def test_loads_real_example_storyboard(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        example = os.path.join(repo, "examples", "evidence-budget.story.yaml")
        sb = load_storyboard(example)
        self.assertEqual(
            [b.id for b in sb.beats], ["per-line", "floor-and-cap", "no-proof-no-badge"]
        )
        self.assertEqual(sb.beats[1].code_callout.cite, "explainer/plan/evidence.py:205-211")
        # repo_root resolves to an absolute path (CWD-independent citation gate);
        # the example's ".." is relative to the storyboard file, i.e. this repo.
        self.assertTrue(os.path.isabs(sb.source.repo_root))
        self.assertEqual(os.path.normpath(sb.source.repo_root), os.path.normpath(repo))


# A code callout whose display has exactly two lines; REGION_LINES is substituted
# per test. Region bounds must be validated at LOAD time so `explainer verify`
# catches an out-of-range highlight before any render is paid for.
CALLOUT_WITH_REGION = textwrap.dedent("""
    version: 1
    meta:
      title: "T"
      source: { repo_root: "/tmp/repo" }
    beats:
      - id: b1
        canvas: { kind: code }
        narration: "hi"
        code_callout:
          display: |
            line one
            line two
          language: text
          cite: "src/x.py:1"
          evidence: ["line one"]
          regions:
            - { lines: [REGION_LINES], narration: "r" }
    """)


class RegionBoundsAtLoadTests(unittest.TestCase):
    def _load(self, text):
        return _load(text)

    def test_region_line_beyond_display_rejected_at_load(self):
        with self.assertRaises(StoryboardError) as cm:
            self._load(CALLOUT_WITH_REGION.replace("REGION_LINES", "3"))
        self.assertIn("region line 3", str(cm.exception))

    def test_region_line_zero_rejected_at_load(self):
        with self.assertRaises(StoryboardError):
            self._load(CALLOUT_WITH_REGION.replace("REGION_LINES", "0"))

    def test_region_lines_within_display_accepted(self):
        sb = self._load(CALLOUT_WITH_REGION.replace("REGION_LINES", "1, 2"))
        self.assertEqual(sb.beats[0].code_callout.regions[0].lines, (1, 2))

    def test_theme_background_defaults_empty_and_parses(self):
        self.assertEqual(Theme().background, "")
        theme = _parse_theme({"background": "#1a1814"})
        self.assertEqual(theme.background, "#1a1814")


if __name__ == "__main__":
    unittest.main()
