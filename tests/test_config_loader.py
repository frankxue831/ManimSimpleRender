"""Tests for app.config.loader / app.config.models (config schema v2).

These are pure, Qt-free unit tests. Batch 1 of the rewrite.
Run: python3 -m unittest tests.test_config_loader
"""

import json
import os
import tempfile
import unittest

from app.config.loader import default_config, load_config
from app.config.models import (
    AppConfig,
    ConfigError,
    EngineOption,
    ResolutionOption,
)
from app.render.quality import known_quality_flags


def _write(tmpdir, name, text):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# A minimal but valid v2 document used by the typed-return test.
VALID_JSON = """\
{
    "schema_version": 2,
    "resolutions": [
        {"label": "Low Quality (-ql)", "flag": "-ql"},
        {"label": "4K Quality (-qk)", "flag": "-qk"}
    ],
    "engines": [
        {"label": "Default", "renderer_flag": ""},
        {"label": "OpenGL", "renderer_flag": "--renderer=opengl"}
    ]
}
"""


class LoadDefaultConfigTests(unittest.TestCase):
    """load_config(None) loads the packaged config.v2.json, cwd-independently."""

    def test_default_config_is_cwd_independent(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                cfg = load_config()
            finally:
                os.chdir(original)
        self.assertEqual(cfg.schema_version, 2)

    def test_default_config_resolution_flags_in_order(self):
        cfg = load_config()
        self.assertEqual([r.flag for r in cfg.resolutions], ["-ql", "-qm", "-qh", "-qk"])
        self.assertEqual(cfg.resolutions[3].flag, "-qk")

    def test_default_config_engine_map(self):
        cfg = load_config()
        by_label = {e.label: e.renderer_flag for e in cfg.engines}
        self.assertEqual(by_label["Default"], "")
        self.assertEqual(by_label["OpenGL (experimental)"], "--renderer=opengl")


class LoadTypedTests(unittest.TestCase):
    def test_returns_typed_appconfig(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "c.json", VALID_JSON)
            cfg = load_config(path)
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.schema_version, 2)
        self.assertIsInstance(cfg.resolutions[0], ResolutionOption)
        self.assertIsInstance(cfg.engines[0], EngineOption)
        self.assertEqual(cfg.resolutions[0].label, "Low Quality (-ql)")
        self.assertEqual(cfg.resolutions[0].flag, "-ql")


class FallbackConfigTests(unittest.TestCase):
    """BL-33: a corrupt packaged config must degrade to built-in defaults, not exit."""

    def test_default_config_mirrors_packaged_config(self):
        from app.config.loader import default_config

        cfg = default_config()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.schema_version, 2)
        self.assertEqual([r.flag for r in cfg.resolutions], ["-ql", "-qm", "-qh", "-qk"])
        self.assertEqual([e.renderer_flag for e in cfg.engines], ["", "--renderer=opengl"])

    def test_load_or_default_returns_real_config_when_load_succeeds(self):
        from app.config.loader import load_config_or_default

        cfg = load_config_or_default()  # packaged config is valid
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual([r.flag for r in cfg.resolutions], ["-ql", "-qm", "-qh", "-qk"])

    def test_load_or_default_falls_back_and_warns_on_broken_packaged_config(self):
        from app.config.loader import default_config, load_config_or_default

        def broken(_path):
            raise ConfigError("packaged config is corrupt")

        with self.assertLogs("app.config.loader", level="WARNING") as logs:
            cfg = load_config_or_default(load=broken)
        self.assertEqual(cfg, default_config())
        self.assertTrue(any("corrupt" in line for line in logs.output))

    def test_load_or_default_reraises_for_user_supplied_path(self):
        from app.config.loader import load_config_or_default

        def broken(_path):
            raise ConfigError("bad user config")

        with self.assertRaises(ConfigError):
            load_config_or_default(path="/some/user/config.json", load=broken)


class SchemaValidationTests(unittest.TestCase):
    """BL-15: reject duplicate labels/flags, loose schema_version, and bad flag values."""

    def _load(self, doc):
        with tempfile.TemporaryDirectory() as tmp:
            return load_config(_write(tmp, "c.json", doc))

    def test_float_schema_version_rejected(self):
        doc = (
            '{"schema_version": 2.0, "resolutions": [{"label": "L", "flag": "-ql"}], '
            '"engines": [{"label": "Default", "renderer_flag": ""}]}'
        )
        with self.assertRaises(ConfigError):
            self._load(doc)

    def test_bool_schema_version_rejected(self):
        doc = (
            '{"schema_version": true, "resolutions": [{"label": "L", "flag": "-ql"}], '
            '"engines": [{"label": "Default", "renderer_flag": ""}]}'
        )
        with self.assertRaises(ConfigError):
            self._load(doc)

    def test_duplicate_resolution_label_rejected(self):
        doc = (
            '{"schema_version": 2, "resolutions": ['
            '{"label": "Same", "flag": "-ql"}, {"label": "Same", "flag": "-qh"}], '
            '"engines": [{"label": "Default", "renderer_flag": ""}]}'
        )
        with self.assertRaises(ConfigError):
            self._load(doc)

    def test_duplicate_engine_label_rejected(self):
        doc = (
            '{"schema_version": 2, "resolutions": [{"label": "L", "flag": "-ql"}], '
            '"engines": [{"label": "Dup", "renderer_flag": ""}, '
            '{"label": "Dup", "renderer_flag": "--renderer=opengl"}]}'
        )
        with self.assertRaises(ConfigError):
            self._load(doc)

    def test_duplicate_engine_renderer_flag_rejected(self):
        doc = (
            '{"schema_version": 2, "resolutions": [{"label": "L", "flag": "-ql"}], '
            '"engines": [{"label": "A", "renderer_flag": ""}, '
            '{"label": "B", "renderer_flag": ""}]}'
        )
        with self.assertRaises(ConfigError):
            self._load(doc)

    def test_unrecognized_resolution_flag_rejected(self):
        for bad in ("-ql; rm -rf ~", "highquality"):
            doc = (
                '{"schema_version": 2, "resolutions": [{"label": "L", "flag": "%s"}], '
                '"engines": [{"label": "Default", "renderer_flag": ""}]}' % bad
            )
            with self.assertRaises(ConfigError):
                self._load(doc)

    def test_unrecognized_engine_flag_rejected(self):
        doc = (
            '{"schema_version": 2, "resolutions": [{"label": "L", "flag": "-ql"}], '
            '"engines": [{"label": "Bad", "renderer_flag": "--renderer=metal"}]}'
        )
        with self.assertRaises(ConfigError):
            self._load(doc)

    def test_valid_config_with_all_known_flags_loads(self):
        cfg = self._load(VALID_JSON)
        self.assertEqual([r.flag for r in cfg.resolutions], ["-ql", "-qk"])

    def test_packaged_config_passes_validation(self):
        cfg = load_config()  # the shipped config.v2.json must satisfy the new rules
        self.assertTrue(cfg.resolutions and cfg.engines)


class ErrorTests(unittest.TestCase):
    def test_missing_file_raises_configerror_from_filenotfound(self):
        with self.assertRaises(ConfigError) as cm:
            load_config("/no/such/dir/missing-config.json")
        self.assertIsInstance(cm.exception.__cause__, FileNotFoundError)

    def test_malformed_json_raises_configerror_from_jsondecode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.json", "{ not valid json ]")
            with self.assertRaises(ConfigError) as cm:
                load_config(path)
        self.assertIsInstance(cm.exception.__cause__, json.JSONDecodeError)

    def test_wrong_schema_version_raises(self):
        doc = (
            '{"schema_version": 1, "resolutions": [{"label": "x", "flag": "-ql"}], '
            '"engines": [{"label": "Default", "renderer_flag": ""}]}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "v1.json", doc)
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_missing_required_key_raises(self):
        doc = (
            '{"schema_version": 2, "resolutions": [{"label": "x", "flag": "-ql"}]}'  # no "engines"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "noeng.json", doc)
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_resolution_item_missing_flag_raises(self):
        doc = (
            '{"schema_version": 2, "resolutions": [{"label": "x"}], '
            '"engines": [{"label": "Default", "renderer_flag": ""}]}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "badres.json", doc)
            with self.assertRaises(ConfigError):
                load_config(path)


class DefaultConfigLadderTests(unittest.TestCase):
    """The built-in fallback config must not drift from the quality ladder."""

    def test_default_flags_are_known_quality_flags(self):
        flags = {r.flag for r in default_config().resolutions}
        self.assertTrue(
            flags.issubset(known_quality_flags()),
            f"default_config flags {sorted(flags)} not all in ladder",
        )


if __name__ == "__main__":
    unittest.main()
