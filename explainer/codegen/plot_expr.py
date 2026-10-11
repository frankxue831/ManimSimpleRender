"""A tiny, safe function mini-language for plot curves.

Curve ``fn`` strings (e.g. ``"0.5 ** (x / 30)"``) are parsed with ``ast`` and
evaluated through a strict node/name/function whitelist -- never ``eval`` on raw
input. Sample points that raise (domain error, division by zero) or are
non-finite (an asymptote) are dropped, so a render never crashes on a curve.
"""

import ast
import math

MAX_DEPTH = 40

_ALLOWED_FUNCS = {
    "max": max,
    "min": min,
    "abs": abs,
    "exp": math.exp,
    "log": math.log,
}
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)


class PlotExprError(Exception):
    """Raised when a curve expression is malformed or uses a disallowed construct."""


def _check(node, depth):
    if depth > MAX_DEPTH:
        raise PlotExprError("expression too deeply nested")
    if isinstance(node, ast.Expression):
        _check(node.body, depth)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise PlotExprError(f"only numeric constants allowed, got {node.value!r}")
    elif isinstance(node, ast.Name):
        if node.id != "x":
            raise PlotExprError(f"unknown name {node.id!r} (only 'x' is allowed)")
    elif isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise PlotExprError(f"operator {type(node.op).__name__} not allowed")
        _check(node.left, depth + 1)
        _check(node.right, depth + 1)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise PlotExprError(f"unary operator {type(node.op).__name__} not allowed")
        _check(node.operand, depth + 1)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise PlotExprError("only max/min/abs/exp/log calls are allowed")
        if node.keywords:
            raise PlotExprError("keyword arguments are not allowed")
        for arg in node.args:
            _check(arg, depth + 1)
    else:
        raise PlotExprError(f"disallowed expression element: {type(node).__name__}")


def _parse_validated(expr):
    """Parse ``expr`` and run it through the whitelist; return the validated tree."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PlotExprError(f"syntax error: {exc}") from exc
    _check(tree, 0)
    return tree


def normalize_curve(expr):
    """Validate ``expr`` and return its canonical single-line source.

    Codegen embeds THIS, not the raw input. ``ast.parse`` silently discards
    comments and line breaks, so a whitelisted ``"0  # )"`` would otherwise be
    embedded verbatim and comment out the rest of the generated ``plot(...)``
    line. ``ast.unparse`` re-emits the validated tree as safe, compilable source.
    """
    return ast.unparse(_parse_validated(expr))


def compile_curve(expr):
    """Parse and validate ``expr``; return a callable ``f(x) -> float``.

    Raises :class:`PlotExprError` on a syntax error or any disallowed construct.
    """
    tree = _parse_validated(expr)
    code = compile(tree, "<curve>", "eval")
    namespace = {"__builtins__": {}, **_ALLOWED_FUNCS}

    def fn(x):
        # Safe: the AST was validated against the whitelist and builtins are empty.
        return float(eval(code, namespace, {"x": x}))

    return fn


def sample_curve(expr, xs):
    """Evaluate ``expr`` at each x in ``xs``, dropping points that raise or are
    non-finite. Returns a list of ``(x, y)`` pairs."""
    fn = compile_curve(expr)
    points = []
    for x in xs:
        try:
            y = fn(x)
        except (ZeroDivisionError, ValueError, OverflowError):
            continue
        if math.isfinite(y):
            points.append((x, y))
    return points
