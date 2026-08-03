import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import fetch_ahr999
from scripts import fetch_asopr
from scripts import fetch_cvdd
from scripts import fetch_etf_flow
from scripts import fetch_funding
from scripts import fetch_mvrv
from scripts import fetch_ssr
from scripts import prepare_daily_brief


class PrepareDailyBriefTests(unittest.TestCase):
    AS_OF = date(2026, 7, 30)
    NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    GENERATED_AT = "2026-07-30T12:05:00Z"

    MODULE_ORDER = (
        "cvdd",
        "etf",
        "ahr999",
        "funding",
        "mvrv",
        "asopr",
        "ssr",
    )
    MODULE_SECTIONS = {
        "cvdd": "cvdd_bgeometrics",
        "etf": "etf_flow",
        "ahr999": "ahr999",
        "funding": "funding",
        "mvrv": "mvrv",
        "asopr": "asopr",
        "ssr": "ssr",
    }
    MODULE_LOADERS = {
        "cvdd": "load_cvdd_output",
        "etf": "load_etf_output",
        "ahr999": "load_ahr999_output",
        "funding": "load_funding_output",
        "mvrv": "load_mvrv_output",
        "asopr": "load_asopr_output",
        "ssr": "load_ssr_output",
    }

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

    def make_ahr999_output(self, missing_dates=None, requested_as_of=None):
        missing_dates = {value.isoformat() for value in (missing_dates or set())}
        start = self.AS_OF - timedelta(days=40)
        payload = []
        for offset in range(41):
            current = start + timedelta(days=offset)
            if current.isoformat() in missing_dates:
                continue
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
        return fetch_ahr999.build_result(
            payload,
            requested_as_of or self.AS_OF,
            generated_at=self.NOW,
            fetched_at=self.NOW,
            cache_status={
                "used_cache": True,
                "status": "使用缓存",
                "reason": "离线测试fixture",
                "cache_file": "/tmp/fixture-ahr999.json",
            },
            network_request_count=0,
        )

    def make_funding_provider(
        self,
        provider,
        *,
        current_rate,
        previous_rate,
        interval_hours=8.0,
    ):
        fetched_at = fetch_funding.format_utc_timestamp(self.NOW)
        current = {
            "rate": current_rate,
            "type": "estimated" if provider.key == "okx" else "current",
            "funding_time": "2026-07-30T16:00:00Z",
            "next_funding_time": "2026-07-31T00:00:00Z",
            "interval_hours": interval_hours,
            "interval_source": "fixture_explicit_interval",
        }
        previous = {
            "rate": previous_rate,
            "funding_time": "2026-07-30T08:00:00Z",
            "interval_hours": interval_hours,
            "interval_source": "fixture_history_interval",
        }
        requests = [
            {
                "endpoint": endpoint,
                "url": url,
                "http_status": 200,
                "fetched_at_utc": fetched_at,
                "cache_status": "使用缓存",
                "error": None,
            }
            for endpoint, url in provider.endpoints.items()
        ]
        return {
            "provider": provider.key,
            "provider_name": provider.display_name,
            "status": "available",
            "reason": None,
            "symbol": provider.symbol,
            "fetched_at_utc": fetched_at,
            "urls": dict(provider.endpoints),
            "requests": requests,
            "cache_status": {
                "used_cache": True,
                "status": "使用缓存",
                "reason": "离线测试fixture",
                "cache_file": f"/tmp/fixture-{provider.key}-funding.json",
            },
            "network_request_count": 0,
            "current": current,
            "previous_settled": previous,
            "normalized_8h": {
                "current_rate": fetch_funding.normalized_8h(
                    current_rate, interval_hours
                ),
                "previous_settled_rate": fetch_funding.normalized_8h(
                    previous_rate, interval_hours
                ),
            },
            "classification": fetch_funding.classify_funding(
                fetch_funding.normalized_8h(current_rate, interval_hours)
            ),
            "warnings": [],
        }

    def make_funding_unavailable_provider(self, provider, reason="fixture unavailable"):
        fetched_at = fetch_funding.format_utc_timestamp(self.NOW)
        requests = [
            {
                "endpoint": endpoint,
                "url": url,
                "http_status": 451 if endpoint == "current" else None,
                "fetched_at_utc": fetched_at,
                "cache_status": "网络请求失败",
                "error": reason,
            }
            for endpoint, url in provider.endpoints.items()
        ]
        bundle = fetch_funding.ProviderBundle(
            payloads={},
            requests=requests,
            errors={"current": {"reason": reason, "http_status": 451}},
            fetched_at=self.NOW,
            cache_status={
                "used_cache": False,
                "status": "网络请求失败",
                "reason": reason,
                "cache_file": f"/tmp/fixture-{provider.key}-funding.json",
            },
            network_request_count=0,
        )
        return provider.unavailable_result(bundle, reason)

    def make_funding_output(self, *, binance_available=True):
        providers = {
            "okx": self.make_funding_provider(
                fetch_funding.OkxFundingProvider(),
                current_rate=0.0001,
                previous_rate=0.00008,
            ),
            "bybit": self.make_funding_provider(
                fetch_funding.BybitFundingProvider(),
                current_rate=0.0002,
                previous_rate=0.00015,
            ),
        }
        binance_provider = fetch_funding.BinanceFundingProvider()
        providers["binance"] = (
            self.make_funding_provider(
                binance_provider,
                current_rate=-0.00005,
                previous_rate=0.00003,
            )
            if binance_available
            else self.make_funding_unavailable_provider(
                binance_provider, "HTTP 451 fixture region restriction"
            )
        )
        return fetch_funding.build_result(providers, generated_at=self.NOW)

    @staticmethod
    def make_mvrv_point(target_date, value):
        return {
            "available": value is not None,
            "target_date": target_date.isoformat(),
            "actual_date": target_date.isoformat() if value is not None else None,
            "value": value,
            "fallback_days": 0 if value is not None else None,
        }

    @staticmethod
    def make_mvrv_change(current, historical):
        if current is None or historical is None:
            return {
                "absolute_change": None,
                "percentage_change": None,
                "percent": None,
                "direction": "unavailable",
            }
        absolute = current - historical
        percentage = current / historical - 1
        return {
            "absolute_change": absolute,
            "percentage_change": percentage,
            "percent": percentage * 100,
            "direction": "上升" if absolute > 0 else "下降" if absolute < 0 else "不变",
        }

    def make_mvrv_metric(self, current, values, *, sth=False):
        metric_date = self.AS_OF
        previous = self.make_mvrv_point(metric_date - timedelta(days=1), values[0])
        d7 = self.make_mvrv_point(metric_date - timedelta(days=7), values[1])
        d30 = self.make_mvrv_point(metric_date - timedelta(days=30), values[2])
        metric = {
            "status": "Verified",
            "current": current,
            "date": metric_date.isoformat(),
            "previous": previous,
            "d7": d7,
            "d30": d30,
            "change_1d": self.make_mvrv_change(current, values[0]),
            "change_7d": self.make_mvrv_change(current, values[1]),
            "change_30d": self.make_mvrv_change(current, values[2]),
        }
        if sth:
            metric["distance_from_1"] = current - 1
        return metric

    @staticmethod
    def make_mvrv_quality(metric_date):
        window_start = metric_date - timedelta(
            days=fetch_mvrv.QUALITY_WINDOW_DAYS - 1
        )
        return {
            "window_start": window_start.isoformat(),
            "window_end": metric_date.isoformat(),
            "expected_date_count": fetch_mvrv.QUALITY_WINDOW_DAYS,
            "record_count": fetch_mvrv.QUALITY_WINDOW_DAYS,
            "unique_date_count": fetch_mvrv.QUALITY_WINDOW_DAYS,
            "valid_record_count": fetch_mvrv.QUALITY_WINDOW_DAYS,
            "missing_dates": [],
            "duplicate_dates": [],
            "invalid_values": [],
            "latest_date": metric_date.isoformat(),
            "sort_order": "ascending",
            "is_complete": True,
        }

    def make_mvrv_output(self, *, sth_available=True, warnings=None):
        mvrv = self.make_mvrv_metric(1.24, (1.22, 1.18, 1.10))
        if sth_available:
            sth_mvrv = self.make_mvrv_metric(
                0.96, (0.95, 0.91, 0.88), sth=True
            )
            date_alignment = "aligned"
        else:
            sth_mvrv = {
                "status": "Unavailable",
                "current": None,
                "date": None,
                "previous": self.make_mvrv_point(
                    self.AS_OF - timedelta(days=1), None
                ),
                "d7": self.make_mvrv_point(self.AS_OF - timedelta(days=7), None),
                "d30": self.make_mvrv_point(
                    self.AS_OF - timedelta(days=30), None
                ),
                "change_1d": self.make_mvrv_change(None, None),
                "change_7d": self.make_mvrv_change(None, None),
                "change_30d": self.make_mvrv_change(None, None),
                "distance_from_1": None,
            }
            date_alignment = "unavailable"
        warning_list = list(warnings or [])
        return {
            "generated_at_utc": self.GENERATED_AT,
            "requested_as_of": self.AS_OF.isoformat(),
            "actual_latest_date": self.AS_OF.isoformat(),
            "data_delay_days": 0,
            "date_alignment": date_alignment,
            "source": {
                "provider": "BGeometrics",
                "urls": [
                    fetch_mvrv.MVRV_ENDPOINT,
                    fetch_mvrv.STH_MVRV_ENDPOINT,
                ],
                "endpoints": {
                    "mvrv": {
                        "endpoint": fetch_mvrv.MVRV_ENDPOINT,
                        "request_url": fetch_mvrv.MVRV_ENDPOINT + "?fixture=1",
                        "value_field": "mvrv",
                        "http_status": 200,
                        "fetched_at_utc": self.GENERATED_AT,
                        "cache_status": "使用缓存",
                    },
                    "sth_mvrv": {
                        "endpoint": fetch_mvrv.STH_MVRV_ENDPOINT,
                        "request_url": fetch_mvrv.STH_MVRV_ENDPOINT + "?fixture=1",
                        "value_field": "sthMvrv",
                        "http_status": 200 if sth_available else None,
                        "fetched_at_utc": self.GENERATED_AT,
                        "cache_status": (
                            "使用缓存" if sth_available else "不可用"
                        ),
                    },
                },
                "fetched_at_utc": self.GENERATED_AT,
            },
            "cache_status": {
                "used_cache": True,
                "status": "使用缓存",
            },
            "network_request_count": 0,
            "mvrv": mvrv,
            "sth_mvrv": sth_mvrv,
            "quality_checks": {
                "mvrv": self.make_mvrv_quality(self.AS_OF),
                "sth_mvrv": (
                    self.make_mvrv_quality(self.AS_OF)
                    if sth_available
                    else None
                ),
                "latest_date": self.AS_OF.isoformat(),
                "date_alignment": date_alignment,
            },
            "warnings": warning_list,
        }

    def make_asopr_output(self, *, warnings=None, status="verified"):
        value = 1.0107
        seven_day_value = 1.0035857142857143
        warning_list = list(warnings or [])
        return {
            "generated_at_utc": self.GENERATED_AT,
            "requested_as_of": self.AS_OF.isoformat(),
            "metric": "asopr",
            "status": status,
            "actual_latest_date": self.AS_OF.isoformat(),
            "data_delay_days": 0,
            "asopr": {
                "metric": "asopr",
                "status": status,
                "value": value,
                "data_date": self.AS_OF.isoformat(),
                "source": "BGeometrics / Bitcoin Data API",
                "source_url": fetch_asopr.ASOPR_ENDPOINT,
                "fetched_at": self.GENERATED_AT,
                "freshness": {"delay_days": 0, "is_fresh": True},
                "warning": warning_list,
                "reason": None,
            },
            "asopr_7dma": {
                "metric": "asopr_7dma",
                "status": status,
                "value": seven_day_value,
                "data_date": self.AS_OF.isoformat(),
                "source": "BGeometrics / Bitcoin Data API",
                "source_url": fetch_asopr.ASOPR_ENDPOINT,
                "method": "derived_arithmetic_mean_7_consecutive_calendar_days",
                "window_start_date": (self.AS_OF - timedelta(days=6)).isoformat(),
                "window_end_date": self.AS_OF.isoformat(),
                "observation_count": 7,
                "warning": warning_list[0] if warning_list else None,
                "reason": None,
            },
            "source": {
                "provider": "BGeometrics",
                "endpoint": fetch_asopr.ASOPR_ENDPOINT,
                "request_url": fetch_asopr.ASOPR_ENDPOINT + "?fixture=1",
                "value_field": "asopr",
                "definition": "Adjusted SOPR; spent outputs have lifespan greater than one hour.",
                "fetched_at_utc": self.GENERATED_AT,
                "http_status": 200,
                "requires_api_key": False,
            },
            "cache_status": {
                "used_cache": True,
                "status": "使用缓存",
                "reason": "离线测试fixture",
                "cache_file": "/tmp/fixture-asopr.json",
                "ttl_hours": 6,
                "fetched_at_utc": self.GENERATED_AT,
                "age_seconds": 300,
            },
            "network_request_count": 0,
            "quality_checks": {
                "window_start": (
                    self.AS_OF - timedelta(days=fetch_asopr.QUALITY_WINDOW_DAYS - 1)
                ).isoformat(),
                "window_end": self.AS_OF.isoformat(),
                "expected_date_count": fetch_asopr.QUALITY_WINDOW_DAYS,
                "record_count": fetch_asopr.QUALITY_WINDOW_DAYS,
                "unique_date_count": fetch_asopr.QUALITY_WINDOW_DAYS,
                "valid_date_count": fetch_asopr.QUALITY_WINDOW_DAYS,
                "missing_dates": [],
                "duplicate_dates": [],
                "canonicalized_exact_duplicate_dates": [],
                "conflicting_duplicate_dates": [],
                "invalid_values": [],
                "source_sort_order": "ascending",
                "latest_date": self.AS_OF.isoformat(),
                "seven_day_window_complete": True,
                "is_complete": True,
            },
            "warnings": warning_list,
        }

    def make_ssr_output(
        self,
        *,
        requested_as_of=None,
        raw_available=True,
        missing_oscillator_dates=None,
    ):
        analysis_date = requested_as_of or self.AS_OF
        missing = {
            item.isoformat() for item in (missing_oscillator_dates or set())
        }
        start = analysis_date - timedelta(days=40)
        oscillator_payload = []
        raw_payload = []
        for offset in range(41):
            current = start + timedelta(days=offset)
            oscillator_value = round(1.10 + offset * 0.0025, 12)
            raw_value = round(6.0 + offset * 0.01, 12)
            if current.isoformat() not in missing:
                oscillator_payload.append(
                    {
                        "d": current.isoformat(),
                        "ssrStablecoinOscillator": oscillator_value,
                    }
                )
            raw_payload.append(
                {"d": current.isoformat(), "ssrStablecoin": raw_value}
            )
        statuses = {
            key: {
                "used_cache": True,
                "status": "使用缓存",
                "reason": "离线测试fixture",
                "cache_file": f"/tmp/fixture-{key}.json",
                "endpoint": endpoint,
                "request_url": endpoint + "?fixture=1",
                "http_status": 200 if key == "ssr_oscillator" or raw_available else None,
                "fetched_at_utc": self.GENERATED_AT,
            }
            for key, endpoint in {
                "ssr_oscillator": fetch_ssr.SSR_OSCILLATOR_ENDPOINT,
                "raw_ssr": fetch_ssr.RAW_SSR_ENDPOINT,
            }.items()
        }
        if not raw_available:
            statuses["raw_ssr"]["used_cache"] = False
            statuses["raw_ssr"]["status"] = "不可用"
            statuses["raw_ssr"]["reason"] = "fixture raw unavailable"
        return fetch_ssr.build_result(
            oscillator_payload,
            raw_payload if raw_available else None,
            analysis_date,
            generated_at=self.NOW,
            cache_statuses=statuses,
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

    def write_ahr999_fixture(self, directory, payload):
        path = Path(directory) / "ahr999_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def write_funding_fixture(self, directory, payload):
        path = Path(directory) / "funding_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def write_mvrv_fixture(self, directory, payload):
        path = Path(directory) / "mvrv_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def write_asopr_fixture(self, directory, payload):
        path = Path(directory) / "asopr_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def write_ssr_fixture(self, directory, payload):
        path = Path(directory) / "ssr_latest.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def write_all_module_fixtures(self, directory, *, stale_modules=None):
        stale_modules = set(stale_modules or ())
        payloads = {
            "cvdd": self.make_cvdd_output(),
            "etf": self.make_etf_output(),
            "ahr999": self.make_ahr999_output(),
            "funding": self.make_funding_output(),
            "mvrv": self.make_mvrv_output(),
            "asopr": self.make_asopr_output(),
            "ssr": self.make_ssr_output(),
        }
        writers = {
            "cvdd": self.write_cvdd_fixture,
            "etf": self.write_etf_fixture,
            "ahr999": self.write_ahr999_fixture,
            "funding": self.write_funding_fixture,
            "mvrv": self.write_mvrv_fixture,
            "asopr": self.write_asopr_fixture,
            "ssr": self.write_ssr_fixture,
        }
        argument_names = {
            "cvdd": "cvdd_json_path",
            "etf": "etf_json_path",
            "ahr999": "ahr999_json_path",
            "funding": "funding_json_path",
            "mvrv": "mvrv_json_path",
            "asopr": "asopr_json_path",
            "ssr": "ssr_json_path",
        }
        paths = {}
        for module in self.MODULE_ORDER:
            payload = (
                {"stale_marker": f"STALE_{module.upper()}_MUST_NOT_BE_READ"}
                if module in stale_modules
                else payloads[module]
            )
            paths[argument_names[module]] = writers[module](directory, payload)
        return paths

    def make_tracking_invokers(
        self,
        *,
        failed_modules=None,
        exception_modules=None,
    ):
        failed_modules = set(failed_modules or ())
        exception_modules = set(exception_modules or ())
        calls = []
        invokers = {}

        for module in self.MODULE_ORDER:
            def invoke(as_of, refresh, *, module=module):
                calls.append((module, as_of, refresh))
                if module in exception_modules:
                    raise OSError(f"fixture {module} provider exception")
                if module in failed_modules:
                    return prepare_daily_brief.CvddInvocation(
                        1,
                        stderr=f"fixture {module} provider failure",
                    )
                return prepare_daily_brief.CvddInvocation(
                    2 if module == "ssr" else 0
                )

            invokers[f"{module}_invoker"] = invoke
        return invokers, calls

    def run_failsoft_scenario(
        self,
        directory,
        *,
        failed_modules=None,
        exception_modules=None,
        patch_failed_loaders=True,
    ):
        failed_modules = set(failed_modules or ())
        exception_modules = set(exception_modules or ())
        unavailable_modules = failed_modules | exception_modules
        fixture_paths = self.write_all_module_fixtures(
            directory,
            stale_modules=unavailable_modules,
        )
        invokers, calls = self.make_tracking_invokers(
            failed_modules=failed_modules,
            exception_modules=exception_modules,
        )
        output_dir = Path(directory) / "context"
        stdout = io.StringIO()
        stderr = io.StringIO()
        loader_mocks = {}

        with contextlib.ExitStack() as stack:
            if patch_failed_loaders:
                for module in unavailable_modules:
                    loader_mocks[module] = stack.enter_context(
                        mock.patch.object(
                            prepare_daily_brief,
                            self.MODULE_LOADERS[module],
                            side_effect=AssertionError(
                                f"stale {module} output was read"
                            ),
                        )
                    )
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            exit_code = self.execute_preparation(
                as_of=self.AS_OF.isoformat(),
                refresh=False,
                copy_requested=False,
                output_dir=output_dir,
                generated_at_utc=self.GENERATED_AT,
                **fixture_paths,
                **invokers,
            )

        loaded = None
        latest_path = output_dir / "latest.json"
        if latest_path.exists():
            loaded = json.loads(latest_path.read_text(encoding="utf-8"))
        return {
            "exit_code": exit_code,
            "context": loaded,
            "calls": calls,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "output_dir": output_dir,
            "loader_mocks": loader_mocks,
        }

    def assert_unavailable_section(self, context, module, reason_fragment):
        section = context["sections"][self.MODULE_SECTIONS[module]]
        self.assertEqual(section["metric"], module)
        self.assertEqual(section["status"], "unavailable")
        self.assertEqual(section["availability"], "unavailable")
        self.assertIsNone(section["value"])
        self.assertIsNone(section["data_date"])
        self.assertEqual(section["requested_as_of"], self.AS_OF.isoformat())
        self.assertIn("source", section)
        self.assertTrue(section["source"])
        self.assertIn(reason_fragment, section["reason"])
        warning = section.get("warning") or section.get("warnings")
        self.assertTrue(warning)
        self.assertIn("fetched_at", section)

    def assert_normal_section_value_is_preserved(self, context, module):
        section = context["sections"][self.MODULE_SECTIONS[module]]
        if module == "cvdd":
            self.assertGreater(section["cvdd"]["current"]["value"], 0)
        elif module == "etf":
            self.assertEqual(section["flows"]["latest"], -265.4)
        elif module == "ahr999":
            self.assertEqual(section["current"]["ahr999"], 0.9)
        elif module == "funding":
            self.assertEqual(section["reference_funding"]["provider"], "okx")
        elif module == "mvrv":
            self.assertEqual(section["mvrv"]["current"], 1.24)
        elif module == "asopr":
            self.assertEqual(section["asopr"]["value"], 1.0107)
        elif module == "ssr":
            self.assertEqual(section["ssr_oscillator"]["current"], 1.2)

    def assert_metric_exit_one_is_failsoft(self, module):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_failsoft_scenario(
                directory,
                failed_modules={module},
            )

            self.assertEqual(result["exit_code"], 2)
            self.assertIsNotNone(result["context"])
            self.assertTrue((result["output_dir"] / "latest.json").exists())
            self.assertTrue((result["output_dir"] / "latest.md").exists())
            self.assertEqual(
                result["context"]["generated_at_utc"], self.GENERATED_AT
            )
            self.assertEqual(
                result["context"]["requested_as_of"], self.AS_OF.isoformat()
            )
            self.assertEqual(
                result["calls"],
                [
                    (item, self.AS_OF.isoformat(), False)
                    for item in self.MODULE_ORDER
                ],
            )
            result["loader_mocks"][module].assert_not_called()
            self.assertNotIn(
                f"STALE_{module.upper()}_MUST_NOT_BE_READ",
                json.dumps(result["context"], ensure_ascii=False),
            )
            self.assert_unavailable_section(
                result["context"],
                module,
                f"fixture {module} provider failure",
            )
            for normal_module in set(self.MODULE_ORDER) - {module}:
                self.assert_normal_section_value_is_preserved(
                    result["context"], normal_module
                )
            self.assertEqual(result["context"]["aggregate_status"], "provisional")
            self.assertEqual(result["context"]["aggregate_exit_code"], 2)
            self.assertEqual(
                result["context"]["data_quality"]["aggregate_exit_code"], 2
            )
            self.assertEqual(
                result["context"]["data_quality"]["module_exit_codes"][module],
                1,
            )
            self.assertIn("Daily Brief Context ready", result["stdout"])
            markdown = (result["output_dir"] / "latest.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("未取得可靠值", markdown)
            self.assertIn(f"fixture {module} provider failure", markdown)

    def build_context(
        self,
        payload=None,
        exit_code=0,
        *,
        etf_payload=None,
        etf_exit_code=0,
        ahr999_payload=None,
        ahr999_exit_code=0,
        funding_payload=None,
        funding_exit_code=0,
        mvrv_payload=None,
        mvrv_exit_code=0,
        asopr_payload=None,
        asopr_exit_code=0,
        ssr_payload=None,
        ssr_exit_code=2,
    ):
        return prepare_daily_brief.build_daily_brief_context(
            payload or self.make_cvdd_output(),
            etf_payload or self.make_etf_output(),
            ahr999_payload or self.make_ahr999_output(),
            funding_payload or self.make_funding_output(),
            mvrv_payload or self.make_mvrv_output(),
            asopr_payload or self.make_asopr_output(),
            ssr_payload or self.make_ssr_output(),
            cvdd_exit_code=exit_code,
            etf_exit_code=etf_exit_code,
            ahr999_exit_code=ahr999_exit_code,
            funding_exit_code=funding_exit_code,
            mvrv_exit_code=mvrv_exit_code,
            asopr_exit_code=asopr_exit_code,
            ssr_exit_code=ssr_exit_code,
            generated_at_utc=self.GENERATED_AT,
        )

    def execute_preparation(self, **kwargs):
        """为既有prepare测试注入稳定的离线aSOPR模块。"""

        if "asopr_invoker" not in kwargs:
            kwargs["asopr_invoker"] = (
                lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(0)
            )
        if "asopr_json_path" not in kwargs and kwargs.get("output_dir") is not None:
            fixture_dir = Path(kwargs["output_dir"]).parent
            kwargs["asopr_json_path"] = self.write_asopr_fixture(
                fixture_dir, self.make_asopr_output()
            )
        return prepare_daily_brief.execute_preparation(**kwargs)

    def execute_with_funding_payload(self, directory, funding_payload):
        cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
        etf_path = self.write_etf_fixture(directory, self.make_etf_output())
        ahr999_path = self.write_ahr999_fixture(
            directory, self.make_ahr999_output()
        )
        funding_path = self.write_funding_fixture(directory, funding_payload)
        mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
        ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
        output_dir = Path(directory) / "context"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = self.execute_preparation(
                as_of="2026-07-30",
                refresh=False,
                copy_requested=False,
                cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    2
                ),
                cvdd_json_path=cvdd_path,
                etf_json_path=etf_path,
                ahr999_json_path=ahr999_path,
                funding_json_path=funding_path,
                mvrv_json_path=mvrv_path,
                ssr_json_path=ssr_path,
                output_dir=output_dir,
                generated_at_utc=self.GENERATED_AT,
            )
        return exit_code, stderr.getvalue(), output_dir

    def execute_with_mvrv_payload(
        self, directory, mvrv_payload, *, mvrv_exit_code=0
    ):
        cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
        etf_path = self.write_etf_fixture(directory, self.make_etf_output())
        ahr999_path = self.write_ahr999_fixture(
            directory, self.make_ahr999_output()
        )
        funding_path = self.write_funding_fixture(
            directory, self.make_funding_output()
        )
        mvrv_path = self.write_mvrv_fixture(directory, mvrv_payload)
        ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
        output_dir = Path(directory) / "context"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = self.execute_preparation(
                as_of="2026-07-30",
                refresh=False,
                copy_requested=False,
                cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    0
                ),
                mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    mvrv_exit_code
                ),
                ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    2
                ),
                cvdd_json_path=cvdd_path,
                etf_json_path=etf_path,
                ahr999_json_path=ahr999_path,
                funding_json_path=funding_path,
                mvrv_json_path=mvrv_path,
                ssr_json_path=ssr_path,
                output_dir=output_dir,
                generated_at_utc=self.GENERATED_AT,
            )
        return exit_code, stdout.getvalue(), stderr.getvalue(), output_dir

    def execute_with_ssr_payload(
        self, directory, ssr_payload, *, ssr_exit_code=2
    ):
        cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
        etf_path = self.write_etf_fixture(directory, self.make_etf_output())
        ahr999_path = self.write_ahr999_fixture(
            directory, self.make_ahr999_output()
        )
        funding_path = self.write_funding_fixture(
            directory, self.make_funding_output()
        )
        mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
        ssr_path = self.write_ssr_fixture(directory, ssr_payload)
        output_dir = Path(directory) / "context"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = self.execute_preparation(
                as_of="2026-07-30",
                refresh=False,
                copy_requested=False,
                cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(0),
                etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(0),
                ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(0),
                funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(0),
                mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(0),
                ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                    ssr_exit_code
                ),
                cvdd_json_path=cvdd_path,
                etf_json_path=etf_path,
                ahr999_json_path=ahr999_path,
                funding_json_path=funding_path,
                mvrv_json_path=mvrv_path,
                ssr_json_path=ssr_path,
                output_dir=output_dir,
                generated_at_utc=self.GENERATED_AT,
            )
        return exit_code, stdout.getvalue(), stderr.getvalue(), output_dir

    def test_normal_cvdd_input_generates_json(self):
        context = self.build_context()

        with tempfile.TemporaryDirectory() as directory:
            json_path, markdown_path, _markdown = (
                prepare_daily_brief.write_context_outputs(context, Path(directory))
            )
            loaded = json.loads(json_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded["generated_at_utc"], self.GENERATED_AT)
            self.assertEqual(loaded["requested_as_of"], "2026-07-30")
            self.assertEqual(loaded["aggregate_status"], "provisional")
            self.assertEqual(loaded["aggregate_exit_code"], 2)
            self.assertIn("cvdd_bgeometrics", loaded["sections"])
            self.assertIn("etf_flow", loaded["sections"])
            self.assertIn("ahr999", loaded["sections"])
            self.assertIn("funding", loaded["sections"])
            self.assertIn("mvrv", loaded["sections"])
            self.assertIn("asopr", loaded["sections"])
            self.assertIn("ssr", loaded["sections"])
            section = loaded["sections"]["cvdd_bgeometrics"]
            self.assertGreater(section["cvdd"]["current"]["value"], 0)
            self.assertGreater(section["price_cvdd"]["current"]["value"], 0)
            self.assertEqual(
                loaded["sections"]["etf_flow"]["latest_complete_date"],
                "2026-07-30",
            )
            ahr999_section = loaded["sections"]["ahr999"]
            self.assertEqual(ahr999_section["actual_latest_date"], "2026-07-30")
            self.assertEqual(ahr999_section["current"]["ahr999"], 0.9)
            self.assertEqual(ahr999_section["current"]["quantile5y"], 0.4)
            self.assertEqual(ahr999_section["current"]["regime_code"], "dca")
            funding_section = loaded["sections"]["funding"]
            self.assertEqual(funding_section["temporal_scope"], "current_snapshot")
            self.assertEqual(funding_section["reference_funding"]["provider"], "okx")
            self.assertEqual(
                funding_section["reference_funding"]["normalized_8h"][
                    "current_rate"
                ],
                0.0001,
            )
            mvrv_section = loaded["sections"]["mvrv"]
            self.assertEqual(mvrv_section["mvrv"]["current"], 1.24)
            self.assertEqual(mvrv_section["sth_mvrv"]["current"], 0.96)
            self.assertEqual(mvrv_section["date_alignment"], "aligned")
            asopr_section = loaded["sections"]["asopr"]
            self.assertEqual(asopr_section["asopr"]["value"], 1.0107)
            self.assertEqual(asopr_section["asopr"]["data_date"], "2026-07-30")
            self.assertAlmostEqual(
                asopr_section["asopr_7dma"]["value"], 1.0035857142857143
            )
            self.assertEqual(
                asopr_section["asopr_7dma"]["method"],
                "derived_arithmetic_mean_7_consecutive_calendar_days",
            )
            ssr_section = loaded["sections"]["ssr"]
            self.assertAlmostEqual(
                ssr_section["ssr_oscillator"]["current"], 1.2
            )
            self.assertEqual(ssr_section["ssr_oscillator"]["status"], "Provisional")
            self.assertEqual(ssr_section["ssr_oscillator"]["direction"], "unavailable")
            self.assertFalse(
                ssr_section["definition"]["compatible_with_daily_brief_rules"]
            )
            self.assertTrue(markdown_path.exists())

    def test_normal_markdown_generation(self):
        context = self.build_context()
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertIn("# Daily Brief Data Context", markdown)
        self.assertIn("生成时间：2026-07-30T12:05:00Z", markdown)
        self.assertIn("分析截止日期：2026-07-30", markdown)
        self.assertIn("聚合状态：provisional", markdown)
        self.assertIn("聚合退出码：2", markdown)
        self.assertIn("## CVDD｜BGeometrics", markdown)
        self.assertIn("### 底部锚趋势", markdown)
        self.assertIn("### BTC距CVDD", markdown)
        self.assertIn("### 数据质量", markdown)
        self.assertIn("| 当前 |", markdown)
        self.assertIn("ETF Flow", markdown)
        self.assertIn("最近完整交易日", markdown)
        self.assertIn("-$265.4M", markdown)
        self.assertIn("## AHR999", markdown)
        self.assertIn("最新完整UTC日线：2026-07-30", markdown)
        self.assertIn("AHR999：0.900000", markdown)
        self.assertIn("5年历史分位：40.00%", markdown)
        self.assertIn("5年历史分位7D变化", markdown)
        self.assertIn("估值区间：定投区（dca）", markdown)
        self.assertIn("AHR999是长期估值辅助指标，不单独作为买卖信号。", markdown)
        self.assertIn("## BTC Funding", markdown)
        self.assertIn("Reference平台：OKX", markdown)
        self.assertIn("抓取时点的current_snapshot", markdown)
        self.assertIn("当前简单平均不是持仓量加权 Funding", markdown)
        self.assertIn("CoinGlass OI-weighted Funding", markdown)
        self.assertIn("## BTC MVRV", markdown)
        self.assertIn("### MVRV Ratio", markdown)
        self.assertIn("### STH-MVRV", markdown)
        self.assertIn("- 最新：1.2400", markdown)
        self.assertIn("- 最新：0.9600", markdown)
        self.assertIn("## BTC aSOPR", markdown)
        self.assertIn("- aSOPR：1.010700", markdown)
        self.assertIn("- aSOPR数据日期：2026-07-30", markdown)
        self.assertIn("- 7DMA：1.003586", markdown)
        self.assertIn("同一BGeometrics正式aSOPR日线", markdown)
        self.assertIn("未使用SOPR、STH-SOPR或其他proxy", markdown)

    def test_prepare_accepts_fetch_mvrv_standard_output(self):
        start = self.AS_OF - timedelta(days=40)
        mvrv_payload = []
        sth_payload = []
        for offset in range(41):
            current = start + timedelta(days=offset)
            mvrv_payload.append(
                {"d": current.isoformat(), "mvrv": 1.0 + offset * 0.01}
            )
            sth_payload.append(
                {"d": current.isoformat(), "sthMvrv": 0.8 + offset * 0.005}
            )
        output = fetch_mvrv.build_result(
            mvrv_payload,
            sth_payload,
            self.AS_OF,
            generated_at=self.NOW,
        )

        context = self.build_context(mvrv_payload=output)

        section = context["sections"]["mvrv"]
        self.assertEqual(section["mvrv"]["current"], 1.4)
        self.assertEqual(section["sth_mvrv"]["current"], 1.0)
        self.assertEqual(section["date_alignment"], "aligned")

    def test_prepare_accepts_fetch_asopr_standard_output(self):
        start = self.AS_OF - timedelta(days=40)
        payload = [
            {
                "d": (start + timedelta(days=offset)).isoformat(),
                "asopr": 0.98 + offset * 0.001,
            }
            for offset in range(41)
        ]
        output = fetch_asopr.build_result(
            payload,
            self.AS_OF,
            generated_at=self.NOW,
        )

        context = self.build_context(asopr_payload=output)

        section = context["sections"]["asopr"]
        self.assertAlmostEqual(section["asopr"]["value"], 1.02)
        self.assertEqual(section["asopr"]["data_date"], "2026-07-30")
        self.assertAlmostEqual(section["asopr_7dma"]["value"], 1.017)
        self.assertEqual(
            section["asopr_7dma"]["method"],
            "derived_arithmetic_mean_7_consecutive_calendar_days",
        )

    def test_asopr_7dma_unavailable_is_preserved_with_exit_two(self):
        start = self.AS_OF - timedelta(days=5)
        payload = [
            {
                "d": (start + timedelta(days=offset)).isoformat(),
                "asopr": 1.0 + offset * 0.001,
            }
            for offset in range(6)
        ]
        output = fetch_asopr.build_result(
            payload,
            self.AS_OF,
            generated_at=self.NOW,
        )

        context = self.build_context(asopr_payload=output, asopr_exit_code=2)

        section = context["sections"]["asopr"]
        self.assertEqual(section["asopr"]["data_date"], "2026-07-30")
        self.assertEqual(section["asopr_7dma"]["status"], "unavailable")
        self.assertIsNone(section["asopr_7dma"]["value"])
        self.assertIn("7DMA需要", section["asopr_7dma"]["reason"])
        self.assertEqual(context["data_quality"]["asopr_exit_code"], 2)

    def test_rejects_proxy_field_in_asopr_output(self):
        output = self.make_asopr_output()
        output["asopr"]["sopr"] = output["asopr"]["value"]

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "代用字段"
        ):
            self.build_context(asopr_payload=output)

    def test_mismatched_asopr_as_of_is_rejected_by_validator(self):
        output = self.make_asopr_output()
        output["requested_as_of"] = "2026-07-29"

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "aSOPR输出"
        ):
            self.build_context(asopr_payload=output)

    def test_asopr_exit_one_is_unavailable_without_reading_stale_json(self):
        self.assert_metric_exit_one_is_failsoft("asopr")

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
        ahr999_payload = self.make_ahr999_output()
        ahr999_payload["warnings"] = ["fixture AHR999 warning"]
        funding_payload = self.make_funding_output()
        funding_payload["warnings"] = ["fixture Funding warning"]
        mvrv_payload = self.make_mvrv_output(
            warnings=["fixture MVRV warning"]
        )
        asopr_payload = self.make_asopr_output(
            warnings=["fixture aSOPR warning"], status="provisional"
        )
        ssr_payload = self.make_ssr_output()

        context = self.build_context(
            payload,
            exit_code=2,
            etf_payload=etf_payload,
            etf_exit_code=2,
            ahr999_payload=ahr999_payload,
            ahr999_exit_code=2,
            funding_payload=funding_payload,
            funding_exit_code=2,
            mvrv_payload=mvrv_payload,
            mvrv_exit_code=2,
            asopr_payload=asopr_payload,
            asopr_exit_code=2,
            ssr_payload=ssr_payload,
            ssr_exit_code=2,
        )
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertCountEqual(
            context["warnings"],
            [
                "fixture CVDD warning",
                "fixture ETF warning",
                "fixture AHR999 warning",
                "fixture Funding warning",
                "fixture MVRV warning",
                "fixture aSOPR warning",
                *ssr_payload["warnings"],
            ],
        )
        self.assertIn("fixture CVDD warning", markdown)
        self.assertIn("fixture ETF warning", markdown)
        self.assertIn("fixture AHR999 warning", markdown)
        self.assertIn("fixture Funding warning", markdown)
        self.assertIn("fixture MVRV warning", markdown)
        self.assertIn("fixture aSOPR warning", markdown)

    def test_cvdd_exit_one_is_unavailable_and_later_modules_continue(self):
        self.assert_metric_exit_one_is_failsoft("cvdd")

    def test_cvdd_exit_two_still_generates_context_and_returns_two(self):
        payload = self.make_cvdd_output()
        payload["warnings"] = ["fixture quality warning"]
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, payload)
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
            ahr999_path = self.write_ahr999_fixture(
                directory, self.make_ahr999_output()
            )
            funding_path = self.write_funding_fixture(
                directory, self.make_funding_output()
            )
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())
            loaded = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertIn("fixture quality warning", loaded["warnings"])
            self.assertTrue(any("不兼容" in item for item in loaded["warnings"]))
            self.assertEqual(loaded["data_quality"]["cvdd_exit_code"], 2)
            self.assertIn("Daily Brief Context ready", stdout.getvalue())

    def test_etf_exit_one_is_unavailable_without_reading_stale_json(self):
        self.assert_metric_exit_one_is_failsoft("etf")

    def test_etf_exit_two_generates_context_and_aggregates_two(self):
        etf_payload = self.make_etf_output()
        etf_payload["warnings"] = ["fixture ETF provisional warning"]
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, etf_payload)
            ahr999_path = self.write_ahr999_fixture(
                directory, self.make_ahr999_output()
            )
            funding_path = self.write_funding_fixture(
                directory, self.make_funding_output()
            )
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            loaded = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertIn("etf_flow", loaded["sections"])
            self.assertIn("fixture ETF provisional warning", loaded["warnings"])
            self.assertEqual(loaded["data_quality"]["etf_exit_code"], 2)
            self.assertTrue((output_dir / "latest.md").exists())

    def test_ahr999_fallback_metadata_is_preserved(self):
        target = self.AS_OF - timedelta(days=7)
        ahr999_payload = self.make_ahr999_output(missing_dates={target})
        context = self.build_context(
            ahr999_payload=ahr999_payload,
            ahr999_exit_code=2,
        )
        section = context["sections"]["ahr999"]
        point = section["history"]["7d"]

        self.assertEqual(point["target_date"], "2026-07-23")
        self.assertEqual(point["actual_date"], "2026-07-22")
        self.assertEqual(point["fallback_days"], 1)
        self.assertIsNotNone(point["value"])
        self.assertEqual(
            section["changes"]["ahr999_7d"],
            ahr999_payload["changes"]["ahr999_7d"],
        )
        markdown = prepare_daily_brief.render_context_markdown(context)
        self.assertIn("7D: 2026-07-23 → 2026-07-22（回退1日）", markdown)

    def test_ahr999_exit_one_is_unavailable_without_reading_stale_json(self):
        self.assert_metric_exit_one_is_failsoft("ahr999")

    def test_ahr999_exit_two_generates_context_and_aggregates_two(self):
        ahr999_payload = self.make_ahr999_output()
        ahr999_payload["warnings"] = ["fixture AHR999 quality warning"]
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
            ahr999_path = self.write_ahr999_fixture(directory, ahr999_payload)
            funding_path = self.write_funding_fixture(
                directory, self.make_funding_output()
            )
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            loaded = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertIn("ahr999", loaded["sections"])
            self.assertIn("fixture AHR999 quality warning", loaded["warnings"])
            self.assertEqual(loaded["data_quality"]["ahr999_exit_code"], 2)
            self.assertEqual(loaded["data_quality"]["aggregate_exit_code"], 2)
            self.assertTrue((output_dir / "latest.md").exists())

    def test_ahr999_historical_quality_warning_stays_exit_two(self):
        ahr999_payload = self.make_ahr999_output()
        ahr999_payload["history"]["7d"]["close"] = -1.0
        ahr999_payload["warnings"] = ["fixture historical close quality warning"]

        context = self.build_context(
            ahr999_payload=ahr999_payload,
            ahr999_exit_code=2,
        )

        self.assertEqual(context["sections"]["ahr999"]["history"]["7d"]["close"], -1.0)
        self.assertIn("fixture historical close quality warning", context["warnings"])
        self.assertEqual(context["data_quality"]["aggregate_exit_code"], 2)

    def test_funding_exit_one_is_unavailable_without_reading_stale_json(self):
        self.assert_metric_exit_one_is_failsoft("funding")

    def test_funding_exit_two_generates_context_and_aggregates_two(self):
        funding_payload = self.make_funding_output()
        funding_payload["warnings"] = ["fixture Funding quality warning"]
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
            ahr999_path = self.write_ahr999_fixture(
                directory, self.make_ahr999_output()
            )
            funding_path = self.write_funding_fixture(directory, funding_payload)
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            loaded = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertIn("funding", loaded["sections"])
            self.assertIn("fixture Funding quality warning", loaded["warnings"])
            self.assertEqual(loaded["data_quality"]["funding_exit_code"], 2)
            self.assertEqual(loaded["data_quality"]["aggregate_exit_code"], 2)
            self.assertTrue((output_dir / "latest.md").exists())
            self.assertIn("Daily Brief Context ready", stdout.getvalue())

    def test_funding_unavailable_provider_is_not_converted_to_zero(self):
        funding_payload = self.make_funding_output(binance_available=False)
        context = self.build_context(funding_payload=funding_payload)
        provider = context["sections"]["funding"]["providers"]["binance"]

        self.assertEqual(provider["status"], "unavailable")
        self.assertIsNone(provider["current"]["rate"])
        self.assertIsNone(provider["normalized_8h"]["current_rate"])
        self.assertNotEqual(provider["current"]["rate"], 0)
        self.assertIn("HTTP 451", provider["reason"])
        markdown = prepare_daily_brief.render_context_markdown(context)
        self.assertIn("unavailable：HTTP 451 fixture region restriction", markdown)

    def test_funding_is_explicit_current_snapshot_with_coinglass_note(self):
        context = self.build_context()
        section = context["sections"]["funding"]
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertEqual(context["requested_as_of"], "2026-07-30")
        self.assertEqual(section["temporal_scope"], "current_snapshot")
        self.assertIsNone(section["data_date"])
        self.assertEqual(
            section["timestamp"],
            section["reference_funding"]["current"]["funding_time"],
        )
        self.assertEqual(section["source"]["provider"], "exchange_public_apis")
        self.assertIs(section["summary"]["is_oi_weighted"], False)
        self.assertIn("不是历史 --as-of 回放", markdown)
        self.assertIn(fetch_funding.COINGLASS_UPGRADE_NOTE, markdown)

    def test_funding_reference_missing_current_becomes_unavailable(self):
        funding_payload = self.make_funding_output()
        del funding_payload["reference_funding"]["current"]

        with tempfile.TemporaryDirectory() as directory:
            exit_code, stderr, output_dir = self.execute_with_funding_payload(
                directory, funding_payload
            )

            self.assertEqual(exit_code, 2)
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())
            context = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assert_unavailable_section(context, "funding", "reference")
            self.assertEqual(context["aggregate_exit_code"], 2)

    def test_funding_reference_mismatch_becomes_unavailable(self):
        funding_payload = self.make_funding_output()
        funding_payload["reference_funding"]["normalized_8h"][
            "current_rate"
        ] = 0.123456

        with tempfile.TemporaryDirectory() as directory:
            exit_code, stderr, output_dir = self.execute_with_funding_payload(
                directory, funding_payload
            )

            self.assertEqual(exit_code, 2)
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())
            context = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assert_unavailable_section(context, "funding", "reference")
            self.assertEqual(context["aggregate_exit_code"], 2)

    def test_mvrv_exit_one_is_unavailable_without_reading_stale_json(self):
        self.assert_metric_exit_one_is_failsoft("mvrv")

    def test_mvrv_exit_two_generates_context_and_aggregates_two(self):
        payload = self.make_mvrv_output(
            warnings=["fixture MVRV quality warning"]
        )
        with tempfile.TemporaryDirectory() as directory:
            exit_code, _stdout, _stderr, output_dir = self.execute_with_mvrv_payload(
                directory, payload, mvrv_exit_code=2
            )

            self.assertEqual(exit_code, 2)
            loaded = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertIn("mvrv", loaded["sections"])
            self.assertIn("fixture MVRV quality warning", loaded["warnings"])
            self.assertEqual(loaded["data_quality"]["mvrv_exit_code"], 2)
            self.assertEqual(loaded["data_quality"]["aggregate_exit_code"], 2)

    def test_mvrv_quality_checks_are_preserved_in_section(self):
        payload = self.make_mvrv_output()

        context = self.build_context(mvrv_payload=payload)

        self.assertEqual(
            context["sections"]["mvrv"]["quality_checks"],
            payload["quality_checks"],
        )

    def test_mvrv_quality_issue_cannot_pass_with_exit_zero(self):
        payload = self.make_mvrv_output()
        payload["quality_checks"]["mvrv"].update(
            {"missing_dates": ["2026-07-29"], "is_complete": False}
        )

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "质量问题.*exit 2"
        ):
            self.build_context(mvrv_payload=payload)

    def test_mvrv_quality_issue_requires_warning(self):
        payload = self.make_mvrv_output()
        payload["quality_checks"]["mvrv"].update(
            {"missing_dates": ["2026-07-29"], "is_complete": False}
        )

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "warnings为空"
        ):
            self.build_context(mvrv_payload=payload, mvrv_exit_code=2)

    def test_mvrv_quality_window_must_match_latest_date(self):
        payload = self.make_mvrv_output()
        payload["quality_checks"]["mvrv"]["window_end"] = "1999-01-01"

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "指标日期不一致"
        ):
            self.build_context(mvrv_payload=payload)

    def test_mvrv_quality_issue_arrays_are_required(self):
        payload = self.make_mvrv_output()
        payload["quality_checks"]["mvrv"].pop("invalid_values")

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "缺少字段.*invalid_values"
        ):
            self.build_context(mvrv_payload=payload)

    def test_unavailable_sth_mvrv_requires_warning(self):
        payload = self.make_mvrv_output(sth_available=False)

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "warnings为空"
        ):
            self.build_context(mvrv_payload=payload, mvrv_exit_code=2)

    def test_sth_mvrv_unavailable_stays_null_and_uses_no_proxy(self):
        payload = self.make_mvrv_output(
            sth_available=False,
            warnings=["未取得可靠STH-MVRV正式值"],
        )
        context = self.build_context(
            mvrv_payload=payload,
            mvrv_exit_code=2,
        )
        sth = context["sections"]["mvrv"]["sth_mvrv"]
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertEqual(sth["status"], "Unavailable")
        self.assertIsNone(sth["current"])
        self.assertIsNone(sth["date"])
        self.assertIsNone(sth["distance_from_1"])
        self.assertIsNone(sth["previous"]["value"])
        self.assertNotEqual(sth["current"], 0)
        self.assertNotIn("long_short_diff", json.dumps(context))
        self.assertIn("未取得可靠STH-MVRV正式值", markdown)
        self.assertIn("未使用任何代用指标替代", markdown)

    def test_mvrv_proxy_field_is_rejected(self):
        payload = self.make_mvrv_output(sth_available=False)
        payload["sth_mvrv"]["longShortDiff"] = 0.27

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "代理指标"
        ):
            self.build_context(mvrv_payload=payload, mvrv_exit_code=2)

    def test_mvrv_proxy_source_endpoint_is_rejected(self):
        payload = self.make_mvrv_output()
        payload["source"]["urls"][1] = (
            "https://api.bitcoin-data.com/v1/sth-mvrv-momentum"
        )
        payload["source"]["endpoints"]["sth_mvrv"].update(
            {
                "endpoint": "https://api.bitcoin-data.com/v1/sth-mvrv-momentum",
                "request_url": (
                    "https://api.bitcoin-data.com/v1/sth-mvrv-momentum?fixture=1"
                ),
                "value_field": "sthMvrvMomentum",
            }
        )

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "正式endpoint"
        ):
            self.build_context(mvrv_payload=payload)

    def test_mvrv_fallback_alignment_and_warning_are_preserved(self):
        payload = self.make_mvrv_output(
            warnings=["fixture MVRV fallback and alignment warning"]
        )
        point = payload["mvrv"]["d7"]
        point.update(
            {
                "actual_date": "2026-07-22",
                "value": 1.17,
                "fallback_days": 1,
            }
        )
        payload["mvrv"]["change_7d"] = self.make_mvrv_change(1.24, 1.17)
        sth = payload["sth_mvrv"]
        sth["date"] = "2026-07-29"
        for point_key, offset in (("previous", 1), ("d7", 7), ("d30", 30)):
            target = date(2026, 7, 29) - timedelta(days=offset)
            sth[point_key]["target_date"] = target.isoformat()
            sth[point_key]["actual_date"] = target.isoformat()
        payload["date_alignment"] = "misaligned"
        payload["quality_checks"]["date_alignment"] = "misaligned"
        payload["quality_checks"]["sth_mvrv"] = self.make_mvrv_quality(
            date(2026, 7, 29)
        )

        context = self.build_context(
            mvrv_payload=payload,
            mvrv_exit_code=2,
        )
        section = context["sections"]["mvrv"]
        markdown = prepare_daily_brief.render_context_markdown(context)

        self.assertEqual(section["mvrv"]["d7"]["target_date"], "2026-07-23")
        self.assertEqual(section["mvrv"]["d7"]["actual_date"], "2026-07-22")
        self.assertEqual(section["mvrv"]["d7"]["fallback_days"], 1)
        self.assertEqual(section["sth_mvrv"]["date"], "2026-07-29")
        self.assertEqual(section["date_alignment"], "misaligned")
        self.assertIn("fixture MVRV fallback and alignment warning", context["warnings"])
        self.assertIn("回退1日", markdown)
        self.assertIn("日期是否对齐：misaligned", markdown)

    def test_mismatched_mvrv_as_of_is_rejected_by_validator(self):
        payload = self.make_mvrv_output()
        payload["requested_as_of"] = "2026-07-31"
        payload["data_delay_days"] = 1

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "requested_as_of"
        ):
            self.build_context(mvrv_payload=payload)

    def test_ssr_standard_output_is_preserved_as_sixth_section(self):
        payload = self.make_ssr_output()

        context = self.build_context(ssr_payload=payload, ssr_exit_code=2)
        section = context["sections"]["ssr"]

        self.assertEqual(section["ssr_oscillator"]["current"], 1.2)
        self.assertEqual(section["raw_ssr"]["current"], 6.4)
        self.assertEqual(section["actual_latest_date"], "2026-07-30")
        self.assertEqual(section["ssr_oscillator"]["status"], "Provisional")
        self.assertEqual(section["ssr_oscillator"]["direction"], "unavailable")
        self.assertEqual(
            section["ssr_oscillator"]["extreme_status"], "not_applicable"
        )
        self.assertFalse(section["definition"]["compatible_with_daily_brief_rules"])
        self.assertEqual(context["data_quality"]["ssr_exit_code"], 2)

    def test_ssr_ratio_must_remain_positive_in_prepare(self):
        for invalid_value in (0.0, -0.1):
            with self.subTest(invalid_value=invalid_value):
                payload = self.make_ssr_output()
                payload["ssr_oscillator"]["current"] = invalid_value
                with self.assertRaisesRegex(
                    prepare_daily_brief.PreparationError, "必须大于0"
                ):
                    self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_ssr_markdown_keeps_incompatibility_and_auxiliary_boundaries(self):
        markdown = prepare_daily_brief.render_context_markdown(self.build_context())

        self.assertIn("## BTC SSR Oscillator", markdown)
        self.assertIn("SSR / SMA365", markdown)
        self.assertIn("与现行rules的经典0中心/±2口径不兼容", markdown)
        self.assertIn("不套用0轴方向或±2极端解释", markdown)
        self.assertIn("Raw SSR仅为辅助数据，不是SSR Oscillator", markdown)
        self.assertIn("数据状态：Provisional", markdown)
        self.assertNotIn("官方图表交叉验证", markdown)

    def test_ssr_exit_two_generates_context_and_aggregates_two(self):
        payload = self.make_ssr_output()

        with tempfile.TemporaryDirectory() as directory:
            exit_code, stdout, stderr, output_dir = self.execute_with_ssr_payload(
                directory, payload, ssr_exit_code=2
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(stderr, "")
            self.assertIn("Daily Brief Context ready", stdout)
            loaded = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertIn("ssr", loaded["sections"])
            self.assertTrue(any("不兼容" in item for item in loaded["warnings"]))
            self.assertEqual(loaded["aggregate_status"], "provisional")
            self.assertEqual(loaded["aggregate_exit_code"], 2)
            self.assertEqual(loaded["data_quality"]["aggregate_exit_code"], 2)

    def test_ssr_exit_one_is_unavailable_without_reading_stale_json(self):
        self.assert_metric_exit_one_is_failsoft("ssr")

    def test_ssr_current_official_definition_cannot_return_exit_zero(self):
        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "必须返回exit 2"
        ):
            self.build_context(ssr_payload=self.make_ssr_output(), ssr_exit_code=0)

    def test_ssr_proxy_and_reconstructed_fields_are_rejected(self):
        forbidden_fields = (
            "proxy",
            "reconstructedOscillator",
            "btcMarketCap",
            "stablecoinMarketCap",
            "sma200",
            "bollingerBands",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                payload = self.make_ssr_output()
                payload[field] = 123.0
                with self.assertRaisesRegex(
                    prepare_daily_brief.PreparationError, "代理/自建字段"
                ):
                    self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_raw_ssr_key_is_allowed_but_cannot_replace_oscillator(self):
        payload = self.make_ssr_output()
        context = self.build_context(ssr_payload=payload, ssr_exit_code=2)
        self.assertIn("raw_ssr", context["sections"]["ssr"])

        payload = self.make_ssr_output()
        payload["source"]["endpoints"]["ssr_oscillator"][
            "value_field"
        ] = "ssrStablecoin"
        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "正式endpoint/字段"
        ):
            self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_ssr_source_provider_endpoint_and_field_are_strict(self):
        mutations = (
            ("provider", lambda payload: payload["source"].update({"provider": "Glassnode"})),
            (
                "endpoint",
                lambda payload: payload["source"]["endpoints"]["ssr_oscillator"].update(
                    {"endpoint": fetch_ssr.RAW_SSR_ENDPOINT}
                ),
            ),
            (
                "field",
                lambda payload: payload["source"]["endpoints"]["ssr_oscillator"].update(
                    {"value_field": "rawSsr"}
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                payload = self.make_ssr_output()
                mutate(payload)
                with self.assertRaises(prepare_daily_brief.PreparationError):
                    self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_ssr_definition_schema_and_provisional_status_are_strict(self):
        mutations = (
            lambda payload: payload["definition"].update({"confirmed": False}),
            lambda payload: payload["definition"].update(
                {"compatible_with_daily_brief_rules": True}
            ),
            lambda payload: payload["definition"].update({"formula": "BB(200,2)"}),
            lambda payload: payload["ssr_oscillator"].update({"status": "Verified"}),
            lambda payload: payload["ssr_oscillator"].update(
                {"direction": "weaker_stablecoin_purchasing_power"}
            ),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                payload = self.make_ssr_output()
                mutate(payload)
                with self.assertRaises(prepare_daily_brief.PreparationError):
                    self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_ssr_as_of_mismatch_is_rejected_by_validator(self):
        payload = self.make_ssr_output(requested_as_of=date(2026, 7, 29))

        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "requested_as_of"
        ):
            self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_ssr_fallback_metadata_is_preserved_and_limited_to_seven_days(self):
        target = self.AS_OF - timedelta(days=7)
        payload = self.make_ssr_output(missing_oscillator_dates={target})

        context = self.build_context(ssr_payload=payload, ssr_exit_code=2)
        point = context["sections"]["ssr"]["ssr_oscillator"]["d7"]
        self.assertEqual(point["target_date"], "2026-07-23")
        self.assertEqual(point["actual_date"], "2026-07-22")
        self.assertEqual(point["fallback_days"], 1)

        payload = self.make_ssr_output()
        point = payload["ssr_oscillator"]["d7"]
        point["actual_date"] = "2026-07-15"
        point["fallback_days"] = 8
        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "回退超过7日"
        ):
            self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_ssr_quality_and_cache_metadata_are_preserved_and_validated(self):
        payload = self.make_ssr_output()
        context = self.build_context(ssr_payload=payload, ssr_exit_code=2)
        section = context["sections"]["ssr"]

        self.assertEqual(section["quality_checks"], payload["quality_checks"])
        self.assertEqual(section["cache_status"], payload["cache_status"])
        self.assertEqual(section["network_request_count"], 0)
        self.assertEqual(context["data_quality"]["ssr_network_request_count"], 0)

        payload = self.make_ssr_output()
        payload["quality_checks"]["ssr_oscillator"]["expected_date_count"] = 39
        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "必须为40"
        ):
            self.build_context(ssr_payload=payload, ssr_exit_code=2)

        payload = self.make_ssr_output()
        payload["cache_status"]["ssr_oscillator"]["endpoint"] = fetch_ssr.RAW_SSR_ENDPOINT
        with self.assertRaisesRegex(
            prepare_daily_brief.PreparationError, "endpoint/request_url"
        ):
            self.build_context(ssr_payload=payload, ssr_exit_code=2)

    def test_ssr_raw_unavailable_remains_null_and_exit_two(self):
        payload = self.make_ssr_output(raw_available=False)
        context = self.build_context(ssr_payload=payload, ssr_exit_code=2)

        raw_ssr = context["sections"]["ssr"]["raw_ssr"]
        self.assertEqual(raw_ssr["status"], "Unavailable")
        self.assertIsNone(raw_ssr["current"])
        self.assertIsNone(raw_ssr["date"])
        self.assertEqual(context["sections"]["ssr"]["date_alignment"], "unavailable")

    def test_ssr_malformed_json_loader_rejects_invalid_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text('{"broken": NaN}', encoding="utf-8")
            with self.assertRaises(prepare_daily_brief.PreparationError):
                prepare_daily_brief.load_ssr_output(path)

    def test_ssr_malformed_json_becomes_unavailable_in_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture_paths = self.write_all_module_fixtures(directory)
            fixture_paths["ssr_json_path"].write_text(
                '{"broken": NaN}', encoding="utf-8"
            )
            invokers, calls = self.make_tracking_invokers()
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_code = self.execute_preparation(
                    as_of=self.AS_OF.isoformat(),
                    refresh=False,
                    copy_requested=False,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                    **fixture_paths,
                    **invokers,
                )

            self.assertEqual(exit_code, 2)
            context = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            section = context["sections"]["ssr"]
            self.assertEqual(section["status"], "unavailable")
            self.assertEqual(section["availability"], "unavailable")
            self.assertIsNone(section["value"])
            self.assertTrue(section["reason"])
            self.assertEqual(
                calls,
                [
                    (module, self.AS_OF.isoformat(), False)
                    for module in self.MODULE_ORDER
                ],
            )

    def test_two_metric_failures_still_generate_context(self):
        failed_modules = {"cvdd", "ahr999"}
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_failsoft_scenario(
                directory,
                failed_modules=failed_modules,
            )

            self.assertEqual(result["exit_code"], 2)
            self.assertIsNotNone(result["context"])
            for module in failed_modules:
                result["loader_mocks"][module].assert_not_called()
                self.assert_unavailable_section(
                    result["context"],
                    module,
                    f"fixture {module} provider failure",
                )
            for module in set(self.MODULE_ORDER) - failed_modules:
                self.assert_normal_section_value_is_preserved(
                    result["context"], module
                )
            self.assertEqual(
                result["calls"],
                [
                    (module, self.AS_OF.isoformat(), False)
                    for module in self.MODULE_ORDER
                ],
            )
            self.assertEqual(result["context"]["aggregate_exit_code"], 2)
            self.assertEqual(
                result["context"]["data_quality"]["module_exit_codes"]["cvdd"],
                1,
            )
            self.assertEqual(
                result["context"]["data_quality"]["module_exit_codes"]["ahr999"],
                1,
            )

    def test_multiple_metric_failures_preserve_all_normal_sections(self):
        failed_modules = {"etf", "funding", "ssr"}
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_failsoft_scenario(
                directory,
                failed_modules=failed_modules,
            )

            self.assertEqual(result["exit_code"], 2)
            for module in failed_modules:
                self.assert_unavailable_section(
                    result["context"],
                    module,
                    f"fixture {module} provider failure",
                )
            for module in set(self.MODULE_ORDER) - failed_modules:
                self.assert_normal_section_value_is_preserved(
                    result["context"], module
                )
            self.assertEqual(
                result["calls"],
                [
                    (module, self.AS_OF.isoformat(), False)
                    for module in self.MODULE_ORDER
                ],
            )
            self.assertEqual(result["context"]["aggregate_status"], "provisional")
            self.assertEqual(result["context"]["aggregate_exit_code"], 2)
            markdown = (result["output_dir"] / "latest.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("### 完整标准化模块数据", markdown)
            self.assertIn('"change_365d"', markdown)
            self.assertIn('"quantile5y_7d"', markdown)
            self.assertIn('"asopr_7dma"', markdown)

    def test_known_invoker_exception_becomes_metric_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_failsoft_scenario(
                directory,
                exception_modules={"mvrv"},
            )

            self.assertEqual(result["exit_code"], 2)
            self.assert_unavailable_section(
                result["context"],
                "mvrv",
                "fixture mvrv provider exception",
            )
            result["loader_mocks"]["mvrv"].assert_not_called()
            self.assertEqual(
                result["calls"],
                [
                    (module, self.AS_OF.isoformat(), False)
                    for module in self.MODULE_ORDER
                ],
            )
            self.assert_normal_section_value_is_preserved(
                result["context"], "asopr"
            )
            self.assert_normal_section_value_is_preserved(
                result["context"], "ssr"
            )

    def test_successful_run_replaces_old_latest_with_current_run_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture_paths = self.write_all_module_fixtures(directory)
            invokers, _calls = self.make_tracking_invokers()
            output_dir = Path(directory) / "context"
            output_dir.mkdir(parents=True)
            old_context = {
                "generated_at_utc": "1999-01-01T00:00:00Z",
                "requested_as_of": "1999-01-01",
                "stale_marker": "OLD_CONTEXT_MUST_BE_REPLACED",
            }
            (output_dir / "latest.json").write_text(
                json.dumps(old_context), encoding="utf-8"
            )
            (output_dir / "latest.md").write_text(
                "OLD MARKDOWN MUST BE REPLACED", encoding="utf-8"
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.execute_preparation(
                    as_of=self.AS_OF.isoformat(),
                    refresh=False,
                    copy_requested=False,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                    **fixture_paths,
                    **invokers,
                )

            self.assertEqual(exit_code, 2)
            context = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(context["generated_at_utc"], self.GENERATED_AT)
            self.assertEqual(
                context["requested_as_of"], self.AS_OF.isoformat()
            )
            self.assertEqual(context["aggregate_status"], "provisional")
            self.assertEqual(context["aggregate_exit_code"], 2)
            self.assertEqual(context["data_quality"]["aggregate_exit_code"], 2)
            self.assertNotIn("stale_marker", context)
            self.assertNotIn(
                "OLD MARKDOWN MUST BE REPLACED",
                (output_dir / "latest.md").read_text(encoding="utf-8"),
            )

    def test_system_write_failure_returns_one_and_preserves_old_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture_paths = self.write_all_module_fixtures(directory)
            invokers, calls = self.make_tracking_invokers()
            output_dir = Path(directory) / "context"
            output_dir.mkdir(parents=True)
            old_json = json.dumps(
                {
                    "generated_at_utc": "1999-01-01T00:00:00Z",
                    "requested_as_of": "1999-01-01",
                    "stale_marker": "OLD_CONTEXT_REMAINS_AFTER_FATAL",
                }
            )
            old_markdown = "OLD MARKDOWN REMAINS AFTER FATAL"
            (output_dir / "latest.json").write_text(old_json, encoding="utf-8")
            (output_dir / "latest.md").write_text(
                old_markdown, encoding="utf-8"
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with mock.patch.object(
                prepare_daily_brief,
                "write_context_outputs",
                side_effect=OSError("fixture output filesystem unavailable"),
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = self.execute_preparation(
                    as_of=self.AS_OF.isoformat(),
                    refresh=False,
                    copy_requested=False,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                    **fixture_paths,
                    **invokers,
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(
                calls,
                [
                    (module, self.AS_OF.isoformat(), False)
                    for module in self.MODULE_ORDER
                ],
            )
            self.assertEqual(
                (output_dir / "latest.json").read_text(encoding="utf-8"),
                old_json,
            )
            self.assertEqual(
                (output_dir / "latest.md").read_text(encoding="utf-8"),
                old_markdown,
            )
            self.assertNotIn("Daily Brief Context ready", stdout.getvalue())
            self.assertIn("fixture output filesystem unavailable", stderr.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            fixture_paths = self.write_all_module_fixtures(directory)
            invokers, _calls = self.make_tracking_invokers()
            output_dir = Path(directory) / "context"
            stderr = io.StringIO()
            with mock.patch.object(
                prepare_daily_brief,
                "print_terminal_summary",
                side_effect=RuntimeError("fixture terminal rendering failed"),
            ), contextlib.redirect_stderr(stderr):
                exit_code = self.execute_preparation(
                    as_of=self.AS_OF.isoformat(),
                    refresh=False,
                    copy_requested=False,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                    **fixture_paths,
                    **invokers,
                )
            self.assertEqual(exit_code, 1)
            written = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(written["generated_at_utc"], self.GENERATED_AT)
            self.assertIn("本次context已写出", stderr.getvalue())
            self.assertNotIn("本次context未生成", stderr.getvalue())

    def test_exit_code_aggregation_matrix(self):
        cases = (
            (0, 0, 0, 0, 0, 2),
            (2, 0, 0, 0, 0, 2),
            (0, 2, 0, 0, 0, 2),
            (0, 0, 2, 0, 0, 2),
            (0, 0, 0, 2, 0, 2),
            (0, 0, 0, 0, 2, 2),
            (2, 2, 2, 2, 2, 2),
        )
        for (
            cvdd_code,
            etf_code,
            ahr999_code,
            funding_code,
            mvrv_code,
            expected,
        ) in cases:
            with self.subTest(
                cvdd=cvdd_code,
                etf=etf_code,
                ahr999=ahr999_code,
                funding=funding_code,
                mvrv=mvrv_code,
            ):
                cvdd_payload = self.make_cvdd_output()
                etf_payload = self.make_etf_output()
                ahr999_payload = self.make_ahr999_output()
                funding_payload = self.make_funding_output()
                mvrv_payload = self.make_mvrv_output()
                ssr_payload = self.make_ssr_output()
                if cvdd_code == 2:
                    cvdd_payload["warnings"] = ["fixture CVDD quality warning"]
                if etf_code == 2:
                    etf_payload["warnings"] = ["fixture ETF quality warning"]
                if ahr999_code == 2:
                    ahr999_payload["warnings"] = [
                        "fixture AHR999 quality warning"
                    ]
                if funding_code == 2:
                    funding_payload["warnings"] = [
                        "fixture Funding quality warning"
                    ]
                if mvrv_code == 2:
                    mvrv_payload["warnings"] = [
                        "fixture MVRV quality warning"
                    ]
                with tempfile.TemporaryDirectory() as directory:
                    cvdd_path = self.write_cvdd_fixture(directory, cvdd_payload)
                    etf_path = self.write_etf_fixture(directory, etf_payload)
                    ahr999_path = self.write_ahr999_fixture(
                        directory, ahr999_payload
                    )
                    funding_path = self.write_funding_fixture(
                        directory, funding_payload
                    )
                    mvrv_path = self.write_mvrv_fixture(directory, mvrv_payload)
                    ssr_path = self.write_ssr_fixture(directory, ssr_payload)
                    output_dir = Path(directory) / "context"
                    stdout = io.StringIO()

                    with contextlib.redirect_stdout(stdout):
                        actual = self.execute_preparation(
                            as_of="2026-07-30",
                            refresh=False,
                            copy_requested=False,
                            cvdd_invoker=lambda _as_of, _refresh, code=cvdd_code: prepare_daily_brief.CvddInvocation(
                                code
                            ),
                            etf_invoker=lambda _as_of, _refresh, code=etf_code: prepare_daily_brief.CvddInvocation(
                                code
                            ),
                            ahr999_invoker=lambda _as_of, _refresh, code=ahr999_code: prepare_daily_brief.CvddInvocation(
                                code
                            ),
                            funding_invoker=lambda _as_of, _refresh, code=funding_code: prepare_daily_brief.CvddInvocation(
                                code
                            ),
                            mvrv_invoker=lambda _as_of, _refresh, code=mvrv_code: prepare_daily_brief.CvddInvocation(
                                code
                            ),
                            ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                                2
                            ),
                            cvdd_json_path=cvdd_path,
                            etf_json_path=etf_path,
                            ahr999_json_path=ahr999_path,
                            funding_json_path=funding_path,
                            mvrv_json_path=mvrv_path,
                            ssr_json_path=ssr_path,
                            output_dir=output_dir,
                            generated_at_utc=self.GENERATED_AT,
                        )

                    self.assertEqual(actual, expected)
                    self.assertTrue((output_dir / "latest.json").exists())
                    self.assertTrue((output_dir / "latest.md").exists())
                    loaded = json.loads(
                        (output_dir / "latest.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(
                        loaded["data_quality"]["aggregate_exit_code"], expected
                    )
                    self.assertEqual(loaded["aggregate_exit_code"], expected)
                    self.assertEqual(loaded["aggregate_status"], "provisional")

        self.assertEqual(
            prepare_daily_brief.aggregate_exit_codes(0, 0, 0, 0, 0, 0, 0), 0
        )
        self.assertEqual(
            prepare_daily_brief.aggregate_exit_codes(1, 0, 0, 0, 0, 0, 0), 2
        )
        self.assertEqual(
            prepare_daily_brief.aggregate_exit_codes(0, 1, 2, 0, 2, 0, 2), 2
        )
        self.assertEqual(
            prepare_daily_brief.aggregate_exit_codes(0, 0, 0, 3, 0, 0, 0), 1
        )
        minimal_sections = {
            module: {"network_request_count": 0, "warnings": []}
            for module in self.MODULE_ORDER
        }
        all_zero_codes = {module: 0 for module in self.MODULE_ORDER}
        warning_context = prepare_daily_brief.assemble_daily_brief_context(
            self.AS_OF.isoformat(),
            minimal_sections,
            {
                module: ["fixture nonfatal warning"] if module == "cvdd" else []
                for module in self.MODULE_ORDER
            },
            {module: {} for module in self.MODULE_ORDER},
            all_zero_codes,
            all_zero_codes,
            generated_at_utc=self.GENERATED_AT,
        )
        self.assertEqual(warning_context["aggregate_exit_code"], 2)
        self.assertEqual(warning_context["aggregate_status"], "provisional")

    def test_mismatched_etf_as_of_becomes_unavailable(self):
        etf_payload = copy.deepcopy(self.make_etf_output())
        etf_payload["requested_as_of"] = "2026-07-29"
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, etf_payload)
            ahr999_path = self.write_ahr999_fixture(
                directory, self.make_ahr999_output()
            )
            funding_path = self.write_funding_fixture(
                directory, self.make_funding_output()
            )
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())
            context = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assert_unavailable_section(context, "etf", "requested_as_of")
            self.assertEqual(context["aggregate_exit_code"], 2)

    def test_mismatched_ahr999_as_of_becomes_unavailable(self):
        ahr999_payload = self.make_ahr999_output(
            requested_as_of=date(2026, 7, 29)
        )
        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
            ahr999_path = self.write_ahr999_fixture(directory, ahr999_payload)
            funding_path = self.write_funding_fixture(
                directory, self.make_funding_output()
            )
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=False,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue((output_dir / "latest.json").exists())
            self.assertTrue((output_dir / "latest.md").exists())
            context = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assert_unavailable_section(
                context, "ahr999", "requested_as_of"
            )
            self.assertEqual(context["aggregate_exit_code"], 2)

    def test_empty_explicit_as_of_is_fatal_before_fetching(self):
        calls = []
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = self.execute_preparation(
                as_of="",
                refresh=False,
                copy_requested=False,
                cvdd_invoker=lambda as_of, refresh: calls.append(
                    ("cvdd", as_of, refresh)
                ),
                etf_invoker=lambda as_of, refresh: calls.append(
                    ("etf", as_of, refresh)
                ),
                ahr999_invoker=lambda as_of, refresh: calls.append(
                    ("ahr999", as_of, refresh)
                ),
                funding_invoker=lambda as_of, refresh: calls.append(
                    ("funding", as_of, refresh)
                ),
                mvrv_invoker=lambda as_of, refresh: calls.append(
                    ("mvrv", as_of, refresh)
                ),
                ssr_invoker=lambda as_of, refresh: calls.append(
                    ("ssr", as_of, refresh)
                ),
                ahr999_json_path=Path("/not-read-invalid-as-of.json"),
                funding_json_path=Path("/not-read-invalid-as-of-funding.json"),
                mvrv_json_path=Path("/not-read-invalid-as-of-mvrv.json"),
                ssr_json_path=Path("/not-read-invalid-as-of-ssr.json"),
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

    def test_all_fetchers_receive_arguments_and_success_output_stays_concise(self):
        calls = []

        def cvdd_invoker(as_of, refresh):
            calls.append(("cvdd", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(0, stdout="noisy CVDD output")

        def etf_invoker(as_of, refresh):
            calls.append(("etf", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(0, stdout="noisy ETF output")

        def ahr999_invoker(as_of, refresh):
            calls.append(("ahr999", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(
                0, stdout="noisy AHR999 output"
            )

        def funding_invoker(as_of, refresh):
            calls.append(("funding", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(
                0, stdout="noisy Funding output"
            )

        def mvrv_invoker(as_of, refresh):
            calls.append(("mvrv", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(
                0, stdout="noisy MVRV output"
            )

        def asopr_invoker(as_of, refresh):
            calls.append(("asopr", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(
                0, stdout="noisy aSOPR output"
            )

        def ssr_invoker(as_of, refresh):
            calls.append(("ssr", as_of, refresh))
            return prepare_daily_brief.CvddInvocation(
                2, stdout="noisy SSR output"
            )

        with tempfile.TemporaryDirectory() as directory:
            cvdd_path = self.write_cvdd_fixture(directory, self.make_cvdd_output())
            etf_path = self.write_etf_fixture(directory, self.make_etf_output())
            ahr999_path = self.write_ahr999_fixture(
                directory, self.make_ahr999_output()
            )
            funding_path = self.write_funding_fixture(
                directory, self.make_funding_output()
            )
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            asopr_path = self.write_asopr_fixture(
                directory, self.make_asopr_output()
            )
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=True,
                    copy_requested=False,
                    cvdd_invoker=cvdd_invoker,
                    etf_invoker=etf_invoker,
                    ahr999_invoker=ahr999_invoker,
                    funding_invoker=funding_invoker,
                    mvrv_invoker=mvrv_invoker,
                    asopr_invoker=asopr_invoker,
                    ssr_invoker=ssr_invoker,
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    asopr_json_path=asopr_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(
                calls,
                [
                    ("cvdd", "2026-07-30", True),
                    ("etf", "2026-07-30", True),
                    ("ahr999", "2026-07-30", True),
                    ("funding", "2026-07-30", True),
                    ("mvrv", "2026-07-30", True),
                    ("asopr", "2026-07-30", True),
                    ("ssr", "2026-07-30", True),
                ],
            )
            loaded = json.loads(
                (output_dir / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(loaded["sections"]), set(self.MODULE_SECTIONS.values()))
            self.assertEqual(loaded["aggregate_status"], "provisional")
            self.assertEqual(loaded["aggregate_exit_code"], 2)
            terminal = stdout.getvalue()
            self.assertIn("Daily Brief Context ready", terminal)
            self.assertNotIn("noisy CVDD output", terminal)
            self.assertNotIn("noisy ETF output", terminal)
            self.assertNotIn("noisy AHR999 output", terminal)
            self.assertNotIn("noisy Funding output", terminal)
            self.assertNotIn("noisy MVRV output", terminal)
            self.assertNotIn("noisy aSOPR output", terminal)
            self.assertNotIn("noisy SSR output", terminal)

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
            ahr999_path = self.write_ahr999_fixture(
                directory, self.make_ahr999_output()
            )
            funding_path = self.write_funding_fixture(
                directory, self.make_funding_output()
            )
            mvrv_path = self.write_mvrv_fixture(directory, self.make_mvrv_output())
            ssr_path = self.write_ssr_fixture(directory, self.make_ssr_output())
            output_dir = Path(directory) / "context"
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = self.execute_preparation(
                    as_of="2026-07-30",
                    refresh=False,
                    copy_requested=True,
                    cvdd_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    etf_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ahr999_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    funding_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    mvrv_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        0
                    ),
                    ssr_invoker=lambda _as_of, _refresh: prepare_daily_brief.CvddInvocation(
                        2
                    ),
                    cvdd_json_path=cvdd_path,
                    etf_json_path=etf_path,
                    ahr999_json_path=ahr999_path,
                    funding_json_path=funding_path,
                    mvrv_json_path=mvrv_path,
                    ssr_json_path=ssr_path,
                    output_dir=output_dir,
                    clipboard_copier=lambda markdown: (
                        False,
                        "未复制到剪贴板：未找到 pbcopy。",
                    ),
                    generated_at_utc=self.GENERATED_AT,
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue((output_dir / "latest.md").exists())
            self.assertIn("未复制到剪贴板", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
