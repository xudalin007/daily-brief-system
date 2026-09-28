import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief_paths = load_module("brief_paths", ROOT / "scripts" / "brief_paths.py")
litb_chart = load_module("litb_chart", ROOT / "scripts" / "litb_chart.py")


class ReportPaths(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        env = {"HERMES_HOME": self.home.name}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("DAILY_BRIEF_REPORT_DIR", None)
        self.legacy = Path(self.home.name) / "legacy-desktop"
        legacy = patch.object(brief_paths, "LEGACY_WSL_REPORT_DIR", str(self.legacy))
        legacy.start()
        self.addCleanup(legacy.stop)

    def test_default_is_under_hermes_home(self):
        paths = brief_paths.report_paths("2026-09-28")
        root = Path(self.home.name).resolve() / "reports"
        self.assertEqual(paths["directory"], str(root))
        self.assertEqual(paths["html"], str(root / "每日简报_2026-09-28.html"))
        self.assertEqual(paths["markdown"], str(root / "每日简报_2026-09-28.md"))

    def test_config_file_overrides_default(self):
        target = Path(self.home.name) / "desk"
        (Path(self.home.name) / "daily_brief.json").write_text(
            json.dumps({"report_dir": str(target)}), encoding="utf-8")
        self.assertEqual(brief_paths.report_dir(), target.resolve())

    def test_env_overrides_config_file(self):
        (Path(self.home.name) / "daily_brief.json").write_text(
            json.dumps({"report_dir": "/ignored"}), encoding="utf-8")
        target = Path(self.home.name) / "from-env"
        with patch.dict(os.environ, {"DAILY_BRIEF_REPORT_DIR": str(target)}):
            self.assertEqual(brief_paths.report_dir(), target.resolve())

    def test_existing_wsl_desktop_folder_is_kept(self):
        self.legacy.mkdir()
        self.assertEqual(brief_paths.report_dir(), self.legacy.resolve())

    def test_config_file_overrides_legacy_folder(self):
        self.legacy.mkdir()
        target = Path(self.home.name) / "desk"
        (Path(self.home.name) / "daily_brief.json").write_text(
            json.dumps({"report_dir": str(target)}), encoding="utf-8")
        self.assertEqual(brief_paths.report_dir(), target.resolve())

    def test_rejects_placeholders_and_traversal(self):
        for bad in ("YYYY-MM-DD", "../2026-09-28", "2026-13-01"):
            with self.assertRaises(ValueError):
                brief_paths.report_paths(bad)

    def test_example_config_is_portable(self):
        example = json.loads((ROOT / "daily_brief.example.json").read_text(encoding="utf-8"))
        self.assertTrue(example["report_dir"].startswith("~"))


class NoHardcodedReportPaths(unittest.TestCase):
    def test_skill_and_cron_do_not_pin_wsl_desktop(self):
        prompt = json.loads((ROOT / "cron_jobs.example.json").read_text(encoding="utf-8"))[0]["prompt"]
        skill = (ROOT / "skills" / "productivity" / "daily-briefing" / "SKILL.md").read_text(encoding="utf-8")
        for text in (prompt, skill):
            self.assertNotIn("/mnt/d/Desktop/每日简报/", text)
            self.assertIn("brief_paths.py", text)


class BrowserModeSelection(unittest.TestCase):
    def test_invalid_mode_is_rejected(self):
        with patch.dict(os.environ, {"LITB_BROWSER_MODE": "edge"}):
            with self.assertRaises(ValueError):
                litb_chart.fetch_chart("cvdd")

    def test_wsl_mode_skips_native_lookup(self):
        with patch.dict(os.environ, {"LITB_BROWSER_MODE": "wsl"}), \
                patch.object(litb_chart, "_fetch_chart_wsl", return_value={"via": "wsl"}) as wsl, \
                patch.object(litb_chart, "find_native_browser") as find:
            self.assertEqual(litb_chart.fetch_chart("cvdd"), {"via": "wsl"})
            find.assert_not_called()
            wsl.assert_called_once()

    def test_auto_on_wsl_keeps_edge_bridge_even_with_linux_browser(self):
        with patch.dict(os.environ, {"LITB_BROWSER_MODE": "auto"}), \
                patch.object(litb_chart, "is_wsl", return_value=True), \
                patch.object(litb_chart, "find_native_browser", return_value="/usr/bin/chromium") as find, \
                patch.object(litb_chart, "_fetch_chart_wsl", return_value={"via": "wsl"}):
            self.assertEqual(litb_chart.fetch_chart("cvdd"), {"via": "wsl"})
            find.assert_not_called()

    def test_native_mode_on_wsl_is_an_explicit_opt_in(self):
        with patch.dict(os.environ, {"LITB_BROWSER_MODE": "native"}), \
                patch.object(litb_chart, "is_wsl", return_value=True), \
                patch.object(litb_chart, "find_native_browser", return_value="/usr/bin/chromium"), \
                patch.object(litb_chart, "_fetch_chart_native", return_value={"via": "native"}):
            self.assertEqual(litb_chart.fetch_chart("cvdd"), {"via": "native"})

    def test_wsl_detection(self):
        with patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}):
            self.assertTrue(litb_chart.is_wsl())

    def test_auto_prefers_native_browser(self):
        with patch.dict(os.environ, {"LITB_BROWSER_MODE": "auto"}), \
                patch.object(litb_chart, "is_wsl", return_value=False), \
                patch.object(litb_chart, "find_native_browser", return_value="/bin/chrome"), \
                patch.object(litb_chart, "_fetch_chart_native", return_value={"via": "native"}) as native:
            self.assertEqual(litb_chart.fetch_chart("cvdd", days=7), {"via": "native"})
            native.assert_called_once_with("/bin/chrome", "cvdd", 7)

    def test_auto_falls_back_to_wsl_bridge_on_linux(self):
        with patch.dict(os.environ, {"LITB_BROWSER_MODE": "auto"}), \
                patch.object(litb_chart, "is_wsl", return_value=False), \
                patch.object(litb_chart, "find_native_browser", return_value=None), \
                patch.object(litb_chart.platform, "system", return_value="Linux"), \
                patch.object(litb_chart.shutil, "which", return_value="/mnt/c/powershell.exe"), \
                patch.object(litb_chart, "_fetch_chart_wsl", return_value={"via": "wsl"}):
            self.assertEqual(litb_chart.fetch_chart("cvdd"), {"via": "wsl"})

    def test_native_mode_without_browser_fails_clearly(self):
        with patch.dict(os.environ, {"LITB_BROWSER_MODE": "native"}), \
                patch.object(litb_chart, "find_native_browser", return_value=None), \
                patch.object(litb_chart, "_fetch_chart_wsl") as wsl:
            with self.assertRaisesRegex(RuntimeError, "LITB_BROWSER_PATH"):
                litb_chart.fetch_chart("cvdd")
            wsl.assert_not_called()

    def test_browser_path_override_must_exist(self):
        with patch.dict(os.environ, {"LITB_BROWSER_PATH": "/no/such/browser"}):
            with self.assertRaises(RuntimeError):
                litb_chart.find_native_browser()


if __name__ == "__main__":
    unittest.main()
