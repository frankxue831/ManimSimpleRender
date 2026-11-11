"""Optional requested render rate, independent of observed media evidence."""


def validate_fps(fps):
    """Accept only no override or a strict integer request in [1, 240]."""
    if fps is not None and (type(fps) is not int or not 1 <= fps <= 240):
        raise ValueError("fps must be an integer in [1, 240] or None")
    return fps


def parse_fps(token):
    """Adapt an integer CLI token to the shared strict request validator."""
    return validate_fps(int(token))
