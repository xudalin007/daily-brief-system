import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts import fetch_cvdd
from scripts import prepare_daily_brief


class PrepareDailyBriefTests(unittest.TestCase):
    AS_OF = date(2026, 7, 30)
    NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    GENERATED_AT = "2026-07-30T12:05:00Z"

    def make_cvdd_output(self, missing_dates=None):
        missing_dates = {value.isoformat() for value in (missing_dates or set())}
        start = self.AS_OF - timedelta(days=400)
        cvdd_payload = []
        price_payload = []
        for offset in range(401):
            current = start + timedelta(days=offset)
            if current.isoformat() in missing_dates:
                continue
            cvdd_value = 10_000.0 + offset * 5.0
            price_value = cvdd_value * (4.0 + offset / 1000.0)
            cvdd_payload.append(
                {"d": current.isoformat(), "unixTs": offset, "cvdd": cvdd_value}
            )
            price_payload.append(
                {
                    "d": current.isoformat(),
                    "unixTs": offset,
                    "btcPrice": price_value,
                }
            )

        cvdd = fetch_cvdd.parse_series(cvdd_payload, "cvdd", "CVDD")
        price = fetch_cvdd.parse_series(price_payload, "btcPrice", "BTC Price")
        cache_statuses = {
            "cvdd": {
                "used_cache": True,
                "status": "使用缓存",
                "request_url": "https://api.bitcoin-data.com/v1/cvdd?fixture=1",
            },
            "btc_price": {
                "used_cache": True,
                "status": "使用缓存",
                "request_url": "https://api.bitcoin-data.com/v1/btc-price?fixture=1",
            },
        }
        return fetch_cvdd.build_result(
            cvdd,
            price,
            self.AS_OF,
            self.NOW,
            cache_statuses=cache_statuses,
            network_request_count=0,
        )

    def write_cvdd_fixture(self, directory, payload):
        path = Path(directory) / "cvdd_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def build_context(self, payload=None, exit_code=0):
        return prepare_daily_brief.build_daily_brief_context(
            payload or self.make_cvdd_output(),
            cvdd_exit_code=exit_code,
            generated_at_utc=self.GENERATED_AT,
        )

    def test_normal_cvdd_input_generates_json(self):
        context = self.build_context()

        with tempfile.TemporaryDirectory() as directory:
            json_path, markdown_path, _markdown = (
                prepare_daily_brief.write_context_outputs(context, Path(directory))
            )
            loaded = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded["generated_at_utc"], self.GENERATED_AT)
            self.assertEqual(loaded["requested_as_of"], "2026-07-30")
            self.assertIn("cvdd_bgeometrics", loaded["sections"])
            section = loaded["sections"]["cvdd_bgeometrics"]
            self.assertGreater(section["cvdd"]["current"]["value"], 0)
            self.assertGreater(section["price_cvdd"]["current"]["value"], 0)
            self.assertTrue(markdown_path.exists())

    def test_normal_markdown_generation(self):
        context = self.build_context()
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertIn("# Daily Brief Data Context", markdown)
        self.assertIn("生成时间：2026-07-30T12:05:00Z", markdown)
        self.assertIn("分析截止日期：2026-07-30", markdown)
        self.assertIn("## CVDD｜BGeometrics", markdown)
        self.assertIn("### 底部锚趋势", markdown)
        self.assertIn("### BTC距CVDD", markdown)
        self.assertIn("### 数据质量", markdown)
        self.assertIn("| 当前 |", markdown)

    def test_fallback_metadata_is_preserved(self):
        target = self.AS_OF - timedelta(days=30)
        payload = self.make_cvdd_output(missing_dates={target})
        context = self.build_context(payload)
        section = context["sections"]["cvdd_bgeometrics"]

        cvdd_point = section["cvdd"]["30d"]
        price_point = section["price_cvdd"]["30d"]
        self.assertEqual(cvdd_point["target_date"], "2026-06-30")
        self.assertEqual(cvdd_point["actual_date"], "2026-06-29")
        self.assertEqual(cvdd_point["fallback_days"], 1)
        self.assertIsNotNone(cvdd_point["value"])
        self.assertEqual(price_point["target_date"], "2026-06-30")
        self.assertEqual(price_point["actual_date"], "2026-06-29")
        self.assertEqual(price_point["fallback_days"], 1)
        markdown = prepare_daily_brief.render_context_markdown(context)
        self.assertIn("回退1日", markdown)

    def test_warnings_are_propagated(self):
        payload = self.make_cvdd_output()
        payload["warnings"] = ["fixture warning"]

        context = self.build_context(payload, exit_code=2)
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertEqual(context["warnings"], ["fixture warning"])
        self.assertIn("fixture warning", markdown)

    def test_cvdd_fatal_error_stops_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            stale_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            output_dir = Path(directory) / "context"
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                exit_code = prepare_daily_brief.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        1, stderr="fixture network failure"
                    ),
                    cvdd_json_path=stale_path,
                    output_dir=output_dir,
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("fixture network failure", stderr.getvalue())
            self.assertFalse((output_dir / "latest.json").exists())
            self.assertFalse((output_dir / "latest.md").exists())

    def test_cvdd_exit_two_still_generates_context_and_returns_two(self):
        payload = self.make_cvdd_output()
        payload["warnings"] = ["fixture quality warning"]
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, payload)
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = prepare_daily_brief.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())
            loaded = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded["warnings"], ["fixture quality warning"])
            self.assertEqual(loaded["data_quality"]["cvdd_exit_code"], 2)
            self.assertIn("Daily Brief Context ready", stdout.getvalue())

    def test_markdown_contains_glassnode_pit_prohibition(self):
        markdown = prepare_daily_brief.render_context_markdown(self.build_context())

        self.assertIn(
            "不得套用Glassnode PiT CVDD的1.30/1.15/1.05/1.00阈值。",
            markdown,
        )
        self.assertIn("Glassnode PiT CVDD属于独立指标体系。", markdown)

    def test_365d_is_monthly_reference_not_daily_requirement(self):
        context = self.build_context()
        section = context["sections"]["cvdd_bgeometrics"]
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertIn("365d", section["cvdd"])
        self.assertIn("365d", section["price_cvdd"])
        self.assertIn("月度参考数据", markdown)
        self.assertIn("不表示每日必须展示", markdown)
        self.assertIn("最终由日报生成层决定是否显示", markdown)

    def test_copy_without_pbcopy_is_nonfatal(self):
        copied, message = prepare_daily_brief.copy_markdown_to_clipboard(
            "fixture markdown", which=lambda _name: None
        )
        self.assertFalse(copied)
        self.assertIn("未复制到剪贴板", message)

        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = prepare_daily_brief.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=True,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    cvdd_json_path=cvdd_path,
                    output_dir=output_dir,
                    clipboard_copier=lambda markdown: (
                        False,
                        "未复制到剪贴板：未找到 pbcopy。",
                    ),
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "latest.md").exists())
            self.assertIn("未复制到剪贴板", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
