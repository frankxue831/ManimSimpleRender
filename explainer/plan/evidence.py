"""Turn a cited source range into evidence tokens that are true by construction.

The planner's LLM picks WHAT to cite and writes the prose; this module picks
the tokens that prove it. A CODE token is a literal substring of the cited
range. A PROSE token is a whitespace-normalized JOIN of one -- see
_prose_phrases -- because prose can wrap or double-space a phrase that is
still, after normalization, exactly what is on the page. Either way,
explainer/cite/verifier.py compares whitespace-normalized text, so both kinds
are guaranteed findable and generated evidence cannot fail its own citation
gate. That is deliberate: it leaves the gate doing the job it should do,
catching future source DRIFT, instead of catching hallucination.

It also removes the failure mode where a model, asked to fix a red gate,
simply deletes the failing token until the gate goes green. There is no token
for it to delete: it never writes one.
"""

import re
import string
from bisect import bisect_left
from itertools import zip_longest

from explainer.storyboard.cites import is_regex_token

# Ranked strongest-first. A "strong" token is one whose presence is a real
# claim about the source: a literal value, or a qualified/compound name.
_STRING = r"\"[^\"\n]*\"|'[^'\n]*'"
_FLOAT = r"\b\d+\.\d+\b"
_INT = r"\b\d+\b"
_QUALIFIED = r"\b[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.|->)[A-Za-z_][A-Za-z0-9_]*)+"
_COMPOUND = r"\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b"
_PLAIN = r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b"

_STRONG_RES = (_STRING, _QUALIFIED, _FLOAT, _COMPOUND, _INT)
_WEAK_RES = (_PLAIN,)

# The evidence budget is PROPORTIONAL to the cited range, because the badge's
# promise is about the whole range: "these lines are verified at this commit".
# A flat budget made that promise quietly size-dependent -- five tokens prove
# a one-line cite completely and a seven-line table barely at all, while the
# gate prints the same "OK" either way.
#
# The failure that motivated this was found by planning a real video against a
# lookup table (flag string -> pixel height, one pair per line). Every slot
# went to the string keys, because _STRING outranks _INT in _STRONG_RES and
# the ranking applied GLOBALLY across the range rather than per line. Renaming
# the table or changing any pixel height left the gate green: half of what the
# cited lines meant was never evidence at all.
#
# BASE_TOKENS is a floor, not a target, so this only ever adds coverage: a
# short cite still yields everything it used to. MAX_TOKENS is the ceiling --
# proportional must not mean unbounded, or a careless whole-file cite would
# write hundreds of tokens into the storyboard and make the gate red on any
# edit anywhere in the file.
BASE_TOKENS = 5
TOKENS_PER_LINE = 2
MAX_TOKENS = 24
MIN_TOKENS = 2
MIN_PHRASE_WORDS = 4
_PHRASE_WINDOW = 7
_PHRASE_STEP = 4

# Language-agnostic keyword set: present in the source but claiming nothing.
_KEYWORDS = frozenset("""
    and as assert async await break case catch class const continue def default del elif else
    enum except export extern false final finally fn for from func global if impl import in
    int interface is let match mod module mut new nil none not null or pass priv pub raise
    return self static struct super switch then this throw trait true try type typedef union
    unsafe use var void where while with yield
    """.split())


# The prose counterpart to _KEYWORDS. Code gets a strength floor for free:
# the strong regexes match literals and qualified names, and _KEYWORDS drops
# the words that claim nothing. Prose has neither, so without this list a
# window of pure English glue -- "it is what it is and that is all" -- would
# clear the floor and put a verified badge on filler. The second block is the
# words that are not grammatical glue but still assert nothing: hedge adverbs
# and nonsense placeholders.
#
# The bar for the second block is that a word can have no plausible role as
# the PAYLOAD of a technical claim. That rules out several words this list
# once carried: "placeholder", "todo", "nothing", "sort", "kind" all read as
# filler in isolation but are routinely the whole point of a terse sentence
# ("it is only a placeholder for now", "the sort is stable"), and this module
# already uses "kind" as a field name. Listing them cost a real cite: the
# floor is not the place to catch a thin one.
#
# SCOPE, stated so it is a decision and not an oversight, in both directions.
# Under-inclusion: this list is English, and it is a list. Filler in another
# language, or an unlisted vague word, still reads as distinctive and clears
# the floor. Widening it far enough to catch every thin sentence would start
# rejecting real claims -- "happens" is filler in one sentence and the point
# of the next. Over-inclusion is the worse error and the reason the bar above
# is strict: dropping a true cite costs a fact the video could have proved,
# while a thin cite that gets through is caught by review of the draft. The
# floor's job is to stop evidence that proves nothing about the LOCATION, not
# to grade the prose.
_STOPWORDS = frozenset("""
    a about after all also an and any are as at be because been being both but by
    can could did do does doing done down during each either else even ever every
    for from further had has have having he her here hers him his how however
    i if in into is it its itself just like made make many may me might more most
    much must my neither no nor not now of off on once only or other others our
    out over own rather same she should since so some such than that the their
    them then there these they this those though through thus to too under until
    up upon us very was we were what when where whether which while who whom why
    will with within would you your yours

    actually basically blah certainly essentially generally honestly ipsum
    literally lorem obviously probably really seriously simply sometimes
    somewhat somewhere truly usually whatever
    """.split())

# Four characters is where English content words start showing up in bulk
# ("hash", "byte", "lock", "test") while nearly every function word is either
# shorter or listed above. Lower and the floor lets "was"-shaped glue through;
# higher and it starts rejecting real claims.
#
# The limit of a lexical rule: "This section intentionally left blank" is full
# of distinctive words and still says nothing, so it clears this floor. That
# is deliberate rather than a gap. Prose evidence exists to detect DRIFT, and
# a boilerplate line detects drift perfectly well -- if it changes, the token
# stops matching. Citing a pointless line is a planning mistake, caught by
# review of the draft, not something extraction can decide from the words.
MIN_DISTINCTIVE_WORD = 4


class WeakCiteError(Exception):
    """The cited range cannot support a claim worth putting a badge on."""


def _cited_text(target, start, end):
    if start < target.start or end > target.end or end < start:
        raise WeakCiteError(
            f"cite {target.path}:{start}-{end} falls outside focus range "
            f"{target.start}-{target.end}"
        )
    offset = start - target.start
    return "\n".join(target.text.split("\n")[offset : offset + (end - start) + 1])


def _acceptable(token):
    # is_regex_token: the verifier reads a /.../-wrapped token as a regular
    # expression, so emitting one would change the meaning of the claim.
    return bool(token.strip()) and not is_regex_token(token)


def _code_tokens(text):
    """Return ``(strong, weak)`` lists of ``(line_index, token)`` candidates.

    Both lists are ordered by pattern strength -- every ``_STRING`` match
    precedes every ``_QUALIFIED`` match, and so on through ``_STRONG_RES``
    before ``_WEAK_RES`` (``_PLAIN``) even starts. That ranking now decides
    only which token represents a line FIRST; ``_spread`` decides which
    survive the budget, taking one line at a time. The distinction matters:
    when the ranking also decided survival, a range whose lines each carried
    a string and an integer spent the entire budget on strings, and every
    integer in it could drift undetected. Weak-tier candidates whose lowercase
    form is a _STOPWORDS entry are dropped; strong candidates never are.

    A match is dropped when its span falls entirely inside the span of a
    token already selected -- e.g. the "5" in a "0.5" _FLOAT match also
    satisfies the bare _INT pattern, and a qualified name's tail matches
    _PLAIN on its own. Such a match is a FRAGMENT of evidence already
    captured, not new evidence, and letting it through would let a run of
    fragments crowd out tokens that would have covered a different part of
    the cited range. This is a POSITIONAL check: a token that merely shares a
    VALUE with a different, non-nested occurrence elsewhere in the text is
    unaffected and stays legitimate evidence.
    """
    seen = set()
    claimed = []  # (start, end) character spans of every token already kept
    strong, weak = [], []
    # Newline offsets once up front: the line index of a match is then a
    # binary search instead of a per-token rescan of the text from 0, which
    # on a whole-file cite turned line numbering into O(tokens x characters).
    newline_at = [m.start() for m in re.finditer("\n", text)]

    def _is_fragment(start, end):
        return any(c_start <= start and end <= c_end for c_start, c_end in claimed)

    for group, bucket in ((_STRONG_RES, strong), (_WEAK_RES, weak)):
        for pattern in group:
            for match in re.finditer(pattern, text):
                token = match.group(0)
                if token in seen or not _acceptable(token):
                    continue
                if token.lower() in _KEYWORDS:
                    continue
                if bucket is weak and token.lower() in _STOPWORDS:
                    # Comment/docstring English matches _PLAIN in code just
                    # as identifiers do; a stopword proves nothing about the
                    # LOCATION, so it may not fill a weak slot. Strong tokens
                    # are exempt: a string literal "the" is a real claim.
                    continue
                if _is_fragment(match.start(), match.end()):
                    continue
                seen.add(token)
                claimed.append((match.start(), match.end()))
                bucket.append((bisect_left(newline_at, match.start()), token))
    return strong, weak


def _token_budget(line_count):
    """Evidence slots for a cite spanning ``line_count`` lines.

    Floored at ``BASE_TOKENS`` so short cites keep the coverage they always
    had, and capped at ``MAX_TOKENS`` so a whole-file cite stays readable.
    """
    return min(MAX_TOKENS, max(BASE_TOKENS, TOKENS_PER_LINE * line_count))


def _spread(entries, budget):
    """Pick up to ``budget`` tokens, round-robin over the lines they came from.

    Each pass takes the next-strongest unused token from every line that still
    has one, in line order, so a cite is covered end to end before any single
    line is covered in depth. Drift is a property of the whole cited range --
    evidence clustered on its first lines silently narrows the gate to a
    prefix of what the badge claims.

    KNOWN LIMIT, stated so the next reader does not mistake it for an
    oversight: evidence is a POSITION-FREE list of strings, matched by the
    shipped verifier against the cited text as a whole. So when the same value
    appears on several cited lines, changing it on ONE of them leaves the
    token still findable on another, and the gate stays green -- cite
    "alpha = 1 / beta = 1 / gamma = 1", change the last to 2, and nothing goes
    red. De-duplicating less does not help: emitting "1" three times is three
    identical substring checks, and none of them knows which line it came
    from. Closing this needs a position-aware evidence format in the SHIPPED
    verifier, or code tokens spanning more than one lexeme -- both real design
    changes, neither one a tweak to selection. Pinned by
    test_a_value_repeated_across_lines_is_a_known_blind_spot.
    """
    by_line = {}
    for line, token in entries:
        by_line.setdefault(line, []).append(token)
    buckets = [by_line[line] for line in sorted(by_line)]
    interleaved = [token for tier in zip_longest(*buckets) for token in tier if token is not None]
    return interleaved[:budget]


def _prose_phrases(text):
    """Return verbatim multi-word ``(line_index, phrase)`` pairs, de-duplicated.

    Phrases are joined with single spaces. The verifier normalizes whitespace
    on both sides before matching, so a normalized phrase is still found in
    source that wrapped or double-spaced it.

    Ordered longest-first WITHIN a line rather than globally, because
    ``_spread`` consumes these one line at a time: the ordering that matters
    is which phrase represents its own line first, not which line wins.
    """
    seen = set()
    entries = []
    for index, line in enumerate(text.split("\n")):
        words = line.split()
        if len(words) < MIN_PHRASE_WORDS:
            continue
        line_phrases = []
        for begin in range(0, len(words), _PHRASE_STEP):
            window = words[begin : begin + _PHRASE_WINDOW]
            if len(window) < MIN_PHRASE_WORDS:
                break
            phrase = " ".join(window)
            key = phrase.lower()
            if key in seen or not _acceptable(phrase):
                continue
            seen.add(key)
            line_phrases.append(phrase)
        line_phrases.sort(key=lambda p: -len(p.split()))
        entries.extend((index, phrase) for phrase in line_phrases)
    return entries


def _is_strong_phrase(phrase):
    """True when the phrase carries at least one word doing real work.

    Case and edge punctuation are normalized for the TEST only -- otherwise
    "That!" reads as a word the stopword list has never heard of. The phrase
    itself is always emitted verbatim, because the verifier has to find it in
    the source exactly as the source wrote it.
    """
    for word in phrase.split():
        bare = word.strip(string.punctuation).lower()
        if len(bare) >= MIN_DISTINCTIVE_WORD and bare not in _STOPWORDS:
            return True
    return False


def extract_evidence(target, start, end):
    """Evidence tokens for ``target``'s lines ``start``..``end`` (1-based, inclusive).

    Raises :class:`WeakCiteError` when the range cannot yield at least
    ``MIN_TOKENS`` tokens including one strong one -- a cite that cannot prove
    something is dropped rather than shipped with a weak badge.
    """
    text = _cited_text(target, start, end)
    budget = _token_budget(len(text.split("\n")))
    if target.kind == "prose":
        chosen = _spread(_prose_phrases(text), budget)
        strong_count = len([p for p in chosen if _is_strong_phrase(p)])
    else:
        strong, weak = _code_tokens(text)
        # Strong first, weak only to fill what strong could not: _spread
        # balances coverage WITHIN a strength tier, never across them.
        chosen = _spread(strong, budget)
        strong_count = len(chosen)
        if len(chosen) < budget:
            chosen = chosen + _spread(weak, budget - len(chosen))
    if len(chosen) < MIN_TOKENS or strong_count < 1:
        raise WeakCiteError(
            f"cite {target.path}:{start}-{end} yields only {len(chosen)} usable "
            f"evidence token(s); a verified badge must prove something"
        )
    return tuple(chosen)
