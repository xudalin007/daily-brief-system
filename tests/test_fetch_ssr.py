import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import fetch_ssr


class FetchSsrTests(unittest.TestCase):
    END = date(2026, 8, 1)
    AS_OF = date(2026, 8, 2)
    NOW = datetime(2026, 8, 2, 8, 30, tzinfo=timezone.utc)

    @classmethod
    def make_payloads(cls, days=91, oscillator_end=None, raw_end=None):
        oscillator_end = oscillator_end or cls.END
        raw_end = raw_end or oscillator_end

        def rows(end, field, base, step):
            start = end - timedelta(days=days - 1)
            return [
                {
                    "d": (start + timedelta(days=offset)).isoformat(),
                    "unixTs": 1_700_000_000 + offset * 86_400,
                    field: round(base + offset * step, 8),
                }
                for offset in range(days)
            ]

        return rows(
            oscillator_end, "ssrStablecoinOscillator", 0.1, 1 / 300
        ), rows(raw_end, "ssrStablecoin", 4.0, 0.02)

    @staticmethod
    def remove_dates(payload, *dates):
        texts = {item.isoformat() for item in dates}
        payload[:] = [row for row in payload if row.get("d") not in texts]

    def build(self, oscillator=None, raw=None, as_of=None, **kwargs):
        default_oscillator, default_raw = self.make_payloads()
        return fetch_ssr.build_result(
            default_oscillator if oscillator is None else oscillator,
            default_raw if raw is None else raw,
            as_of or self.AS_OF,
            generated_at=self.NOW,
            **kwargs,
        )

    # 1. The exact official BGeometrics ratio field is parsed as a positive value.
    def test_official_oscillator_parse(self):
        oscillator, _ = self.make_payloads()
        parsed = fetch_ssr.parse_series(
            oscillator,
            "ssrStablecoinOscillator",
            "SSR Oscillator",
            positive_only=True,
        )
        self.assertAlmostEqual(parsed.values[self.END], 0.4)

    # 2. Raw SSR is independently parsed as a positive auxiliary series.
    def test_raw_ssr_parse(self):
        _, raw = self.make_payloads()
        parsed = fetch_ssr.parse_series(
            raw, "ssrStablecoin", "Raw SSR", positive_only=True
        )
        self.assertAlmostEqual(parsed.values[self.END], 5.8)

    # 3. Raw SSR cannot masquerade as the formal oscillator.
    def test_raw_ssr_cannot_masquerade_as_oscillator(self):
        _, raw = self.make_payloads()
        with self.assertRaises(fetch_ssr.SsrError):
            fetch_ssr.build_result(raw, raw, self.AS_OF, generated_at=self.NOW)

    # 4. Hand-built proxy fields are not accepted as the formal field.
    def test_proxy_field_is_rejected(self):
        oscillator, raw = self.make_payloads()
        proxy = [
            {"d": row["d"], "ssrSma200BollingerProxy": row["ssrStablecoinOscillator"]}
            for row in oscillator
        ]
        with self.assertRaises(fetch_ssr.SsrError):
            fetch_ssr.build_result(proxy, raw, self.AS_OF, generated_at=self.NOW)

    # 5-8. Current and all requested points retain target/actual/fallback metadata.
    def test_current_and_history_points(self):
        result = self.build(definition_compatible=True)
        self.assertAlmostEqual(result["ssr_oscillator"]["current"], 0.4)
        self.assertEqual(result["ssr_oscillator"]["date"], "2026-08-01")
        for key, expected in {
            "previous": "2026-07-31",
            "d7": "2026-07-25",
            "d30": "2026-07-02",
        }.items():
            with self.subTest(key=key):
                point = result["ssr_oscillator"][key]
                self.assertEqual(point["target_date"], expected)
                self.assertEqual(point["actual_date"], expected)
                self.assertEqual(point["fallback_days"], 0)

    # 9. Negative values mean stronger relative stablecoin purchasing power.
    def test_negative_direction(self):
        classification = fetch_ssr.classify_oscillator(
            -0.25, compatible_with_daily_brief_rules=True
        )
        self.assertEqual(
            classification["direction"], "stronger_stablecoin_purchasing_power"
        )

    # 10. Positive values mean weaker relative stablecoin purchasing power.
    def test_positive_direction(self):
        classification = fetch_ssr.classify_oscillator(
            0.25, compatible_with_daily_brief_rules=True
        )
        self.assertEqual(
            classification["direction"], "weaker_stablecoin_purchasing_power"
        )

    # 11. Without an invented tolerance, exact zero is the balanced point.
    def test_zero_is_balanced(self):
        classification = fetch_ssr.classify_oscillator(
            0.0, compatible_with_daily_brief_rules=True
        )
        self.assertEqual(classification["direction"], "balanced")

    # 12-13. The formal ±2 statistical boundaries are inclusive.
    def test_negative_two_extreme_boundary(self):
        value = fetch_ssr.classify_oscillator(
            -2.0, compatible_with_daily_brief_rules=True
        )
        self.assertEqual(value["extreme_status"], "statistical_extreme_negative")

    def test_positive_two_extreme_boundary(self):
        value = fetch_ssr.classify_oscillator(
            2.0, compatible_with_daily_brief_rules=True
        )
        self.assertEqual(value["extreme_status"], "statistical_extreme_positive")

    # 14. Statistical extremes are explicitly not trading signals.
    def test_extreme_is_not_trading_signal(self):
        for number in (-3.0, 3.0):
            with self.subTest(number=number):
                value = fetch_ssr.classify_oscillator(
                    number, compatible_with_daily_brief_rules=True
                )
                self.assertFalse(value["is_trading_signal"])

    # 15. T+1 is fresh for a daily on-chain series when definition is compatible.
    def test_t_plus_one_exit_zero_when_compatible(self):
        result = self.build(definition_compatible=True)
        self.assertEqual(result["data_delay_days"], 1)
        self.assertEqual(fetch_ssr.determine_exit_code(result), 0)

    # 16. More than T+1 is delayed and nonfatal.
    def test_delay_over_one_exit_two(self):
        result = self.build(as_of=date(2026, 8, 4), definition_compatible=True)
        self.assertEqual(result["data_delay_days"], 3)
        self.assertEqual(result["ssr_oscillator"]["status"], "Delayed")
        self.assertEqual(fetch_ssr.determine_exit_code(result), 2)

    # 17. Missing target dates search backward only.
    def test_history_fallback(self):
        oscillator, raw = self.make_payloads()
        target = self.END - timedelta(days=7)
        self.remove_dates(oscillator, target)
        result = self.build(
            oscillator=oscillator, raw=raw, definition_compatible=True
        )
        point = result["ssr_oscillator"]["d7"]
        self.assertEqual(point["actual_date"], (target - timedelta(days=1)).isoformat())
        self.assertEqual(point["fallback_days"], 1)
        self.assertEqual(fetch_ssr.determine_exit_code(result), 2)

    # 18. More than seven unavailable days never falls back farther.
    def test_fallback_never_exceeds_seven_days(self):
        oscillator, raw = self.make_payloads()
        target = self.END - timedelta(days=7)
        self.remove_dates(
            oscillator, *(target - timedelta(days=offset) for offset in range(8))
        )
        result = self.build(
            oscillator=oscillator, raw=raw, definition_compatible=True
        )
        point = result["ssr_oscillator"]["d7"]
        self.assertFalse(point["available"])
        self.assertIsNone(point["value"])
        self.assertIsNone(point["fallback_days"])

    # 19. Missing history remains null and is never converted to zero.
    def test_missing_never_becomes_zero(self):
        oscillator, raw = self.make_payloads()
        target = self.END - timedelta(days=30)
        self.remove_dates(
            oscillator, *(target - timedelta(days=offset) for offset in range(8))
        )
        result = self.build(
            oscillator=oscillator, raw=raw, definition_compatible=True
        )
        self.assertIsNone(result["ssr_oscillator"]["d30"]["value"])

    # 20. Duplicate dates are surfaced.
    def test_duplicate_detection(self):
        oscillator, raw = self.make_payloads()
        oscillator.append(copy.deepcopy(oscillator[-2]))
        result = self.build(
            oscillator=oscillator, raw=raw, definition_compatible=True
        )
        duplicate = result["quality_checks"]["ssr_oscillator"]["duplicate_dates"]
        self.assertEqual(duplicate[0]["count"], 2)
        self.assertEqual(fetch_ssr.determine_exit_code(result), 2)

    def test_conflicting_duplicate_on_latest_date_is_not_hidden(self):
        oscillator, raw = self.make_payloads()
        conflicting = copy.deepcopy(oscillator[-1])
        conflicting["ssrStablecoinOscillator"] += 0.25
        oscillator.append(conflicting)

        result = self.build(
            oscillator=oscillator, raw=raw, definition_compatible=True
        )

        self.assertEqual(result["actual_latest_date"], "2026-07-31")
        self.assertTrue(
            any("最新有效日线之后存在重复日期" in item for item in result["warnings"])
        )
        self.assertEqual(fetch_ssr.determine_exit_code(result), 2)

    # 21. BGeometrics SSR/SMA365 must be positive and finite.
    def test_invalid_values(self):
        oscillator, raw = self.make_payloads()
        oscillator[-1]["ssrStablecoinOscillator"] = None
        oscillator[-2]["ssrStablecoinOscillator"] = "not-a-number"
        oscillator[-3]["ssrStablecoinOscillator"] = 0.0
        oscillator[-4]["ssrStablecoinOscillator"] = -0.2
        result = self.build(
            oscillator=oscillator, raw=raw, definition_compatible=True
        )
        parsed = fetch_ssr.parse_series(
            oscillator,
            "ssrStablecoinOscillator",
            "SSR Oscillator",
            positive_only=True,
        )
        self.assertEqual(len(parsed.invalid_records), 4)
        self.assertNotIn(self.END - timedelta(days=2), parsed.values)
        self.assertNotIn(self.END - timedelta(days=3), parsed.values)
        self.assertTrue(any("最新有效日线之后存在无效记录" in item for item in result["warnings"]))

    # 22. The official BGeometrics definition is preserved as confirmed metadata.
    def test_definition_confirmed(self):
        result = self.build()
        self.assertTrue(result["definition"]["confirmed"])
        self.assertEqual(result["definition"]["formula"], "SSR / SMA365")
        self.assertEqual(result["definition"]["center"], 1.0)

    # 23. An unconfirmed definition warns and disables interpretation.
    def test_definition_unconfirmed_warning(self):
        result = self.build(
            definition_confirmed=False, definition_compatible=True
        )
        self.assertEqual(result["ssr_oscillator"]["status"], "Provisional")
        self.assertEqual(result["ssr_oscillator"]["direction"], "unavailable")
        self.assertFalse(
            result["definition"]["compatible_with_daily_brief_rules"]
        )
        self.assertTrue(any("定义尚未可靠确认" in item for item in result["warnings"]))
        self.assertEqual(fetch_ssr.determine_exit_code(result), 2)

    # Production definition is confirmed but incompatible, so no 0/±2 semantics apply.
    def test_production_definition_is_provisional_and_unclassified(self):
        result = self.build()
        metric = result["ssr_oscillator"]
        self.assertEqual(metric["status"], "Provisional")
        self.assertEqual(metric["direction"], "unavailable")
        self.assertEqual(metric["extreme_status"], "not_applicable")
        self.assertFalse(metric["classification_applied"])
        self.assertEqual(fetch_ssr.determine_exit_code(result), 2)

    # 24. A fresh cache avoids every network request.
    def test_cache_hit(self):
        oscillator, raw = self.make_payloads()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return raw if "/ssr?" in url else oscillator

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            first, _markdown, _code = fetch_ssr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW,
                fetcher=fake,
                definition_compatible=True,
            )
            second, _markdown, code = fetch_ssr.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output,
                now=self.NOW + timedelta(minutes=1),
                fetcher=fake,
                definition_compatible=True,
            )
        self.assertEqual(first["network_request_count"], 2)
        self.assertEqual(second["network_request_count"], 0)
        self.assertTrue(second["cache_status"]["used_cache"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(code, 0)

    # 25. Refresh bypasses a still-valid cache.
    def test_refresh_bypasses_cache(self):
        oscillator, raw = self.make_payloads()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return raw if "/ssr?" in url else oscillator

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for offset in range(2):
                fetch_ssr.execute(
                    requested_as_of=self.AS_OF,
                    refresh=True,
                    output_dir=output,
                    now=self.NOW + timedelta(minutes=offset),
                    fetcher=fake,
                    definition_compatible=True,
                )
        self.assertEqual(len(calls), 4)

    # 26-27. JSON and Markdown outputs are generated atomically and are usable.
    def test_json_and_markdown_output(self):
        result = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            json_path, markdown_path, markdown = fetch_ssr.write_outputs(
                result, Path(tmp)
            )
            stored = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["ssr_oscillator"]["current"], 0.4)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), markdown)
            self.assertIn("## BTC SSR Oscillator", markdown)
            self.assertIn("Raw SSR仅为辅助数据", markdown)
            self.assertIn("未套用0/±2", markdown)
            self.assertIn("当前BGeometrics口径不适用±2", markdown)

    # 28. Endpoint, field, HTTP status and fetched time all remain visible.
    def test_source_metadata(self):
        statuses = {
            key: {
                "used_cache": False,
                "status": "网络请求",
                "request_url": endpoint + "?x=1",
                "http_status": 200,
                "fetched_at_utc": "2026-08-02T08:30:00Z",
            }
            for key, endpoint in {
                "ssr_oscillator": fetch_ssr.SSR_OSCILLATOR_ENDPOINT,
                "raw_ssr": fetch_ssr.RAW_SSR_ENDPOINT,
            }.items()
        }
        oscillator, raw = self.make_payloads()
        result = fetch_ssr.build_result(
            oscillator,
            raw,
            self.AS_OF,
            generated_at=self.NOW,
            cache_statuses=statuses,
        )
        endpoint = result["source"]["endpoints"]["ssr_oscillator"]
        self.assertEqual(endpoint["endpoint"], fetch_ssr.SSR_OSCILLATOR_ENDPOINT)
        self.assertEqual(endpoint["value_field"], "ssrStablecoinOscillator")
        self.assertEqual(endpoint["http_status"], 200)
        self.assertEqual(endpoint["fetched_at_utc"], "2026-08-02T08:30:00Z")

    # 29. A core API failure is fatal even if raw SSR would be available.
    def test_fatal_core_api_failure(self):
        _, raw = self.make_payloads()

        def fake(url, timeout):
            if "ssr-oscillator" in url:
                raise fetch_ssr.SsrError("offline")
            return raw

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(fetch_ssr.SsrError):
                fetch_ssr.execute(
                    requested_as_of=self.AS_OF,
                    refresh=True,
                    output_dir=Path(tmp),
                    now=self.NOW,
                    fetcher=fake,
                )

    # Only raw data is not a successful SSR module.
    def test_only_raw_available_is_fatal(self):
        _, raw = self.make_payloads()
        with self.assertRaises(fetch_ssr.SsrError):
            fetch_ssr.build_result(raw, raw, self.AS_OF, generated_at=self.NOW)

    # Raw failure is nonfatal but exits two.
    def test_raw_unavailable_is_nonfatal_exit_two(self):
        oscillator, _ = self.make_payloads()
        result = fetch_ssr.build_result(
            oscillator,
            None,
            self.AS_OF,
            generated_at=self.NOW,
            definition_compatible=True,
        )
        self.assertEqual(result["raw_ssr"]["status"], "Unavailable")
        self.assertIsNone(result["raw_ssr"]["current"])
        self.assertEqual(fetch_ssr.determine_exit_code(result), 2)

    # Raw values must be positive; zero must not become a valid auxiliary point.
    def test_raw_ssr_must_be_positive(self):
        _, raw = self.make_payloads()
        raw[-1]["ssrStablecoin"] = 0
        parsed = fetch_ssr.parse_series(
            raw, "ssrStablecoin", "Raw SSR", positive_only=True
        )
        self.assertNotIn(self.END, parsed.values)
        self.assertEqual(parsed.invalid_records[-1]["reason"], "ssrStablecoin不是正数")

    # Generic change metadata keeps percentage unavailable when the baseline is zero.
    def test_change_from_zero_has_null_percentage(self):
        change = fetch_ssr.calculate_change(
            0.4,
            {
                "available": True,
                "target_date": "2026-07-31",
                "actual_date": "2026-07-31",
                "value": 0.0,
                "fallback_days": 0,
            },
        )
        self.assertAlmostEqual(change["absolute_change"], 0.4)
        self.assertIsNone(change["percentage_change"])
        self.assertIsNone(change["percent"])

    # Separate official endpoints require exactly two requests.
    def test_separate_endpoints_make_two_requests(self):
        oscillator, raw = self.make_payloads()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return raw if "/ssr?" in url else oscillator

        with tempfile.TemporaryDirectory() as tmp:
            result, _markdown, _code = fetch_ssr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=Path(tmp),
                now=self.NOW,
                fetcher=fake,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["network_request_count"], 2)

    # If an official combined endpoint ever exists, it is requested only once.
    def test_same_endpoint_is_deduplicated(self):
        oscillator, raw = self.make_payloads()
        combined = [
            dict(left, ssrStablecoin=right["ssrStablecoin"])
            for left, right in zip(oscillator, raw)
        ]
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return fetch_ssr.HttpJson(combined, 200)

        endpoints = {
            "ssr_oscillator": "https://example.test/ssr-all",
            "raw_ssr": "https://example.test/ssr-all",
        }
        with tempfile.TemporaryDirectory() as tmp:
            result, _markdown, _code = fetch_ssr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=Path(tmp),
                now=self.NOW,
                fetcher=fake,
                endpoints=endpoints,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["network_request_count"], 1)

    # A corrupted cache is not silently treated as fresh.
    def test_corrupt_cache_triggers_network_refresh(self):
        oscillator, raw = self.make_payloads()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return raw if "/ssr?" in url else oscillator

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            cache = output / "cache"
            cache.mkdir()
            (cache / "ssr_oscillator_raw.json").write_text("{broken", encoding="utf-8")
            result, _markdown, _code = fetch_ssr.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output,
                now=self.NOW,
                fetcher=fake,
            )
        self.assertEqual(result["network_request_count"], 2)
        self.assertEqual(len(calls), 2)

    # Historical as-of freshness is relative to as-of, not the wall-clock date.
    def test_historical_as_of_has_no_false_freshness_warning(self):
        result = self.build(
            as_of=date(2026, 7, 31), definition_compatible=True
        )
        self.assertEqual(result["actual_latest_date"], "2026-07-31")
        self.assertEqual(result["data_delay_days"], 0)
        self.assertEqual(fetch_ssr.determine_exit_code(result), 0)

    # Actual saved reconnaissance payloads replay without network and remain complete.
    def test_real_response_offline_replay(self):
        oscillator_path = Path("/tmp/ssr-oscillator-response.json")
        raw_path = Path("/tmp/ssr-raw-response.json")
        if not oscillator_path.exists() or not raw_path.exists():
            self.skipTest("侦察响应文件不存在")
        oscillator = json.loads(oscillator_path.read_text(encoding="utf-8"))
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        result = fetch_ssr.build_result(
            oscillator, raw, self.AS_OF, generated_at=self.NOW
        )
        self.assertAlmostEqual(result["ssr_oscillator"]["current"], 0.288723)
        self.assertAlmostEqual(result["raw_ssr"]["current"], 4.9757)
        self.assertEqual(result["actual_latest_date"], "2026-08-01")
        self.assertTrue(result["quality_checks"]["ssr_oscillator"]["is_complete"])
        self.assertTrue(result["quality_checks"]["raw_ssr"]["is_complete"])
        self.assertEqual(result["quality_checks"]["ssr_oscillator"]["record_count"], 40)

    def test_strict_json_rejects_nonfinite_constant(self):
        with self.assertRaises(fetch_ssr.SsrError):
            fetch_ssr.loads_json_strict('[{"x": NaN}]', "测试")

    def test_main_invalid_as_of_returns_one(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(fetch_ssr.main(["--as-of", ""]), 1)


if __name__ == "__main__":
    unittest.main()
