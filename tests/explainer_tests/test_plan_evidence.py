"""Tests for explainer.plan.evidence.

The load-bearing test is test_extracted_tokens_always_satisfy_the_real_gate:
extraction is only safe if its output is, by construction, findable by the
SHIPPED verifier. That test pins the property the whole planner rests on.
"""

import unittest

from explainer.cite.verifier import match_evidence
from explainer.plan.facts import FocusTarget
from explainer.plan.evidence import (
    BASE_TOKENS,
    MAX_TOKENS,
    WeakCiteError,
    _code_tokens,
    _line_candidates,
    _spread,
    _token_budget,
    content_lines,
    extract_evidence,
)


def _missing(text, tokens):
    """The tokens the real citation gate cannot find in cited *text*."""
    return match_evidence(text.split("\n"), tokens).missing


RUST = FocusTarget(
    path="decay.rs",
    start=90,
    end=94,
    text=(
        "let grounding_floor = match last_grounding {\n"
        "    GroundingStatus::Confirmed => 0.4,\n"
        "    GroundingStatus::Contradicted => 0.5,\n"
        "};\n"
        "let floor = f32::max(access_floor, grounding_floor);"
    ),
    language="rust",
    kind="code",
)

PROSE = FocusTarget(
    path="notes.md",
    start=1,
    end=2,
    text=(
        "Force is also what fails to resist a wrong trajectory.\n"
        "A missing test is among the highest-leverage forces in the field."
    ),
    language="markdown",
    kind="prose",
)

TRIVIAL = FocusTarget(
    path="x.py",
    start=1,
    end=1,
    text="}",
    language="python",
    kind="code",
)

# Prose has no keyword list to filter it, so filler is the prose analogue of
# TRIVIAL: text long enough to yield phrases, saying nothing worth a badge.
FILLER = FocusTarget(
    path="notes.md",
    start=1,
    end=1,
    text="It is what it is, and that is all there is to it.",
    language="markdown",
    kind="prose",
)

# Same filler, but every function word is capitalized and punctuated. A
# strength test that forgets to normalize would read "That!" as distinctive.
DRESSED_FILLER = FocusTarget(
    path="notes.md",
    start=1,
    end=1,
    text="That! When, Were. Such; With: Than? Then. This,",
    language="markdown",
    kind="prose",
)

# One filler line and one line that actually claims something.
MIXED = FocusTarget(
    path="notes.md",
    start=1,
    end=2,
    text=(
        "It is what it is, and that is all there is to it.\n"
        "Retries are capped at the configured backoff ceiling."
    ),
    language="markdown",
    kind="prose",
)

# F2 repro: a float on line 1 whose digits ("0", "5", "1") are also valid
# standalone _INT matches -- these fragments must not crowd the budget out of
# "threshold" on line 2, which is the only token that can catch drift there.
FRAGMENT_STARVED = FocusTarget(
    path="rate.py",
    start=1,
    end=2,
    text="rate = 0.5 * 1.5\nthreshold",
    language="python",
    kind="code",
)

# A lookup table: every line carries TWO facts of different kinds -- a string
# key and an integer value -- and the whole point of the table is the mapping
# between them. Found by planning a real video against app/render/quality.py,
# where a flat 5-token budget spent every slot on the string keys and left all
# five integers unprotected.
LADDER = FocusTarget(
    path="quality.py",
    start=11,
    end=17,
    text=(
        "QUALITY_LADDER = (\n"
        '    ("-ql", 480),\n'
        '    ("-qm", 720),\n'
        '    ("-qh", 1080),\n'
        '    ("-qp", 1440),\n'
        '    ("-qk", 2160),\n'
        ")"
    ),
    language="python",
    kind="code",
)

# Twelve constants put two strong tokens on each of their lines, which fills
# the 24-token budget, and the function under them holds only weak ones. The
# reviewer's example on pull request 8: a cite of lines 1-20 was trimmed to
# 1-12, cutting the whole function without a word.
CONSTANTS_THEN_FUNCTION = FocusTarget(
    path="limits.py",
    start=1,
    end=20,
    text="\n".join(
        [f"LIMIT_{i} = {i + 10}" for i in range(12)]
        + [
            "def scale(values, factor):",
            "    running = []",
            "    for value in values:",
            "        running += [value * factor]",
            "    total = sum(running)",
            "    if total > factor:",
            "        total = factor",
            "    return round(total)",
        ]
    ),
    language="python",
    kind="code",
)

# More lines holding a strong token than the budget has slots, so even the
# first pass of _spread, one token per line, ran out before the last lines.
LONG_TABLE = FocusTarget(
    path="table.py",
    start=1,
    end=30,
    text="\n".join(f'value_{i} = "token_{i}"' for i in range(30)),
    language="python",
    kind="code",
)

LONG_NOTES = FocusTarget(
    path="notes.md",
    start=1,
    end=30,
    text="\n".join(f"Step {i} retries the upload with a longer backoff." for i in range(30)),
    language="markdown",
    kind="prose",
)

# Every line could fill the budget alone, and the first and last lines' own
# strongest tokens are taken anyway: the case where nothing may change.
DENSE = FocusTarget(
    path="dense.py",
    start=1,
    end=3,
    text=(
        'route("alpha", "beta", "gamma", "delta")\n'
        'route("one", "two", "three", "four")\n'
        'route("red", "green", "blue", "cyan")'
    ),
    language="python",
    kind="code",
)

# Code between a blank first line, a keyword-only line and a closing brace.
FENCED = FocusTarget(
    path="m.rs",
    start=10,
    end=14,
    text="\nlet floor = f32::max(access_floor, grounding_floor);\n} else {\nlet m = 0.5;\n}",
    language="rust",
    kind="code",
)

# The first line holds only weak words, and the strong tokens of the twelve
# constants under it fill the budget: the first line needs its own token too.
HEADED_CONSTANTS = FocusTarget(
    path="limits.py",
    start=1,
    end=13,
    text="\n".join(
        ["def limits(table, scale):"] + [f"    LIMIT_{i} = {i + 10}" for i in range(12)]
    ),
    language="python",
    kind="code",
)

# The last line's one candidate, "value", is first seen on line 15, so only a
# token drawn from the last line itself is sure to stand for it.
ECHOED_FUNCTION = FocusTarget(
    path="limits.py",
    start=1,
    end=20,
    text=CONSTANTS_THEN_FUNCTION.text.replace("return round(total)", "return value"),
    language="python",
    kind="code",
)

# Prose drops a phrase it has seen in other letter case, so the last line here,
# line 1 in lower case, has no phrase of its own in the whole-text ranking.
ECHOED_NOTES = FocusTarget(
    path="notes.md",
    start=1,
    end=30,
    text="\n".join(
        [f"Step {i} retries the upload with backoff." for i in range(29)]
        + ["step 0 retries the upload with backoff."]
    ),
    language="markdown",
    kind="prose",
)

# Filler on every line but line 24, the only strong phrase, which the first
# pass of _spread reaches with no slot to spare.
FILLER_ROWS = FocusTarget(
    path="notes.md",
    start=1,
    end=30,
    text="\n".join(f"It is {i} and it is {'retried' if i == 23 else 'all'}" for i in range(30)),
    language="markdown",
    kind="prose",
)

# The first line's two longest phrases are weak and its shortest is strong;
# every other line holds one phrase, and the lines' phrases outnumber the
# budget by two.
PHRASED_FIRST_LINE = FocusTarget(
    path="notes.md",
    start=1,
    end=24,
    text="\n".join(
        ["it is what it is and that is all but the retry"]
        + [f"Step {i} retries the upload with backoff." for i in range(1, 24)]
    ),
    language="markdown",
    kind="prose",
)

# "Ok" is too short to be a token, so line 3 is code that no evidence can reach.
CHECK = FocusTarget(
    path="check.rs",
    start=1,
    end=4,
    text="fn check(&self) -> Result<(), Error> {\n    validate(&self.config)?;\n    Ok(())\n}",
    language="rust",
    kind="code",
)


class ExtractEvidenceTests(unittest.TestCase):
    def test_code_extraction_finds_literals_and_qualified_names(self):
        tokens = extract_evidence(RUST, 91, 92)
        self.assertIn("GroundingStatus::Confirmed", tokens)
        self.assertIn("0.4", tokens)

    def test_prose_extraction_returns_multiword_phrases(self):
        tokens = extract_evidence(PROSE, 1, 2)
        self.assertTrue(all(len(t.split()) >= 4 for t in tokens), tokens)

    def test_token_cap_is_respected(self):
        # The budget is proportional to the cited range (see
        # ProportionalBudgetTests), so this pins the absolute ceiling.
        self.assertLessEqual(len(extract_evidence(RUST, 90, 94)), MAX_TOKENS)

    def test_weak_range_is_rejected_not_weakened(self):
        with self.assertRaises(WeakCiteError):
            extract_evidence(TRIVIAL, 1, 1)

    def test_no_token_is_shaped_like_a_regex(self):
        # verifier.py treats a /.../-wrapped token as a regex, which would
        # silently change what the token means.
        target = FocusTarget(
            path="x.py",
            start=1,
            end=1,
            text="pattern = /alpha/ and value = 12.5",
            language="python",
            kind="code",
        )
        for token in extract_evidence(target, 1, 1):
            self.assertFalse(token.startswith("/") and token.endswith("/"), token)

    def test_cite_outside_target_range_raises(self):
        with self.assertRaises(WeakCiteError):
            extract_evidence(RUST, 200, 201)

    def test_extracted_tokens_always_satisfy_the_real_gate(self):
        # THE property: whatever we emit must be findable by the shipped
        # verifier in the exact text we cited. If this ever fails, the
        # planner can emit storyboards that fail their own citation gate.
        for target, lo, hi in (
            (RUST, 90, 94),
            (RUST, 91, 92),
            (RUST, 94, 94),
            (PROSE, 1, 2),
            (PROSE, 2, 2),
            (MIXED, 1, 2),
        ):
            with self.subTest(path=target.path, lo=lo, hi=hi):
                tokens = extract_evidence(target, lo, hi)
                offset = lo - target.start
                cited = "\n".join(target.text.split("\n")[offset : offset + (hi - lo) + 1])
                self.assertEqual(_missing(cited, tokens), [])

    def test_floor_requires_two_tokens_with_one_strong(self):
        # The name promises "with one strong", so assert it: a count-only
        # check would pass on two weak _PLAIN identifiers, which is exactly
        # the badge-on-nothing case the floor exists to reject.
        tokens = extract_evidence(RUST, 94, 94)
        self.assertGreaterEqual(len(tokens), 2)
        strong, _weak = _code_tokens(RUST.text.split("\n")[4])
        self.assertTrue(
            any(token in tokens for _line, token in strong),
            f"no strong token survived selection: {tokens}",
        )


class ProseStrengthFloorTests(unittest.TestCase):
    """The prose floor must reject filler the way the code floor rejects '}'.

    Code gets this for free: _KEYWORDS strips the words that claim nothing.
    Prose has no such list, so without a strength test every window of English
    function words earns the same verified badge as a real claim.
    """

    def test_pure_function_word_prose_is_rejected(self):
        with self.assertRaises(WeakCiteError):
            extract_evidence(FILLER, 1, 1)

    def test_capitalized_punctuated_stopwords_do_not_count_as_distinctive(self):
        with self.assertRaises(WeakCiteError):
            extract_evidence(DRESSED_FILLER, 1, 1)

    def _prose_line(self, text):
        return FocusTarget(
            path="notes.md", start=1, end=1, text=text, language="markdown", kind="prose"
        )

    def test_hedge_and_nonsense_filler_is_rejected(self):
        # Words absent from a plain function-word list but carrying no claim:
        # hedge adverbs and nonsense placeholders.
        for label, text in (
            ("hedges", "Blah blah blah blah blah blah blah, seriously, that is truly all."),
            ("lorem", "Lorem ipsum lorem ipsum lorem ipsum lorem, ipsum lorem ipsum lorem."),
        ):
            with self.subTest(label):
                with self.assertRaises(WeakCiteError):
                    extract_evidence(self._prose_line(text), 1, 1)

    def test_dual_use_words_are_not_treated_as_filler(self):
        # These read as filler in isolation but are routinely the payload of a
        # terse technical sentence. Listing them as stopwords once cost this
        # exact cite -- a plausible README line about a stub -- so it is
        # pinned: over-tightening the floor drops facts the video could prove.
        for text in (
            "It is only a placeholder for now, nothing more than that.",
            "The sort is stable, so equal keys keep their original order.",
        ):
            with self.subTest(text[:24]):
                self.assertGreaterEqual(len(extract_evidence(self._prose_line(text), 1, 1)), 2)

    def test_prose_with_distinctive_words_still_passes(self):
        tokens = extract_evidence(PROSE, 1, 2)
        self.assertGreaterEqual(len(tokens), 2)

    def test_one_strong_line_carries_its_filler_neighbours(self):
        # The floor is about strength, not exclusion: a weak phrase stays
        # usable as ordinary evidence, exactly as a weak code token does.
        tokens = extract_evidence(MIXED, 1, 2)
        self.assertTrue(
            any("configured backoff" in t for t in tokens),
            tokens,
        )
        self.assertTrue(
            any("what it is" in t for t in tokens),
            tokens,
        )


class FragmentSuppressionTests(unittest.TestCase):
    """F2: a float like '0.5' also satisfies the bare _INT pattern on its own
    digits ('0', '5'). Those fragments are substrings of a token already
    chosen, not new evidence, and letting them fill MAX_TOKENS starves a
    multi-line cite of tokens that would have covered its other lines --
    silently defeating the drift guarantee this module exists to provide.
    """

    def test_short_float_line_is_too_weak_without_its_own_fragments(self):
        # '    return 0.5' used to clear the 2-token floor on '0.5' plus the
        # fragments '0'/'5' of that SAME token. With fragments suppressed
        # there is only one real token here, correctly below MIN_TOKENS.
        target = FocusTarget(
            path="x.py", start=1, end=1, text="    return 0.5", language="python", kind="code"
        )
        with self.assertRaises(WeakCiteError):
            extract_evidence(target, 1, 1)

    def test_multiline_evidence_covers_more_than_the_first_line(self):
        # THE property: a multi-line cite's evidence must not be entirely
        # consumed by fragments of a single-line literal, leaving nothing to
        # prove the rest of the cited range ever changes.
        tokens = extract_evidence(FRAGMENT_STARVED, 1, 2)
        second_line = FRAGMENT_STARVED.text.split("\n")[1]
        self.assertTrue(
            any(t in second_line for t in tokens),
            f"no token covers line 2 {second_line!r}: {tokens}",
        )

    def test_drift_on_the_second_line_is_caught(self):
        # The exact reported repro: renaming "threshold" to "ceiling" must
        # turn up as a miss once fragments stop starving line 2 of coverage.
        tokens = extract_evidence(FRAGMENT_STARVED, 1, 2)
        mutated = FRAGMENT_STARVED.text.replace("threshold", "ceiling")
        self.assertEqual(_missing(mutated, tokens), ["threshold"])

    def test_standalone_digit_elsewhere_in_range_is_still_legitimate(self):
        # The guardrail the fix must not overshoot: a "0" or "5" that is NOT
        # a substring-position of an already-chosen token is still a real,
        # independent claim about the source and must still be emitted.
        target = FocusTarget(
            path="x.py",
            start=1,
            end=2,
            text="grounding_floor_count = 0\nmultiplier_value = 5",
            language="python",
            kind="code",
        )
        tokens = extract_evidence(target, 1, 2)
        self.assertIn("0", tokens)
        self.assertIn("5", tokens)


class ProportionalBudgetTests(unittest.TestCase):
    """Evidence must cover the range it claims, not a corner of it.

    A flat budget makes the gate's promise size-dependent in a way nothing
    surfaces: the badge reads the same whether five tokens covered a one-line
    cite completely or a seven-line table barely at all. Found by planning a
    real video, where every slot went to string keys because _STRING outranks
    _INT globally -- so the five pixel heights, half of what the cited table
    MEANS, could drift with the gate still green.
    """

    def _drifts(self, target, lo, hi, mutations):
        """Return which of *mutations* the extracted evidence actually catches."""
        tokens = extract_evidence(target, lo, hi)
        offset = lo - target.start
        cited = "\n".join(target.text.split("\n")[offset : offset + (hi - lo) + 1])
        return {
            label: bool(_missing(cited.replace(old, new), tokens))
            for label, (old, new) in mutations.items()
        }

    def test_a_dense_table_protects_its_values_not_just_its_keys(self):
        caught = self._drifts(
            LADDER,
            11,
            17,
            {
                "key": ('"-qh"', '"-qz"'),
                "value": ("1080", "1081"),
                "name": ("QUALITY_LADDER", "QUALITY_STEPS"),
            },
        )
        self.assertEqual(caught, {"key": True, "value": True, "name": True}, caught)

    def test_evidence_is_spread_across_lines_not_clustered_on_one(self):
        tokens = extract_evidence(LADDER, 11, 17)
        lines = LADDER.text.split("\n")
        covered = {i for i, line in enumerate(lines) if any(t in line for t in tokens)}
        # Six of the seven lines carry a fact; the bare ")" carries none.
        self.assertGreaterEqual(len(covered), 6, f"only covered {covered}: {tokens}")

    def test_budget_grows_with_the_cited_range(self):
        # Asserting only "bigger range yields more tokens" would NOT pin
        # proportional budgeting: under the old flat cap of 5 the full ladder
        # still yielded 5 tokens against the 2-line slice's 4, so that
        # comparison passes with this whole change reverted. Pin the policy.
        self.assertGreater(len(extract_evidence(LADDER, 11, 17)), BASE_TOKENS)
        self.assertEqual(len(extract_evidence(LADDER, 12, 13)), 4)

    def test_a_value_repeated_across_lines_is_a_known_blind_spot(self):
        # Evidence is a POSITION-FREE list checked against the cited text as a
        # whole, so a value that survives on one line masks its own drift on
        # another. This test exists to stop a future reader "fixing" it by
        # de-duplicating less: the second assertion shows that emitting the
        # token three times changes nothing, because no copy knows its line.
        # Closing this needs a position-aware evidence format in the shipped
        # verifier, or multi-lexeme code tokens. See _spread's KNOWN LIMIT.
        target = FocusTarget(
            path="x.py",
            start=1,
            end=3,
            text="alpha = 1\nbeta = 1\ngamma = 1",
            language="python",
            kind="code",
        )
        tokens = extract_evidence(target, 1, 3)
        drifted = target.text.replace("gamma = 1", "gamma = 2")
        self.assertEqual(_missing(drifted, tokens), [])
        self.assertEqual(_missing(drifted, ["1", "1", "1"]), [])

    def test_a_one_line_cite_is_no_weaker_than_the_old_flat_budget(self):
        # The budget scales UP with range size and never down: a single line
        # must still be able to yield the five tokens it always could, or this
        # change would trade new coverage for lost coverage.
        target = FocusTarget(
            path="x.py",
            start=1,
            end=1,
            text='cfg = Loader.build("alpha", "beta", retry_limit=7)',
            language="python",
            kind="code",
        )
        self.assertGreaterEqual(len(extract_evidence(target, 1, 1)), 5)

    def test_budget_has_a_ceiling_so_a_huge_cite_stays_readable(self):
        # Proportional must not mean unbounded -- a careless whole-file cite
        # would otherwise write hundreds of tokens into the storyboard.
        text = "\n".join(f'value_{i} = "token_{i}"' for i in range(200))
        target = FocusTarget(
            path="big.py", start=1, end=200, text=text, language="python", kind="code"
        )
        self.assertLessEqual(len(extract_evidence(target, 1, 200)), MAX_TOKENS)

    def test_the_real_gate_still_accepts_a_proportional_cite(self):
        # The load-bearing property, re-checked at the new budget: more tokens
        # must not mean tokens the shipped verifier cannot find.
        for target, lo, hi in ((LADDER, 11, 17), (RUST, 90, 94), (PROSE, 1, 2)):
            with self.subTest(path=target.path):
                tokens = extract_evidence(target, lo, hi)
                offset = lo - target.start
                cited = "\n".join(target.text.split("\n")[offset : offset + (hi - lo) + 1])
                self.assertEqual(_missing(cited, tokens), [])


class WeakStopwordFilterTests(unittest.TestCase):
    """Comment English matches _PLAIN like identifiers do; a stopword proves
    nothing about the cited LOCATION, so it may not fill a weak slot."""

    def test_weak_stopwords_are_not_evidence(self):
        _strong, weak = _code_tokens("value = compute()  # the result, more or less")
        weak_tokens = [token for _line, token in weak]
        self.assertNotIn("the", weak_tokens)
        self.assertNotIn("more", weak_tokens)
        self.assertIn("value", weak_tokens)
        self.assertIn("result", weak_tokens)

    def test_a_string_literal_stopword_stays_strong_evidence(self):
        strong, _weak = _code_tokens('mode = "the"')
        self.assertIn('"the"', [token for _line, token in strong])

    def test_stopword_only_range_is_a_weak_cite(self):
        target = FocusTarget(
            path="a.py",
            start=1,
            end=1,
            text="# the and for was",
            language="python",
            kind="code",
        )
        with self.assertRaises(WeakCiteError):
            extract_evidence(target, 1, 1)

    def test_one_strong_token_with_only_stopword_weak_fill_is_a_weak_cite(self):
        # Pre-filter, "42" plus stopword fill shipped 4 tokens; now the
        # stopwords may not pad the count, so one strong token alone is
        # below MIN_TOKENS and the cite drops -- the filter's causal
        # contribution, not just the floor's survival.
        target = FocusTarget(
            path="a.py",
            start=1,
            end=1,
            text="x = 42  # the was once",
            language="python",
            kind="code",
        )
        with self.assertRaises(WeakCiteError):
            extract_evidence(target, 1, 1)


def _cited_lines(target, lo, hi):
    offset = lo - target.start
    return target.text.split("\n")[offset : offset + (hi - lo) + 1]


def _evidence_span(target, lo, hi):
    """The 0-based ``(first, last)`` cited lines the real gate finds evidence on."""
    return match_evidence(_cited_lines(target, lo, hi), extract_evidence(target, lo, hi)).span


def _candidate_lines(target, lo, hi):
    """The 0-based cited lines that hold a token candidate."""
    lines = _cited_lines(target, lo, hi)
    return [index for index, line in enumerate(lines) if _line_candidates(line, target.kind)]


class EdgeLineTests(unittest.TestCase):
    """Evidence reaches the first and last cited lines that hold a candidate.

    expand.py trims every cite it writes to the lines its evidence reaches,
    because the gate fails a cite whose first or last line holds none. When
    the budget ran out before the last lines of a long cite, that trim cut
    real code, and the on-screen label covered less than the display showed.
    """

    def test_the_last_line_keeps_evidence_after_the_budget_runs_out(self):
        self.assertEqual(_evidence_span(CONSTANTS_THEN_FUNCTION, 1, 20), (0, 19))

    def test_the_first_line_keeps_evidence_too(self):
        self.assertEqual(_evidence_span(HEADED_CONSTANTS, 1, 13), (0, 12))

    def test_a_cite_longer_than_the_budget_keeps_evidence_on_its_last_line(self):
        for target in (LONG_TABLE, LONG_NOTES):
            with self.subTest(path=target.path):
                self.assertEqual(_evidence_span(target, 1, 30), (0, 29))

    def test_an_edge_line_whose_candidates_all_appear_earlier_keeps_evidence(self):
        # The whole-text candidates name each token once, at the line where it
        # first appears, so an edge token must come from the edge line itself.
        for target in (ECHOED_FUNCTION, ECHOED_NOTES):
            with self.subTest(path=target.path):
                last = len(target.text.split("\n")) - 1
                self.assertEqual(_evidence_span(target, 1, last + 1), (0, last))

    def test_the_trim_never_cuts_a_line_holding_a_candidate(self):
        # Evidence spans every line that holds a token candidate, so the trim
        # to it cuts only lines no token can be drawn from.
        for target, lo, hi in (
            (CONSTANTS_THEN_FUNCTION, 1, 20),
            (CONSTANTS_THEN_FUNCTION, 5, 17),
            (HEADED_CONSTANTS, 1, 13),
            (ECHOED_FUNCTION, 1, 20),
            (ECHOED_NOTES, 1, 30),
            (FILLER_ROWS, 1, 30),
            (LONG_TABLE, 1, 30),
            (LONG_NOTES, 3, 30),
            (DENSE, 1, 3),
            (FENCED, 10, 14),
            (CHECK, 1, 4),
            (RUST, 90, 94),
            (LADDER, 11, 17),
            (MIXED, 1, 2),
        ):
            with self.subTest(path=target.path, lo=lo, hi=hi):
                first, last = _evidence_span(target, lo, hi)
                bearing = _candidate_lines(target, lo, hi)
                self.assertLessEqual(first, bearing[0])
                self.assertGreaterEqual(last, bearing[-1])

    def test_lines_that_cannot_hold_evidence_are_left_for_the_trim(self):
        # A blank line, "} else {" and a closing brace hold nothing to cite.
        self.assertEqual(content_lines(FENCED, 10, 14), (11, 13))
        self.assertEqual(_evidence_span(FENCED, 10, 14), (1, 3))

    def test_a_code_fence_is_markup_not_content(self):
        # A fence delimits the content, as a brace does in code, even when it
        # names a language.
        target = FocusTarget(
            path="README.md",
            start=1,
            end=4,
            text="```bash\ngit clone the repository first\n~~~\n```",
            language="markdown",
            kind="prose",
        )
        self.assertEqual(content_lines(target, 1, 4), (2,))

    def test_code_without_a_candidate_is_still_content(self):
        # No token can be drawn from Ok(()), so the trim cuts it, but it is code
        # the display shows, and expand.py reports cutting it.
        self.assertEqual(_candidate_lines(CHECK, 1, 4), [0, 1])
        self.assertEqual(content_lines(CHECK, 1, 4), (1, 2, 3))

    def test_a_weak_edge_does_not_push_out_the_only_strong_phrase(self):
        # Prose is spread for coverage, not strength. Here the weak first and
        # last lines took the two slots that reached line 24, the only strong
        # phrase, and the cite would have been dropped as weak.
        tokens = extract_evidence(FILLER_ROWS, 1, 30)
        self.assertIn("It is 23 and it is retried", tokens)
        self.assertEqual(_evidence_span(FILLER_ROWS, 1, 30), (0, 29))

    def test_an_edge_line_spends_one_slot(self):
        # An edge takes the token _spread would give its line first, not a
        # second one beside it, so the budget still reaches every line here.
        lines = PHRASED_FIRST_LINE.text.split("\n")
        tokens = extract_evidence(PHRASED_FIRST_LINE, 1, 24)
        covered = [i for i, line in enumerate(lines) if any(token in line for token in tokens)]
        self.assertEqual(covered, list(range(24)))

    def test_strong_tokens_still_fill_the_rest_of_the_budget(self):
        # Only the edge lines take a token out of turn: "round" holds the last
        # line. Every other slot still goes to a strong token while one is
        # left, so the function's other weak identifiers stay out.
        tokens = extract_evidence(CONSTANTS_THEN_FUNCTION, 1, 20)
        self.assertEqual(len(tokens), MAX_TOKENS)
        self.assertIn("round", tokens)
        for weak in ("scale", "values", "factor", "running", "value", "total"):
            self.assertNotIn(weak, tokens)

    def test_a_selection_that_already_holds_both_edge_tokens_is_unchanged(self):
        # Taking the edge tokens first must not reshuffle the rest of the
        # spread: dropping them from it and spreading the remainder afresh
        # would trade a token here for another the old selection never chose.
        lines = _cited_lines(DENSE, 1, 3)
        strong, _weak = _code_tokens("\n".join(lines))
        before = _spread(strong, _token_budget(len(lines)))
        self.assertEqual(sorted(extract_evidence(DENSE, 1, 3)), sorted(before))


if __name__ == "__main__":
    unittest.main()
