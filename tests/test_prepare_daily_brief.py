import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts import fetch_cvdd
from scripts import fetch_etf_flow
from scripts import prepare_daily_brief


class PrepareDailyBriefTests(unittest.TestCase):
    AS_OF = date(2026, 7, 30)
    NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    GENERATED_AT = "2026-07-30T12:05:00Z"

    ETF_FUNDS = (
        "IBIT",
        "FBTC",
        "BITB",
        "ARKB",
        "BTCO",
        "EZBC",
        "BRRR",
        "HODL",
        "BTCW",
        "MSBT",
        "GBTC",
        "BTC",
    )

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

    def make_etf_output(self):
        totals = (
            ("2026-07-24", 10.0),
            ("2026-07-27", -11.6),
            ("2026-07-28", -49.7),
            ("2026-07-29", 0.0),
            ("2026-07-30", -265.4),
        )
        rows = []
        for row_date, total in totals:
            funds = {fund: 0.0 for fund in self.ETF_FUNDS}
            funds["IBIT"] = total
            rows.append(
                {
                    "date": row_date,
                    "funds": funds,
                    "total": total,
                    "missing_funds": [],
                    "missing_core_funds": [],
                    "status": "complete",
                }
            )
        parsed_page = fetch_etf_flow.ParsedPage(
            rows=rows,
            headers=["Date", *self.ETF_FUNDS, "Total"],
            fund_columns={
                fund: index + 1 for index, fund in enumerate(self.ETF_FUNDS)
            },
            duplicate_dates=[],
            table_count=1,
        )
        return fetch_etf_flow.build_result(
            parsed_page,
            self.AS_OF,
            self.NOW,
            cache_status={
                "used_cache": True,
                "status": "使用缓存",
                "source_url": fetch_etf_flow.SOURCE_URL,
            },
            fetched_at_utc="2026-07-30T12:00:00Z",
            network_request_count=0,
        )

    def write_cvdd_fixture(self, directory, payload):
        path = Path(directory) / "cvdd_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def write_etf_fixture(self, directory, payload):
        path = Path(directory) / "etf_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def build_context(
        self,
        payload=None,
        exit_code=0,
        *,
        etf_payload=None,
        etf_exit_code=0,
    ):
        return prepare_daily_brief.build_daily_brief_context(
            payload or self.make_cvdd_output(),
            etf_payload or self.make_etf_output(),
            cvdd_exit_code=exit_code,
            etf_exit_code=etf_exit_code,
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
            self.assertIn("etf_flow", loaded["sections"])
            section = loaded["sections"]["cvdd_bgeometrics"]
            self.assertGreater(section["cvdd"]["current"]["value"], 0)
            self.assertGreater(section["price_cvdd"]["current"]["value"], 0)
            self.assertEqual(
                loaded["sections"]["etf_flow"]["latest_complete_date"],
                "2026-07-30",
            )
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
        self.assertIn("ETF Flow", markdown)
        self.assertIn("最近完整交易日", markdown)
        self.assertIn("-$265.4M", markdown)

    def test_rejects_etf_complete_row_with_missing_core_fund(self):
        etf_payload = self.make_etf_output()
        latest = etf_payload["latest_complete_row"]
        latest["funds"]["IBIT"] = None
        latest["missing_funds"] = ["IBIT"]
        latest["missing_core_funds"] = ["IBIT"]

        with self.assertRaises(prepare_daily_brief.PreparationError):
            self.build_context(etf_payload=etf_payload)

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
        payload["warnings"] = ["fixture CVDD warning"]
        etf_payload = self.make_etf_output()
        etf_payload["warnings"] = ["fixture ETF warning"]

        context = self.build_context(
            payload,
            exit_code=2,
            etf_payload=etf_payload,
            etf_exit_code=2,
        )
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertCountEqual(
            context["warnings"],
            ["fixture CVDD warning", "fixture ETF warning"],
        )
        self.assertIn("fixture CVDD warning", markdown)
        self.assertIn("fixture ETF warning", markdown)

    def test_cvdd_fatal_error_stops_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            stale_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
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
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    cvdd_json_path=stale_path,
                    etf_json_path=etf_path,
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
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
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
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
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

    def test_etf_fatal_error_stops_prepare_without_writing_context(self):
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            stale_etf_path = self.write_etf_fixture(
                directory, self.make_etf_output()
            )
            output_dir = Path(directory) / "context"
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                exit_code = prepare_daily_brief.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        1, stderr="fixture ETF network failure"
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=stale_etf_path,
                    output_dir=output_dir,
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("fixture ETF network failure", stderr.getvalue())
            self.assertFalse((output_dir / "latest.json").exists())
            self.assertFalse((output_dir / "latest.md").exists())

    def test_etf_exit_two_generates_context_and_aggregates_two(self):
        etf_payload = self.make_etf_output()
        etf_payload["warnings"] = ["fixture ETF provisional warning"]
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, etf_payload)
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = prepare_daily_brief.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            loaded = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertIn("etf_flow", loaded["sections"])
            self.assertIn("fixture ETF provisional warning", loaded["warnings"])
            self.assertEqual(loaded["data_quality"]["etf_exit_code"], 2)
            self.assertTrue((output_dir / "latest.md").exists())

    def test_exit_code_aggregation_matrix(self):
        cases = (
            (0, 0, 0),
            (2, 0, 2),
            (0, 2, 2),
        )
        for cvdd_code, etf_code, expected in cases:
            with self.subTest(cvdd=cvdd_code, etf=etf_code):
                cvdd_payload = self.make_cvdd_output()
                etf_payload = self.make_etf_output()
                if cvdd_code == 2:
                    cvdd_payload["warnings"] = ["fixture CVDD quality warning"]
                if etf_code == 2:
                    etf_payload["warnings"] = ["fixture ETF quality warning"]
                with tempfile.TemporaryDirectory() as directory:
                    cvdd_path = self.write_cvdd_fixture(directory, cvdd_payload)
                    etf_path = self.write_etf_fixture(directory, etf_payload)
                    output_dir = Path(directory) / "context"
                    stdout = io.StringIO()

                    with contextlib.redirect_stdout(stdout):
                        actual = prepare_daily_brief.execute_preparation(
                            as_of="2026-07-30",
                            refresh=False,
                            copy_requested=False,
                            cvdd_invoker=lambda _as_of, _refresh, code=cvdd_code: prepare_daily_brief.CvddInvocation(
                                code
                            ),
                            etf_invoker=lambda _as_of, _refresh, code=etf_code: prepare_daily_brief.CvddInvocation(
                                code
                            ),
                            cvdd_json_path=cvdd_path,
                            etf_json_path=etf_path,
                            output_dir=output_dir,
                            generated_at_utc=self.GENERATED_AT,
                        )

                    self.assertEqual(actual, expected)
                    self.assertTrue((output_dir / "latest.json").exists())
                    self.assertTrue((output_dir / "latest.md").exists())

    def test_mismatched_etf_as_of_is_fatal(self):
        etf_payload = copy.deepcopy(self.make_etf_output())
        etf_payload["requested_as_of"] = "2026-07-29"
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, etf_payload)
            output_dir = Path(directory) / "context"
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                exit_code = prepare_daily_brief.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    output_dir=output_dir,
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("requested_as_of", stderr.getvalue())
            self.assertFalse((output_dir / "latest.json").exists())
            self.assertFalse((output_dir / "latest.md").exists())

    def test_empty_explicit_as_of_is_fatal_before_fetching(self):
        calls = []
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = prepare_daily_brief.execute_preparation(
                as_of="",
                refresh=False,
                copy_requested=False,
                cvdd_invoker=lambda as_of, refresh: calls.append(
                    ("cvdd", as_of, refresh)
                ),
                etf_invoker=lambda as_of, refresh: calls.append(
                    ("etf", as_of, refresh)
                ),
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(calls, [])
        self.assertIn("无效的 --as-of 日期", stderr.getvalue())

    def test_rejects_future_etf_rows_for_historical_as_of(self):
        etf_payload = self.make_etf_output()
        etf_payload["latest_page_date"] = "2026-07-31"
        etf_payload["latest_complete_date"] = "2026-07-31"
        etf_payload["latest_page_row"]["date"] = "2026-07-31"
        etf_payload["latest_complete_row"]["date"] = "2026-07-31"

        with self.assertRaises(prepare_daily_brief.PreparationError):
            self.build_context(etf_payload=etf_payload)

    def test_rejects_missing_provisional_details(self):
        etf_payload = self.make_etf_output()
        page_row = etf_payload["latest_page_row"]
        page_row["funds"]["IBIT"] = None
        page_row["missing_funds"] = ["IBIT"]
        page_row["missing_core_funds"] = ["IBIT"]
        page_row["status"] = "provisional"
        etf_payload["latest_row_status"] = "provisional"
        etf_payload["provisional_latest"] = None

        with self.assertRaises(prepare_daily_brief.PreparationError):
            self.build_context(etf_payload=etf_payload)

    def test_negative_and_zero_etf_flows_are_preserved(self):
        context = self.build_context()
        etf_section = context["sections"]["etf_flow"]

        self.assertEqual(etf_section["flows"]["latest"], -265.4)
        self.assertEqual(etf_section["flows"]["previous"], 0.0)
        self.assertIsNotNone(etf_section["flows"]["previous"])
        self.assertEqual(
            etf_section["previous_complete_row"]["funds"]["FBTC"], 0.0
        )

    def test_both_fetchers_receive_arguments_and_success_output_stays_concise(self):
        calls = []

        def cvdd_invoker(as_of, refresh):
            calls.append(("cvdd", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(0, stdout="noisy CVDD output")

        def etf_invoker(as_of, refresh):
            calls.append(("etf", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(0, stdout="noisy ETF output")

        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = prepare_daily_brief.execute_preparation(
                    as_of="2026-07-30",
                    refresh=True,
                    copy_requested=False,
                    cvdd_invoker=cvdd_invoker,
                    etf_invoker=etf_invoker,
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                calls,
                [
                    ("cvdd", "2026-07-30", True),
                    ("etf", "2026-07-30", True),
                ],
            )
            terminal = stdout.getvalue()
            self.assertIn("Daily Brief Context ready", terminal)
            self.assertNotIn("noisy CVDD output", terminal)
            self.assertNotIn("noisy ETF output", terminal)

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
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
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
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
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
