"""Cite-string parsing, shared by the loader (model) and the verifier.

Lives on the storyboard side (a dependency-free leaf) so the IR core never has
to import the citation gate -- the verifier imports this and the model's typed
citation targets, never the reverse.
"""


def parse_cite(cite):
    """Split ``path:line`` or ``path:start-end`` into ``(path, start, end)`` (1-based)."""
    path, sep, lineref = cite.rpartition(":")
    if not sep or not lineref.strip():
        raise ValueError(f"malformed cite (expected path:line): {cite!r}")
    lineref = lineref.strip()
    if "-" in lineref:
        start_s, end_s = lineref.split("-", 1)
        start, end = int(start_s), int(end_s)
    else:
        start = end = int(lineref)
    return path, start, end


def format_cite(path, start, end):
    """The inverse of :func:`parse_cite`: render ``(path, start, end)`` back to
    the ``path:line`` / ``path:start-end`` spelling. Lives next to the parser
    so the grammar and its inverse cannot drift apart."""
    return f"{path}:{start}" if start == end else f"{path}:{start}-{end}"


def is_regex_token(token):
    """True when *token* uses the ``/pattern/`` evidence spelling, which the
    verifier matches as a regular expression rather than a literal substring.

    The ONE definition of that convention: the verifier interprets such
    tokens, and evidence generation must refuse to emit one (it would change
    the meaning of the claim), so both sides import this predicate.
    """
    return len(token) >= 2 and token.startswith("/") and token.endswith("/")
