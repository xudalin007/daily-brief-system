import importlib.util
import json
import os
from pathlib import Path
import ssl
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseChecks(unittest.TestCase):
    def test_cron_example_is_local_and_has_no_exported_ids(self):
        jobs = json.loads((ROOT / "cron_jobs.example.json").read_text(encoding="utf-8"))
        self.assertEqual(len(jobs), 3)
        self.assertTrue(all(job["deliver"] == "local" for job in jobs))
        self.assertTrue(all("id" not in job for job in jobs))
        self.assertTrue(all("model" not in job and "provider" not in job for job in jobs))
        restore = load_module("restore_cron_jobs", ROOT / "restore_cron_jobs.py")
        for job in jobs:
            command = restore.build(job, None)
            self.assertEqual(command[command.index("--deliver") + 1], "local")

    def test_weixin_target_requires_environment_configuration(self):
        with patch.dict(os.environ, {"HERMES_CHAT_ID": ""}):
            notify = load_module("post_restart_notify", ROOT / "scripts/post_restart_notify.py")
            with self.assertRaisesRegex(RuntimeError, "HERMES_CHAT_ID"):
                notify.send("test", dry_run=True)

    def test_report_verification_keeps_tls_validation(self):
        verify = load_module("verify_report", ROOT / "scripts/verify_report.py")
        self.assertTrue(verify.ctx.check_hostname)
        self.assertEqual(verify.ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_cvdd_seed_is_present(self):
        seed = json.loads((ROOT / "data/cvdd_seed.json").read_text(encoding="utf-8"))
        self.assertIn("cvdd", seed)

    def test_blockbeats_html_is_parsed_without_executing_javascript(self):
        news = load_module("fetch_news", ROOT / "scripts/fetch_news.py")
        page = ('<div class="news-flash-item-top">'
                '<a class="news-flash-title">09:30 '
                '<div class="news-flash-title-text">标题</div></a>'
                '<div class="news-flash-item-content"><p>正文</p></div>'
                '<script>throw new Error("must not run")</script>')
        with patch.object(news, "fetch", return_value=page):
            self.assertEqual(news.blockbeats(), [
                {"time": "09:30", "title": "标题", "content": "正文"}
            ])


if __name__ == "__main__":
    unittest.main()
