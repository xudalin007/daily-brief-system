import copy
import contextlib
from http.client import IncompleteRead
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import fetch_mvrv


class FetchMvrvTests(unittest.TestCase):
    END = date(2026, 8, 1)
    AS_OF = date(2026, 8, 2)
    NOW = datetime(2026, 8, 2, 6, 30, tzinfo=timezone.utc)

    @classmethod
    def make_payloads(cls, days=91, mvrv_end=None, sth_end=None):
        mvrv_end = mvrv_end or cls.END
        sth_end = sth_end or mvrv_end

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

        return rows(mvrv_end, "mvrv", 1.0, 0.01), rows(
            sth_end, "sthMvrv", 0.8, 0.002
        )

    @staticmethod
    def remove_dates(payload, *dates):
        texts = {item.isoformat() for item in dates}
        payload[:] = [row for row in payload if row.get("d") not in texts]

    def build(self, mvrv=None, sth=None, as_of=None):
        default_mvrv, default_sth = self.make_payloads()
        return fetch_mvrv.build_result(
            default_mvrv if mvrv is None else mvrv,
            default_sth if sth is None else sth,
            as_of or self.AS_OF,
            generated_at=self.NOW,
        )

    # 1. Official MVRV field parses as a positive daily series.
    def test_mvrv_normal_parse(self):
        payload, _ = self.make_payloads()
        parsed = fetch_mvrv.parse_series(payload, "mvrv", "MVRV")
        self.assertAlmostEqual(parsed.values[self.END], 1.9)

    # 2. Official STH-MVRV field is parsed directly.
    def test_sth_mvrv_normal_parse(self):
        _, payload = self.make_payloads()
        parsed = fetch_mvrv.parse_series(payload, "sthMvrv", "STH-MVRV")
        self.assertAlmostEqual(parsed.values[self.END], 0.98)

    # 3. A combined official response is requested only once.
    def test_same_endpoint_is_deduplicated(self):
        mvrv, sth = self.make_payloads()
        combined = [dict(left, sthMvrv=right["sthMvrv"]) for left, right in zip(mvrv, sth)]
        calls = []

        def fake(url, timeout):
            calls.append((url, timeout))
            return fetch_mvrv.HttpJson(combined, 200)

        with tempfile.TemporaryDirectory() as tmp:
            result, _markdown, code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=Path(tmp),
                now=self.NOW,
                fetcher=fake,
                endpoints={"mvrv": "https://example.test/all", "sth_mvrv": "https://example.test/all"},
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["network_request_count"], 1)
        self.assertEqual(code, 0)

    # 4. Separate official endpoints make exactly two requests.
    def test_different_endpoints_make_two_requests(self):
        mvrv, sth = self.make_payloads()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return sth if "sth" in url else mvrv

        with tempfile.TemporaryDirectory() as tmp:
            result, _markdown, code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=Path(tmp),
                now=self.NOW,
                fetcher=fake,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["network_request_count"], 2)
        self.assertEqual(code, 0)

    # 5. Missing formal STH data is explicit Unavailable, not fatal.
    def test_sth_missing_is_unavailable(self):
        mvrv, _ = self.make_payloads()
        result = self.build(mvrv=mvrv, sth=None)
        # build() uses the default for None; call directly to represent failed provider.
        result = fetch_mvrv.build_result(mvrv, None, self.AS_OF, generated_at=self.NOW)
        self.assertEqual(result["sth_mvrv"]["status"], "Unavailable")
        self.assertIsNone(result["sth_mvrv"]["current"])
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 2)

    # 6. A proxy-only field cannot masquerade as STH-MVRV.
    def test_proxy_field_is_rejected(self):
        mvrv, sth = self.make_payloads()
        proxy = [
            {"d": row["d"], "sthMvrvMomentum": row["sthMvrv"], "sthRiskIndex": 22}
            for row in sth
        ]
        result = self.build(mvrv=mvrv, sth=proxy)
        self.assertEqual(result["sth_mvrv"]["status"], "Unavailable")
        self.assertIn("未取得可靠STH-MVRV正式值", " ".join(result["warnings"]))

    # 7-10. Current and each requested historical point are preserved.
    def test_current_and_history_points(self):
        result = self.build()
        self.assertAlmostEqual(result["mvrv"]["current"], 1.9)
        self.assertEqual(result["mvrv"]["date"], "2026-08-01")
        expected = {
            "previous": "2026-07-31",
            "d7": "2026-07-25",
            "d30": "2026-07-02",
        }
        for key, actual_date in expected.items():
            with self.subTest(key=key):
                point = result["mvrv"][key]
                self.assertTrue(point["available"])
                self.assertEqual(point["target_date"], actual_date)
                self.assertEqual(point["actual_date"], actual_date)
                self.assertEqual(point["fallback_days"], 0)

    # 11. Missing targets search backward, never forward.
    def test_target_falls_back_backward(self):
        mvrv, sth = self.make_payloads()
        target = self.END - timedelta(days=7)
        self.remove_dates(mvrv, target)
        result = self.build(mvrv=mvrv, sth=sth)
        point = result["mvrv"]["d7"]
        self.assertEqual(point["actual_date"], (target - timedelta(days=1)).isoformat())
        self.assertEqual(point["fallback_days"], 1)
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 2)

    # 12. More than seven missing days makes the point unavailable.
    def test_fallback_never_exceeds_seven_days(self):
        mvrv, sth = self.make_payloads()
        target = self.END - timedelta(days=7)
        self.remove_dates(mvrv, *(target - timedelta(days=i) for i in range(8)))
        result = self.build(mvrv=mvrv, sth=sth)
        point = result["mvrv"]["d7"]
        self.assertFalse(point["available"])
        self.assertIsNone(point["fallback_days"])

    # 13. Missing values remain null and never become zero.
    def test_missing_value_never_becomes_zero(self):
        mvrv, _ = self.make_payloads()
        result = fetch_mvrv.build_result(mvrv, None, self.AS_OF, generated_at=self.NOW)
        self.assertIsNone(result["sth_mvrv"]["current"])
        self.assertIsNone(result["sth_mvrv"]["d7"]["value"])
        self.assertEqual(result["sth_mvrv"]["d7"]["target_date"], "2026-07-25")

    # 14. Duplicate dates are surfaced in recent quality checks.
    def test_duplicate_detection(self):
        mvrv, sth = self.make_payloads()
        mvrv.append(copy.deepcopy(mvrv[-3]))
        result = self.build(mvrv=mvrv, sth=sth)
        self.assertEqual(result["quality_checks"]["mvrv"]["duplicate_dates"][0]["count"], 2)
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 2)

    # 15. Invalid and non-positive values are never analysis values.
    def test_invalid_value_detection(self):
        mvrv, sth = self.make_payloads()
        mvrv[-2]["mvrv"] = 0
        mvrv[-3]["mvrv"] = "NaN-like"
        result = self.build(mvrv=mvrv, sth=sth)
        quality = result["quality_checks"]["mvrv"]
        self.assertEqual(len(quality["invalid_values"]), 2)
        self.assertNotEqual(result["mvrv"]["current"], 0)

    # 16. Latest source dates align when equal.
    def test_date_aligned(self):
        self.assertEqual(self.build()["date_alignment"], "aligned")

    # 17. Different latest dates remain visible and warn.
    def test_date_misaligned(self):
        mvrv, sth = self.make_payloads(sth_end=self.END - timedelta(days=1))
        result = self.build(mvrv=mvrv, sth=sth)
        self.assertEqual(result["date_alignment"], "misaligned")
        self.assertEqual(result["mvrv"]["date"], "2026-08-01")
        self.assertEqual(result["sth_mvrv"]["date"], "2026-07-31")
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 2)

    # 18. T+1 is normal for daily on-chain data.
    def test_t_plus_one_exits_zero(self):
        result = self.build()
        self.assertEqual(result["data_delay_days"], 1)
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 0)

    # 19. More than T+1 is delayed and exits two.
    def test_delay_over_one_exits_two(self):
        result = self.build(as_of=date(2026, 8, 4))
        self.assertEqual(result["data_delay_days"], 3)
        self.assertEqual(result["mvrv"]["status"], "Delayed")
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 2)

    def test_delay_plus_quality_problem_is_provisional(self):
        mvrv, sth = self.make_payloads()
        target = self.END - timedelta(days=7)
        self.remove_dates(mvrv, target)

        result = self.build(
            mvrv=mvrv,
            sth=sth,
            as_of=date(2026, 8, 4),
        )

        self.assertEqual(result["data_delay_days"], 3)
        self.assertEqual(result["mvrv"]["status"], "Provisional")
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 2)

    # 20. Core MVRV request failure is fatal.
    def test_fatal_mvrv_api_failure(self):
        def fail(url, timeout):
            raise fetch_mvrv.MvrvError("offline")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(fetch_mvrv.MvrvError):
                fetch_mvrv.execute(
                    requested_as_of=self.AS_OF,
                    refresh=True,
                    output_dir=Path(tmp),
                    now=self.NOW,
                    fetcher=fail,
                )

    # 21. Valid six-hour caches avoid all network calls.
    def test_cache_hit_uses_zero_requests(self):
        mvrv, sth = self.make_payloads()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return sth if "sth" in url else mvrv

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            first, _md, _code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW,
                fetcher=fake,
            )
            second, _md, code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output,
                now=self.NOW + timedelta(minutes=5),
                fetcher=fake,
            )
        self.assertEqual(first["network_request_count"], 2)
        self.assertEqual(second["network_request_count"], 0)
        self.assertTrue(second["cache_status"]["used_cache"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(code, 0)

    # 22. --refresh bypasses otherwise valid caches.
    def test_refresh_bypasses_cache(self):
        mvrv, sth = self.make_payloads()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return sth if "sth" in url else mvrv

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            fetch_mvrv.execute(requested_as_of=self.AS_OF, refresh=True, output_dir=output, now=self.NOW, fetcher=fake)
            result, _md, _code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW + timedelta(minutes=5),
                fetcher=fake,
            )
        self.assertEqual(result["network_request_count"], 2)
        self.assertEqual(len(calls), 4)

    # 23-24. JSON and Markdown outputs are both generated atomically.
    def test_json_and_markdown_outputs(self):
        result = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            json_path, markdown_path, markdown = fetch_mvrv.write_outputs(result, output)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["mvrv"]["current"], result["mvrv"]["current"])
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), markdown)
            self.assertIn("## BTC MVRV", markdown)
            self.assertIn("### STH-MVRV", markdown)

    # 25-26. Source URLs and fetch timestamps survive normalization.
    def test_source_url_and_fetched_at_preserved(self):
        mvrv, sth = self.make_payloads()
        status = {
            key: {
                "used_cache": False,
                "status": "网络请求",
                "request_url": f"https://example.test/{key}?size=500",
                "http_status": 200,
                "fetched_at_utc": "2026-08-02T06:30:00Z",
            }
            for key in ("mvrv", "sth_mvrv")
        }
        result = fetch_mvrv.build_result(
            mvrv,
            sth,
            self.AS_OF,
            generated_at=self.NOW,
            cache_statuses=status,
        )
        self.assertIn("size=500", result["source"]["endpoints"]["mvrv"]["request_url"])
        self.assertEqual(
            result["source"]["endpoints"]["sth_mvrv"]["fetched_at_utc"],
            "2026-08-02T06:30:00Z",
        )

    def test_change_calculation_has_absolute_and_percentage(self):
        result = self.build()
        change = result["mvrv"]["change_7d"]
        self.assertAlmostEqual(change["absolute_change"], 0.07)
        self.assertAlmostEqual(change["percentage_change"], 1.9 / 1.83 - 1)
        self.assertAlmostEqual(change["percent"], (1.9 / 1.83 - 1) * 100)

    def test_distance_from_one(self):
        result = self.build()
        self.assertAlmostEqual(
            result["sth_mvrv"]["distance_from_1"],
            result["sth_mvrv"]["current"] - 1,
        )

    def test_sth_request_failure_is_nonfatal_exit_two(self):
        mvrv, _sth = self.make_payloads()

        def fake(url, timeout):
            if "sth" in url:
                raise fetch_mvrv.MvrvError("HTTP请求失败（状态码503）")
            return mvrv

        with tempfile.TemporaryDirectory() as tmp:
            result, markdown, code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=Path(tmp),
                now=self.NOW,
                fetcher=fake,
            )
        self.assertEqual(code, 2)
        self.assertEqual(result["sth_mvrv"]["status"], "Unavailable")
        self.assertIn("未取得可靠STH-MVRV正式值", markdown)

    def test_proxy_only_response_is_not_cached(self):
        mvrv, sth = self.make_payloads()
        proxy = [{"d": row["d"], "sthMvrvMomentum": row["sthMvrv"]} for row in sth]

        def fake(url, timeout):
            return proxy if "sth" in url else mvrv

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            result, _md, code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW,
                fetcher=fake,
            )
            self.assertFalse((output / "cache" / "sth_mvrv_raw.json").exists())
        self.assertEqual(result["sth_mvrv"]["status"], "Unavailable")
        self.assertEqual(code, 2)

    def test_invalid_record_after_latest_is_warning(self):
        mvrv, sth = self.make_payloads()
        mvrv.append({"d": "2026-08-02", "mvrv": None})
        result = self.build(mvrv=mvrv, sth=sth)
        self.assertIn("最新有效日线之后存在无效记录", " ".join(result["warnings"]))
        self.assertEqual(fetch_mvrv.determine_exit_code(result), 2)

    def test_historical_as_of_does_not_use_today_for_freshness(self):
        mvrv, sth = self.make_payloads()
        result = fetch_mvrv.build_result(
            mvrv,
            sth,
            date(2026, 7, 31),
            generated_at=self.NOW,
        )
        self.assertEqual(result["actual_latest_date"], "2026-07-31")
        self.assertEqual(result["data_delay_days"], 0)

    def test_malformed_json_is_clear_fatal_error(self):
        with self.assertRaises(fetch_mvrv.MvrvError):
            fetch_mvrv.loads_json_strict("{broken", "fixture")

    def test_empty_explicit_as_of_is_fatal_before_network(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = fetch_mvrv.main(["--as-of", ""])

        self.assertEqual(exit_code, 1)
        self.assertIn("日期必须使用 YYYY-MM-DD 格式", stderr.getvalue())

    def test_non_utf8_cache_is_treated_as_cache_miss(self):
        mvrv, sth = self.make_payloads()
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            cache_path = output_dir / "cache" / "mvrv_raw.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_bytes(b"\xff\xfe")

            result, _markdown, code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output_dir,
                now=self.NOW,
                fetcher=lambda url, _timeout: (
                    calls.append(url) or (sth if "/sth-mvrv" in url else mvrv)
                ),
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["network_request_count"], 2)
        self.assertEqual(len(calls), 2)

    def test_cache_without_valid_request_metadata_is_miss(self):
        mvrv, sth = self.make_payloads()
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output_dir,
                now=self.NOW,
                fetcher=lambda url, _timeout: sth if "/sth-mvrv" in url else mvrv,
            )
            cache_path = output_dir / "cache" / "mvrv_raw.json"
            envelope = json.loads(cache_path.read_text(encoding="utf-8"))
            envelope.pop("request_url")
            cache_path.write_text(json.dumps(envelope), encoding="utf-8")
            calls = []

            result, _markdown, code = fetch_mvrv.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output_dir,
                now=self.NOW + timedelta(minutes=1),
                fetcher=lambda url, _timeout: (
                    calls.append(url) or (sth if "/sth-mvrv" in url else mvrv)
                ),
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["network_request_count"], 1)
        self.assertEqual(len(calls), 1)

    def test_incomplete_http_read_becomes_clear_mvrv_error(self):
        class BrokenResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def getcode(self):
                return self.status

            def read(self, _size):
                raise IncompleteRead(b"partial", 10)

        with mock.patch.object(fetch_mvrv, "urlopen", return_value=BrokenResponse()):
            with self.assertRaisesRegex(
                fetch_mvrv.MvrvError, "网络请求超时或连接失败"
            ):
                fetch_mvrv.fetch_json(fetch_mvrv.MVRV_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
