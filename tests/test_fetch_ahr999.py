import copy
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts import fetch_ahr999


class FetchAhr999Tests(unittest.TestCase):
    AS_OF = date(2026, 7, 31)
    NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def make_payload(cls, days=41, end_date=None):
        series_end = end_date or cls.AS_OF
        start = series_end - timedelta(days=days - 1)
        payload = []
        for offset in range(days):
            current = start + timedelta(days=offset)
            payload.append(
                {
                    "date": current.isoformat(),
                    "close": 60_000.0 + offset * 10.0,
                    "ma200": 70_000.0 + offset * 5.0,
                    "ahr999": round(0.50 + offset * 0.01, 12),
                    "quantile5y": round(0.20 + offset * 0.005, 12),
                    "windowKind": "rolling_5y",
                }
            )
        return payload

    @staticmethod
    def find_record(payload, target_date):
        target_text = target_date.isoformat()
        for record in payload:
            if record.get("date") == target_text:
                return record
        raise AssertionError(f"fixture record not found: {target_text}")

    def build(self, payload=None, requested_as_of=None, generated_at=None):
        return fetch_ahr999.build_result(
            payload or self.make_payload(),
            requested_as_of or self.AS_OF,
            generated_at=generated_at or self.NOW,
            fetched_at=generated_at or self.NOW,
            cache_status={
                "used_cache": True,
                "status": "使用缓存",
                "reason": "离线测试fixture",
                "cache_file": "/tmp/fixture.json",
            },
            network_request_count=0,
        )

    # 1. A fully continuous series resolves every required point exactly.
    def test_continuous_data(self):
        result = self.build()

        self.assertEqual(result["actual_latest_date"], "2026-07-31")
        self.assertEqual(result["data_delay_days"], 0)
        self.assertEqual(result["current"]["ahr999"], 0.9)
        self.assertEqual(result["current"]["regime_code"], "dca")
        self.assertEqual(result["current"]["regime_zh"], "定投区")
        expected_points = {
            "previous": "2026-07-30",
            "7d": "2026-07-24",
            "30d": "2026-07-01",
        }
        for key, expected_date in expected_points.items():
            with self.subTest(point=key):
                point = result["history"][key]
                self.assertTrue(point["available"])
                self.assertEqual(point["target_date"], expected_date)
                self.assertEqual(point["actual_date"], expected_date)
                self.assertEqual(point["fallback_days"], 0)
        self.assertEqual(result["quality_checks"]["missing_dates"], [])
        self.assertTrue(result["quality_checks"]["is_complete"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 0)

    # 2. One missing latest UTC day is an acceptable source-publication delay.
    def test_one_day_delay_exits_zero(self):
        payload = self.make_payload(end_date=date(2026, 7, 30))
        result = self.build(payload, requested_as_of=self.AS_OF)

        self.assertEqual(result["actual_latest_date"], "2026-07-30")
        self.assertEqual(result["data_delay_days"], 1)
        self.assertEqual(result["quality_checks"]["window_end"], "2026-07-30")
        self.assertNotIn("2026-07-31", result["quality_checks"]["missing_dates"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 0)

    # 3. A delay greater than one day remains usable but exits with warnings.
    def test_delay_over_one_day_exits_two(self):
        payload = self.make_payload(end_date=date(2026, 7, 28))
        result = self.build(payload, requested_as_of=self.AS_OF)

        self.assertEqual(result["actual_latest_date"], "2026-07-28")
        self.assertEqual(result["data_delay_days"], 3)
        self.assertTrue(any("延迟3天" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    # 4. A null AHR999 is invalid, never zero or a usable current value.
    def test_null_ahr999_is_invalid(self):
        payload = self.make_payload()
        payload[-1]["ahr999"] = None
        parsed = fetch_ahr999.parse_dataset(payload)
        result = self.build(payload)

        self.assertEqual(len(parsed.invalid_ahr999_records), 1)
        self.assertEqual(parsed.invalid_ahr999_records[0]["date"], "2026-07-31")
        self.assertNotIn(self.AS_OF, parsed.records)
        self.assertEqual(result["actual_latest_date"], "2026-07-30")
        self.assertNotEqual(result["current"]["ahr999"], 0)
        self.assertTrue(any("AHR999无效" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    # 5. A missing quantile is non-fatal but explicitly warned about.
    def test_null_quantile_is_warning_and_exit_two(self):
        payload = self.make_payload()
        payload[-1]["quantile5y"] = None
        result = self.build(payload)

        self.assertEqual(result["actual_latest_date"], "2026-07-31")
        self.assertEqual(result["current"]["ahr999"], 0.9)
        self.assertIsNone(result["current"]["quantile5y"])
        self.assertTrue(any("quantile5y" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    # 6. Quantiles outside the closed 0..1 range are invalid, not clipped.
    def test_out_of_range_quantile_is_invalid(self):
        for invalid_value in (-0.01, 1.01):
            with self.subTest(quantile5y=invalid_value):
                payload = self.make_payload()
                payload[-1]["quantile5y"] = invalid_value
                parsed = fetch_ahr999.parse_dataset(payload)
                result = self.build(payload)

                self.assertEqual(len(parsed.invalid_quantile5y_records), 1)
                self.assertIsNone(parsed.records[self.AS_OF]["quantile5y"])
                self.assertIsNone(result["current"]["quantile5y"])
                self.assertTrue(
                    any("quantile5y" in warning for warning in result["warnings"])
                )
                self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    # 7. Duplicate dates are detected even when their values agree.
    def test_duplicate_date_detection(self):
        payload = self.make_payload()
        duplicate_date = date(2026, 7, 20)
        duplicate_index = next(
            index
            for index, record in enumerate(payload)
            if record["date"] == duplicate_date.isoformat()
        )
        payload.insert(duplicate_index + 1, copy.deepcopy(payload[duplicate_index]))
        parsed = fetch_ahr999.parse_dataset(payload)
        result = self.build(payload)

        self.assertEqual(
            parsed.duplicate_dates,
            [
                {
                    "date": "2026-07-20",
                    "count": 2,
                    "conflicting_or_invalid": False,
                }
            ],
        )
        self.assertEqual(
            result["quality_checks"]["duplicate_dates"],
            [
                {
                    "date": "2026-07-20",
                    "count": 2,
                    "conflicting_or_invalid": False,
                }
            ],
        )
        self.assertTrue(any("重复日期" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    def test_conflicting_duplicate_date_is_not_silently_selected(self):
        payload = self.make_payload()
        target = date(2026, 7, 20)
        original = copy.deepcopy(self.find_record(payload, target))
        conflicting = copy.deepcopy(original)
        conflicting["ahr999"] += 0.1
        payload.append(conflicting)

        parsed = fetch_ahr999.parse_dataset(payload)
        result = self.build(payload)

        self.assertNotIn(target, parsed.records)
        self.assertTrue(parsed.duplicate_dates[0]["conflicting_or_invalid"])
        self.assertTrue(any("重复日期" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    def test_unsorted_latest_forty_days_is_warning(self):
        payload = self.make_payload()
        payload[-10], payload[-9] = payload[-9], payload[-10]

        result = self.build(payload)

        self.assertEqual(result["quality_checks"]["sort_order"], "unsorted")
        self.assertFalse(result["quality_checks"]["is_complete"])
        self.assertTrue(any("顺序混乱" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    # 8. A 7D target can fall back only to an earlier valid calendar date.
    def test_seven_day_target_falls_back_backward(self):
        payload = self.make_payload()
        target = self.AS_OF - timedelta(days=7)
        payload = [record for record in payload if record["date"] != target.isoformat()]
        actual = target - timedelta(days=1)
        actual_value = self.find_record(payload, actual)["ahr999"]
        result = self.build(payload)
        point = result["history"]["7d"]

        self.assertEqual(point["target_date"], target.isoformat())
        self.assertEqual(point["actual_date"], actual.isoformat())
        self.assertEqual(point["fallback_days"], 1)
        self.assertAlmostEqual(point["ahr999"], actual_value)
        self.assertAlmostEqual(
            result["changes"]["ahr999_7d"]["rate"],
            result["current"]["ahr999"] / actual_value - 1,
        )
        self.assertTrue(any("7D目标日期" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    # 9. More than seven missing days makes a target unavailable.
    def test_more_than_seven_missing_days_is_unavailable(self):
        payload = self.make_payload()
        target = self.AS_OF - timedelta(days=7)
        removed = {
            (target - timedelta(days=offset)).isoformat() for offset in range(8)
        }
        payload = [record for record in payload if record["date"] not in removed]
        result = self.build(payload)
        point = result["history"]["7d"]
        change = result["changes"]["ahr999_7d"]

        self.assertFalse(point["available"])
        self.assertEqual(point["target_date"], target.isoformat())
        self.assertIsNone(point["actual_date"])
        self.assertIsNone(point["fallback_days"])
        self.assertIsNone(point["value"])
        self.assertEqual(
            change,
            {"rate": None, "percent": None, "direction": "unavailable"},
        )
        self.assertTrue(any("向前7日内无有效" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    def test_exactly_seven_day_fallback_is_available(self):
        payload = self.make_payload()
        target = self.AS_OF - timedelta(days=7)
        removed = {
            (target - timedelta(days=offset)).isoformat() for offset in range(7)
        }
        payload = [record for record in payload if record["date"] not in removed]

        point = self.build(payload)["history"]["7d"]

        self.assertTrue(point["available"])
        self.assertEqual(point["actual_date"], (target - timedelta(days=7)).isoformat())
        self.assertEqual(point["fallback_days"], 7)

    # 10-13. Regime boundaries are explicit and non-overlapping.
    def test_bargain_regime(self):
        self.assertEqual(
            fetch_ahr999.classify_regime(0.449999),
            ("bargain", "历史低估区"),
        )

    def test_dca_regime(self):
        for value in (0.45, 1.199999):
            with self.subTest(value=value):
                self.assertEqual(
                    fetch_ahr999.classify_regime(value),
                    ("dca", "定投区"),
                )

    def test_caution_regime(self):
        for value in (1.20, 2.999999):
            with self.subTest(value=value):
                self.assertEqual(
                    fetch_ahr999.classify_regime(value),
                    ("caution", "谨慎区"),
                )

    def test_bubble_regime_includes_three(self):
        for value in (3.00, 4.0):
            with self.subTest(value=value):
                self.assertEqual(
                    fetch_ahr999.classify_regime(value),
                    ("bubble", "泡沫区"),
                )

    # 14. AHR uses ratio changes; quantile uses an absolute 7D difference.
    def test_change_calculations(self):
        payload = self.make_payload()
        current = self.AS_OF
        self.find_record(payload, current)["ahr999"] = 1.2
        self.find_record(payload, current)["quantile5y"] = 0.5
        self.find_record(payload, current - timedelta(days=1))["ahr999"] = 1.0
        self.find_record(payload, current - timedelta(days=7))["ahr999"] = 0.8
        self.find_record(payload, current - timedelta(days=7))["quantile5y"] = 0.3
        self.find_record(payload, current - timedelta(days=30))["ahr999"] = 0.6
        result = self.build(payload)

        expected_ahr_changes = {
            "ahr999_1d": (0.2, 20.0),
            "ahr999_7d": (0.5, 50.0),
            "ahr999_30d": (1.0, 100.0),
        }
        for key, (expected_rate, expected_percent) in expected_ahr_changes.items():
            with self.subTest(change=key):
                change = result["changes"][key]
                self.assertAlmostEqual(change["rate"], expected_rate)
                self.assertAlmostEqual(change["percent"], expected_percent)
                self.assertEqual(change["direction"], "上升")
        quantile_change = result["changes"]["quantile5y_7d"]
        self.assertAlmostEqual(quantile_change["delta"], 0.2)
        self.assertAlmostEqual(quantile_change["percentage_points"], 20.0)
        self.assertEqual(quantile_change["direction"], "上升")

    # 15. JSON output is atomic, serializable, and preserves fallback metadata/nulls.
    def test_json_output_generation(self):
        payload = self.make_payload()
        target = self.AS_OF - timedelta(days=7)
        removed = {
            (target - timedelta(days=offset)).isoformat() for offset in range(8)
        }
        payload = [record for record in payload if record["date"] not in removed]
        result = self.build(payload)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            json_path, markdown_path, markdown = fetch_ahr999.write_outputs(
                result, output_dir
            )
            loaded = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded["generated_at_utc"], "2026-07-31T12:00:00Z")
            self.assertEqual(loaded["requested_as_of"], "2026-07-31")
            self.assertIn("source", loaded)
            self.assertIn("cache_status", loaded)
            self.assertIsNone(loaded["history"]["7d"]["actual_date"])
            self.assertIsNone(loaded["history"]["7d"]["fallback_days"])
            self.assertIsNone(loaded["history"]["7d"]["value"])
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), markdown)
            self.assertEqual(list(output_dir.glob(".*.tmp")), [])

    # 16. Markdown exposes dates, source values, quality, and unavailable quantile.
    def test_markdown_output_generation(self):
        payload = self.make_payload()
        payload[-1]["quantile5y"] = None
        result = self.build(payload)
        markdown = fetch_ahr999.render_markdown(result)

        self.assertIn("## AHR999", markdown)
        self.assertIn("最新完整UTC日线：2026-07-31", markdown)
        self.assertIn("AHR999：0.900000", markdown)
        self.assertIn("5年历史分位：unavailable", markdown)
        self.assertIn("估值区间：定投区（dca）", markdown)
        self.assertIn("| 周期 | 目标日期 | 实际日期 | AHR999 | 回退天数 |", markdown)
        self.assertIn("数据延迟：0天（相对分析截止日期 2026-07-31）", markdown)
        self.assertIn("quantile5y", markdown)
        self.assertIn("AHR999是长期估值辅助指标，不单独作为买卖信号。", markdown)
        self.assertIn(fetch_ahr999.SOURCE_URL, markdown)
        self.assertIn("抓取时间（UTC）：2026-07-31T12:00:00Z", markdown)
        self.assertIn("5年历史分位7D变化", markdown)

    def test_missing_seven_day_quantile_warns_and_markdown_is_explicit(self):
        payload = self.make_payload()
        self.find_record(payload, self.AS_OF - timedelta(days=7))["quantile5y"] = None

        result = self.build(payload)
        markdown = fetch_ahr999.render_markdown(result)

        self.assertIsNone(result["changes"]["quantile5y_7d"]["delta"])
        self.assertTrue(any("无法计算7D分位变化" in item for item in result["warnings"]))
        self.assertIn("5年历史分位7D变化：unavailable", markdown)
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    # 17. Historical --as-of freshness and warnings ignore later source rows.
    def test_historical_as_of_has_no_false_freshness_warning(self):
        payload = self.make_payload()
        payload[-1]["ahr999"] = None
        payload[-1]["quantile5y"] = 1.5
        payload.append(copy.deepcopy(payload[-1]))
        historical_as_of = date(2026, 7, 30)
        result = self.build(
            payload,
            requested_as_of=historical_as_of,
            generated_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(result["requested_as_of"], "2026-07-30")
        self.assertEqual(result["actual_latest_date"], "2026-07-30")
        self.assertEqual(result["data_delay_days"], 0)
        self.assertEqual(result["quality_checks"]["window_end"], "2026-07-30")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 0)

    def test_actual_aix4u_record_shape(self):
        payload = [
            {
                "date": "2026-07-31",
                "close": 62887.88,
                "ma200": 71497.44350000004,
                "ahr999": 0.32932831598366435,
                "quantile5y": 0.08547945205479453,
                "windowKind": "rolling_5y",
            }
        ]
        parsed = fetch_ahr999.parse_dataset(payload)
        record = parsed.records[self.AS_OF]

        self.assertEqual(record["close"], 62887.88)
        self.assertEqual(record["ma200"], 71497.44350000004)
        self.assertEqual(record["ahr999"], 0.32932831598366435)
        self.assertEqual(record["quantile5y"], 0.08547945205479453)
        self.assertEqual(record["windowKind"], "rolling_5y")

    def test_invalid_top_level_and_no_valid_ahr_are_fatal(self):
        with self.assertRaises(fetch_ahr999.Ahr999Error):
            fetch_ahr999.parse_dataset({"data": []})
        with self.assertRaises(fetch_ahr999.Ahr999Error):
            fetch_ahr999.parse_dataset(
                [
                    {
                        "date": "2026-07-31",
                        "close": 62887.88,
                        "ma200": 71497.44,
                        "ahr999": None,
                        "quantile5y": None,
                        "windowKind": "rolling_5y",
                    }
                ]
            )

    def test_current_close_and_ma200_must_be_positive(self):
        for field, invalid_value in (("close", 0), ("ma200", -1), ("close", None)):
            with self.subTest(field=field, value=invalid_value):
                payload = self.make_payload()
                payload[-1][field] = invalid_value
                with self.assertRaises(fetch_ahr999.Ahr999Error):
                    self.build(payload)

    def test_historical_nonpositive_close_is_quality_warning_not_fatal(self):
        payload = self.make_payload()
        historical_date = self.AS_OF - timedelta(days=7)
        self.find_record(payload, historical_date)["close"] = -1

        result = self.build(payload)

        self.assertEqual(result["history"]["7d"]["close"], -1.0)
        self.assertTrue(any("close非正数" in item for item in result["warnings"]))
        self.assertEqual(fetch_ahr999.determine_exit_code(result), 2)

    def test_explicit_empty_as_of_is_rejected_before_network(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = fetch_ahr999.main(["--as-of", ""])

        self.assertEqual(exit_code, 1)
        self.assertIn("日期", stderr.getvalue())

    def test_execute_cache_hit_expiry_and_refresh_without_network(self):
        payload = self.make_payload()
        calls = []

        def fake_fetcher(url):
            calls.append(url)
            return copy.deepcopy(payload)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)

            first, _markdown, first_code = fetch_ahr999.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output_dir,
                now=self.NOW,
                fetcher=fake_fetcher,
            )
            self.assertEqual(first_code, 0)
            self.assertEqual(calls, [fetch_ahr999.SOURCE_URL])
            self.assertFalse(first["cache_status"]["used_cache"])
            self.assertEqual(first["network_request_count"], 1)

            calls.clear()
            second, _markdown, second_code = fetch_ahr999.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output_dir,
                now=self.NOW + timedelta(minutes=1),
                fetcher=fake_fetcher,
            )
            self.assertEqual(second_code, 0)
            self.assertEqual(calls, [])
            self.assertTrue(second["cache_status"]["used_cache"])
            self.assertEqual(second["network_request_count"], 0)
            self.assertEqual(second["fetched_at_utc"], "2026-07-31T12:00:00Z")

            calls.clear()
            expired, _markdown, expired_code = fetch_ahr999.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output_dir,
                now=self.NOW + timedelta(hours=6),
                fetcher=fake_fetcher,
            )
            self.assertEqual(expired_code, 0)
            self.assertEqual(calls, [fetch_ahr999.SOURCE_URL])
            self.assertFalse(expired["cache_status"]["used_cache"])
            self.assertEqual(expired["network_request_count"], 1)

            calls.clear()
            refreshed, _markdown, refreshed_code = fetch_ahr999.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output_dir,
                now=self.NOW + timedelta(hours=6, minutes=1),
                fetcher=fake_fetcher,
            )
            self.assertEqual(refreshed_code, 0)
            self.assertEqual(calls, [fetch_ahr999.SOURCE_URL])
            self.assertFalse(refreshed["cache_status"]["used_cache"])
            self.assertEqual(refreshed["network_request_count"], 1)

            cache_path = output_dir / "cache" / "ahr999_raw.json"
            envelope = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["source_url"], fetch_ahr999.SOURCE_URL)
            self.assertEqual(envelope["request_url"], fetch_ahr999.SOURCE_URL)
            self.assertEqual(
                envelope["fetched_at_utc"],
                "2026-07-31T18:01:00Z",
            )
            self.assertEqual(envelope["raw_data"], payload)


if __name__ == "__main__":
    unittest.main()
