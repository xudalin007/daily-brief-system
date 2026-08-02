import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts import fetch_asopr


class FetchAsoprTests(unittest.TestCase):
    END = date(2026, 8, 1)
    AS_OF = date(2026, 8, 2)
    NOW = datetime(2026, 8, 2, 6, 30, tzinfo=timezone.utc)

    @classmethod
    def make_payload(cls, days=91, end=None):
        end = end or cls.END
        start = end - timedelta(days=days - 1)
        return [
            {
                "d": (start + timedelta(days=offset)).isoformat(),
                "unixTs": 1_700_000_000 + offset * 86_400,
                "asopr": round(0.9 + offset * 0.001, 8),
            }
            for offset in range(days)
        ]

    def build(self, payload=None, as_of=None):
        return fetch_asopr.build_result(
            self.make_payload() if payload is None else payload,
            as_of or self.AS_OF,
            generated_at=self.NOW,
        )

    def test_normal_parse_uses_formal_asopr_field(self):
        parsed = fetch_asopr.parse_series(self.make_payload())
        self.assertAlmostEqual(parsed.values[self.END], 0.99)
        self.assertEqual(parsed.source_sort_order, "ascending")

    def test_proxy_fields_cannot_masquerade_as_asopr(self):
        payload = [
            {
                "d": row["d"],
                "sopr": row["asopr"],
                "sthSopr": row["asopr"],
                "asoprMomentum": row["asopr"],
            }
            for row in self.make_payload()
        ]
        with self.assertRaisesRegex(fetch_asopr.AsoprError, "正式aSOPR"):
            self.build(payload)

    def test_data_date_is_not_fetch_time(self):
        result = self.build()
        self.assertEqual(result["asopr"]["data_date"], "2026-08-01")
        self.assertEqual(result["asopr"]["fetched_at"], "2026-08-02T06:30:00Z")
        self.assertNotEqual(result["asopr"]["data_date"], result["asopr"]["fetched_at"])

    def test_historical_as_of_selects_latest_not_after_date(self):
        result = self.build(as_of=date(2026, 7, 30))
        self.assertEqual(result["requested_as_of"], "2026-07-30")
        self.assertEqual(result["actual_latest_date"], "2026-07-30")
        self.assertEqual(result["data_delay_days"], 0)

    def test_t_plus_one_is_fresh_and_exit_zero(self):
        result = self.build()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["data_delay_days"], 1)
        self.assertEqual(fetch_asopr.determine_exit_code(result), 0)

    def test_more_than_t_plus_one_is_delayed_exit_two(self):
        result = self.build(as_of=date(2026, 8, 4))
        self.assertEqual(result["status"], "delayed")
        self.assertEqual(result["data_delay_days"], 3)
        self.assertEqual(fetch_asopr.determine_exit_code(result), 2)

    def test_derived_7dma_uses_seven_consecutive_calendar_days(self):
        result = self.build()
        seven = result["asopr_7dma"]
        self.assertEqual(seven["status"], "verified")
        self.assertAlmostEqual(seven["value"], 0.987)
        self.assertEqual(seven["data_date"], "2026-08-01")
        self.assertEqual(seven["window_start_date"], "2026-07-26")
        self.assertEqual(seven["window_end_date"], "2026-08-01")
        self.assertEqual(seven["observation_count"], 7)

    def test_7dma_is_unavailable_when_one_day_is_missing(self):
        payload = self.make_payload()
        missing = self.END - timedelta(days=3)
        payload[:] = [row for row in payload if row["d"] != missing.isoformat()]
        result = self.build(payload)
        seven = result["asopr_7dma"]
        self.assertEqual(seven["status"], "unavailable")
        self.assertIsNone(seven["value"])
        self.assertIsNone(seven["data_date"])
        self.assertIn(missing.isoformat(), seven["reason"])
        self.assertEqual(fetch_asopr.determine_exit_code(result), 2)

    def test_fewer_than_seven_records_makes_7dma_unavailable(self):
        result = self.build(self.make_payload(days=6))
        self.assertEqual(result["asopr_7dma"]["status"], "unavailable")
        self.assertEqual(result["asopr_7dma"]["observation_count"], 6)

    def test_null_and_invalid_values_never_become_zero(self):
        payload = self.make_payload()
        payload[-2]["asopr"] = None
        payload[-3]["asopr"] = "not-a-number"
        result = self.build(payload)
        self.assertNotEqual(result["asopr"]["value"], 0)
        self.assertTrue(result["quality_checks"]["invalid_values"])
        self.assertEqual(result["status"], "provisional")

    def test_exact_identical_duplicates_are_canonicalized_without_warning(self):
        payload = []
        for row in self.make_payload():
            payload.extend([row, copy.deepcopy(row)])
        result = self.build(payload)
        quality = result["quality_checks"]
        self.assertEqual(len(quality["canonicalized_exact_duplicate_dates"]), 40)
        self.assertFalse(quality["conflicting_duplicate_dates"])
        self.assertTrue(quality["is_complete"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(fetch_asopr.determine_exit_code(result), 0)

    def test_conflicting_duplicate_is_not_selected(self):
        payload = self.make_payload()
        conflict = copy.deepcopy(payload[-1])
        conflict["asopr"] += 0.1
        payload.append(conflict)
        result = self.build(payload)
        self.assertEqual(result["actual_latest_date"], "2026-07-31")
        self.assertIn("冲突或无效重复日期", " ".join(result["warnings"]))
        self.assertEqual(result["status"], "provisional")

    def test_descending_source_order_is_quality_warning(self):
        result = self.build(list(reversed(self.make_payload())))
        self.assertEqual(result["quality_checks"]["source_sort_order"], "descending")
        self.assertIn("未按日期升序", " ".join(result["warnings"]))

    def test_valid_cache_hit_uses_zero_network_requests(self):
        payload = self.make_payload()
        calls = []

        def fake(url, timeout):
            calls.append((url, timeout))
            return fetch_asopr.HttpJson(payload, 200)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            first, _markdown, _code = fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW,
                fetcher=fake,
            )
            second, _markdown, code = fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output,
                now=self.NOW + timedelta(minutes=5),
                fetcher=fake,
            )
        self.assertEqual(first["network_request_count"], 1)
        self.assertEqual(second["network_request_count"], 0)
        self.assertTrue(second["cache_status"]["used_cache"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(code, 0)

    def test_refresh_bypasses_valid_cache(self):
        payload = self.make_payload()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW,
                fetcher=fake,
            )
            result, _markdown, _code = fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW + timedelta(minutes=5),
                fetcher=fake,
            )
        self.assertEqual(result["network_request_count"], 1)
        self.assertEqual(len(calls), 2)

    def test_corrupt_cache_is_ignored_and_refetched(self):
        payload = self.make_payload()
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            cache = output / "cache" / "asopr_raw.json"
            cache.parent.mkdir(parents=True)
            cache.write_text("{broken", encoding="utf-8")
            result, _markdown, code = fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output,
                now=self.NOW,
                fetcher=lambda url, timeout: calls.append(url) or payload,
            )
        self.assertEqual(result["network_request_count"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(code, 0)

    def test_expired_cache_is_not_silently_used(self):
        payload = self.make_payload()
        calls = []

        def fake(url, timeout):
            calls.append(url)
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=output,
                now=self.NOW,
                fetcher=fake,
            )
            result, _markdown, _code = fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=False,
                output_dir=output,
                now=self.NOW + timedelta(hours=6),
                fetcher=fake,
            )
        self.assertEqual(result["network_request_count"], 1)
        self.assertEqual(len(calls), 2)

    def test_network_failure_is_fatal(self):
        def fail(url, timeout):
            raise fetch_asopr.AsoprError("offline")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(fetch_asopr.AsoprError, "offline"):
                fetch_asopr.execute(
                    requested_as_of=self.AS_OF,
                    refresh=True,
                    output_dir=Path(tmp),
                    now=self.NOW,
                    fetcher=fail,
                )

    def test_one_history_request_contains_required_query(self):
        payload = self.make_payload()
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            result, _markdown, _code = fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=Path(tmp),
                now=self.NOW,
                fetcher=lambda url, timeout: calls.append(url) or payload,
            )
        self.assertEqual(len(calls), 1)
        self.assertIn("startday=2026-05-04", calls[0])
        self.assertIn("endday=2026-08-02", calls[0])
        self.assertIn("size=500", calls[0])
        self.assertEqual(result["network_request_count"], 1)

    def test_json_and_markdown_outputs_are_generated(self):
        result = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            json_path, markdown_path, markdown = fetch_asopr.write_outputs(result, output)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["asopr"]["value"], result["asopr"]["value"])
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), markdown)
            self.assertIn("## BTC aSOPR", markdown)
            self.assertIn("### aSOPR 7DMA", markdown)

    def test_output_contract_and_source_method_are_explicit(self):
        result = self.build()
        self.assertEqual(result["metric"], "asopr")
        self.assertEqual(result["source"]["value_field"], "asopr")
        self.assertFalse(result["source"]["requires_api_key"])
        self.assertEqual(
            result["asopr_7dma"]["method"],
            "derived_arithmetic_mean_7_consecutive_calendar_days",
        )
        self.assertEqual(result["asopr"]["source_url"], fetch_asopr.ASOPR_ENDPOINT)

    def test_cache_preserves_source_url_http_and_fetched_at(self):
        payload = self.make_payload()
        with tempfile.TemporaryDirectory() as tmp:
            result, _markdown, _code = fetch_asopr.execute(
                requested_as_of=self.AS_OF,
                refresh=True,
                output_dir=Path(tmp),
                now=self.NOW,
                fetcher=lambda url, timeout: fetch_asopr.HttpJson(payload, 200),
            )
        self.assertIn("startday=", result["source"]["request_url"])
        self.assertEqual(result["source"]["http_status"], 200)
        self.assertEqual(result["source"]["fetched_at_utc"], "2026-08-02T06:30:00Z")

    def test_malformed_json_is_clear_error(self):
        with self.assertRaises(fetch_asopr.AsoprError):
            fetch_asopr.loads_json_strict("{broken", "fixture")

    def test_empty_explicit_as_of_fails_before_network(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = fetch_asopr.main(["--as-of", ""])
        self.assertEqual(code, 1)
        self.assertIn("日期必须使用 YYYY-MM-DD 格式", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
