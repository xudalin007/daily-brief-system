import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts import fetch_cvdd


class FetchCvddTests(unittest.TestCase):
    AS_OF = date(2026, 7, 30)
    NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

    def make_payloads(self, days=401, end_date=None):
        series_end = end_date or self.AS_OF
        start = series_end - timedelta(days=days - 1)
        cvdd = []
        price = []
        for offset in range(days):
            current = start + timedelta(days=offset)
            cvdd_value = 10_000.0 + offset * 5.0
            price_value = cvdd_value * (4.0 + offset / 1000.0)
            cvdd.append(
                {
                    "d": current.isoformat(),
                    "unixTs": offset,
                    "cvdd": cvdd_value,
                }
            )
            price.append(
                {
                    "d": current.isoformat(),
                    "unixTs": offset,
                    "btcPrice": price_value,
                }
            )
        return cvdd, price

    def parse_and_build(
        self, cvdd_payload, price_payload, now=None, requested_as_of=None
    ):
        cvdd = fetch_cvdd.parse_series(cvdd_payload, "cvdd", "CVDD")
        price = fetch_cvdd.parse_series(price_payload, "btcPrice", "BTC Price")
        return fetch_cvdd.build_result(
            cvdd,
            price,
            requested_as_of or self.AS_OF,
            now or self.NOW,
        )

    def test_normal_continuous_data(self):
        cvdd, price = self.make_payloads()
        result = self.parse_and_build(cvdd, price)

        self.assertEqual(result["actual_latest_common_date"], "2026-07-30")
        self.assertEqual(
            result["7d"]["price_cvdd_point"]["actual_date"], "2026-07-23"
        )
        self.assertEqual(
            result["30d"]["cvdd_point"]["actual_date"], "2026-06-30"
        )
        self.assertEqual(
            result["90d"]["cvdd_point"]["actual_date"], "2026-05-01"
        )
        self.assertEqual(
            result["365d"]["cvdd_point"]["actual_date"], "2025-07-30"
        )
        self.assertEqual(result["quality_checks"]["expected_date_count"], 40)
        self.assertEqual(result["quality_checks"]["cvdd"]["missing_dates"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 0)

    def test_one_day_delay_excludes_unformed_as_of_day_and_exits_zero(self):
        requested_as_of = date(2026, 8, 1)
        latest_common = date(2026, 7, 31)
        cvdd, price = self.make_payloads(end_date=latest_common)

        result = self.parse_and_build(
            cvdd,
            price,
            now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            requested_as_of=requested_as_of,
        )

        self.assertEqual(result["actual_latest_common_date"], "2026-07-31")
        self.assertEqual(result["data_delay_days"], 1)
        self.assertEqual(result["quality_checks"]["window_end"], "2026-07-31")
        self.assertNotIn(
            "2026-08-01", result["quality_checks"]["cvdd"]["missing_dates"]
        )
        self.assertEqual(result["quality_checks"]["common"]["missing_dates"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 0)

    def test_historical_as_of_uses_requested_date_for_zero_delay(self):
        requested_as_of = date(2026, 7, 30)
        cvdd, price = self.make_payloads(end_date=requested_as_of)

        result = self.parse_and_build(
            cvdd,
            price,
            now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            requested_as_of=requested_as_of,
        )

        self.assertEqual(result["actual_latest_common_date"], "2026-07-30")
        self.assertEqual(result["data_delay_days"], 0)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 0)

    def test_three_day_delay_exits_two(self):
        requested_as_of = date(2026, 8, 1)
        latest_common = date(2026, 7, 29)
        cvdd, price = self.make_payloads(end_date=latest_common)

        result = self.parse_and_build(
            cvdd,
            price,
            now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            requested_as_of=requested_as_of,
        )

        self.assertEqual(result["data_delay_days"], 3)
        self.assertTrue(
            any("相对请求截止日期的数据延迟为 3 天" in item for item in result["warnings"])
        )
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 2)

    def test_real_missing_day_in_latest_complete_40_days_exits_two(self):
        requested_as_of = date(2026, 8, 1)
        latest_common = date(2026, 7, 31)
        missing_date = latest_common - timedelta(days=11)
        cvdd, price = self.make_payloads(end_date=latest_common)
        cvdd = [record for record in cvdd if record["d"] != missing_date.isoformat()]
        price = [record for record in price if record["d"] != missing_date.isoformat()]

        result = self.parse_and_build(
            cvdd,
            price,
            now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            requested_as_of=requested_as_of,
        )

        self.assertEqual(result["quality_checks"]["window_end"], "2026-07-31")
        self.assertIn(
            missing_date.isoformat(),
            result["quality_checks"]["common"]["missing_dates"],
        )
        self.assertTrue(any("最近40日" in item for item in result["warnings"]))
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 2)

    def test_missing_target_falls_back_only_backward(self):
        cvdd, price = self.make_payloads()
        target = self.AS_OF - timedelta(days=7)
        cvdd = [record for record in cvdd if record["d"] != target.isoformat()]

        result = self.parse_and_build(cvdd, price)

        self.assertEqual(result["7d"]["target_date"], target.isoformat())
        self.assertEqual(
            result["7d"]["price_cvdd_point"]["actual_date"],
            (target - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(result["7d"]["price_cvdd_point"]["fallback_days"], 1)
        self.assertTrue(
            any(
                "Price/CVDD 7D 历史点发生日期回退" in item
                for item in result["warnings"]
            )
        )
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 2)

    def test_more_than_seven_missing_days_is_unavailable(self):
        cvdd, price = self.make_payloads()
        target = self.AS_OF - timedelta(days=90)
        missing = {
            (target - timedelta(days=offset)).isoformat() for offset in range(8)
        }
        price = [record for record in price if record["d"] not in missing]

        result = self.parse_and_build(cvdd, price)

        self.assertFalse(result["90d"]["price_cvdd_point"]["available"])
        self.assertIsNone(result["90d"]["price_cvdd_point"]["actual_date"])
        self.assertTrue(result["90d"]["cvdd_point"]["available"])
        self.assertEqual(result["90d"]["cvdd_point"]["actual_date"], target.isoformat())
        self.assertIsNotNone(result["cvdd_changes"]["90d"]["rate"])
        self.assertIsNone(result["price_cvdd_changes"]["90d"]["rate"])
        self.assertTrue(
            any(
                "Price/CVDD 90D 历史点 unavailable" in item
                for item in result["warnings"]
            )
        )

    def test_exactly_seven_day_fallback_is_available(self):
        cvdd, price = self.make_payloads()
        target = self.AS_OF - timedelta(days=30)
        missing = {
            (target - timedelta(days=offset)).isoformat() for offset in range(7)
        }
        cvdd = [record for record in cvdd if record["d"] not in missing]

        result = self.parse_and_build(cvdd, price)

        self.assertTrue(result["30d"]["cvdd_point"]["available"])
        self.assertEqual(result["30d"]["cvdd_point"]["fallback_days"], 7)
        self.assertEqual(
            result["30d"]["cvdd_point"]["actual_date"],
            (target - timedelta(days=7)).isoformat(),
        )

    def test_duplicate_date_detection(self):
        cvdd, price = self.make_payloads()
        duplicate = dict(cvdd[-20])
        cvdd.insert(len(cvdd) - 19, duplicate)

        parsed = fetch_cvdd.parse_series(cvdd, "cvdd", "CVDD")
        result = fetch_cvdd.build_result(
            parsed,
            fetch_cvdd.parse_series(price, "btcPrice", "BTC Price"),
            self.AS_OF,
            self.NOW,
        )

        self.assertEqual(len(parsed.duplicate_dates), 1)
        self.assertEqual(parsed.duplicate_dates[0]["date"], duplicate["d"])
        self.assertFalse(parsed.duplicate_dates[0]["conflicting_or_invalid"])
        self.assertIn(date.fromisoformat(duplicate["d"]), parsed.values)
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 2)

    def test_cvdd_nonpositive_detection(self):
        cvdd, price = self.make_payloads()
        cvdd[-10]["cvdd"] = 0
        cvdd[-11]["cvdd"] = -1

        parsed = fetch_cvdd.parse_series(cvdd, "cvdd", "CVDD")
        result = fetch_cvdd.build_result(
            parsed,
            fetch_cvdd.parse_series(price, "btcPrice", "BTC Price"),
            self.AS_OF,
            self.NOW,
        )

        self.assertEqual(len(parsed.nonpositive_records), 2)
        self.assertTrue(any("CVDD 存在非正数" in item for item in result["warnings"]))
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 2)

    def test_btc_price_nonpositive_detection(self):
        cvdd, price = self.make_payloads()
        price[-15]["btcPrice"] = 0
        price[-16]["btcPrice"] = -100

        parsed = fetch_cvdd.parse_series(price, "btcPrice", "BTC Price")
        result = fetch_cvdd.build_result(
            fetch_cvdd.parse_series(cvdd, "cvdd", "CVDD"),
            parsed,
            self.AS_OF,
            self.NOW,
        )

        self.assertEqual(len(parsed.nonpositive_records), 2)
        self.assertTrue(
            any("BTC Price 存在非正数" in item for item in result["warnings"])
        )
        self.assertEqual(fetch_cvdd.determine_exit_code(result), 2)

    def test_latest_common_date_when_series_end_dates_differ(self):
        cvdd, price = self.make_payloads()
        price.pop()

        result = self.parse_and_build(cvdd, price)

        self.assertEqual(result["actual_latest_common_date"], "2026-07-29")
        self.assertEqual(
            result["current"]["price_cvdd_point"]["actual_date"], "2026-07-29"
        )
        self.assertTrue(
            any("两个序列的最新日期不一致" in item for item in result["warnings"])
        )

    def test_change_rate_and_directions(self):
        upward = fetch_cvdd.calculate_change(120.0, 100.0)
        downward = fetch_cvdd.calculate_change(80.0, 100.0)
        unchanged = fetch_cvdd.calculate_change(100.0, 100.0)

        self.assertAlmostEqual(upward["rate"], 0.2)
        self.assertEqual(upward["direction"], "上升")
        self.assertAlmostEqual(downward["rate"], -0.2)
        self.assertEqual(downward["direction"], "下降")
        self.assertEqual(unchanged["rate"], 0.0)
        self.assertEqual(unchanged["direction"], "不变")

        cvdd, price = self.make_payloads()
        result = self.parse_and_build(cvdd, price)
        expected = (
            result["current"]["price_cvdd_point"]["price_cvdd"]
            / result["30d"]["price_cvdd_point"]["price_cvdd"]
            - 1
        )
        self.assertAlmostEqual(result["price_cvdd_changes"]["30d"]["rate"], expected)

    def test_json_and_markdown_outputs(self):
        cvdd, price = self.make_payloads()
        result = self.parse_and_build(cvdd, price)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            json_path, markdown_path, markdown = fetch_cvdd.write_outputs(
                result, output_dir
            )
            loaded = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded["requested_as_of"], "2026-07-30")
            self.assertIn("target_date", loaded["365d"])
            self.assertIn("### BGeometrics CVDD趋势", markdown)
            self.assertIn("### BTC距BGeometrics CVDD", markdown)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), markdown)
            self.assertEqual(list(output_dir.glob(".*.tmp")), [])

    def test_max_daily_change_does_not_bridge_gap(self):
        values = {
            date(2026, 7, 1): 100.0,
            date(2026, 7, 3): 500.0,
            date(2026, 7, 4): 550.0,
        }
        result = fetch_cvdd.max_cvdd_single_day_change(
            values, date(2026, 7, 1), date(2026, 7, 4)
        )

        self.assertEqual(result["from_date"], "2026-07-03")
        self.assertEqual(result["to_date"], "2026-07-04")
        self.assertAlmostEqual(result["rate"], 0.1)

    def test_cache_hit_and_refresh_request_counts(self):
        cvdd_payload, price_payload = self.make_payloads()
        responses = {
            fetch_cvdd.CVDD_ENDPOINT: cvdd_payload,
            fetch_cvdd.BTC_PRICE_ENDPOINT: price_payload,
        }
        calls = []

        def fake_fetcher(url, timeout):
            calls.append((url, timeout))
            endpoint = url.split("?", 1)[0]
            return responses[endpoint]

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            _, first_statuses, first_count = fetch_cvdd.obtain_payloads(
                self.AS_OF,
                output_dir,
                True,
                self.NOW,
                fetcher=fake_fetcher,
            )
            self.assertEqual(first_count, 2)
            self.assertEqual(len(calls), 2)
            self.assertFalse(first_statuses["cvdd"]["used_cache"])

            calls.clear()
            _, second_statuses, second_count = fetch_cvdd.obtain_payloads(
                self.AS_OF,
                output_dir,
                False,
                self.NOW + timedelta(minutes=1),
                fetcher=fake_fetcher,
            )
            self.assertEqual(second_count, 0)
            self.assertEqual(calls, [])
            self.assertTrue(second_statuses["cvdd"]["used_cache"])
            self.assertTrue(second_statuses["btc_price"]["used_cache"])

            calls.clear()
            _, expired_statuses, expired_count = fetch_cvdd.obtain_payloads(
                self.AS_OF,
                output_dir,
                False,
                self.NOW + timedelta(hours=6),
                fetcher=fake_fetcher,
            )
            self.assertEqual(expired_count, 2)
            self.assertEqual(len(calls), 2)
            self.assertFalse(expired_statuses["cvdd"]["used_cache"])

    def test_nonfinite_values_are_invalid(self):
        cvdd, _ = self.make_payloads()
        cvdd[-5]["cvdd"] = float("nan")
        cvdd[-6]["cvdd"] = 10**10_000

        parsed = fetch_cvdd.parse_series(cvdd, "cvdd", "CVDD")

        self.assertEqual(len(parsed.invalid_records), 2)
        self.assertNotIn(date.fromisoformat(cvdd[-5]["d"]), parsed.values)
        self.assertNotIn(date.fromisoformat(cvdd[-6]["d"]), parsed.values)

    def test_atomic_write_wraps_parent_directory_error(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "not-a-directory"
            blocker.write_text("block", encoding="utf-8")

            with self.assertRaises(fetch_cvdd.CvddError):
                fetch_cvdd.atomic_write_text(blocker / "latest.md", "data")

    def test_actual_btc_price_field_and_numeric_string(self):
        payload = [
            {
                "d": "2026-07-30",
                "unixTs": "1.7750016E9",
                "btcPrice": "64827.5700000000",
            }
        ]

        parsed = fetch_cvdd.parse_series(payload, "btcPrice", "BTC Price")

        self.assertEqual(parsed.values[date(2026, 7, 30)], 64827.57)
        wrong_field = fetch_cvdd.parse_series(payload, "price", "BTC Price")
        self.assertEqual(wrong_field.values, {})
        self.assertEqual(len(wrong_field.invalid_records), 1)

    def test_records_after_as_of_do_not_pollute_warnings(self):
        cvdd, price = self.make_payloads()
        future = self.AS_OF + timedelta(days=1)
        cvdd.extend(
            [
                {"d": future.isoformat(), "unixTs": 999, "cvdd": -1},
                {"d": future.isoformat(), "unixTs": 999, "cvdd": -1},
            ]
        )
        price.append(
            {"d": future.isoformat(), "unixTs": 999, "btcPrice": 60_000}
        )

        result = self.parse_and_build(cvdd, price)

        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["quality_checks"]["window_end"], "2026-07-30")

    def test_no_common_date_is_fatal(self):
        cvdd = fetch_cvdd.parse_series(
            [{"d": "2026-07-30", "cvdd": 10_000}], "cvdd", "CVDD"
        )
        price = fetch_cvdd.parse_series(
            [{"d": "2026-07-29", "btcPrice": 60_000}],
            "btcPrice",
            "BTC Price",
        )

        with self.assertRaises(fetch_cvdd.CvddError):
            fetch_cvdd.build_result(cvdd, price, self.AS_OF, self.NOW)

    def test_invalid_network_schema_is_not_cached(self):
        cvdd_payload, price_payload = self.make_payloads()
        invalid_price = [
            {"d": record["d"], "price": record["btcPrice"]}
            for record in price_payload
        ]
        responses = {
            fetch_cvdd.CVDD_ENDPOINT: cvdd_payload,
            fetch_cvdd.BTC_PRICE_ENDPOINT: invalid_price,
        }

        def fake_fetcher(url, timeout):
            return responses[url.split("?", 1)[0]]

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with self.assertRaises(fetch_cvdd.CvddError):
                fetch_cvdd.obtain_payloads(
                    self.AS_OF,
                    output_dir,
                    True,
                    self.NOW,
                    fetcher=fake_fetcher,
                )
            self.assertFalse((output_dir / "cache" / "cvdd_raw.json").exists())
            self.assertFalse((output_dir / "cache" / "btc_price_raw.json").exists())


if __name__ == "__main__":
    unittest.main()
