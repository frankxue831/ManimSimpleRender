"""Tests for explainer.plan.emit.

The output path is written exactly once, after both the schema check and the
citation gate pass, so a failed planner run can never leave a half-written or
truncated storyboard behind.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from explainer.cite.verifier import CiteFailure
from explainer.plan.emit import EmitError, validate_output_location, write_storyboard
from explainer.storyboard.model import load_storyboard
from tests.explainer_tests.support import confirm_write_is_blocked, git_head, make_git_repo


def _document(repo_root="."):
    return {
        "version": 1,
        "meta": {
            "title": "T",
            "subtitle": "S",
            "source": {"repo_root": repo_root, "commit": "", "allow_dirty": True},
            "voice": "gtts",
        },
        "theme": {},
        "beats": [
            {
                "id": "b1",
                "canvas": {"kind": "code", "spec": {}},
                "narration": "n",
                "code_callout": {
                    "display": "x = 1",
                    "language": "python",
                    "cite": "a.py:1",
                    "evidence": ["x = 1", "1"],
                },
            }
        ],
    }


class WriteStoryboardTests(unittest.TestCase):
    def test_writes_the_file_when_everything_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            out = os.path.join(directory, "s.story.yaml")
            written = write_storyboard(_document(), out)
            self.assertEqual(written, out)
            self.assertTrue(os.path.exists(out))

    def test_leaves_no_temp_file_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            out = os.path.join(directory, "s.story.yaml")
            write_storyboard(_document(), out)
            self.assertEqual(sorted(os.listdir(directory)), ["a.py", "s.story.yaml"])

    def test_writes_the_file_creating_missing_nested_directories(self):
        # E2 regression guard: the up-front output-location check (added
        # separately in cli.py) must not change what write_storyboard()
        # itself does on a normal successful run -- it still creates however
        # many missing parent directories -o needs, exactly as it did before
        # that check existed.
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            out = os.path.join(directory, "nested", "sub", "s.story.yaml")
            written = write_storyboard(_document(repo_root=directory), out)
            self.assertEqual(written, out)
            self.assertTrue(os.path.exists(out))

    def test_schema_failure_raises_and_keeps_the_rejected_file(self):
        document = _document()
        del document["beats"][0]["narration"]  # loader requires narration
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            with self.assertRaises(EmitError) as caught:
                write_storyboard(document, out)
            self.assertEqual(caught.exception.kind, "schema")
            self.assertTrue(os.path.exists(caught.exception.rejected_path))
            self.assertFalse(os.path.exists(out))

    def test_citation_failure_raises_and_keeps_the_rejected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            out = os.path.join(directory, "s.story.yaml")
            failure = [CiteFailure("b1", "a.py:1", ("nope",))]
            with patch("explainer.plan.emit.verify_storyboard", return_value=failure):
                with self.assertRaises(EmitError) as caught:
                    write_storyboard(_document(), out)
            self.assertEqual(caught.exception.kind, "citation")
            self.assertTrue(os.path.exists(caught.exception.rejected_path))
            self.assertFalse(os.path.exists(out))

    def test_a_failed_run_does_not_truncate_an_existing_storyboard(self):
        document = _document()
        del document["beats"][0]["narration"]
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            with open(out, "w", encoding="utf-8") as handle:
                handle.write("PRECIOUS EXISTING CONTENT\n")
            with self.assertRaises(EmitError):
                write_storyboard(document, out)
            with open(out, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "PRECIOUS EXISTING CONTENT\n")

    def test_emitted_yaml_is_ascii(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            out = os.path.join(directory, "s.story.yaml")
            write_storyboard(_document(), out)
            with open(out, "rb") as handle:
                handle.read().decode("ascii")

    # -- P4: load_storyboard does not raise only StoryboardError -----------
    #
    # yaml.safe_dump happily serializes values load_storyboard's own parsing
    # helpers cannot handle -- a python `set` round-trips through the `!!set`
    # YAML tag, and a bare string stands in for a mapping just fine at the
    # YAML level. Both then reach a `.get()` call on a non-mapping deep
    # inside _parse_theme/_parse_source and raise a raw AttributeError,
    # pre-fix, instead of the EmitError every other failure path here uses.

    def test_non_dict_theme_raises_emit_error_not_a_raw_exception(self):
        document = _document()
        document["theme"] = {1, 2, 3}  # valid YAML (!!set), invalid Theme input
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            with self.assertRaises(EmitError) as caught:
                write_storyboard(document, out)
            self.assertEqual(caught.exception.kind, "schema")

    def test_non_dict_theme_leaves_no_staging_file_behind(self):
        document = _document()
        document["theme"] = {1, 2, 3}
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            with self.assertRaises(EmitError) as caught:
                write_storyboard(document, out)
            self.assertFalse(os.path.exists(out + ".staging"))
            self.assertTrue(os.path.exists(caught.exception.rejected_path))

    def test_non_dict_source_raises_emit_error_not_a_raw_exception(self):
        document = _document()
        document["meta"]["source"] = "oops"  # valid YAML string, invalid Source input
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            with self.assertRaises(EmitError) as caught:
                write_storyboard(document, out)
            self.assertEqual(caught.exception.kind, "schema")
            self.assertFalse(os.path.exists(out + ".staging"))

    def test_undumpable_document_raises_emit_error(self):
        # A failure before there is even anything to stage: yaml.safe_dump
        # itself refuses an object it has no representer for. Pre-fix, the
        # destination file was already opened (and truncated to empty) by
        # the time the dump was attempted, orphaning a 0-byte .staging file
        # when the RepresenterError propagated past write_storyboard raw.
        class _Unrepresentable:
            pass

        document = _document()
        document["theme"] = _Unrepresentable()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            with self.assertRaises(EmitError) as caught:
                write_storyboard(document, out)
            self.assertEqual(caught.exception.kind, "schema")
            self.assertIsNone(caught.exception.rejected_path)
            self.assertFalse(os.path.exists(out + ".staging"))
            self.assertFalse(os.path.exists(out + ".rejected"))

    # -- P5: none of the tests above check WHAT was written -----------------

    def test_round_trip_preserves_beat_fields_cites_and_evidence(self):
        # A bug that silently reordered or dropped an evidence token would
        # pass every test above (existence, absence, decodability, error
        # kind) -- none of them reload the file. This does, through the real
        # load_storyboard, and checks field-for-field equality. The evidence
        # values are chosen to stress YAML's implicit typing: "yes"/"null"
        # are YAML 1.1 keywords, "1e3" and "007" are numeric-looking
        # scalars (007 also looks octal), and a leading "-" or "#" is
        # structurally significant to YAML -- every one of them must come
        # back as the exact original string, not a bool/None/number, and
        # whitespace must survive only because it was explicitly quoted.
        tricky = ["yes", "null", "1e3", "007", "-leading-dash", "#leading-hash", "  spacey  "]
        source_line = "x = 1e3  # 007 -leading-dash #leading-hash yes null spacey\n"
        document = {
            "version": 1,
            "meta": {
                "title": "T",
                "subtitle": "S",
                "source": {"repo_root": ".", "commit": "", "allow_dirty": True},
                "voice": "gtts",
            },
            "theme": {},
            "beats": [
                {
                    "id": "b1",
                    "canvas": {"kind": "code", "spec": {}},
                    "narration": "n1",
                    "caption": "#leading-hash",
                    "code_callout": {
                        "display": "x = 1e3",
                        "language": "python",
                        "cite": "a.py:1",
                        "evidence": list(tricky),
                    },
                },
                {
                    "id": "b2",
                    "canvas": {
                        "kind": "diagram",
                        "spec": {
                            "nodes": [{"id": "n1", "shape": "dot", "at": "center", "label": "007"}],
                            "edges": [],
                        },
                    },
                    "narration": "n2",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write(source_line)
            out = os.path.join(directory, "s.story.yaml")
            write_storyboard(document, out)
            reloaded = load_storyboard(out)

        self.assertEqual([b.id for b in reloaded.beats], ["b1", "b2"])
        self.assertEqual([b.canvas.kind for b in reloaded.beats], ["code", "diagram"])
        self.assertEqual(reloaded.beats[0].code_callout.cite, "a.py:1")
        self.assertEqual(reloaded.beats[0].code_callout.evidence, tuple(tricky))
        self.assertEqual(reloaded.beats[0].caption, "#leading-hash")
        self.assertEqual(reloaded.beats[1].canvas.diagram.nodes[0].label, "007")

    # -- E2: an unwritable output directory must not crash -----------------
    #
    # os.makedirs/open/os.replace can all raise OSError (a read-only parent
    # directory being the concrete repro), and pre-fix that OSError escaped
    # write_storyboard() raw instead of surfacing as EmitError.

    def test_unwritable_output_directory_raises_emit_error_not_a_raw_exception(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = os.path.join(parent, "locked")
            os.makedirs(directory)
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            out = os.path.join(directory, "s.story.yaml")
            os.chmod(directory, 0o500)  # r-x: cannot create files in here
            try:
                confirm_write_is_blocked(self, directory)
                with self.assertRaises(EmitError) as caught:
                    write_storyboard(_document(repo_root=directory), out)
            finally:
                os.chmod(directory, 0o700)
            self.assertEqual(caught.exception.kind, "io")
            self.assertIsNone(caught.exception.rejected_path)
            self.assertIn(directory, str(caught.exception))

    def test_unwritable_output_directory_leaves_no_staging_file_behind(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = os.path.join(parent, "locked")
            os.makedirs(directory)
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n")
            out = os.path.join(directory, "s.story.yaml")
            os.chmod(directory, 0o500)
            try:
                confirm_write_is_blocked(self, directory)
                with self.assertRaises(EmitError):
                    write_storyboard(_document(repo_root=directory), out)
            finally:
                os.chmod(directory, 0o700)
            self.assertEqual(os.listdir(directory), ["a.py"])


@unittest.skipUnless(shutil.which("git"), "git not installed")
class WriteStoryboardInsideCitedRepoTests(unittest.TestCase):
    """F1: writing the storyboard INTO the repo it explains must not fail
    merely because emit.py's own .staging file is momentarily untracked.

    verify_storyboard's dirty check only runs when meta.source.commit is set
    (see verify_source_state), so these tests -- unlike the rest of this
    file's _document() fixture -- stamp a real commit to actually exercise it.
    """

    def _git_repo(self):
        root = make_git_repo({"a.py": "x = 1\n"})
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root, git_head(root)

    def _cited_document(self, root, head):
        document = _document(repo_root=root)
        document["meta"]["source"]["commit"] = head
        document["meta"]["source"]["allow_dirty"] = False
        return document

    def test_writing_into_a_clean_cited_repo_succeeds(self):
        root, head = self._git_repo()
        out = os.path.join(root, "story.yaml")
        written = write_storyboard(self._cited_document(root, head), out)
        self.assertEqual(written, out)
        self.assertTrue(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".staging"))
        self.assertFalse(os.path.exists(out + ".rejected"))
        self.assertFalse(load_storyboard(out).source.allow_dirty)

    def test_a_genuine_uncommitted_edit_in_the_same_repo_still_fails(self):
        root, head = self._git_repo()
        with open(os.path.join(root, "a.py"), "a", encoding="utf-8") as handle:
            handle.write("y = 2\n")
        out = os.path.join(root, "story.yaml")
        with self.assertRaises(EmitError) as caught:
            write_storyboard(self._cited_document(root, head), out)
        self.assertEqual(caught.exception.kind, "citation")

    def test_a_leftover_rejected_file_from_a_previous_run_does_not_block_this_one(self):
        # Simulates the exact permanently-broken-retry scenario: a prior run
        # already left "<out>.rejected" (e.g. from a citation failure before
        # this fix). It must not itself cause THIS run to also fail.
        root, head = self._git_repo()
        out = os.path.join(root, "story.yaml")
        with open(out + ".rejected", "w", encoding="utf-8") as handle:
            handle.write("stale rejected draft from a previous run\n")
        written = write_storyboard(self._cited_document(root, head), out)
        self.assertEqual(written, out)


class ValidateOutputLocationTests(unittest.TestCase):
    """E2 part 1: catch a bad output location before the LLM client exists.

    cli.py calls this BEFORE constructing AnthropicClient, so a doomed
    output path is reported at zero API cost -- see explainer/cli.py's
    _plan(). It deliberately performs the same kind of filesystem probe
    write_storyboard() itself will need, which is why success here still
    cannot be a full guarantee (the directory can turn unwritable again
    before the real write; that path is covered by write_storyboard()'s own
    OSError handling above).
    """

    def test_accepts_an_existing_writable_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            validate_output_location(out)  # must not raise
            self.assertEqual(os.listdir(directory), [])

    def test_creates_a_missing_directory_and_leaves_no_probe_file(self):
        with tempfile.TemporaryDirectory() as parent:
            out = os.path.join(parent, "nested", "sub", "s.story.yaml")
            validate_output_location(out)
            self.assertTrue(os.path.isdir(os.path.join(parent, "nested", "sub")))
            self.assertEqual(os.listdir(os.path.join(parent, "nested", "sub")), [])

    def test_rejects_a_read_only_directory_with_emit_error_kind_io(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = os.path.join(parent, "locked")
            os.makedirs(directory)
            out = os.path.join(directory, "s.story.yaml")
            os.chmod(directory, 0o500)
            try:
                confirm_write_is_blocked(self, directory)
                with self.assertRaises(EmitError) as caught:
                    validate_output_location(out)
            finally:
                os.chmod(directory, 0o700)
            self.assertEqual(caught.exception.kind, "io")
            self.assertIsNone(caught.exception.rejected_path)
            self.assertIn(directory, str(caught.exception))

    def test_rejects_when_the_parent_of_a_missing_directory_is_read_only(self):
        with tempfile.TemporaryDirectory() as parent:
            os.chmod(parent, 0o500)
            try:
                confirm_write_is_blocked(self, parent)
                out = os.path.join(parent, "not-yet-created", "s.story.yaml")
                with self.assertRaises(EmitError) as caught:
                    validate_output_location(out)
            finally:
                os.chmod(parent, 0o700)
            self.assertEqual(caught.exception.kind, "io")


if __name__ == "__main__":
    unittest.main()
