"""Unit tests for plugins.py."""
from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ai_brain._testing import InTempDir
from ai_brain.plugins import (
    DEFAULT_OPENCODE_PLUGINS,
    OpenCodePlugin,
    _copy_plugin,
    _register_in_opencode_config,
    install_kilo_skill_stub,
    install_opencode_plugins,
    kilo_command_dir,
    kilo_global_dir,
    opencode_global_config,
    opencode_global_dir,
    uninstall_kilo_skill,
    uninstall_opencode_plugins,
)


# --- Location helpers -----------------------------------------------------------

class TestLocationHelpers(InTempDir):
    def test_opencode_global_dir(self):
        self.assertEqual(opencode_global_dir(), Path(self.tmpdir) / ".config" / "opencode")

    def test_opencode_global_config(self):
        self.assertEqual(opencode_global_config(), Path(self.tmpdir) / ".config" / "opencode" / "opencode.json")

    def test_kilo_global_dir(self):
        self.assertEqual(kilo_global_dir(), Path(self.tmpdir) / ".config" / "kilo")

    def test_kilo_command_dir(self):
        self.assertEqual(kilo_command_dir(), Path(self.tmpdir) / ".config" / "kilo" / "command")


# --- OpenCodePlugin dataclass --------------------------------------------------

class TestOpenCodePlugin(InTempDir):
    def test_frozen_dataclass(self):
        p = OpenCodePlugin(target_name="test.js", source=Path("/tmp/test.js"))
        with self.assertRaises(AttributeError):
            p.target_name = "other.js"

    def test_default_plugins_is_empty_tuple(self):
        self.assertEqual(DEFAULT_OPENCODE_PLUGINS, ())
        self.assertIsInstance(DEFAULT_OPENCODE_PLUGINS, tuple)


# --- _copy_plugin --------------------------------------------------------------

class TestCopyPlugin(InTempDir):
    def _make_source(self, name="graphify.js", content="module.exports = {}"):
        src = Path(self.tmpdir) / "src_pkg" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(content, encoding="utf-8")
        return src

    def test_copies_source_to_plugins_dir(self):
        src = self._make_source()
        plugin = OpenCodePlugin(target_name="graphify.js", source=src)
        dest = _copy_plugin(plugin)
        self.assertIsNotNone(dest)
        self.assertEqual(dest, opencode_global_dir() / "plugins" / "graphify.js")
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(encoding="utf-8"), "module.exports = {}")

    def test_returns_none_when_source_missing(self):
        plugin = OpenCodePlugin(target_name="missing.js", source=Path("/nonexistent/missing.js"))
        buf = StringIO()
        with redirect_stdout(buf):
            result = _copy_plugin(plugin)
        self.assertIsNone(result)
        self.assertIn("跳過", buf.getvalue())

    def test_returns_dest_when_identical(self):
        src = self._make_source(content="same content")
        plugin = OpenCodePlugin(target_name="graphify.js", source=src)
        first = _copy_plugin(plugin)
        self.assertIsNotNone(first)
        second = _copy_plugin(plugin)
        self.assertEqual(second, first)

    def test_returns_none_when_user_modified(self):
        src = self._make_source(content="original")
        plugin = OpenCodePlugin(target_name="graphify.js", source=src)
        _copy_plugin(plugin)
        dest = opencode_global_dir() / "plugins" / "graphify.js"
        dest.write_text("user modified", encoding="utf-8")
        buf = StringIO()
        with redirect_stdout(buf):
            result = _copy_plugin(plugin)
        self.assertIsNone(result)
        self.assertIn("保留", buf.getvalue())


# --- _register_in_opencode_config ----------------------------------------------

class TestRegisterInOpencodeConfig(InTempDir):
    def _write_config(self, data):
        cfg = opencode_global_config()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return cfg

    def test_returns_false_when_config_missing(self):
        buf = StringIO()
        with redirect_stdout(buf):
            result = _register_in_opencode_config("myplugin.js")
        self.assertFalse(result)
        self.assertIn("找不到", buf.getvalue())

    def test_returns_false_on_corrupt_json(self):
        cfg = opencode_global_config()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("{bad json", encoding="utf-8")
        buf = StringIO()
        with redirect_stdout(buf):
            result = _register_in_opencode_config("myplugin.js")
        self.assertFalse(result)
        self.assertIn("損壞", buf.getvalue())

    def test_appends_to_plugin_array(self):
        self._write_config({"plugin": ["existing-pkg"]})
        result = _register_in_opencode_config("myplugin.js")
        self.assertTrue(result)
        data = json.loads(opencode_global_config().read_text(encoding="utf-8"))
        self.assertIn("./plugins/myplugin.js", data["plugin"])
        self.assertIn("existing-pkg", data["plugin"])

    def test_no_duplicate_when_already_registered(self):
        self._write_config({"plugin": ["./plugins/myplugin.js"]})
        result = _register_in_opencode_config("myplugin.js")
        self.assertTrue(result)
        data = json.loads(opencode_global_config().read_text(encoding="utf-8"))
        self.assertEqual(data["plugin"].count("./plugins/myplugin.js"), 1)

    def test_detects_endswith_match(self):
        self._write_config({"plugin": ["/abs/path/myplugin.js"]})
        result = _register_in_opencode_config("myplugin.js")
        self.assertTrue(result)
        data = json.loads(opencode_global_config().read_text(encoding="utf-8"))
        self.assertEqual(len(data["plugin"]), 1)

    def test_creates_plugin_key_when_absent(self):
        self._write_config({})
        result = _register_in_opencode_config("new.js")
        self.assertTrue(result)
        data = json.loads(opencode_global_config().read_text(encoding="utf-8"))
        self.assertEqual(data["plugin"], ["./plugins/new.js"])

    def test_handles_string_plugin_value(self):
        self._write_config({"plugin": "single-plugin"})
        result = _register_in_opencode_config("extra.js")
        self.assertTrue(result)
        data = json.loads(opencode_global_config().read_text(encoding="utf-8"))
        self.assertIn("single-plugin", data["plugin"])
        self.assertIn("./plugins/extra.js", data["plugin"])

    def test_handles_non_list_non_string_plugin_value(self):
        self._write_config({"plugin": 42})
        result = _register_in_opencode_config("new.js")
        self.assertTrue(result)
        data = json.loads(opencode_global_config().read_text(encoding="utf-8"))
        self.assertEqual(data["plugin"], ["./plugins/new.js"])


# --- install_opencode_plugins --------------------------------------------------

class TestInstallOpencodePlugins(InTempDir):
    def _make_source(self, name, content="code"):
        src = Path(self.tmpdir) / "src" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(content, encoding="utf-8")
        return src

    def test_returns_zero_for_empty_plugins(self):
        result = install_opencode_plugins(())
        self.assertEqual(result, 0)

    def test_installs_plugins_and_registers(self):
        src = self._make_source("a.js", "module a")
        cfg = opencode_global_config()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"plugin": []}), encoding="utf-8")
        plugins = [OpenCodePlugin(target_name="a.js", source=src)]
        buf = StringIO()
        with redirect_stdout(buf):
            count = install_opencode_plugins(plugins)
        self.assertEqual(count, 1)
        self.assertTrue((opencode_global_dir() / "plugins" / "a.js").is_file())
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertIn("./plugins/a.js", data["plugin"])

    def test_skips_when_registration_fails(self):
        src = self._make_source("b.js", "module b")
        plugins = [OpenCodePlugin(target_name="b.js", source=src)]
        count = install_opencode_plugins(plugins)
        self.assertEqual(count, 0)


# --- install_kilo_skill_stub ---------------------------------------------------

class TestInstallKiloSkillStub(InTempDir):
    def test_creates_skill_file(self):
        result = install_kilo_skill_stub()
        self.assertTrue(result)
        target = kilo_command_dir() / "ai-brain.md"
        self.assertTrue(target.is_file())
        content = target.read_text(encoding="utf-8")
        self.assertIn("description:", content)
        self.assertIn("mempalace", content)

    def test_returns_true_when_already_up_to_date(self):
        install_kilo_skill_stub()
        result = install_kilo_skill_stub()
        self.assertTrue(result)

    def test_returns_false_when_user_modified(self):
        cmd_dir = kilo_command_dir()
        cmd_dir.mkdir(parents=True, exist_ok=True)
        target = cmd_dir / "ai-brain.md"
        target.write_text("user version", encoding="utf-8")
        buf = StringIO()
        with redirect_stdout(buf):
            result = install_kilo_skill_stub()
        self.assertFalse(result)
        self.assertIn("保留", buf.getvalue())


# --- uninstall_opencode_plugins ------------------------------------------------

class TestUninstallOpencodePlugins(InTempDir):
    def _setup_installed_plugin(self, name="graphify.js", content="code"):
        plugins_dir = opencode_global_dir() / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        target = plugins_dir / name
        target.write_text(content, encoding="utf-8")
        cfg = opencode_global_config()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"plugin": ["./plugins/" + name]}), encoding="utf-8")
        return name

    def test_removes_plugin_file(self):
        name = self._setup_installed_plugin()
        plugins = [OpenCodePlugin(target_name=name, source=Path("/fake"))]
        removed = uninstall_opencode_plugins(plugins)
        self.assertEqual(removed, 1)
        self.assertFalse((opencode_global_dir() / "plugins" / name).is_file())

    def test_strips_entry_from_config(self):
        name = self._setup_installed_plugin("my.js")
        plugins = [OpenCodePlugin(target_name=name, source=Path("/fake"))]
        uninstall_opencode_plugins(plugins)
        data = json.loads(opencode_global_config().read_text(encoding="utf-8"))
        self.assertNotIn("./plugins/my.js", data["plugin"])

    def test_returns_zero_when_no_files(self):
        plugins = [OpenCodePlugin(target_name="nope.js", source=Path("/fake"))]
        removed = uninstall_opencode_plugins(plugins)
        self.assertEqual(removed, 0)

    def test_handles_missing_config(self):
        plugins_dir = opencode_global_dir() / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "x.js").write_text("code", encoding="utf-8")
        plugins = [OpenCodePlugin(target_name="x.js", source=Path("/fake"))]
        removed = uninstall_opencode_plugins(plugins)
        self.assertEqual(removed, 1)

    def test_handles_corrupt_config(self):
        plugins_dir = opencode_global_dir() / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "y.js").write_text("code", encoding="utf-8")
        cfg = opencode_global_config()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("{bad", encoding="utf-8")
        plugins = [OpenCodePlugin(target_name="y.js", source=Path("/fake"))]
        removed = uninstall_opencode_plugins(plugins)
        self.assertEqual(removed, 1)


# --- uninstall_kilo_skill ------------------------------------------------------

class TestUninstallKiloSkill(InTempDir):
    def test_returns_false_when_no_file(self):
        self.assertFalse(uninstall_kilo_skill())

    def test_removes_skill_file(self):
        install_kilo_skill_stub()
        self.assertTrue((kilo_command_dir() / "ai-brain.md").is_file())
        self.assertTrue(uninstall_kilo_skill())
        self.assertFalse((kilo_command_dir() / "ai-brain.md").is_file())


if __name__ == "__main__":
    unittest.main()
