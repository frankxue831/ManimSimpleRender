"""Host-owned acceptance checks, supplied before implementation."""

import ast
import importlib
import pathlib
import unittest


def example():
    return {
        "version": 1,
        "title": "一个概念",
        "font": "PingFang SC",
        "nodes": [
            {"id": "a", "label": "原有工作", "x": -3, "y": 0},
            {"id": "b", "label": "学习与交接", "x": 3, "y": 0},
        ],
        "edges": [{"from": "a", "to": "b"}],
        "steps": [
            {"show": ["a"], "highlight": ["a"], "caption": "先看原来的工作", "duration": 5},
            {"show": ["b"], "highlight": ["b"], "caption": "资源转向交接", "duration": 5},
        ],
    }


class SceneContract(unittest.TestCase):
    def module(self, name):
        self.assertTrue(
            pathlib.Path("concept_render", name + ".py").is_file(),
            "concept renderer not implemented",
        )
        return importlib.import_module("concept_render." + name)

    def test_valid_data(self):
        self.assertEqual(self.module("model").validate(example()), example())

    def test_invalid_references(self):
        value = example()
        value["edges"][0]["to"] = "missing"
        with self.assertRaises(ValueError):
            self.module("model").validate(value)

    def test_finite_positions_and_durations(self):
        for change in ["nan", "bool", "infinity", "time"]:
            value = example()
            if change == "nan":
                value["nodes"][0]["x"] = float("nan")
            if change == "bool":
                value["nodes"][0]["y"] = True
            if change == "infinity":
                value["steps"][0]["duration"] = float("inf")
            if change == "time":
                value["steps"][0]["duration"] = 0
            with self.assertRaises(ValueError):
                self.module("model").validate(value)

    def test_limits_and_duplicate_ids(self):
        for change in ["duplicate", "position", "duration", "text"]:
            value = example()
            if change == "duplicate":
                value["nodes"][1]["id"] = "a"
            if change == "position":
                value["nodes"][0]["x"] = 20
            if change == "duration":
                value["steps"][0]["duration"] = 40
            if change == "text":
                value["nodes"][0]["label"] = "x" * 1000
            with self.assertRaises(ValueError):
                self.module("model").validate(value)

    def test_highlight_requires_visible_node(self):
        value = example()
        value["steps"][0]["highlight"] = ["b"]
        with self.assertRaises(ValueError):
            self.module("model").validate(value)

    def test_generated_python_has_plain_scene(self):
        source = self.module("manim_codegen").generate_scene(example())
        tree = ast.parse(source)
        compile(source, "concept-scene", "exec")
        self.assertTrue(
            any(isinstance(n, ast.ClassDef) and n.name == "ConceptScene" for n in ast.walk(tree))
        )
        self.assertNotIn("VoiceoverScene", source)
        self.assertNotIn("explainer", source)

    def test_text_is_literal_data(self):
        value = example()
        value["title"] = '";__import__("os").system("false");#'
        source = self.module("manim_codegen").generate_scene(value)
        tree = ast.parse(source)
        self.assertIn(
            value["title"],
            [
                n.value
                for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ],
        )
        self.assertFalse(
            any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id in ["eval", "exec", "__import__"]
                for n in ast.walk(tree)
            )
        )

    def test_unknown_schema_rejected(self):
        for change in ["version", "code"]:
            value = example()
            if change == "version":
                value["version"] = 2
            else:
                value["code"] = "print(1)"
            with self.assertRaises(ValueError):
                self.module("model").validate(value)


if __name__ == "__main__":
    unittest.main()
