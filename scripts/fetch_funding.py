#!/usr/bin/env python3
"""获取并标准化 BTC 永续合约 Funding Rate 官方交易所数据。"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "funding" / "generated"
SOURCE_MODE = "exchange_public_apis"

CACHE_TTL = timedelta(minutes=5)
NETWORK_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FRESHNESS_GRACE = timedelta(minutes=10)

OKX_CURRENT_URL = (
    "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP"
)
OKX_HISTORY_URL = (
    "https://www.okx.com/api/v5/public/funding-rate-history"
    "?instId=BTC-USDT-SWAP&limit=5"
)
BYBIT_CURRENT_URL = (
    "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"
)
BYBIT_HISTORY_URL = (
    "https://api.bybit.com/v5/market/funding/history"
    "?category=linear&symbol=BTCUSDT&limit=5"
)
BINANCE_CURRENT_URL = (
    "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
)
BINANCE_HISTORY_URL = (
    "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=5"
)
BINANCE_INFO_URL = "https://fapi.binance.com/fapi/v1/fundingInfo"

PROVIDER_ORDER = ("okx", "bybit", "binance")
GAP_ONLY_SUPPORTED_INTERVALS = frozenset({1.0, 2.0, 4.0, 6.0, 8.0})
COINGLASS_UPGRADE_NOTE = (
    "当前简单平均不是持仓量加权 Funding。未来若取得并验证 CoinGlass API Key，"
    "可增加 CoinGlass OI-weighted Funding 作为主指标；现有交易所官方数据继续"
    "作为交叉验证和 fallback。"
)


class FundingError(Exception):
    """Funding 模块无法形成可靠核心结果时的可读错误。"""


class ProviderParseError(Exception):
    """单个 provider 返回的业务 JSON 或字段不可安全解析。"""


class ProviderHttpError(Exception):
    """单个公开 HTTP 请求失败；错误会被隔离在对应 provider。"""

    def __init__(self, url: str, reason: str, http_status: int | None = None):
        self.url = url
        self.reason = reason
        self.http_status = http_status
        status_text = f"HTTP {http_status}" if http_status is not None else "网络错误"
        super().__init__(f"{status_text}：{reason}；URL：{url}")


@dataclass(frozen=True)
class HttpJsonResponse:
    url: str
    http_status: int
    payload: Any


@dataclass
class ProviderBundle:
    payloads: dict[str, Any]
    requests: list[dict[str, Any]]
    errors: dict[str, dict[str, Any]]
    fetched_at: datetime
    cache_status: dict[str, Any]
    network_request_count: int


Transport = Callable[[str], HttpJsonResponse]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("时间戳不是字符串")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("时间戳缺少时区")
    return parsed.astimezone(timezone.utc)


def reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 包含非有限数值 {value}")


def loads_json_strict(text: str, context: str) -> Any:
    try:
        return json.loads(text, parse_constant=reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise FundingError(f"{context}不是有效 JSON：{exc}") from exc


def atomic_write_text(path: Path, text: str) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise FundingError(f"无法原子写入文件 {path}：{exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FundingError(f"无法序列化 JSON 文件 {path}：{exc}") from exc
    atomic_write_text(path, text + "\n")


def fetch_json_response(
    url: str, timeout: int = NETWORK_TIMEOUT_SECONDS
) -> HttpJsonResponse:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "daily-brief-system-funding-fetcher/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            http_status = int(response.getcode())
    except HTTPError as exc:
        try:
            detail = exc.read(512).decode("utf-8", errors="replace").strip()
        except OSError:
            detail = ""
        reason = f"{exc.reason}"
        if detail:
            reason += f"；响应：{' '.join(detail.split())[:300]}"
        raise ProviderHttpError(url, reason, int(exc.code)) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ProviderHttpError(url, f"连接失败：{reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise ProviderHttpError(url, f"连接超时或失败：{exc}") from exc

    if not raw:
        raise ProviderHttpError(url, "接口返回空响应", http_status)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderHttpError(
            url, f"响应超过安全上限 {MAX_RESPONSE_BYTES} 字节", http_status
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderHttpError(url, "响应不是有效UTF-8", http_status) from exc
    try:
        payload = json.loads(text, parse_constant=reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderHttpError(url, f"响应JSON无效：{exc}", http_status) from exc
    return HttpJsonResponse(url=url, http_status=http_status, payload=payload)


def coerce_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderParseError(f"字段 {field} 不是数值")
    if isinstance(value, str) and not value.strip():
        raise ProviderParseError(f"字段 {field} 为空")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderParseError(f"字段 {field} 不是合法数值") from exc
    if not math.isfinite(numeric):
        raise ProviderParseError(f"字段 {field} 不是有限数")
    return numeric


def coerce_positive_hours(value: Any, field: str) -> float:
    hours = coerce_number(value, field)
    if hours <= 0 or hours > 24:
        raise ProviderParseError(f"字段 {field} 不是1至24小时内的正周期")
    return hours


def timestamp_ms(value: Any, field: str) -> tuple[int, str]:
    numeric = coerce_number(value, field)
    if numeric <= 0 or not numeric.is_integer():
        raise ProviderParseError(f"字段 {field} 不是合法Unix毫秒时间戳")
    milliseconds = int(numeric)
    try:
        parsed = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise ProviderParseError(f"字段 {field} 超出时间戳范围") from exc
    return milliseconds, format_utc_timestamp(parsed)


def _interval_from_difference(
    later_ms: int | None, earlier_ms: int | None
) -> float | None:
    if later_ms is None or earlier_ms is None or later_ms <= earlier_ms:
        return None
    hours = (later_ms - earlier_ms) / 3_600_000
    rounded = round(hours)
    if rounded <= 0 or rounded > 24 or abs(hours - rounded) > (1 / 60):
        return None
    return float(rounded)


def infer_history_interval(timestamps: list[int]) -> float | None:
    unique = sorted(set(timestamps), reverse=True)
    if len(unique) < 2:
        return None
    return _interval_from_difference(unique[0], unique[1])


def normalized_8h(rate: float | None, interval_hours: float | None) -> float | None:
    if rate is None or interval_hours is None or interval_hours <= 0:
        return None
    return rate * 8 / interval_hours


def classify_funding(rate_8h: float | None) -> str:
    if rate_8h is None:
        return "unavailable"
    if rate_8h < 0:
        return "negative"
    if rate_8h < 0.0003:
        return "neutral_or_healthy"
    if rate_8h <= 0.0005:
        return "caution"
    return "overheated"


def _empty_current() -> dict[str, Any]:
    return {
        "rate": None,
        "type": None,
        "funding_time": None,
        "next_funding_time": None,
        "interval_hours": None,
        "interval_source": None,
    }


def _empty_settled() -> dict[str, Any]:
    return {
        "rate": None,
        "funding_time": None,
        "interval_hours": None,
        "interval_source": None,
    }


def _append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


class FundingProvider(ABC):
    """统一 provider 契约；未来 CoinGlass 实现只需实现同一接口。"""

    key: str
    display_name: str
    symbol: str

    @property
    @abstractmethod
    def endpoints(self) -> dict[str, str]:
        """返回本 provider 需要抓取的公开端点。"""

    @abstractmethod
    def parse_available(
        self, bundle: ProviderBundle, now: datetime
    ) -> dict[str, Any]:
        """解析抓取快照；当前核心缺失时抛 ProviderParseError。"""

    def unavailable_result(
        self, bundle: ProviderBundle, reason: str
    ) -> dict[str, Any]:
        return {
            "provider": self.key,
            "provider_name": self.display_name,
            "status": "unavailable",
            "reason": reason,
            "symbol": self.symbol,
            "fetched_at_utc": format_utc_timestamp(bundle.fetched_at),
            "urls": dict(self.endpoints),
            "requests": bundle.requests,
            "cache_status": bundle.cache_status,
            "network_request_count": bundle.network_request_count,
            "current": _empty_current(),
            "previous_settled": _empty_settled(),
            "normalized_8h": {
                "current_rate": None,
                "previous_settled_rate": None,
            },
            "classification": "unavailable",
            "warnings": [],
        }

    def build_result(self, bundle: ProviderBundle, now: datetime) -> dict[str, Any]:
        if "current" not in bundle.payloads:
            error = bundle.errors.get("current", {})
            reason = error.get("reason", "当前Funding响应不可用")
            return self.unavailable_result(bundle, reason)
        try:
            return self.parse_available(bundle, now)
        except ProviderParseError as exc:
            return self.unavailable_result(bundle, f"业务JSON解析失败：{exc}")


def _base_available_result(
    provider: FundingProvider,
    bundle: ProviderBundle,
    current: dict[str, Any],
    previous: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    current_8h = normalized_8h(current["rate"], current["interval_hours"])
    previous_8h = normalized_8h(previous["rate"], previous["interval_hours"])
    if current["interval_hours"] is None:
        _append_warning(warnings, "当前Funding周期无法可靠确认，未计算8h等效值")
    if previous["rate"] is None:
        _append_warning(warnings, "最近实际结算Funding不可用")
    elif previous["interval_hours"] is None:
        _append_warning(warnings, "上一期实际结算周期无法可靠确认")
    return {
        "provider": provider.key,
        "provider_name": provider.display_name,
        "status": "available",
        "reason": None,
        "symbol": provider.symbol,
        "fetched_at_utc": format_utc_timestamp(bundle.fetched_at),
        "urls": dict(provider.endpoints),
        "requests": bundle.requests,
        "cache_status": bundle.cache_status,
        "network_request_count": bundle.network_request_count,
        "current": current,
        "previous_settled": previous,
        "normalized_8h": {
            "current_rate": current_8h,
            "previous_settled_rate": previous_8h,
        },
        "classification": classify_funding(current_8h),
        "warnings": warnings,
    }


def _freshness_warnings(
    current: dict[str, Any],
    previous: dict[str, Any],
    now: datetime,
    warnings: list[str],
) -> None:
    now = now.astimezone(timezone.utc)
    scheduled_text = current.get("funding_time") or current.get("next_funding_time")
    interval = current.get("interval_hours")
    scheduled: datetime | None = None
    if scheduled_text is not None and interval is not None:
        try:
            scheduled = parse_utc_timestamp(scheduled_text)
        except ValueError:
            scheduled = None
        if scheduled is not None:
            if scheduled < now - FRESHNESS_GRACE:
                _append_warning(warnings, "当前/预计Funding结算时间已明显过去")
            elif scheduled > now + timedelta(hours=interval) + FRESHNESS_GRACE:
                _append_warning(warnings, "当前/预计Funding结算时间明显过远")
    previous_text = previous.get("funding_time")
    previous_interval = previous.get("interval_hours")
    settled: datetime | None = None
    if previous_text is not None and previous_interval is not None:
        try:
            settled = parse_utc_timestamp(previous_text)
        except ValueError:
            settled = None
        if settled is not None:
            if settled > now + FRESHNESS_GRACE:
                _append_warning(warnings, "上一期实际结算时间晚于当前抓取时间")
            elif now - settled > timedelta(hours=previous_interval * 2) + FRESHNESS_GRACE:
                _append_warning(warnings, "上一期实际结算Funding明显过期")

    # 距当前/下一结算恰好一个周期，才说明 history 的首行确实是“上一期”。
    # 仅按 now - settled 判断会在临近下一次结算时漏掉整整一期历史数据。
    relation_interval = interval or previous_interval
    if scheduled is not None and previous_text is not None and relation_interval is not None:
        if settled is None:
            try:
                settled = parse_utc_timestamp(previous_text)
            except ValueError:
                settled = None
        if settled is not None:
            observed_gap = scheduled - settled
            expected_gap = timedelta(hours=relation_interval)
            if observed_gap <= timedelta(0):
                _append_warning(warnings, "上一期实际结算时间与当前/预计结算时间顺序异常")
            elif observed_gap > expected_gap + FRESHNESS_GRACE:
                _append_warning(
                    warnings,
                    "上一期实际结算与当前/预计结算不相邻，可能缺少最新结算记录",
                )
            elif observed_gap < expected_gap - FRESHNESS_GRACE:
                _append_warning(warnings, "上一期实际结算与当前/预计结算周期不一致")


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderParseError(f"{context}不是对象")
    return value


def _require_list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderParseError(f"{context}不是数组")
    return value


class OkxFundingProvider(FundingProvider):
    key = "okx"
    display_name = "OKX"
    symbol = "BTC-USDT-SWAP"

    @property
    def endpoints(self) -> dict[str, str]:
        return {"current": OKX_CURRENT_URL, "history": OKX_HISTORY_URL}

    def _data(self, payload: Any, endpoint: str) -> list[dict[str, Any]]:
        root = _require_mapping(payload, f"OKX {endpoint}响应")
        if str(root.get("code")) != "0":
            raise ProviderParseError(
                f"OKX {endpoint}业务码 {root.get('code')}：{root.get('msg', '')}"
            )
        rows = _require_list(root.get("data"), f"OKX {endpoint}.data")
        normalized = [
            _require_mapping(item, f"OKX {endpoint}.data记录") for item in rows
        ]
        matching = [item for item in normalized if item.get("instId") == self.symbol]
        if not matching:
            raise ProviderParseError(f"OKX {endpoint}没有目标合约 {self.symbol}")
        return matching

    def parse_available(
        self, bundle: ProviderBundle, now: datetime
    ) -> dict[str, Any]:
        current_row = self._data(bundle.payloads["current"], "current")[0]
        rate = coerce_number(current_row.get("fundingRate"), "OKX fundingRate")
        funding_ms, funding_time = timestamp_ms(
            current_row.get("fundingTime"), "OKX fundingTime"
        )
        next_ms: int | None = None
        next_time: str | None = None
        try:
            next_ms, next_time = timestamp_ms(
                current_row.get("nextFundingTime"), "OKX nextFundingTime"
            )
        except ProviderParseError:
            pass
        current_interval = _interval_from_difference(next_ms, funding_ms)
        current_interval_source = (
            "fundingTime_to_nextFundingTime" if current_interval is not None else None
        )

        warnings: list[str] = []
        previous = _empty_settled()
        history_interval: float | None = None
        history_payload = bundle.payloads.get("history")
        if history_payload is not None:
            try:
                history_rows = self._data(history_payload, "history")
                parsed_rows: list[tuple[int, str, float]] = []
                for row in history_rows:
                    row_ms, row_time = timestamp_ms(
                        row.get("fundingTime"), "OKX history fundingTime"
                    )
                    realized = coerce_number(
                        row.get("realizedRate"), "OKX history realizedRate"
                    )
                    parsed_rows.append((row_ms, row_time, realized))
                parsed_rows.sort(key=lambda item: item[0], reverse=True)
                history_interval = infer_history_interval(
                    [item[0] for item in parsed_rows]
                )
                if parsed_rows:
                    latest = parsed_rows[0]
                    previous = {
                        "rate": latest[2],
                        "funding_time": latest[1],
                        "interval_hours": history_interval,
                        "interval_source": (
                            "consecutive_history" if history_interval is not None else None
                        ),
                    }
            except ProviderParseError as exc:
                _append_warning(warnings, f"历史实际结算解析失败：{exc}")
        elif "history" in bundle.errors:
            _append_warning(
                warnings,
                "历史实际结算请求失败：" + bundle.errors["history"]["reason"],
            )

        if current_interval is None and history_interval is not None:
            current_interval = history_interval
            current_interval_source = "consecutive_history"
        current = {
            "rate": rate,
            "type": "estimated",
            "funding_time": funding_time,
            "next_funding_time": next_time,
            "interval_hours": current_interval,
            "interval_source": current_interval_source,
        }
        _freshness_warnings(current, previous, now, warnings)
        return _base_available_result(self, bundle, current, previous, warnings)


class BybitFundingProvider(FundingProvider):
    key = "bybit"
    display_name = "Bybit"
    symbol = "BTCUSDT"

    @property
    def endpoints(self) -> dict[str, str]:
        return {"current": BYBIT_CURRENT_URL, "history": BYBIT_HISTORY_URL}

    def _rows(self, payload: Any, endpoint: str) -> tuple[list[dict[str, Any]], int | None]:
        root = _require_mapping(payload, f"Bybit {endpoint}响应")
        if root.get("retCode") != 0:
            raise ProviderParseError(
                f"Bybit {endpoint}业务码 {root.get('retCode')}："
                f"{root.get('retMsg', '')}"
            )
        result = _require_mapping(root.get("result"), f"Bybit {endpoint}.result")
        if result.get("category") != "linear":
            raise ProviderParseError(f"Bybit {endpoint} category不是linear")
        rows = _require_list(result.get("list"), f"Bybit {endpoint}.result.list")
        normalized = [
            _require_mapping(item, f"Bybit {endpoint}.list记录") for item in rows
        ]
        matching = [item for item in normalized if item.get("symbol") == self.symbol]
        if not matching:
            raise ProviderParseError(f"Bybit {endpoint}没有目标合约 {self.symbol}")
        server_time: int | None = None
        try:
            server_time, _server_iso = timestamp_ms(root.get("time"), "Bybit time")
        except ProviderParseError:
            pass
        return matching, server_time

    def parse_available(
        self, bundle: ProviderBundle, now: datetime
    ) -> dict[str, Any]:
        rows, _server_time = self._rows(bundle.payloads["current"], "current")
        current_row = rows[0]
        rate = coerce_number(current_row.get("fundingRate"), "Bybit fundingRate")
        next_ms, next_time = timestamp_ms(
            current_row.get("nextFundingTime"), "Bybit nextFundingTime"
        )
        explicit_interval: float | None = None
        try:
            explicit_interval = coerce_positive_hours(
                current_row.get("fundingIntervalHour"),
                "Bybit fundingIntervalHour",
            )
        except ProviderParseError:
            pass

        warnings: list[str] = []
        previous = _empty_settled()
        history_interval: float | None = None
        history_payload = bundle.payloads.get("history")
        if history_payload is not None:
            try:
                history_rows, _history_server = self._rows(history_payload, "history")
                parsed_rows: list[tuple[int, str, float]] = []
                for row in history_rows:
                    row_ms, row_time = timestamp_ms(
                        row.get("fundingRateTimestamp"),
                        "Bybit fundingRateTimestamp",
                    )
                    settled_rate = coerce_number(
                        row.get("fundingRate"), "Bybit history fundingRate"
                    )
                    parsed_rows.append((row_ms, row_time, settled_rate))
                parsed_rows.sort(key=lambda item: item[0], reverse=True)
                history_interval = infer_history_interval(
                    [item[0] for item in parsed_rows]
                )
                if parsed_rows:
                    latest = parsed_rows[0]
                    adjacent_interval = _interval_from_difference(next_ms, latest[0])
                    previous_interval = history_interval
                    previous_source = (
                        "consecutive_history" if history_interval is not None else None
                    )
                    if history_interval is None and explicit_interval is not None:
                        if adjacent_interval is not None and math.isclose(
                            adjacent_interval, explicit_interval
                        ):
                            previous_interval = explicit_interval
                            previous_source = (
                                "fundingIntervalHour_confirmed_by_nextFundingTime"
                            )
                        else:
                            _append_warning(
                                warnings,
                                "单条历史结算记录无法确认上一期实际周期",
                            )
                    previous = {
                        "rate": latest[2],
                        "funding_time": latest[1],
                        "interval_hours": previous_interval,
                        "interval_source": previous_source,
                    }
            except ProviderParseError as exc:
                _append_warning(warnings, f"历史实际结算解析失败：{exc}")
        elif "history" in bundle.errors:
            _append_warning(
                warnings,
                "历史实际结算请求失败：" + bundle.errors["history"]["reason"],
            )

        if (
            explicit_interval is not None
            and history_interval is not None
            and not math.isclose(explicit_interval, history_interval)
        ):
            _append_warning(
                warnings,
                "ticker周期与连续历史结算周期不一致，当前值采用ticker明确周期",
            )
        current_interval = explicit_interval or history_interval
        current = {
            "rate": rate,
            "type": "estimated",
            "funding_time": None,
            "next_funding_time": next_time,
            "interval_hours": current_interval,
            "interval_source": (
                "fundingIntervalHour"
                if explicit_interval is not None
                else "consecutive_history"
                if history_interval is not None
                else None
            ),
        }
        _freshness_warnings(current, previous, now, warnings)
        return _base_available_result(self, bundle, current, previous, warnings)


class BinanceFundingProvider(FundingProvider):
    key = "binance"
    display_name = "Binance"
    symbol = "BTCUSDT"

    @property
    def endpoints(self) -> dict[str, str]:
        return {
            "current": BINANCE_CURRENT_URL,
            "history": BINANCE_HISTORY_URL,
            "info": BINANCE_INFO_URL,
        }

    def parse_available(
        self, bundle: ProviderBundle, now: datetime
    ) -> dict[str, Any]:
        current_row = _require_mapping(bundle.payloads["current"], "Binance current响应")
        if "code" in current_row and "msg" in current_row:
            raise ProviderParseError(
                f"Binance业务错误 {current_row.get('code')}：{current_row.get('msg')}"
            )
        if current_row.get("symbol") != self.symbol:
            raise ProviderParseError(f"Binance current没有目标合约 {self.symbol}")
        rate = coerce_number(
            current_row.get("lastFundingRate"), "Binance lastFundingRate"
        )
        next_ms, next_time = timestamp_ms(
            current_row.get("nextFundingTime"), "Binance nextFundingTime"
        )

        warnings: list[str] = []
        explicit_interval: float | None = None
        info_payload = bundle.payloads.get("info")
        if info_payload is not None:
            try:
                rows = _require_list(info_payload, "Binance fundingInfo响应")
                matches = [
                    _require_mapping(item, "Binance fundingInfo记录")
                    for item in rows
                    if isinstance(item, dict) and item.get("symbol") == self.symbol
                ]
                if matches:
                    explicit_interval = coerce_positive_hours(
                        matches[0].get("fundingIntervalHours"),
                        "Binance fundingIntervalHours",
                    )
            except ProviderParseError as exc:
                _append_warning(warnings, f"fundingInfo解析失败：{exc}")
        elif "info" in bundle.errors:
            _append_warning(
                warnings,
                "fundingInfo请求失败：" + bundle.errors["info"]["reason"],
            )

        previous = _empty_settled()
        history_interval: float | None = None
        latest_ms: int | None = None
        history_payload = bundle.payloads.get("history")
        if history_payload is not None:
            try:
                rows = _require_list(history_payload, "Binance funding history响应")
                parsed_rows: list[tuple[int, str, float]] = []
                for raw_row in rows:
                    row = _require_mapping(raw_row, "Binance funding history记录")
                    if row.get("symbol") != self.symbol:
                        continue
                    row_ms, row_time = timestamp_ms(
                        row.get("fundingTime"), "Binance fundingTime"
                    )
                    settled_rate = coerce_number(
                        row.get("fundingRate"), "Binance history fundingRate"
                    )
                    parsed_rows.append((row_ms, row_time, settled_rate))
                if not parsed_rows:
                    raise ProviderParseError("Binance history没有目标合约记录")
                parsed_rows.sort(key=lambda item: item[0], reverse=True)
                latest_ms = parsed_rows[0][0]
                history_interval = infer_history_interval(
                    [item[0] for item in parsed_rows]
                )
                previous = {
                    "rate": parsed_rows[0][2],
                    "funding_time": parsed_rows[0][1],
                    "interval_hours": history_interval,
                    "interval_source": (
                        "consecutive_history"
                        if history_interval is not None
                        else None
                    ),
                }
            except ProviderParseError as exc:
                _append_warning(warnings, f"历史实际结算解析失败：{exc}")
        elif "history" in bundle.errors:
            _append_warning(
                warnings,
                "历史实际结算请求失败：" + bundle.errors["history"]["reason"],
            )

        gap_interval = _interval_from_difference(next_ms, latest_ms)
        if explicit_interval is not None:
            current_interval = explicit_interval
            current_source = "fundingIntervalHours"
        elif history_interval is not None:
            current_interval = history_interval
            if gap_interval is not None and math.isclose(
                gap_interval, history_interval
            ):
                current_source = "latest_settled_to_nextFundingTime"
            else:
                current_source = "consecutive_history"
        else:
            # 单条 history 与 nextFundingTime 的差值可能包含漏掉的结算行。
            # 只有本模块明确支持的常见官方周期，才把这项单证据用于换算；
            # 例如16h不能区分“真实16h周期”和“漏掉一期8h记录”。
            if gap_interval in GAP_ONLY_SUPPORTED_INTERVALS:
                current_interval = gap_interval
                current_source = "latest_settled_to_nextFundingTime"
            else:
                current_interval = None
                current_source = None
                if gap_interval is not None:
                    _append_warning(
                        warnings,
                        "仅凭最近结算至下次结算的时间差无法可靠确认当前周期",
                    )
        if (
            explicit_interval is not None
            and gap_interval is not None
            and not math.isclose(explicit_interval, gap_interval)
        ):
            _append_warning(
                warnings,
                "明确周期与最近结算至下次结算的时间差不一致",
            )
        if (
            history_interval is not None
            and gap_interval is not None
            and not math.isclose(history_interval, gap_interval)
        ):
            _append_warning(
                warnings,
                "连续历史周期与最近结算至下次结算的时间差不一致，可能缺少最新结算记录",
            )
        if (
            explicit_interval is not None
            and history_interval is not None
            and not math.isclose(explicit_interval, history_interval)
        ):
            _append_warning(warnings, "明确周期与连续历史结算周期不一致")
        current = {
            "rate": rate,
            "type": "current",
            "funding_time": None,
            "next_funding_time": next_time,
            "interval_hours": current_interval,
            "interval_source": current_source,
        }
        _freshness_warnings(current, previous, now, warnings)
        return _base_available_result(self, bundle, current, previous, warnings)


def select_exchange_public_providers() -> list[FundingProvider]:
    """当前无需任何密钥；未来可在此加入经验证的 CoinGlass provider。"""

    return [OkxFundingProvider(), BybitFundingProvider(), BinanceFundingProvider()]


def _cache_path(output_dir: Path, provider: FundingProvider) -> Path:
    return output_dir / "cache" / f"{provider.key}_raw.json"


def _cache_miss(
    provider: FundingProvider, output_dir: Path, reason: str
) -> tuple[None, dict[str, Any]]:
    return None, {
        "used_cache": False,
        "status": "缓存未使用",
        "reason": reason,
        "cache_file": str(_cache_path(output_dir, provider)),
    }


def load_provider_cache(
    provider: FundingProvider,
    output_dir: Path,
    now: datetime,
) -> tuple[ProviderBundle | None, dict[str, Any]]:
    cache_path = _cache_path(output_dir, provider)
    if not cache_path.exists():
        return _cache_miss(provider, output_dir, "缓存文件不存在")
    try:
        envelope = loads_json_strict(cache_path.read_text(encoding="utf-8"), "缓存文件")
    except (OSError, FundingError) as exc:
        return _cache_miss(provider, output_dir, f"缓存不可用：{exc}")
    if not isinstance(envelope, dict) or envelope.get("provider") != provider.key:
        return _cache_miss(provider, output_dir, "缓存provider不匹配")
    if envelope.get("urls") != provider.endpoints:
        return _cache_miss(provider, output_dir, "缓存URL集合不匹配")
    try:
        fetched_at = parse_utc_timestamp(envelope.get("fetched_at_utc"))
    except (TypeError, ValueError):
        return _cache_miss(provider, output_dir, "缓存抓取时间无效")
    age = now.astimezone(timezone.utc) - fetched_at
    if age < timedelta(0):
        return _cache_miss(provider, output_dir, "缓存抓取时间晚于当前时间")
    if age >= CACHE_TTL:
        return _cache_miss(provider, output_dir, "缓存已过期（有效期5分钟）")
    payloads = envelope.get("payloads")
    requests = envelope.get("requests")
    errors = envelope.get("errors")
    if not isinstance(payloads, dict) or not isinstance(requests, list) or not isinstance(errors, dict):
        return _cache_miss(provider, output_dir, "缓存结构无效")
    cached_requests: list[dict[str, Any]] = []
    for raw_request in requests:
        if not isinstance(raw_request, dict):
            return _cache_miss(provider, output_dir, "缓存请求元数据无效")
        request_copy = dict(raw_request)
        request_copy["cache_status"] = "使用缓存"
        cached_requests.append(request_copy)
    cache_status = {
        "used_cache": True,
        "status": "使用缓存",
        "reason": "缓存仍在5分钟有效期内",
        "cache_file": str(cache_path),
    }
    return ProviderBundle(
        payloads=dict(payloads),
        requests=cached_requests,
        errors={key: dict(value) for key, value in errors.items() if isinstance(value, dict)},
        fetched_at=fetched_at,
        cache_status=cache_status,
        network_request_count=0,
    ), cache_status


def fetch_provider_bundle(
    provider: FundingProvider,
    output_dir: Path,
    now: datetime,
    *,
    refresh: bool,
    transport: Transport,
) -> ProviderBundle:
    miss_reason = "--refresh强制刷新" if refresh else "缓存未检查"
    if not refresh:
        cached, cache_status = load_provider_cache(provider, output_dir, now)
        if cached is not None:
            return cached
        miss_reason = cache_status["reason"]

    fetched_at = now.astimezone(timezone.utc)
    fetched_at_text = format_utc_timestamp(fetched_at)
    payloads: dict[str, Any] = {}
    requests: list[dict[str, Any]] = []
    errors: dict[str, dict[str, Any]] = {}
    request_count = 0
    current_failed = False
    for endpoint_name, url in provider.endpoints.items():
        if current_failed:
            reason = "current端点不可用，未继续请求非核心端点"
            errors[endpoint_name] = {
                "url": url,
                "http_status": None,
                "reason": reason,
            }
            requests.append(
                {
                    "endpoint": endpoint_name,
                    "url": url,
                    "http_status": None,
                    "fetched_at_utc": fetched_at_text,
                    "cache_status": "未请求",
                    "error": reason,
                }
            )
            continue
        request_count += 1
        try:
            response = transport(url)
            if not isinstance(response, HttpJsonResponse):
                raise ProviderHttpError(url, "transport返回类型无效")
            payloads[endpoint_name] = response.payload
            requests.append(
                {
                    "endpoint": endpoint_name,
                    "url": url,
                    "http_status": response.http_status,
                    "fetched_at_utc": fetched_at_text,
                    "cache_status": "网络获取",
                    "error": None,
                }
            )
        except ProviderHttpError as exc:
            recorded_reason = (
                f"HTTP {exc.http_status}：{exc.reason}"
                if exc.http_status is not None
                else exc.reason
            )
            error = {
                "url": url,
                "http_status": exc.http_status,
                "reason": recorded_reason,
            }
            errors[endpoint_name] = error
            requests.append(
                {
                    "endpoint": endpoint_name,
                    "url": url,
                    "http_status": exc.http_status,
                    "fetched_at_utc": fetched_at_text,
                    "cache_status": "网络获取",
                    "error": recorded_reason,
                }
            )
            if endpoint_name == "current":
                current_failed = True

    cache_status = {
        "used_cache": False,
        "status": "网络获取",
        "reason": miss_reason,
        "cache_file": str(_cache_path(output_dir, provider)),
    }
    envelope = {
        "schema_version": 1,
        "provider": provider.key,
        "fetched_at_utc": fetched_at_text,
        "urls": dict(provider.endpoints),
        "payloads": payloads,
        "requests": requests,
        "errors": errors,
    }
    atomic_write_json(_cache_path(output_dir, provider), envelope)
    return ProviderBundle(
        payloads=payloads,
        requests=requests,
        errors=errors,
        fetched_at=fetched_at,
        cache_status=cache_status,
        network_request_count=request_count,
    )


def build_provider_results(
    providers: list[FundingProvider],
    output_dir: Path,
    now: datetime,
    *,
    refresh: bool,
    transport: Transport,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for provider in providers:
        bundle = fetch_provider_bundle(
            provider,
            output_dir,
            now,
            refresh=refresh,
            transport=transport,
        )
        results[provider.key] = provider.build_result(bundle, now)
    return results


def _direction_consensus(values: list[float]) -> str:
    if not values:
        return "unavailable"
    if len(values) == 1:
        return "single_provider"
    if all(value > 0 for value in values):
        return "all_positive"
    if all(value < 0 for value in values):
        return "all_negative"
    return "mixed"


def build_result(
    provider_results: dict[str, dict[str, Any]],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or utc_now()
    missing_keys = [key for key in PROVIDER_ORDER if key not in provider_results]
    if missing_keys:
        raise FundingError("缺少provider结果：" + "、".join(missing_keys))

    providers = {key: provider_results[key] for key in PROVIDER_ORDER}
    comparable: list[tuple[str, float]] = []
    available_count = 0
    for key, result in providers.items():
        if result.get("status") == "available":
            available_count += 1
            value = result.get("normalized_8h", {}).get("current_rate")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                comparable.append((key, float(value)))

    reference_provider: str | None = None
    for key in PROVIDER_ORDER:
        if any(item[0] == key for item in comparable):
            reference_provider = key
            break

    reference: dict[str, Any] | None = None
    if reference_provider is not None:
        selected = providers[reference_provider]
        reference = {
            "provider": reference_provider,
            "provider_name": selected["provider_name"],
            "symbol": selected["symbol"],
            "current": dict(selected["current"]),
            "previous_settled": dict(selected["previous_settled"]),
            "normalized_8h": dict(selected["normalized_8h"]),
            "classification": selected["classification"],
            "fetched_at_utc": selected["fetched_at_utc"],
        }

    values = [item[1] for item in comparable]
    if len(values) >= 2:
        minimum = min(values)
        maximum = max(values)
        simple_mean = sum(values) / len(values)
        spread = maximum - minimum
    else:
        minimum = maximum = simple_mean = spread = None
    summary = {
        "available_provider_count": available_count,
        "normalized_provider_count": len(values),
        "min_8h": minimum,
        "max_8h": maximum,
        "simple_mean_8h": simple_mean,
        "spread_8h": spread,
        "direction_consensus": _direction_consensus(values),
        "mean_method": "simple_unweighted_arithmetic_mean",
        "is_oi_weighted": False,
    }

    warnings: list[str] = []
    okx = providers["okx"]
    bybit = providers["bybit"]
    binance = providers["binance"]
    if okx.get("status") != "available":
        _append_warning(warnings, f"OKX不可用：{okx.get('reason', '未知原因')}")
    if bybit.get("status") != "available":
        _append_warning(warnings, f"Bybit不可用：{bybit.get('reason', '未知原因')}")
    for key, result in providers.items():
        for warning in result.get("warnings", []):
            _append_warning(warnings, f"{result.get('provider_name', key)}：{warning}")
    if reference_provider is not None and reference_provider != "okx":
        _append_warning(warnings, f"reference Funding回退到{providers[reference_provider]['provider_name']}")
    if len(values) == 1:
        _append_warning(warnings, "只有一个provider具有可比较的8h等效Funding")
    if available_count > len(values):
        _append_warning(warnings, "存在当前Funding可用但周期无法可靠确认的provider")
    if binance.get("status") == "unavailable":
        best_effort_unavailable = ["binance"]
    else:
        best_effort_unavailable = []

    network_request_count = sum(
        int(result.get("network_request_count", 0)) for result in providers.values()
    )
    quality_checks = {
        "okx_required_available": okx.get("status") == "available",
        "reference_available": reference is not None,
        "provider_statuses": {
            key: result.get("status") for key, result in providers.items()
        },
        "providers_without_reliable_interval": [
            key
            for key, result in providers.items()
            if result.get("status") == "available"
            and result.get("normalized_8h", {}).get("current_rate") is None
        ],
        "providers_with_warnings": [
            key for key, result in providers.items() if result.get("warnings")
        ],
        "best_effort_unavailable": best_effort_unavailable,
        "cache_statuses": {
            key: result.get("cache_status") for key, result in providers.items()
        },
        "network_request_count": network_request_count,
    }
    return {
        "generated_at_utc": format_utc_timestamp(generated_at),
        "source_mode": SOURCE_MODE,
        "temporal_scope": "current_snapshot",
        "providers": providers,
        "reference_funding": reference,
        "summary": summary,
        "quality_checks": quality_checks,
        "cache_status": quality_checks["cache_statuses"],
        "network_request_count": network_request_count,
        "warnings": warnings,
    }


def determine_exit_code(result: dict[str, Any]) -> int:
    if result.get("reference_funding") is None:
        return 1
    if result.get("warnings"):
        return 2
    return 0


def format_rate(rate: float | None) -> str:
    if rate is None:
        return "unavailable"
    return f"{rate * 100:+.6f}%"


def format_interval(hours: float | None) -> str:
    if hours is None:
        return "unavailable"
    return f"{hours:g}h"


def format_consensus(value: str) -> str:
    return {
        "all_positive": "全部为正",
        "all_negative": "全部为负",
        "mixed": "正负混合",
        "single_provider": "仅单一可比较平台",
    }.get(value, value)


def render_markdown(result: dict[str, Any]) -> str:
    reference = result["reference_funding"]
    summary = result["summary"]
    warnings = result["warnings"]
    lines = [
        "## BTC 永续合约 Funding",
        "",
        f"生成时间（UTC）：{result['generated_at_utc']}",
        f"数据源模式：{result['source_mode']}",
        "",
        "### 参考 Funding",
        "",
    ]
    if reference is None:
        lines.extend(["未取得可计算8h等效值的可靠reference Funding。", ""])
    else:
        current = reference["current"]
        previous = reference["previous_settled"]
        normalized = reference["normalized_8h"]
        lines.extend(
            [
                f"平台：{reference['provider_name']}",
                f"原始 Funding：{format_rate(current['rate'])}",
                f"原始周期：{format_interval(current['interval_hours'])}",
                f"8h等效：{format_rate(normalized['current_rate'])}",
                "上一期实际结算："
                f"{format_rate(previous['rate'])}"
                f"（{previous['funding_time'] or 'unavailable'}；"
                f"{format_interval(previous['interval_hours'])}）",
                f"状态：{reference['classification']}",
                "",
            ]
        )
    lines.extend(
        [
            "### 各交易所",
            "",
            "| 平台 | 当前/预计 | 周期 | 8h等效 | 上一期实际结算 | 状态 |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for key in PROVIDER_ORDER:
        provider = result["providers"][key]
        if provider["status"] == "available":
            current = provider["current"]
            previous = provider["previous_settled"]
            lines.append(
                f"| {provider['provider_name']} | {format_rate(current['rate'])}"
                f"（{current['type']}） | {format_interval(current['interval_hours'])} | "
                f"{format_rate(provider['normalized_8h']['current_rate'])} | "
                f"{format_rate(previous['rate'])} | {provider['classification']} |"
            )
        else:
            reason = str(provider.get("reason") or "未知原因").replace("|", "/")
            lines.append(
                f"| {provider['provider_name']} | unavailable | unavailable | "
                f"unavailable | unavailable | unavailable：{reason} |"
            )

    range_text = (
        "unavailable"
        if summary["min_8h"] is None or summary["max_8h"] is None
        else f"{format_rate(summary['min_8h'])} ～ {format_rate(summary['max_8h'])}"
    )
    provider_details: list[str] = []
    for key in PROVIDER_ORDER:
        provider = result["providers"][key]
        request_summary = "；".join(
            f"{item['endpoint']} HTTP {item['http_status'] if item['http_status'] is not None else 'unavailable'}"
            f"/{item['cache_status']}"
            for item in provider["requests"]
        )
        provider_details.append(
            f"- {provider['provider_name']}：{provider['cache_status'].get('status', '未知')}；"
            f"抓取时间 {provider['fetched_at_utc']}；{request_summary}"
        )
    warning_text = "无" if not warnings else "；".join(warnings)
    lines.extend(
        [
            "",
            "### 市场概览",
            "",
            f"可用平台数：{summary['available_provider_count']}",
            f"可比较平台数：{summary['normalized_provider_count']}",
            f"简单平均8h Funding：{format_rate(summary['simple_mean_8h'])}",
            f"区间：{range_text}",
            f"Spread：{format_rate(summary['spread_8h'])}",
            f"方向一致性：{format_consensus(summary['direction_consensus'])}",
            "",
            "### 数据质量",
            "",
            f"Warnings：{warning_text}",
            *provider_details,
            "",
            COINGLASS_UPGRADE_NOTE,
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path, str]:
    markdown = render_markdown(result)
    json_path = output_dir / "latest.json"
    markdown_path = output_dir / "latest.md"
    atomic_write_json(json_path, result)
    atomic_write_text(markdown_path, markdown)
    return json_path, markdown_path, markdown


def execute(
    *,
    refresh: bool,
    output_dir: Path,
    now: datetime | None = None,
    providers: list[FundingProvider] | None = None,
    transport: Transport = fetch_json_response,
) -> tuple[dict[str, Any], str, int]:
    now = now or utc_now()
    providers = providers or select_exchange_public_providers()
    provider_results = build_provider_results(
        providers,
        output_dir,
        now,
        refresh=refresh,
        transport=transport,
    )
    result = build_result(provider_results, generated_at=now)
    _json_path, _markdown_path, markdown = write_outputs(result, output_dir)
    return result, markdown, determine_exit_code(result)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="获取并标准化BTC永续合约Funding Rate。"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="忽略5分钟缓存并强制刷新"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录（默认：{DEFAULT_OUTPUT_DIR}）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        _result, markdown, exit_code = execute(
            refresh=args.refresh,
            output_dir=args.output_dir,
        )
    except (FundingError, OSError, ValueError) as exc:
        print(f"Funding取数失败：{exc}", file=sys.stderr)
        print(
            "退出码1：所有provider均失败、没有可用reference或核心计算失败。",
            file=sys.stderr,
        )
        return 1

    print(markdown, end="")
    if exit_code == 0:
        print("退出码0：OKX与交叉验证数据可用，Funding质量正常。")
    elif exit_code == 2:
        print("退出码2：主体可用，但存在provider、周期、时效或质量警告。")
    else:
        print("退出码1：没有可形成reference的可靠Funding。", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
