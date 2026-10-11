"""Validate the version-one concept scene contract."""

import math
import re

_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")


def _object(value, keys):
    if type(value) is not dict or value.keys() != set(keys):
        raise ValueError("Expected an object with exactly the required fields")


def _list(value, minimum, maximum):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError("Invalid list or list length")


def _text(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise ValueError("Invalid text or text length")


def _number(value, minimum, maximum):
    if type(value) not in (int, float):
        raise ValueError("Expected a number, excluding booleans")
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError("Number must be finite and within bounds")


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("Expected an ASCII identifier of at most 32 characters")


def _references(value, known):
    _list(value, 0, 8)
    result = set()
    for identifier in value:
        _identifier(identifier)
        if identifier not in known or identifier in result:
            raise ValueError("Unknown or duplicate node reference")
        result.add(identifier)
    return result


def validate(data: dict) -> dict:
    """Return the original input unchanged, or raise ValueError."""
    _object(data, ("version", "title", "font", "nodes", "edges", "steps"))
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Only integer version 1 is supported")
    _text(data["title"], 100)
    _text(data["font"], 80)
    _list(data["nodes"], 1, 8)
    _list(data["edges"], 0, 12)
    _list(data["steps"], 1, 12)

    known = set()
    for node in data["nodes"]:
        _object(node, ("id", "label", "x", "y"))
        _identifier(node["id"])
        if node["id"] in known:
            raise ValueError("Duplicate node ID")
        known.add(node["id"])
        _text(node["label"], 60)
        _number(node["x"], -5, 5)
        _number(node["y"], -2.5, 2.5)

    connections = set()
    for edge in data["edges"]:
        _object(edge, ("from", "to"))
        start, end = edge["from"], edge["to"]
        _identifier(start)
        _identifier(end)
        if start not in known or end not in known or start == end:
            raise ValueError("Edges must connect distinct known nodes")
        pair = (start, end)
        if pair in connections:
            raise ValueError("Duplicate edge")
        connections.add(pair)

    visible = set()
    durations = []
    for step in data["steps"]:
        _object(step, ("show", "highlight", "caption", "duration"))
        shown = _references(step["show"], known)
        highlighted = _references(step["highlight"], known)
        if shown & visible:
            raise ValueError("A node may be shown only once")
        visible.update(shown)
        if not highlighted <= visible:
            raise ValueError("Highlights must refer to visible nodes")
        _text(step["caption"], 120)
        _number(step["duration"], 1, 10)
        durations.append(step["duration"])
    if not 10 <= math.fsum(durations) <= 30:
        raise ValueError("Total duration must be between 10 and 30 seconds")
    return data
