"""Tests for explainer.codegen.plot_expr -- the safe plot function mini-language.

Curve `fn` strings are parsed with `ast` and evaluated through a whitelist, never
`eval` on arbitrary input. Disallowed constructs are rejected at compile time;
non-finite / erroring sample points are dropped, not rendered.
"""

import math
import unittest

from explainer.codegen.plot_expr import (
    PlotExprError,
    compile_curve,
    is_piecewise,
    normalize_curve,
    sample_curve,
)


class CompileCurveTests(unittest.TestCase):
    def test_half_life_evaluates(self):
        fn = compile_curve("0.5 ** (x / 30)")
        self.assertAlmostEqual(fn(0), 1.0)
        self.assertAlmostEqual(fn(30), 0.5)
        self.assertAlmostEqual(fn(60), 0.25)

    def test_max_min_abs(self):
        self.assertAlmostEqual(compile_curve("max(0.4, 0.5 ** (x / 30))")(120), 0.4)
        self.assertAlmostEqual(compile_curve("min(1, x)")(5), 1.0)
        self.assertAlmostEqual(compile_curve("abs(0 - x)")(3), 3.0)

    def test_constant_curve(self):
        self.assertEqual(compile_curve("0.06")(999), 0.06)

    def test_rejects_unknown_name(self):
        with self.assertRaises(PlotExprError):
            compile_curve("y + 1")

    def test_rejects_unlisted_call(self):
        with self.assertRaises(PlotExprError):
            compile_curve("eval('1')")

    def test_rejects_attribute_access(self):
        with self.assertRaises(PlotExprError):
            compile_curve("x.__class__")

    def test_rejects_lambda(self):
        with self.assertRaises(PlotExprError):
            compile_curve("(lambda: 1)()")

    def test_rejects_excessive_depth(self):
        with self.assertRaises(PlotExprError):
            compile_curve("x" + " + x" * 100)  # a genuine 100-deep BinOp chain

    def test_rejects_syntax_error(self):
        with self.assertRaises(PlotExprError):
            compile_curve("0.5 ** ")


class NormalizeCurveTests(unittest.TestCase):
    """normalize_curve returns canonical single-line source for codegen to embed,
    so a whitelisted-but-comment-bearing fn can never break the generated scene."""

    def test_canonical_source_round_trips(self):
        self.assertEqual(normalize_curve("0.5 ** (x / 30)"), "0.5 ** (x / 30)")
        self.assertEqual(normalize_curve("max(0.4, 0.5 ** (x / 30))"), "max(0.4, 0.5 ** (x / 30))")

    def test_strips_trailing_comment(self):
        # '#' passes the AST whitelist (comments are discarded by ast.parse) but
        # would comment out the rest of the generated line -- normalization drops it.
        out = normalize_curve("0  # )")
        self.assertEqual(out, "0")
        self.assertNotIn("#", out)

    def test_rejects_unsafe_like_compile(self):
        with self.assertRaises(PlotExprError):
            normalize_curve("eval('1')")

    def test_rejects_syntax_error(self):
        with self.assertRaises(PlotExprError):
            normalize_curve("0.5 ** ")


class SampleCurveTests(unittest.TestCase):
    def test_samples_points(self):
        pts = sample_curve("x * 2", [0, 1, 2])
        self.assertEqual(pts, [(0, 0), (1, 2), (2, 4)])

    def test_drops_zero_division(self):
        pts = sample_curve("1 / x", [0, 1, 2])
        self.assertEqual(pts, [(1, 1.0), (2, 0.5)])  # x=0 dropped

    def test_drops_non_finite(self):
        pts = sample_curve("log(x)", [0.0, 1.0])  # log(0) -> domain error, dropped
        self.assertEqual(pts, [(1.0, 0.0)])
        self.assertTrue(all(math.isfinite(y) for _, y in pts))


class IsPiecewiseTests(unittest.TestCase):
    def test_min_max_abs_mod_are_piecewise(self):
        for expr in ("min(24, max(5, 2 * x))", "max(0, x)", "abs(x - 5)", "x % 4"):
            with self.subTest(expr=expr):
                self.assertTrue(is_piecewise(expr))

    def test_a_nested_bend_is_found(self):
        self.assertTrue(is_piecewise("0.5 ** (min(x, 60) / 30)"))

    def test_smooth_expressions_are_not_piecewise(self):
        for expr in ("0.5 ** (x / 30)", "2 * x", "exp(-x) + log(x + 1)", "0.06"):
            with self.subTest(expr=expr):
                self.assertFalse(is_piecewise(expr))

    def test_invalid_expression_raises(self):
        with self.assertRaises(PlotExprError):
            is_piecewise("eval('x')")


if __name__ == "__main__":
    unittest.main()
