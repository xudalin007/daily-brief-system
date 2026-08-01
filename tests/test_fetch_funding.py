import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import fetch_funding


def unix_ms(value):
    return int(value.timestamp() * 1000)


class FetchFundingTests(unittest.TestCase):
    NOW = datetime(2026, 8, 1, 15, 45, tzinfo=timezone.utc)
    UPCOMING = datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc)

    @classmethod
    def okx_payloads(
        cls,
        *,
        current_rate=0.00004,
        current_interval=8,
        previous_rate=0.00002,
        history_interval=None,
    ):
        history_interval = history_interval or current_interval
        funding_time = cls.UPCOMING
        next_funding_time = funding_time + timedelta(hours=current_interval)
        latest_settled = funding_time - timedelta(hours=history_interval)
        older_settled = latest_settled - timedelta(hours=history_interval)
        current = {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": str(current_rate),
                    "fundingTime": str(unix_ms(funding_time)),
                    "nextFundingTime": str(unix_ms(next_funding_time)),
                    "settFundingRate": "0.000019",
                    "settState": "settled",
                    "method": "current_period",
                    "ts": str(unix_ms(cls.NOW)),
                }
            ],
        }
        history = {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    # Deliberately differs from realizedRate. The latter is the
                    # actual settled value required by the daily brief.
                    "fundingRate": "0.000099",
                    "realizedRate": str(previous_rate),
                    "fundingTime": str(unix_ms(latest_settled)),
                },
                {
                    "instId": "BTC-USDT-SWAP",
                    "fundingRate": "0.000088",
                    "realizedRate": "0.00001",
                    "fundingTime": str(unix_ms(older_settled)),
                },
            ],
        }
        return {"current": current, "history": history}

    @classmethod
    def bybit_payloads(
        cls,
        *,
        current_rate=0.00006,
        interval=8,
        previous_rate=0.00003,
        include_interval=True,
        history_rows=2,
        history_oldest_first=False,
    ):
        current_row = {
            "symbol": "BTCUSDT",
            "fundingRate": str(current_rate),
            "nextFundingTime": str(unix_ms(cls.UPCOMING)),
        }
        if include_interval:
            current_row["fundingIntervalHour"] = str(interval)
        current = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"category": "linear", "list": [current_row]},
            "retExtInfo": {},
            "time": unix_ms(cls.NOW),
        }
        latest = cls.UPCOMING - timedelta(hours=interval)
        rows = [
            {
                "symbol": "BTCUSDT",
                "fundingRate": str(previous_rate),
                "fundingRateTimestamp": str(unix_ms(latest)),
            },
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.00002",
                "fundingRateTimestamp": str(
                    unix_ms(latest - timedelta(hours=interval))
                ),
            },
        ][:history_rows]
        if history_oldest_first:
            rows.reverse()
        history = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"category": "linear", "list": rows},
            "retExtInfo": {},
            "time": unix_ms(cls.NOW),
        }
        return {"current": current, "history": history}

    @classmethod
    def binance_payloads(
        cls,
        *,
        current_rate=0.00002,
        interval=8,
        previous_rate=0.00001,
        include_info=True,
    ):
        latest = cls.UPCOMING - timedelta(hours=interval)
        payloads = {
            "current": {
                "symbol": "BTCUSDT",
                "markPrice": "63020.10",
                "indexPrice": "63030.20",
                "estimatedSettlePrice": "63025.00",
                "lastFundingRate": str(current_rate),
                "interestRate": "0.00010000",
                "nextFundingTime": unix_ms(cls.UPCOMING),
                "time": unix_ms(cls.NOW),
            },
            "history": [
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": str(previous_rate),
                    "fundingTime": unix_ms(latest),
                    "markPrice": "63010.0",
                },
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": "0.00000",
                    "fundingTime": unix_ms(latest - timedelta(hours=interval)),
                    "markPrice": "62990.0",
                },
            ],
        }
        if include_info:
            payloads["info"] = [
                {
                    "symbol": "BTCUSDT",
                    "adjustedFundingRateCap": "0.02000000",
                    "adjustedFundingRateFloor": "-0.02000000",
                    "fundingIntervalHours": interval,
                    "disclaimer": False,
                }
            ]
        return payloads

    @classmethod
    def bundle(cls, provider, payloads, *, errors=None, used_cache=True):
        errors = errors or {}
        requests = []
        for endpoint, url in provider.endpoints.items():
            error = errors.get(endpoint)
            requests.append(
                {
                    "endpoint": endpoint,
                    "url": url,
                    "http_status": error.get("http_status") if error else 200,
                    "fetched_at_utc": fetch_funding.format_utc_timestamp(cls.NOW),
                    "cache_status": "使用缓存" if used_cache else "网络获取",
                    "error": error.get("reason") if error else None,
                }
            )
        return fetch_funding.ProviderBundle(
            payloads=copy.deepcopy(payloads),
            requests=requests,
            errors=copy.deepcopy(errors),
            fetched_at=cls.NOW,
            cache_status={
                "used_cache": used_cache,
                "status": "使用缓存" if used_cache else "网络获取",
                "reason": "离线fixture",
                "cache_file": "/tmp/funding-fixture.json",
            },
            network_request_count=0 if used_cache else len(provider.endpoints),
        )

    @classmethod
    def parse_okx(cls, payloads=None):
        provider = fetch_funding.OkxFundingProvider()
        return provider.build_result(
            cls.bundle(provider, payloads or cls.okx_payloads()), cls.NOW
        )

    @classmethod
    def parse_bybit(cls, payloads=None):
        provider = fetch_funding.BybitFundingProvider()
        return provider.build_result(
            cls.bundle(provider, payloads or cls.bybit_payloads()), cls.NOW
        )

    @classmethod
    def parse_binance(cls, payloads=None):
        provider = fetch_funding.BinanceFundingProvider()
        return provider.build_result(
            cls.bundle(provider, payloads or cls.binance_payloads()), cls.NOW
        )

    @classmethod
    def unavailable(cls, provider, *, status=503, reason="服务不可用"):
        errors = {
            endpoint: {
                "url": url,
                "http_status": status,
                "reason": f"HTTP {status} {reason}",
            }
            for endpoint, url in provider.endpoints.items()
        }
        return provider.build_result(cls.bundle(provider, {}, errors=errors), cls.NOW)

    @classmethod
    def default_provider_results(cls):
        return {
            "okx": cls.parse_okx(),
            "bybit": cls.parse_bybit(),
            "binance": cls.parse_binance(),
        }

    @classmethod
    def default_response_map(cls):
        okx = cls.okx_payloads()
        bybit = cls.bybit_payloads()
        binance = cls.binance_payloads()
        return {
            fetch_funding.OKX_CURRENT_URL: okx["current"],
            fetch_funding.OKX_HISTORY_URL: okx["history"],
            fetch_funding.BYBIT_CURRENT_URL: bybit["current"],
            fetch_funding.BYBIT_HISTORY_URL: bybit["history"],
            fetch_funding.BINANCE_CURRENT_URL: binance["current"],
            fetch_funding.BINANCE_HISTORY_URL: binance["history"],
            fetch_funding.BINANCE_INFO_URL: binance["info"],
        }

    @staticmethod
    def transport_from(response_map, calls, failures=None):
        failures = failures or {}

        def transport(url):
            calls.append(url)
            if url in failures:
                failure = failures[url]
                raise fetch_funding.ProviderHttpError(
                    url,
                    failure.get("reason", "请求失败"),
                    failure.get("status"),
                )
            return fetch_funding.HttpJsonResponse(
                url=url,
                http_status=200,
                payload=copy.deepcopy(response_map[url]),
            )

        return transport

    # 1. OKX's live schema supplies an 8h estimate and history realizedRate.
    def test_okx_real_schema_uses_realized_rate_and_eight_hour_interval(self):
        result = self.parse_okx()

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["symbol"], "BTC-USDT-SWAP")
        self.assertEqual(result["current"]["type"], "estimated")
        self.assertEqual(result["current"]["interval_hours"], 8.0)
        self.assertEqual(
            result["current"]["interval_source"],
            "fundingTime_to_nextFundingTime",
        )
        self.assertAlmostEqual(result["normalized_8h"]["current_rate"], 0.00004)
        # Must be realizedRate=0.00002, not the deliberately different 0.000099.
        self.assertAlmostEqual(result["previous_settled"]["rate"], 0.00002)
        self.assertEqual(result["previous_settled"]["interval_hours"], 8.0)
        self.assertAlmostEqual(
            result["normalized_8h"]["previous_settled_rate"], 0.00002
        )

    # 2. Providers must retain a 4h raw cycle while converting it to 8h.
    def test_okx_four_hour_interval_normalizes_to_eight_hours(self):
        result = self.parse_okx(
            self.okx_payloads(
                current_rate=0.00004,
                current_interval=4,
                previous_rate=0.00002,
            )
        )

        self.assertEqual(result["current"]["interval_hours"], 4.0)
        self.assertAlmostEqual(result["normalized_8h"]["current_rate"], 0.00008)
        self.assertEqual(result["previous_settled"]["interval_hours"], 4.0)
        self.assertAlmostEqual(
            result["normalized_8h"]["previous_settled_rate"], 0.00004
        )

    def test_okx_missing_one_expected_settlement_is_not_silent(self):
        payloads = self.okx_payloads(current_interval=8, history_interval=8)
        # Current funding targets 16:00, while the latest history row is moved
        # from 08:00 back to 00:00. History itself remains an 8h sequence, so
        # the 16h current-to-latest gap represents one missing settlement.
        for row in payloads["history"]["data"]:
            original = datetime.fromtimestamp(
                int(row["fundingTime"]) / 1000, tz=timezone.utc
            )
            row["fundingTime"] = str(unix_ms(original - timedelta(hours=8)))

        providers = self.default_provider_results()
        providers["okx"] = self.parse_okx(payloads)
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertEqual(providers["okx"]["current"]["interval_hours"], 8.0)
        self.assertEqual(
            providers["okx"]["previous_settled"]["interval_hours"], 8.0
        )
        self.assertTrue(
            providers["okx"]["warnings"],
            "OKX current/history相隔两期时必须显式警告",
        )
        self.assertEqual(fetch_funding.determine_exit_code(result), 2)

    # 3. Bybit fundingIntervalHour is an explicit whole-hour string.
    def test_bybit_ticker_schema_reads_funding_interval_hour(self):
        result = self.parse_bybit()

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["current"]["type"], "estimated")
        self.assertIsNone(result["current"]["funding_time"])
        self.assertEqual(result["current"]["next_funding_time"], "2026-08-01T16:00:00Z")
        self.assertEqual(result["current"]["interval_hours"], 8.0)
        self.assertEqual(result["current"]["interval_source"], "fundingIntervalHour")
        self.assertAlmostEqual(result["normalized_8h"]["current_rate"], 0.00006)

    # 4. Funding history is sorted by timestamp rather than trusted response order.
    def test_bybit_history_selects_latest_settlement_when_input_is_unsorted(self):
        payloads = self.bybit_payloads(history_oldest_first=True)
        result = self.parse_bybit(payloads)

        self.assertAlmostEqual(result["previous_settled"]["rate"], 0.00003)
        self.assertEqual(
            result["previous_settled"]["funding_time"], "2026-08-01T08:00:00Z"
        )
        self.assertEqual(result["previous_settled"]["interval_source"], "consecutive_history")

    def test_bybit_single_history_interval_requires_next_time_cross_check(self):
        payloads = self.bybit_payloads(
            interval=8,
            include_interval=True,
            history_rows=1,
        )
        history_row = payloads["history"]["result"]["list"][0]
        # nextFundingTime is 16:00 but the sole history row is 00:00. With no
        # second history row, the explicit 8h current interval cannot prove
        # that this previous settlement also used an 8h interval.
        history_row["fundingRateTimestamp"] = str(
            unix_ms(self.UPCOMING - timedelta(hours=16))
        )

        providers = self.default_provider_results()
        providers["bybit"] = self.parse_bybit(payloads)
        result = fetch_funding.build_result(providers, self.NOW)
        previous = providers["bybit"]["previous_settled"]

        self.assertEqual(providers["bybit"]["current"]["interval_hours"], 8.0)
        self.assertIsNone(
            previous["interval_hours"],
            "单条历史与nextFundingTime相差16h时不能套用当前8h周期",
        )
        self.assertIsNone(
            providers["bybit"]["normalized_8h"]["previous_settled_rate"]
        )
        self.assertTrue(providers["bybit"]["warnings"])
        self.assertEqual(fetch_funding.determine_exit_code(result), 2)

    # Binance's public schema can infer the actual cycle from settlement times.
    def test_binance_real_schema_parses_settlement_and_infers_interval(self):
        payloads = self.binance_payloads(include_info=False)
        result = self.parse_binance(payloads)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["current"]["interval_hours"], 8.0)
        self.assertEqual(
            result["current"]["interval_source"],
            "latest_settled_to_nextFundingTime",
        )
        self.assertAlmostEqual(result["previous_settled"]["rate"], 0.00001)
        self.assertEqual(result["previous_settled"]["interval_hours"], 8.0)

    def test_binance_sixteen_hour_next_gap_does_not_override_eight_hour_history(self):
        payloads = self.binance_payloads(include_info=False, interval=8)
        # Preserve an internally consistent 8h history, but move both rows one
        # period back: nextFundingTime-to-latest is now 16h. That gap signals a
        # missing row; it is not evidence that the current cycle changed to 16h.
        for row in payloads["history"]:
            original = datetime.fromtimestamp(
                int(row["fundingTime"]) / 1000, tz=timezone.utc
            )
            row["fundingTime"] = unix_ms(original - timedelta(hours=8))

        providers = self.default_provider_results()
        providers["binance"] = self.parse_binance(payloads)
        result = fetch_funding.build_result(providers, self.NOW)
        binance = providers["binance"]

        self.assertNotEqual(
            binance["current"]["interval_hours"],
            16.0,
            "16h next/latest gap不得静默覆盖连续历史确认的8h周期",
        )
        self.assertIn(binance["current"]["interval_hours"], (8.0, None))
        if binance["current"]["interval_hours"] == 8.0:
            self.assertAlmostEqual(
                binance["normalized_8h"]["current_rate"],
                binance["current"]["rate"],
            )
        else:
            self.assertIsNone(binance["normalized_8h"]["current_rate"])
        self.assertTrue(binance["warnings"])
        self.assertEqual(fetch_funding.determine_exit_code(result), 2)

    def test_binance_single_history_sixteen_hour_gap_is_not_a_reliable_interval(self):
        payloads = self.binance_payloads(include_info=False, interval=8)
        latest = payloads["history"][0]
        latest_time = datetime.fromtimestamp(
            int(latest["fundingTime"]) / 1000, tz=timezone.utc
        )
        # Leave only one settlement and move it from 08:00 to 00:00. Without
        # fundingInfo or a second history row, the 16h next/latest gap could be
        # a missing record and is not enough to establish a 16h cycle.
        latest["fundingTime"] = unix_ms(latest_time - timedelta(hours=8))
        payloads["history"] = [latest]

        providers = self.default_provider_results()
        providers["binance"] = self.parse_binance(payloads)
        result = fetch_funding.build_result(providers, self.NOW)
        binance = providers["binance"]

        self.assertEqual(binance["status"], "available")
        self.assertIsNone(
            binance["current"]["interval_hours"],
            "单条历史形成的16h缺口不能被当作可靠周期",
        )
        self.assertIsNone(binance["normalized_8h"]["current_rate"])
        self.assertTrue(binance["warnings"])
        self.assertEqual(result["summary"]["available_provider_count"], 3)
        self.assertEqual(result["summary"]["normalized_provider_count"], 2)
        self.assertAlmostEqual(result["summary"]["simple_mean_8h"], 0.00005)
        self.assertIn(
            "binance", result["quality_checks"]["providers_without_reliable_interval"]
        )
        self.assertEqual(fetch_funding.determine_exit_code(result), 2)

    # 5. Binance is best effort; a regional failure must not erase valid sources.
    def test_binance_unavailable_does_not_fail_other_providers(self):
        providers = self.default_provider_results()
        providers["binance"] = self.unavailable(
            fetch_funding.BinanceFundingProvider(),
            status=451,
            reason="Unavailable For Legal Reasons",
        )
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertEqual(result["reference_funding"]["provider"], "okx")
        self.assertNotEqual(fetch_funding.determine_exit_code(result), 1)
        self.assertEqual(result["providers"]["binance"]["status"], "unavailable")
        self.assertIsNone(
            result["providers"]["binance"]["normalized_8h"]["current_rate"]
        )
        self.assertIn("binance", result["quality_checks"]["best_effort_unavailable"])

    # 6. No provider with a reliable normalized value is fatal.
    def test_all_providers_failed_returns_exit_one(self):
        providers = {
            "okx": self.unavailable(fetch_funding.OkxFundingProvider()),
            "bybit": self.unavailable(fetch_funding.BybitFundingProvider()),
            "binance": self.unavailable(fetch_funding.BinanceFundingProvider()),
        }
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertIsNone(result["reference_funding"])
        self.assertEqual(result["summary"]["available_provider_count"], 0)
        self.assertEqual(fetch_funding.determine_exit_code(result), 1)

    # 7. Bybit is the reference fallback when required OKX is unavailable.
    def test_okx_failed_bybit_available_returns_exit_two(self):
        providers = self.default_provider_results()
        providers["okx"] = self.unavailable(
            fetch_funding.OkxFundingProvider(), status=503
        )
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertEqual(result["reference_funding"]["provider"], "bybit")
        self.assertTrue(any("OKX不可用" in warning for warning in result["warnings"]))
        self.assertTrue(any("回退到Bybit" in warning for warning in result["warnings"]))
        self.assertEqual(fetch_funding.determine_exit_code(result), 2)

    # 8-11. Classification is based on decimal 8h-equivalent values.
    def test_negative_funding_classification(self):
        self.assertEqual(fetch_funding.classify_funding(-0.000001), "negative")

    def test_healthy_funding_classification(self):
        self.assertEqual(
            fetch_funding.classify_funding(0.0001), "neutral_or_healthy"
        )

    def test_caution_funding_classification(self):
        self.assertEqual(fetch_funding.classify_funding(0.0004), "caution")

    def test_overheated_funding_classification(self):
        self.assertEqual(fetch_funding.classify_funding(0.0006), "overheated")

    # 12-13. Arbitrary reliable periods use the same linear normalization formula.
    def test_one_hour_rate_normalizes_to_eight_hours(self):
        self.assertAlmostEqual(fetch_funding.normalized_8h(0.00001, 1), 0.00008)

    def test_six_hour_rate_normalizes_to_eight_hours(self):
        self.assertAlmostEqual(fetch_funding.normalized_8h(0.00006, 6), 0.00008)

    # 14. A missing interval cannot silently become the common 8h convention.
    def test_unknown_interval_is_not_assumed_to_be_eight_hours(self):
        payloads = self.bybit_payloads(include_interval=False, history_rows=1)
        result = self.parse_bybit(payloads)

        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["current"]["interval_hours"])
        self.assertIsNone(result["normalized_8h"]["current_rate"])
        self.assertEqual(result["classification"], "unavailable")
        self.assertTrue(any("周期无法可靠确认" in item for item in result["warnings"]))

    # 15. Summary statistics are unweighted and exclude unavailable values.
    def test_simple_mean_min_max_and_spread_are_correct(self):
        result = fetch_funding.build_result(self.default_provider_results(), self.NOW)
        summary = result["summary"]

        self.assertEqual(summary["available_provider_count"], 3)
        self.assertEqual(summary["normalized_provider_count"], 3)
        self.assertAlmostEqual(summary["min_8h"], 0.00002)
        self.assertAlmostEqual(summary["max_8h"], 0.00006)
        self.assertAlmostEqual(summary["simple_mean_8h"], 0.00004)
        self.assertAlmostEqual(summary["spread_8h"], 0.00004)
        self.assertEqual(summary["mean_method"], "simple_unweighted_arithmetic_mean")
        self.assertFalse(summary["is_oi_weighted"])

    # 16-17. Consensus reports facts only, without an invented spread threshold.
    def test_direction_consensus_all_positive(self):
        result = fetch_funding.build_result(self.default_provider_results(), self.NOW)
        self.assertEqual(result["summary"]["direction_consensus"], "all_positive")

    def test_direction_consensus_mixed(self):
        providers = self.default_provider_results()
        providers["bybit"] = self.parse_bybit(
            self.bybit_payloads(current_rate=-0.00006)
        )
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertEqual(result["summary"]["direction_consensus"], "mixed")
        self.assertGreater(result["summary"]["spread_8h"], 0)

    def test_direction_consensus_all_negative_and_single_provider(self):
        providers = {
            "okx": self.parse_okx(self.okx_payloads(current_rate=-0.00004)),
            "bybit": self.parse_bybit(self.bybit_payloads(current_rate=-0.00006)),
            "binance": self.parse_binance(
                self.binance_payloads(current_rate=-0.00002)
            ),
        }
        negative = fetch_funding.build_result(providers, self.NOW)
        self.assertEqual(negative["summary"]["direction_consensus"], "all_negative")

        providers["bybit"] = self.unavailable(fetch_funding.BybitFundingProvider())
        providers["binance"] = self.unavailable(fetch_funding.BinanceFundingProvider())
        single = fetch_funding.build_result(providers, self.NOW)
        self.assertEqual(single["summary"]["direction_consensus"], "single_provider")

    def test_direction_consensus_is_unavailable_with_zero_normalized_providers(self):
        providers = {
            "okx": self.unavailable(fetch_funding.OkxFundingProvider()),
            "bybit": self.unavailable(fetch_funding.BybitFundingProvider()),
            "binance": self.unavailable(fetch_funding.BinanceFundingProvider()),
        }
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertEqual(result["summary"]["normalized_provider_count"], 0)
        self.assertEqual(
            result["summary"]["direction_consensus"],
            "unavailable",
            "零个可比较provider不能被描述为single_provider",
        )
        self.assertEqual(fetch_funding.determine_exit_code(result), 1)

    # 18. Explicit zero is valid; unavailable remains null rather than zero.
    def test_zero_is_valid_but_unavailable_never_becomes_zero(self):
        zero = self.parse_bybit(self.bybit_payloads(current_rate=0.0))
        unavailable = self.unavailable(fetch_funding.BinanceFundingProvider())

        self.assertEqual(zero["normalized_8h"]["current_rate"], 0.0)
        self.assertEqual(zero["classification"], "neutral_or_healthy")
        self.assertIsNone(unavailable["current"]["rate"])
        self.assertIsNone(unavailable["normalized_8h"]["current_rate"])

    def test_reference_priority_skips_provider_without_reliable_interval(self):
        unknown_okx = self.okx_payloads()
        unknown_okx["current"]["data"][0]["nextFundingTime"] = ""
        unknown_okx["history"]["data"] = unknown_okx["history"]["data"][:1]
        providers = self.default_provider_results()
        providers["okx"] = self.parse_okx(unknown_okx)
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertEqual(result["providers"]["okx"]["status"], "available")
        self.assertIsNone(result["providers"]["okx"]["normalized_8h"]["current_rate"])
        self.assertEqual(result["reference_funding"]["provider"], "bybit")
        self.assertIn("okx", result["quality_checks"]["providers_without_reliable_interval"])
        self.assertEqual(fetch_funding.determine_exit_code(result), 2)

    def test_provider_business_errors_are_isolated_despite_http_success(self):
        okx_payloads = self.okx_payloads()
        okx_payloads["current"] = {
            "code": "51001",
            "msg": "Instrument ID does not exist",
            "data": [],
        }
        bybit_payloads = self.bybit_payloads()
        bybit_payloads["current"] = {
            "retCode": 10001,
            "retMsg": "params error: Symbol Invalid",
            "result": {"category": "linear", "list": []},
            "retExtInfo": {},
            "time": unix_ms(self.NOW),
        }

        okx = self.parse_okx(okx_payloads)
        bybit = self.parse_bybit(bybit_payloads)

        self.assertEqual(okx["status"], "unavailable")
        self.assertIn("51001", okx["reason"])
        self.assertEqual(bybit["status"], "unavailable")
        self.assertIn("10001", bybit["reason"])

    def test_nonfinite_and_wrong_symbol_payloads_are_unavailable(self):
        bybit_payloads = self.bybit_payloads()
        bybit_payloads["current"]["result"]["list"][0]["fundingRate"] = "NaN"
        binance_payloads = self.binance_payloads()
        binance_payloads["current"]["symbol"] = "ETHUSDT"

        self.assertEqual(self.parse_bybit(bybit_payloads)["status"], "unavailable")
        self.assertEqual(self.parse_binance(binance_payloads)["status"], "unavailable")

    def test_stale_funding_times_generate_quality_warning_and_exit_two(self):
        payloads = self.okx_payloads()
        stale_funding = self.NOW - timedelta(hours=8)
        payloads["current"]["data"][0]["fundingTime"] = str(unix_ms(stale_funding))
        payloads["current"]["data"][0]["nextFundingTime"] = str(
            unix_ms(stale_funding + timedelta(hours=8))
        )
        providers = self.default_provider_results()
        providers["okx"] = self.parse_okx(payloads)
        result = fetch_funding.build_result(providers, self.NOW)

        self.assertTrue(
            any("明显过去" in warning for warning in providers["okx"]["warnings"])
        )
        self.assertEqual(fetch_funding.determine_exit_code(result), 2)

    # 19-20. Machine JSON and human Markdown are both generated offline.
    def test_json_and_markdown_outputs_are_generated(self):
        result = fetch_funding.build_result(self.default_provider_results(), self.NOW)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            json_path, markdown_path, markdown = fetch_funding.write_outputs(
                result, output_dir
            )
            loaded = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded["source_mode"], "exchange_public_apis")
            self.assertEqual(loaded["reference_funding"]["provider"], "okx")
            self.assertAlmostEqual(
                loaded["providers"]["okx"]["previous_settled"]["rate"],
                0.00002,
            )
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), markdown)

    def test_markdown_shows_unavailable_provider_and_coinglass_upgrade_note(self):
        providers = self.default_provider_results()
        providers["binance"] = self.unavailable(
            fetch_funding.BinanceFundingProvider(), status=451
        )
        markdown = fetch_funding.render_markdown(
            fetch_funding.build_result(providers, self.NOW)
        )

        self.assertIn("## BTC 永续合约 Funding", markdown)
        self.assertIn("### 参考 Funding", markdown)
        self.assertIn("| OKX |", markdown)
        self.assertIn("| Bybit |", markdown)
        self.assertIn("| Binance | unavailable", markdown)
        self.assertIn("当前简单平均不是持仓量加权 Funding", markdown)
        self.assertIn("CoinGlass OI-weighted Funding", markdown)
        self.assertIn("交叉验证和 fallback", markdown)

    # 21-22. Five-minute cache and request metadata are verified without network.
    def test_cache_hit_expiry_refresh_and_request_metadata(self):
        response_map = self.default_response_map()
        calls = []
        transport = self.transport_from(response_map, calls)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            first, _markdown, first_code = fetch_funding.execute(
                refresh=True,
                output_dir=output_dir,
                now=self.NOW,
                transport=transport,
            )
            self.assertEqual(first_code, 0)
            self.assertEqual(len(calls), 7)
            self.assertEqual(first["network_request_count"], 7)
            for key, provider in first["providers"].items():
                self.assertFalse(provider["cache_status"]["used_cache"], key)
                self.assertEqual(provider["fetched_at_utc"], "2026-08-01T15:45:00Z")
                self.assertEqual(
                    {item["url"] for item in provider["requests"]},
                    set(provider["urls"].values()),
                )
                for request in provider["requests"]:
                    self.assertEqual(request["http_status"], 200)
                    self.assertEqual(request["cache_status"], "网络获取")
                    self.assertEqual(request["fetched_at_utc"], "2026-08-01T15:45:00Z")

            okx_cache = output_dir / "cache" / "okx_raw.json"
            envelope = json.loads(okx_cache.read_text(encoding="utf-8"))
            self.assertEqual(envelope["urls"], fetch_funding.OkxFundingProvider().endpoints)
            self.assertEqual(envelope["payloads"]["current"], response_map[fetch_funding.OKX_CURRENT_URL])
            self.assertEqual(envelope["requests"][0]["http_status"], 200)

            calls.clear()
            cached, _markdown, cached_code = fetch_funding.execute(
                refresh=False,
                output_dir=output_dir,
                now=self.NOW + timedelta(minutes=4, seconds=59),
                transport=transport,
            )
            self.assertEqual(cached_code, 0)
            self.assertEqual(calls, [])
            self.assertEqual(cached["network_request_count"], 0)
            for provider in cached["providers"].values():
                self.assertTrue(provider["cache_status"]["used_cache"])
                self.assertTrue(
                    all(
                        request["cache_status"] == "使用缓存"
                        for request in provider["requests"]
                    )
                )

            calls.clear()
            expired, _markdown, expired_code = fetch_funding.execute(
                refresh=False,
                output_dir=output_dir,
                now=self.NOW + timedelta(minutes=5),
                transport=transport,
            )
            self.assertEqual(expired_code, 0)
            self.assertEqual(len(calls), 7)
            self.assertEqual(expired["network_request_count"], 7)

            calls.clear()
            refreshed, _markdown, refresh_code = fetch_funding.execute(
                refresh=True,
                output_dir=output_dir,
                now=self.NOW + timedelta(minutes=5, seconds=1),
                transport=transport,
            )
            self.assertEqual(refresh_code, 0)
            self.assertEqual(len(calls), 7)
            self.assertEqual(refreshed["network_request_count"], 7)

    def test_binance_451_is_negative_cached_for_five_minutes(self):
        response_map = self.default_response_map()
        failures = {
            url: {"status": 451, "reason": "Unavailable For Legal Reasons"}
            for url in fetch_funding.BinanceFundingProvider().endpoints.values()
        }
        calls = []
        transport = self.transport_from(response_map, calls, failures)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            first, _markdown, first_code = fetch_funding.execute(
                refresh=True,
                output_dir=output_dir,
                now=self.NOW,
                transport=transport,
            )
            self.assertNotEqual(first_code, 1)
            # OKX and Bybit use two requests each. Once Binance current is
            # blocked, its non-core history/info endpoints are deliberately
            # not requested, so the first pass performs five requests total.
            self.assertEqual(len(calls), 5)
            binance = first["providers"]["binance"]
            self.assertEqual(binance["status"], "unavailable")
            self.assertIn("451", binance["reason"])
            current_request = binance["requests"][0]
            self.assertEqual(current_request["http_status"], 451)
            self.assertEqual(current_request["cache_status"], "网络获取")
            self.assertTrue(
                all(
                    item["http_status"] is None
                    and item["cache_status"] == "未请求"
                    for item in binance["requests"][1:]
                )
            )

            calls.clear()
            second, _markdown, second_code = fetch_funding.execute(
                refresh=False,
                output_dir=output_dir,
                now=self.NOW + timedelta(minutes=1),
                transport=transport,
            )
            self.assertNotEqual(second_code, 1)
            self.assertEqual(calls, [])
            self.assertEqual(second["network_request_count"], 0)
            self.assertTrue(second["providers"]["binance"]["cache_status"]["used_cache"])
            self.assertTrue(
                all(
                    item["cache_status"] == "使用缓存"
                    for item in second["providers"]["binance"]["requests"]
                )
            )

    def test_missing_coinglass_key_is_not_a_warning_or_dependency(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            providers = fetch_funding.select_exchange_public_providers()
            self.assertEqual([item.key for item in providers], ["okx", "bybit", "binance"])
            result = fetch_funding.build_result(self.default_provider_results(), self.NOW)

        self.assertEqual(result["warnings"], [])
        self.assertNotIn("COINGLASS_API_KEY", json.dumps(result, ensure_ascii=False))
        self.assertEqual(fetch_funding.determine_exit_code(result), 0)


if __name__ == "__main__":
    unittest.main()
