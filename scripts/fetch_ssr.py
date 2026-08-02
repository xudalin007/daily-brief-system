#!/usr/bin/env python3
"""获取并校验 BGeometrics 正式 SSR Oscillator 与辅助 raw SSR 日线。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SSR_OSCILLATOR_ENDPOINT = "https://api.bitcoin-data.com/v1/ssr-oscillator"
RAW_SSR_ENDPOINT = "https://api.bitcoin-data.com/v1/ssr"
PROVIDER_NAME = "BGeometrics"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ssr" / "generated"

CACHE_TTL = timedelta(hours=6)
NETWORK_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REQUEST_HISTORY_DAYS = 90
REQUIRED_HISTORY_DAYS = 40
REQUEST_SIZE = 500
MAX_FALLBACK_DAYS = 7
QUALITY_WINDOW_DAYS = 40
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")

# BGeometrics 官方接口说明采用 SSR/SMA365，并以 1 为中心；它不是当前 rules
# 中采用的经典 0 中心、±2 统计极端口径。抓取值是真实正式指标，但不能套用旧口径。
DEFINITION_CONFIRMED = True
DEFINITION_COMPATIBLE_WITH_RULES = False
DEFINITION_FORMULA = "SSR / SMA365"
DEFINITION_CENTER = 1.0
DEFINITION_DESCRIPTION = (
    "BGeometrics将SSR定义为BTC市值与USDT+USDC总市值之比，并将SSR Oscillator"
    "定义为SSR相对365日移动平均的归一化比值（SSR / SMA365）；高于1表示SSR相对"
    "历史均值偏高，低于1表示SSR受压、稳定币相对购买力较通常更强。"
)


class SsrError(Exception):
    """正式 SSR Oscillator 无法安全取得或计算。"""


@dataclass
class HttpJson:
    payload: Any
    http_status: int


@dataclass
class ParsedSeries:
    name: str
    value_field: str
    values: dict[date, float]
    raw_valid_dates: list[date]
    total_records: int
    duplicate_dates: list[dict[str, Any]]
    invalid_records: list[dict[str, Any]]
    sort_order: str


SERIES_CONFIG: dict[str, dict[str, Any]] = {
    "ssr_oscillator": {
        "name": "SSR Oscillator",
        "endpoint": SSR_OSCILLATOR_ENDPOINT,
        "value_field": "ssrStablecoinOscillator",
        "cache_filename": "ssr_oscillator_raw.json",
        "positive_only": True,
        "required": True,
    },
    "raw_ssr": {
        "name": "Raw SSR",
        "endpoint": RAW_SSR_ENDPOINT,
        "value_field": "ssrStablecoin",
        "cache_filename": "raw_ssr_raw.json",
        "positive_only": True,
        "required": False,
    },
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("时间戳不是字符串")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("时间戳缺少时区")
    return parsed.astimezone(timezone.utc)


def parse_date_strict(value: Any) -> date:
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        raise ValueError("日期必须使用 YYYY-MM-DD 格式")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("日期格式无效")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON包含非有限数值 {value}")


def loads_json_strict(text: str, context: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SsrError(f"{context}不是有效JSON：{exc}") from exc


def extract_records(payload: Any, series_name: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise SsrError(f"{series_name} JSON结构无效：顶层应为记录数组或包含data数组")


def fetch_json(url: str, timeout: int = NETWORK_TIMEOUT_SECONDS) -> HttpJson:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "daily-brief-system-ssr-fetcher/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise SsrError(f"HTTP请求失败（状态码{exc.code}）：{url}") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise SsrError(f"网络请求失败：{reason}；URL：{url}") from exc
    except (TimeoutError, OSError, HTTPException) as exc:
        raise SsrError(f"网络请求超时或连接失败：{exc}；URL：{url}") from exc
    if not 200 <= status < 300:
        raise SsrError(f"HTTP请求失败（状态码{status}）：{url}")
    if not raw:
        raise SsrError(f"接口返回空响应：{url}")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SsrError(f"接口响应超过安全上限{MAX_RESPONSE_BYTES}字节：{url}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SsrError(f"接口响应不是有效UTF-8：{url}") from exc
    return HttpJson(loads_json_strict(text, f"接口响应（{url}）"), status)


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
        raise SsrError(f"无法原子写入文件{path}：{exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SsrError(f"无法序列化JSON文件{path}：{exc}") from exc
    atomic_write_text(path, text + "\n")


def build_request_url(endpoint: str, start_day: date, end_day: date) -> str:
    return endpoint + "?" + urlencode(
        {
            "startday": start_day.isoformat(),
            "endday": end_day.isoformat(),
            "size": REQUEST_SIZE,
        }
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _safe_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _sort_order(items: list[date]) -> str:
    if len(items) < 2:
        return "single_or_empty"
    ascending = all(left <= right for left, right in zip(items, items[1:]))
    descending = all(left >= right for left, right in zip(items, items[1:]))
    if ascending and not descending:
        return "ascending"
    if descending and not ascending:
        return "descending"
    if ascending and descending:
        return "constant"
    return "unsorted"


def parse_series(
    payload: Any,
    value_field: str,
    series_name: str,
    *,
    positive_only: bool,
) -> ParsedSeries:
    records = extract_records(payload, series_name)
    raw_dates: list[date] = []
    occurrences: dict[date, int] = {}
    candidates: dict[date, list[float]] = {}
    invalid: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            invalid.append({"index": index, "date": None, "reason": "记录不是对象"})
            continue
        try:
            record_date = parse_date_strict(record.get("d"))
        except (TypeError, ValueError) as exc:
            invalid.append(
                {
                    "index": index,
                    "date": _safe_value(record.get("d")),
                    "reason": f"日期无效：{exc}",
                }
            )
            continue
        raw_dates.append(record_date)
        occurrences[record_date] = occurrences.get(record_date, 0) + 1
        if value_field not in record:
            invalid.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "reason": f"缺少正式字段{value_field}",
                }
            )
            continue
        value = _number(record[value_field])
        if value is None:
            invalid.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "value": _safe_value(record[value_field]),
                    "reason": f"{value_field}不是有限数值",
                }
            )
        elif positive_only and value <= 0:
            invalid.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "value": value,
                    "reason": f"{value_field}不是正数",
                }
            )
        else:
            candidates.setdefault(record_date, []).append(value)

    values: dict[date, float] = {}
    duplicates: list[dict[str, Any]] = []
    for record_date, count in occurrences.items():
        valid = candidates.get(record_date, [])
        if count > 1:
            all_equal = len(valid) == count and all(item == valid[0] for item in valid)
            duplicates.append(
                {
                    "date": record_date.isoformat(),
                    "count": count,
                    "conflicting_or_invalid": not all_equal,
                }
            )
            if all_equal:
                values[record_date] = valid[0]
        elif len(valid) == 1:
            values[record_date] = valid[0]
    return ParsedSeries(
        name=series_name,
        value_field=value_field,
        values=values,
        raw_valid_dates=raw_dates,
        total_records=len(records),
        duplicate_dates=sorted(duplicates, key=lambda item: item["date"]),
        invalid_records=invalid,
        sort_order=_sort_order(raw_dates),
    )


def _cache_miss(reason: str, path: Path) -> tuple[None, dict[str, Any]]:
    return None, {
        "used_cache": False,
        "status": "缓存未使用",
        "reason": reason,
        "cache_file": str(path),
    }


def load_cache(
    path: Path,
    source_key: str,
    endpoint: str,
    required_start: date,
    required_end: date,
    now: datetime,
) -> tuple[Any | None, dict[str, Any]]:
    if not path.exists():
        return _cache_miss("缓存文件不存在", path)
    try:
        envelope = loads_json_strict(path.read_text(encoding="utf-8"), f"缓存文件{path}")
    except (OSError, UnicodeError, SsrError) as exc:
        return _cache_miss(f"缓存不可用：{exc}", path)
    if not isinstance(envelope, dict):
        return _cache_miss("缓存顶层结构无效", path)
    if envelope.get("source") != source_key or envelope.get("endpoint") != endpoint:
        return _cache_miss("缓存来源不匹配", path)
    try:
        fetched_at = parse_utc_timestamp(envelope.get("fetched_at_utc"))
        request_start = parse_date_strict(envelope["request"]["startday"])
        request_end = parse_date_strict(envelope["request"]["endday"])
        request_size = envelope["request"]["size"]
    except (KeyError, TypeError, ValueError):
        return _cache_miss("缓存元数据无效", path)
    request_url = envelope.get("request_url")
    http_status = envelope.get("http_status")
    if request_size != REQUEST_SIZE:
        return _cache_miss("缓存请求size元数据无效", path)
    if request_url != build_request_url(endpoint, request_start, request_end):
        return _cache_miss("缓存请求URL与日期范围不一致", path)
    if (
        isinstance(http_status, bool)
        or not isinstance(http_status, int)
        or not 200 <= http_status < 300
    ):
        return _cache_miss("缓存HTTP状态元数据无效", path)
    age = now.astimezone(timezone.utc) - fetched_at
    if age < timedelta(0):
        return _cache_miss("缓存抓取时间晚于当前时间", path)
    if age >= CACHE_TTL:
        return _cache_miss("缓存已超过6小时有效期", path)
    if request_start > required_start or request_end < required_end:
        return _cache_miss("缓存未覆盖本次所需日期", path)
    if "raw_data" not in envelope:
        return _cache_miss("缓存缺少原始返回数据", path)
    config = SERIES_CONFIG[source_key]
    try:
        parsed = parse_series(
            envelope["raw_data"],
            config["value_field"],
            config["name"],
            positive_only=config["positive_only"],
        )
        if not parsed.values:
            raise SsrError(f"缓存不含正式字段{config['value_field']}的有效值")
    except SsrError as exc:
        return _cache_miss(f"缓存原始JSON结构无效：{exc}", path)
    return envelope["raw_data"], {
        "used_cache": True,
        "status": "使用缓存",
        "reason": "缓存仍在6小时有效期内",
        "cache_file": str(path),
        "endpoint": endpoint,
        "request_url": request_url,
        "http_status": http_status,
        "fetched_at_utc": format_utc_timestamp(fetched_at),
        "age_seconds": round(age.total_seconds(), 3),
    }


def _normalize_fetch_result(value: Any) -> HttpJson:
    return value if isinstance(value, HttpJson) else HttpJson(value, 200)


def obtain_payloads(
    as_of: date,
    output_dir: Path,
    refresh: bool,
    now: datetime,
    fetcher: Callable[[str, int], Any] = fetch_json,
    endpoints: dict[str, str] | None = None,
) -> tuple[dict[str, Any | None], dict[str, dict[str, Any]], int]:
    endpoints = endpoints or {
        "ssr_oscillator": SSR_OSCILLATOR_ENDPOINT,
        "raw_ssr": RAW_SSR_ENDPOINT,
    }
    request_start = as_of - timedelta(days=REQUEST_HISTORY_DAYS)
    required_start = as_of - timedelta(days=REQUIRED_HISTORY_DAYS - 1)
    payloads: dict[str, Any | None] = {}
    statuses: dict[str, dict[str, Any]] = {}
    pending_by_url: dict[str, list[tuple[str, Path, str]]] = {}

    for key, config in SERIES_CONFIG.items():
        endpoint = endpoints[key]
        request_url = build_request_url(endpoint, request_start, as_of)
        path = output_dir / "cache" / config["cache_filename"]
        if refresh:
            payload, status = _cache_miss("--refresh强制刷新", path)
        else:
            payload, status = load_cache(
                path, key, endpoint, required_start, as_of, now
            )
        if payload is not None:
            payloads[key] = payload
            statuses[key] = status
        else:
            pending_by_url.setdefault(request_url, []).append(
                (key, path, status["reason"])
            )

    network_count = 0
    for request_url, targets in pending_by_url.items():
        network_count += 1
        try:
            fetched = _normalize_fetch_result(fetcher(request_url, NETWORK_TIMEOUT_SECONDS))
            extract_records(fetched.payload, SERIES_CONFIG[targets[0][0]]["name"])
        except SsrError as exc:
            if any(SERIES_CONFIG[key]["required"] for key, _path, _reason in targets):
                raise
            for key, path, reason in targets:
                payloads[key] = None
                statuses[key] = {
                    "used_cache": False,
                    "status": "不可用",
                    "reason": str(exc),
                    "cache_miss_reason": reason,
                    "cache_file": str(path),
                    "endpoint": endpoints[key],
                    "request_url": request_url,
                    "http_status": None,
                    "fetched_at_utc": format_utc_timestamp(now),
                }
            continue

        fetched_at = format_utc_timestamp(now)
        for key, path, reason in targets:
            config = SERIES_CONFIG[key]
            try:
                parsed = parse_series(
                    fetched.payload,
                    config["value_field"],
                    config["name"],
                    positive_only=config["positive_only"],
                )
                if not parsed.values:
                    raise SsrError(
                        f"响应不含正式字段{config['value_field']}的有效值"
                    )
            except SsrError as exc:
                if config["required"]:
                    raise
                payloads[key] = None
                statuses[key] = {
                    "used_cache": False,
                    "status": "不可用",
                    "reason": str(exc),
                    "cache_miss_reason": reason,
                    "cache_file": str(path),
                    "endpoint": endpoints[key],
                    "request_url": request_url,
                    "http_status": fetched.http_status,
                    "fetched_at_utc": fetched_at,
                }
                continue
            atomic_write_json(
                path,
                {
                    "source": key,
                    "endpoint": endpoints[key],
                    "fetched_at_utc": fetched_at,
                    "request_url": request_url,
                    "http_status": fetched.http_status,
                    "request": {
                        "startday": request_start.isoformat(),
                        "endday": as_of.isoformat(),
                        "size": REQUEST_SIZE,
                    },
                    "raw_data": fetched.payload,
                },
            )
            payloads[key] = fetched.payload
            statuses[key] = {
                "used_cache": False,
                "status": "网络请求",
                "reason": reason,
                "cache_file": str(path),
                "endpoint": endpoints[key],
                "request_url": request_url,
                "http_status": fetched.http_status,
                "fetched_at_utc": fetched_at,
                "age_seconds": 0,
            }
    return payloads, statuses, network_count


def resolve_history_point(values: dict[date, float], target: date) -> dict[str, Any]:
    for fallback_days in range(MAX_FALLBACK_DAYS + 1):
        candidate = target - timedelta(days=fallback_days)
        if candidate in values:
            return {
                "available": True,
                "target_date": target.isoformat(),
                "actual_date": candidate.isoformat(),
                "value": values[candidate],
                "fallback_days": fallback_days,
            }
    return {
        "available": False,
        "target_date": target.isoformat(),
        "actual_date": None,
        "value": None,
        "fallback_days": None,
    }


def calculate_change(current: float, point: dict[str, Any]) -> dict[str, Any]:
    historical = point.get("value") if point.get("available") else None
    if historical is None:
        return {
            "absolute_change": None,
            "percentage_change": None,
            "percent": None,
            "direction": "unavailable",
        }
    absolute = current - historical
    percentage = None if historical == 0 else current / historical - 1
    direction = "上升" if absolute > 0 else "下降" if absolute < 0 else "不变"
    return {
        "absolute_change": absolute,
        "percentage_change": percentage,
        "percent": None if percentage is None else percentage * 100,
        "direction": direction,
    }


def classify_oscillator(
    value: float,
    *,
    compatible_with_daily_brief_rules: bool,
) -> dict[str, Any]:
    """按正式 rules 分类；仅在提供方公式确认兼容时才允许启用。"""
    numeric = _number(value)
    if numeric is None:
        raise ValueError("SSR Oscillator必须是有限数值")
    if not compatible_with_daily_brief_rules:
        return {
            "direction": "unavailable",
            "extreme_status": "not_applicable",
            "is_trading_signal": False,
            "classification_applied": False,
        }
    direction = (
        "stronger_stablecoin_purchasing_power"
        if numeric < 0
        else "weaker_stablecoin_purchasing_power"
        if numeric > 0
        else "balanced"
    )
    extreme = (
        "statistical_extreme_negative"
        if numeric <= -2
        else "statistical_extreme_positive"
        if numeric >= 2
        else "not_extreme"
    )
    return {
        "direction": direction,
        "extreme_status": extreme,
        "is_trading_signal": False,
        "classification_applied": True,
    }


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _issue_in_window(issue: dict[str, Any], start: date, end: date) -> bool:
    try:
        value = parse_date_strict(issue.get("date"))
    except (TypeError, ValueError):
        return False
    return start <= value <= end


def build_quality(parsed: ParsedSeries, latest: date) -> dict[str, Any]:
    start = latest - timedelta(days=QUALITY_WINDOW_DAYS - 1)
    expected = _dates(start, latest)
    raw_in_window = [item for item in parsed.raw_valid_dates if start <= item <= latest]
    raw_set = set(raw_in_window)
    duplicates = [
        item
        for item in parsed.duplicate_dates
        if start <= parse_date_strict(item["date"]) <= latest
    ]
    invalid = [item for item in parsed.invalid_records if _issue_in_window(item, start, latest)]
    sort_order = _sort_order(raw_in_window)
    missing = [item.isoformat() for item in expected if item not in raw_set]
    return {
        "window_start": start.isoformat(),
        "window_end": latest.isoformat(),
        "expected_date_count": QUALITY_WINDOW_DAYS,
        "record_count": len(raw_in_window),
        "unique_date_count": len(raw_set),
        "valid_record_count": sum(1 for item in parsed.values if start <= item <= latest),
        "missing_dates": missing,
        "duplicate_dates": duplicates,
        "invalid_values": invalid,
        "latest_date": latest.isoformat(),
        "sort_order": sort_order,
        "is_complete": not missing
        and not duplicates
        and not invalid
        and sort_order != "unsorted",
    }


def _append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _summarize_dates(items: list[str], limit: int = 12) -> str:
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + f" 等{len(items)}日"


def _build_metric(
    parsed: ParsedSeries,
    requested_as_of: date,
) -> tuple[dict[str, Any], date, int, dict[str, Any], list[str]]:
    eligible = [item for item in parsed.values if item <= requested_as_of]
    if not eligible:
        raise SsrError(f"截至{requested_as_of.isoformat()}没有可用的正式{parsed.name}记录")
    latest = max(eligible)
    current = parsed.values[latest]
    previous = resolve_history_point(parsed.values, latest - timedelta(days=1))
    d7 = resolve_history_point(parsed.values, latest - timedelta(days=7))
    d30 = resolve_history_point(parsed.values, latest - timedelta(days=30))
    delay = (requested_as_of - latest).days
    quality = build_quality(parsed, latest)
    warnings: list[str] = []
    if delay > 1:
        _append_warning(warnings, f"{parsed.name}相对分析截止日期延迟{delay}天")
    for label, point in (("1D", previous), ("7D", d7), ("30D", d30)):
        if not point["available"]:
            _append_warning(
                warnings,
                f"{parsed.name} {label}目标日期{point['target_date']}向前7日内无正式值",
            )
        elif point["fallback_days"]:
            _append_warning(
                warnings,
                f"{parsed.name} {label}目标日期{point['target_date']}回退"
                f"{point['fallback_days']}日，实际使用{point['actual_date']}",
            )
    if quality["missing_dates"]:
        _append_warning(
            warnings,
            f"{parsed.name}最近40日缺失日期：{_summarize_dates(quality['missing_dates'])}",
        )
    if quality["duplicate_dates"]:
        _append_warning(
            warnings,
            f"{parsed.name}最近40日存在重复日期："
            + _summarize_dates([item["date"] for item in quality["duplicate_dates"]]),
        )
    if quality["invalid_values"]:
        dates = [item.get("date") for item in quality["invalid_values"] if item.get("date")]
        _append_warning(
            warnings,
            f"{parsed.name}最近40日存在无效值：{_summarize_dates(dates)}",
        )
    if quality["sort_order"] == "unsorted":
        _append_warning(warnings, f"{parsed.name}最近40日原始记录未按日期排序")
    if any(item.get("date") is None for item in parsed.invalid_records):
        _append_warning(warnings, f"{parsed.name}原始响应存在日期无效记录")
    invalid_after_latest = [
        item
        for item in parsed.invalid_records
        if _issue_in_window(item, latest + timedelta(days=1), requested_as_of)
    ]
    if invalid_after_latest:
        _append_warning(
            warnings,
            f"{parsed.name}在最新有效日线之后存在无效记录："
            + _summarize_dates(
                [item["date"] for item in invalid_after_latest if item.get("date")]
            ),
        )
    duplicates_after_latest = [
        item
        for item in parsed.duplicate_dates
        if _issue_in_window(item, latest + timedelta(days=1), requested_as_of)
    ]
    if duplicates_after_latest:
        _append_warning(
            warnings,
            f"{parsed.name}在最新有效日线之后存在重复日期："
            + _summarize_dates([item["date"] for item in duplicates_after_latest]),
        )
    metric = {
        "status": "Verified",
        "current": current,
        "date": latest.isoformat(),
        "source_date": latest.isoformat(),
        "data_delay_days": delay,
        "previous": previous,
        "d7": d7,
        "d30": d30,
        "change_1d": calculate_change(current, previous),
        "change_7d": calculate_change(current, d7),
        "change_30d": calculate_change(current, d30),
    }
    if warnings:
        metric["status"] = "Delayed" if delay > 1 and len(warnings) == 1 else "Provisional"
    return metric, latest, delay, quality, warnings


def _unavailable_raw(reason: str, basis_date: date) -> dict[str, Any]:
    def point(offset: int) -> dict[str, Any]:
        return {
            "available": False,
            "target_date": (basis_date - timedelta(days=offset)).isoformat(),
            "actual_date": None,
            "value": None,
            "fallback_days": None,
        }

    change = {
        "absolute_change": None,
        "percentage_change": None,
        "percent": None,
        "direction": "unavailable",
    }
    return {
        "status": "Unavailable",
        "current": None,
        "date": None,
        "source_date": None,
        "data_delay_days": None,
        "previous": point(1),
        "d7": point(7),
        "d30": point(30),
        "change_1d": dict(change),
        "change_7d": dict(change),
        "change_30d": dict(change),
        "unavailable_reason": reason,
    }


def build_result(
    oscillator_payload: Any,
    raw_payload: Any | None,
    requested_as_of: date,
    *,
    generated_at: datetime | None = None,
    cache_statuses: dict[str, dict[str, Any]] | None = None,
    network_request_count: int = 0,
    endpoints: dict[str, str] | None = None,
    definition_confirmed: bool = DEFINITION_CONFIRMED,
    definition_compatible: bool = DEFINITION_COMPATIBLE_WITH_RULES,
    definition_description: str = DEFINITION_DESCRIPTION,
) -> dict[str, Any]:
    endpoints = endpoints or {
        "ssr_oscillator": SSR_OSCILLATOR_ENDPOINT,
        "raw_ssr": RAW_SSR_ENDPOINT,
    }
    generated_at = generated_at or utc_now()
    cache_statuses = cache_statuses or {
        key: {
            "used_cache": False,
            "status": "测试数据",
            "endpoint": endpoints[key],
            "request_url": endpoints[key],
            "http_status": 200 if payload is not None else None,
            "fetched_at_utc": format_utc_timestamp(generated_at),
        }
        for key, payload in (("ssr_oscillator", oscillator_payload), ("raw_ssr", raw_payload))
    }
    oscillator_parsed = parse_series(
        oscillator_payload,
        "ssrStablecoinOscillator",
        "SSR Oscillator",
        positive_only=True,
    )
    if not oscillator_parsed.values:
        raise SsrError(
            "SSR Oscillator响应中没有合法有限数值的正式ssrStablecoinOscillator字段"
        )
    oscillator, latest, delay, oscillator_quality, warnings = _build_metric(
        oscillator_parsed, requested_as_of
    )
    effective_definition_compatibility = (
        definition_confirmed and definition_compatible
    )
    classification = classify_oscillator(
        oscillator["current"],
        compatible_with_daily_brief_rules=effective_definition_compatibility,
    )
    oscillator.update(classification)

    if not definition_confirmed:
        _append_warning(warnings, "BGeometrics SSR Oscillator官方定义尚未可靠确认，未应用方向或极端分类")
        oscillator["status"] = "Provisional"
    elif not definition_compatible:
        _append_warning(
            warnings,
            "BGeometrics SSR Oscillator定义为SSR/SMA365且以1为中心，与当前正式规则的"
            "经典0中心/±2口径不兼容；未应用方向或极端分类",
        )
        oscillator["status"] = "Provisional"

    raw_quality: dict[str, Any] | None = None
    raw_latest: date | None = None
    if raw_payload is None:
        reason = cache_statuses.get("raw_ssr", {}).get("reason") or "Raw SSR接口不可用"
        raw_ssr = _unavailable_raw(reason, latest)
        _append_warning(warnings, f"Raw SSR辅助数据不可用：{reason}")
    else:
        try:
            raw_parsed = parse_series(
                raw_payload,
                "ssrStablecoin",
                "Raw SSR",
                positive_only=True,
            )
            if not raw_parsed.values:
                raise SsrError("响应不含合法有限正数的正式字段ssrStablecoin")
            raw_ssr, raw_latest, _raw_delay, raw_quality, raw_warnings = _build_metric(
                raw_parsed, requested_as_of
            )
            for warning in raw_warnings:
                _append_warning(warnings, warning)
        except SsrError as exc:
            raw_ssr = _unavailable_raw(str(exc), latest)
            _append_warning(warnings, f"Raw SSR辅助数据不可用：{exc}")

    date_alignment = (
        "aligned"
        if raw_latest is not None and raw_latest == latest
        else "misaligned"
        if raw_latest is not None
        else "unavailable"
    )
    if date_alignment == "misaligned":
        _append_warning(
            warnings,
            "SSR Oscillator与Raw SSR最新日期不一致："
            f"Oscillator={latest.isoformat()}，Raw SSR={raw_latest.isoformat()}",
        )

    source_endpoints: dict[str, Any] = {}
    fetched_times: list[str] = []
    for key, field in (
        ("ssr_oscillator", "ssrStablecoinOscillator"),
        ("raw_ssr", "ssrStablecoin"),
    ):
        status = cache_statuses.get(key, {})
        fetched_at = status.get("fetched_at_utc")
        if isinstance(fetched_at, str):
            fetched_times.append(fetched_at)
        source_endpoints[key] = {
            "endpoint": endpoints[key],
            "request_url": status.get("request_url", endpoints[key]),
            "value_field": field,
            "http_status": status.get("http_status"),
            "fetched_at_utc": fetched_at,
            "cache_status": status.get("status"),
        }

    source_statuses = [cache_statuses.get(key, {}) for key in SERIES_CONFIG]
    all_used_cache = all(item.get("used_cache") is True for item in source_statuses)
    any_network = any(item.get("status") == "网络请求" for item in source_statuses)
    aggregate_cache_status = (
        "使用缓存"
        if all_used_cache
        else "网络请求"
        if any_network
        else "部分缓存或数据不可用"
    )
    return {
        "generated_at_utc": format_utc_timestamp(generated_at),
        "requested_as_of": requested_as_of.isoformat(),
        "actual_latest_date": latest.isoformat(),
        "data_delay_days": delay,
        "ssr_oscillator": oscillator,
        "raw_ssr": raw_ssr,
        "date_alignment": date_alignment,
        "definition": {
            "provider": PROVIDER_NAME,
            "confirmed": bool(definition_confirmed),
            "description": definition_description,
            "formula": DEFINITION_FORMULA,
            "center": DEFINITION_CENTER,
            "compatible_with_daily_brief_rules": bool(
                effective_definition_compatibility
            ),
            "rules_classification_applied": classification["classification_applied"],
        },
        "source": {
            "provider": PROVIDER_NAME,
            "base_service": "Bitcoin Data API",
            "urls": [endpoints["ssr_oscillator"]]
            + ([endpoints["raw_ssr"]] if endpoints["raw_ssr"] != endpoints["ssr_oscillator"] else []),
            "fetched_at_utc": max(fetched_times) if fetched_times else format_utc_timestamp(generated_at),
            "endpoints": source_endpoints,
        },
        "cache_status": {
            "ttl_hours": CACHE_TTL.total_seconds() / 3600,
            "used_cache": all_used_cache,
            "status": aggregate_cache_status,
            "ssr_oscillator": dict(cache_statuses.get("ssr_oscillator", {})),
            "raw_ssr": dict(cache_statuses.get("raw_ssr", {})),
        },
        "network_request_count": network_request_count,
        "quality_checks": {
            "ssr_oscillator": oscillator_quality,
            "raw_ssr": raw_quality,
            "latest_date": latest.isoformat(),
            "date_alignment": date_alignment,
        },
        "warnings": warnings,
    }


def _format_value(value: float | None, digits: int = 6) -> str:
    return "unavailable" if value is None else f"{value:.{digits}f}"


def _format_change(change: dict[str, Any]) -> str:
    if change.get("absolute_change") is None:
        return "unavailable"
    percent = change.get("percent")
    percent_text = "百分比不可用（基期为0）" if percent is None else f"{percent:+.2f}%"
    return f"{change['absolute_change']:+.6f} / {percent_text}（{change['direction']}）"


def render_markdown(result: dict[str, Any]) -> str:
    oscillator = result["ssr_oscillator"]
    raw_ssr = result["raw_ssr"]
    definition = result["definition"]
    quality = result["quality_checks"]
    warnings = result["warnings"]
    extreme_note = (
        "当前BGeometrics口径不适用±2"
        if not definition["compatible_with_daily_brief_rules"]
        else "±2不是交易信号"
    )
    lines = [
        "## BTC SSR Oscillator",
        "",
        f"最新：{_format_value(oscillator['current'])}",
        f"日期：{oscillator['date']}",
        f"1D：{_format_change(oscillator['change_1d'])}",
        f"7D：{_format_change(oscillator['change_7d'])}",
        f"30D：{_format_change(oscillator['change_30d'])}",
        "",
        f"状态：{oscillator['status']}",
        f"稳定币相对购买力：{oscillator['direction']}",
        f"统计极端状态：{oscillator['extreme_status']}（{extreme_note}）",
        "",
        "### Raw SSR（辅助）",
        "",
        f"最新：{_format_value(raw_ssr['current'])}",
        f"日期：{raw_ssr['date'] or 'unavailable'}",
        f"7D：{_format_change(raw_ssr['change_7d'])}",
        f"30D：{_format_change(raw_ssr['change_30d'])}",
        "",
        "Raw SSR仅为辅助数据，不是SSR Oscillator，也没有用于替代或自行重建正式指标。",
        "",
        "### 指标定义",
        "",
        f"Provider：{definition['provider']}",
        f"公式/官方定义：{definition['description']}",
        f"公式：{definition['formula']}（中心={definition['center']:.1f}）",
        f"是否确认：{'是' if definition['confirmed'] else '否'}",
        "是否确认兼容正式规则："
        + ("是" if definition["compatible_with_daily_brief_rules"] else "否"),
        "由于当前口径不兼容，未套用0/±2方向与极端分类。"
        if not definition["compatible_with_daily_brief_rules"]
        else "方向与极端分类按正式规则计算；±2仅为统计极端，不是交易信号。",
        "",
        "### 数据质量",
        "",
        f"- 延迟：{result['data_delay_days']}天（相对{result['requested_as_of']}）",
        "- SSR Oscillator最近40天完整："
        + ("是" if quality["ssr_oscillator"]["is_complete"] else "否"),
        "- Raw SSR最近40天完整："
        + (
            "unavailable"
            if quality["raw_ssr"] is None
            else "是"
            if quality["raw_ssr"]["is_complete"]
            else "否"
        ),
        f"- 日期是否对齐：{result['date_alignment']}",
        "- 缓存：Oscillator "
        + str(result["cache_status"]["ssr_oscillator"].get("status", "未知"))
        + "；Raw SSR "
        + str(result["cache_status"]["raw_ssr"].get("status", "未知")),
        f"- 本次API请求次数：{result['network_request_count']}",
        f"- warnings：{'无' if not warnings else '；'.join(warnings)}",
        "",
    ]
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path, str]:
    markdown = render_markdown(result)
    json_path = output_dir / "latest.json"
    markdown_path = output_dir / "latest.md"
    atomic_write_json(json_path, result)
    atomic_write_text(markdown_path, markdown)
    return json_path, markdown_path, markdown


def determine_exit_code(result: dict[str, Any]) -> int:
    oscillator = result.get("ssr_oscillator")
    if not isinstance(oscillator, dict) or oscillator.get("current") is None:
        return 1
    return 2 if result.get("warnings") else 0


def execute(
    *,
    requested_as_of: date,
    refresh: bool,
    output_dir: Path,
    now: datetime | None = None,
    fetcher: Callable[[str, int], Any] = fetch_json,
    endpoints: dict[str, str] | None = None,
    definition_confirmed: bool = DEFINITION_CONFIRMED,
    definition_compatible: bool = DEFINITION_COMPATIBLE_WITH_RULES,
) -> tuple[dict[str, Any], str, int]:
    now = now or utc_now()
    payloads, statuses, request_count = obtain_payloads(
        requested_as_of, output_dir, refresh, now, fetcher, endpoints
    )
    result = build_result(
        payloads["ssr_oscillator"],
        payloads.get("raw_ssr"),
        requested_as_of,
        generated_at=now,
        cache_statuses=statuses,
        network_request_count=request_count,
        endpoints=endpoints,
        definition_confirmed=definition_confirmed,
        definition_compatible=definition_compatible,
    )
    _json_path, _markdown_path, markdown = write_outputs(result, output_dir)
    return result, markdown, determine_exit_code(result)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="获取BGeometrics正式SSR Oscillator及辅助Raw SSR日线。"
    )
    parser.add_argument("--as-of", help="分析截止日期（YYYY-MM-DD）")
    parser.add_argument(
        "--refresh", action="store_true", help="忽略6小时缓存并强制重新请求"
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
    now = utc_now()
    try:
        requested_as_of = (
            parse_date_strict(args.as_of) if args.as_of is not None else now.date()
        )
        output_dir = args.output_dir.expanduser()
        if not output_dir.is_absolute():
            output_dir = Path.cwd() / output_dir
        _result, markdown, exit_code = execute(
            requested_as_of=requested_as_of,
            refresh=args.refresh,
            output_dir=output_dir,
            now=now,
        )
    except (SsrError, OSError, ValueError) as exc:
        print(f"SSR取数失败：{exc}", file=sys.stderr)
        print("退出码1：网络失败、JSON无效、无正式SSR Oscillator值或核心计算失败。", file=sys.stderr)
        return 1
    print(markdown, end="")
    if exit_code == 0:
        print("退出码0：正式SSR Oscillator成功，定义、时效和质量正常。")
    else:
        print("退出码2：正式SSR Oscillator主体可用，但存在定义口径、时效、回退或质量警告。")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
