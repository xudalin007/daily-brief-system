#!/usr/bin/env python3
"""Fetch and validate BGeometrics Adjusted SOPR (aSOPR) daily data."""

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


ASOPR_ENDPOINT = "https://api.bitcoin-data.com/v1/asopr"
PROVIDER_NAME = "BGeometrics"
VALUE_FIELD = "asopr"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "asopr" / "generated"

CACHE_TTL = timedelta(hours=6)
NETWORK_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REQUEST_HISTORY_DAYS = 90
REQUIRED_HISTORY_DAYS = 40
REQUEST_SIZE = 500
QUALITY_WINDOW_DAYS = 40
SEVEN_DAY_WINDOW = 7
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class AsoprError(Exception):
    """The formal aSOPR series cannot be fetched or safely evaluated."""


@dataclass
class HttpJson:
    payload: Any
    http_status: int


@dataclass
class ParsedSeries:
    values: dict[date, float]
    raw_dates: list[date]
    total_records: int
    duplicate_dates: list[dict[str, Any]]
    invalid_records: list[dict[str, Any]]
    source_sort_order: str


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
        raise AsoprError(f"{context}不是有效JSON：{exc}") from exc


def extract_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise AsoprError("aSOPR JSON结构无效：顶层应为记录数组或包含data数组")


def fetch_json(url: str, timeout: int = NETWORK_TIMEOUT_SECONDS) -> HttpJson:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "daily-brief-system-asopr-fetcher/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise AsoprError(f"HTTP请求失败（状态码{exc.code}）：{url}") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise AsoprError(f"网络请求失败：{reason}；URL：{url}") from exc
    except (TimeoutError, OSError, HTTPException) as exc:
        raise AsoprError(f"网络请求超时或连接失败：{exc}；URL：{url}") from exc
    if not 200 <= status < 300:
        raise AsoprError(f"HTTP请求失败（状态码{status}）：{url}")
    if not raw:
        raise AsoprError(f"接口返回空响应：{url}")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AsoprError(f"接口响应超过安全上限{MAX_RESPONSE_BYTES}字节：{url}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AsoprError(f"接口响应不是有效UTF-8：{url}") from exc
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
        raise AsoprError(f"无法原子写入文件{path}：{exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AsoprError(f"无法序列化JSON文件{path}：{exc}") from exc
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


def parse_series(payload: Any) -> ParsedSeries:
    """Parse only the formal ``asopr`` field; proxy fields are never accepted."""
    records = extract_records(payload)
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
        if VALUE_FIELD not in record:
            invalid.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "reason": "缺少正式字段asopr；不接受SOPR/STH-SOPR/LTH-SOPR或proxy",
                }
            )
            continue
        value = _number(record[VALUE_FIELD])
        if value is None or value <= 0:
            invalid.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "value": _safe_value(record[VALUE_FIELD]),
                    "reason": "asopr必须是有限正数",
                }
            )
            continue
        candidates.setdefault(record_date, []).append(value)

    values: dict[date, float] = {}
    duplicates: list[dict[str, Any]] = []
    for record_date, count in occurrences.items():
        valid = candidates.get(record_date, [])
        if count == 1 and len(valid) == 1:
            values[record_date] = valid[0]
            continue
        if count > 1:
            exact = len(valid) == count and all(item == valid[0] for item in valid)
            duplicates.append(
                {
                    "date": record_date.isoformat(),
                    "count": count,
                    "classification": (
                        "exact_identical" if exact else "conflicting_or_invalid"
                    ),
                    "canonicalized": exact,
                    "conflicting_or_invalid": not exact,
                }
            )
            if exact:
                values[record_date] = valid[0]

    return ParsedSeries(
        values=values,
        raw_dates=raw_dates,
        total_records=len(records),
        duplicate_dates=sorted(duplicates, key=lambda item: item["date"]),
        invalid_records=invalid,
        source_sort_order=_sort_order(raw_dates),
    )


def _cache_miss(reason: str, path: Path) -> tuple[None, dict[str, Any]]:
    return None, {
        "used_cache": False,
        "status": "缓存未使用",
        "reason": reason,
        "cache_file": str(path),
        "ttl_hours": CACHE_TTL.total_seconds() / 3600,
    }


def load_cache(
    path: Path,
    endpoint: str,
    required_start: date,
    required_end: date,
    now: datetime,
) -> tuple[Any | None, dict[str, Any]]:
    if not path.exists():
        return _cache_miss("缓存文件不存在", path)
    try:
        envelope = loads_json_strict(path.read_text(encoding="utf-8"), f"缓存文件{path}")
    except (OSError, UnicodeError, AsoprError) as exc:
        return _cache_miss(f"缓存不可用：{exc}", path)
    if not isinstance(envelope, dict):
        return _cache_miss("缓存顶层结构无效", path)
    if envelope.get("source") != "asopr" or envelope.get("endpoint") != endpoint:
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
    try:
        parsed = parse_series(envelope["raw_data"])
        if not parsed.values:
            raise AsoprError("缓存不含正式字段asopr的有效值")
    except AsoprError as exc:
        return _cache_miss(f"缓存原始JSON结构无效：{exc}", path)
    return envelope["raw_data"], {
        "used_cache": True,
        "status": "使用缓存",
        "reason": "缓存仍在6小时有效期内",
        "cache_file": str(path),
        "ttl_hours": CACHE_TTL.total_seconds() / 3600,
        "fetched_at_utc": format_utc_timestamp(fetched_at),
        "age_seconds": round(age.total_seconds(), 3),
        "http_status": http_status,
        "request_url": request_url,
    }


def _normalize_fetch_result(value: Any) -> HttpJson:
    return value if isinstance(value, HttpJson) else HttpJson(value, 200)


def obtain_payload(
    requested_as_of: date,
    output_dir: Path,
    refresh: bool,
    now: datetime,
    fetcher: Callable[[str, int], Any] = fetch_json,
    endpoint: str = ASOPR_ENDPOINT,
) -> tuple[Any, dict[str, Any], int]:
    request_start = requested_as_of - timedelta(days=REQUEST_HISTORY_DAYS)
    required_start = requested_as_of - timedelta(days=REQUIRED_HISTORY_DAYS - 1)
    request_url = build_request_url(endpoint, request_start, requested_as_of)
    cache_path = output_dir / "cache" / "asopr_raw.json"
    if refresh:
        payload, cache_status = _cache_miss("--refresh强制刷新", cache_path)
    else:
        payload, cache_status = load_cache(
            cache_path, endpoint, required_start, requested_as_of, now
        )
    if payload is not None:
        return payload, cache_status, 0

    miss_reason = cache_status["reason"]
    fetched = _normalize_fetch_result(fetcher(request_url, NETWORK_TIMEOUT_SECONDS))
    parsed = parse_series(fetched.payload)
    if not parsed.values:
        raise AsoprError("响应中没有合法有限正数的正式asopr字段")
    fetched_at = format_utc_timestamp(now)
    atomic_write_json(
        cache_path,
        {
            "source": "asopr",
            "endpoint": endpoint,
            "fetched_at_utc": fetched_at,
            "request_url": request_url,
            "http_status": fetched.http_status,
            "request": {
                "startday": request_start.isoformat(),
                "endday": requested_as_of.isoformat(),
                "size": REQUEST_SIZE,
            },
            "raw_data": fetched.payload,
        },
    )
    return fetched.payload, {
        "used_cache": False,
        "status": "网络请求",
        "reason": miss_reason,
        "cache_file": str(cache_path),
        "ttl_hours": CACHE_TTL.total_seconds() / 3600,
        "fetched_at_utc": fetched_at,
        "age_seconds": 0,
        "http_status": fetched.http_status,
        "request_url": request_url,
    }, 1


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _issue_in_window(issue: dict[str, Any], start: date, end: date) -> bool:
    try:
        issue_date = parse_date_strict(issue.get("date"))
    except (TypeError, ValueError):
        return False
    return start <= issue_date <= end


def build_quality(parsed: ParsedSeries, latest: date) -> dict[str, Any]:
    start = latest - timedelta(days=QUALITY_WINDOW_DAYS - 1)
    expected = _dates(start, latest)
    raw_in_window = [item for item in parsed.raw_dates if start <= item <= latest]
    unique_raw_dates = set(raw_in_window)
    duplicates = [
        item
        for item in parsed.duplicate_dates
        if start <= parse_date_strict(item["date"]) <= latest
    ]
    exact_duplicates = [
        item["date"] for item in duplicates if item["classification"] == "exact_identical"
    ]
    conflicting = [
        item["date"]
        for item in duplicates
        if item["classification"] == "conflicting_or_invalid"
    ]
    invalid = [
        item for item in parsed.invalid_records if _issue_in_window(item, start, latest)
    ]
    missing = [item.isoformat() for item in expected if item not in parsed.values]
    order = _sort_order(raw_in_window)
    seven_start = latest - timedelta(days=SEVEN_DAY_WINDOW - 1)
    seven_complete = all(item in parsed.values for item in _dates(seven_start, latest))
    return {
        "window_start": start.isoformat(),
        "window_end": latest.isoformat(),
        "expected_date_count": QUALITY_WINDOW_DAYS,
        "record_count": len(raw_in_window),
        "unique_date_count": len(unique_raw_dates),
        "valid_date_count": sum(1 for item in parsed.values if start <= item <= latest),
        "missing_dates": missing,
        "duplicate_dates": duplicates,
        "canonicalized_exact_duplicate_dates": exact_duplicates,
        "conflicting_duplicate_dates": conflicting,
        "invalid_values": invalid,
        "source_sort_order": order,
        "latest_date": latest.isoformat(),
        "seven_day_window_complete": seven_complete,
        "is_complete": (
            not missing
            and not conflicting
            and not invalid
            and order in {"ascending", "single_or_empty", "constant"}
        ),
    }


def _append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _summarize_dates(items: list[str], limit: int = 12) -> str:
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + f" 等{len(items)}日"


def build_result(
    payload: Any,
    requested_as_of: date,
    *,
    generated_at: datetime | None = None,
    cache_status: dict[str, Any] | None = None,
    network_request_count: int = 0,
    endpoint: str = ASOPR_ENDPOINT,
) -> dict[str, Any]:
    generated_at = generated_at or utc_now()
    parsed = parse_series(payload)
    eligible = [item for item in parsed.values if item <= requested_as_of]
    if not eligible:
        raise AsoprError(
            f"截至{requested_as_of.isoformat()}没有可用的正式aSOPR记录"
        )
    latest = max(eligible)
    current = parsed.values[latest]
    delay = (requested_as_of - latest).days
    quality = build_quality(parsed, latest)
    warnings: list[str] = []

    if delay > 1:
        _append_warning(warnings, f"aSOPR相对分析截止日期延迟{delay}天")
    if quality["missing_dates"]:
        _append_warning(
            warnings,
            "aSOPR最近40日缺失日期：" + _summarize_dates(quality["missing_dates"]),
        )
    if quality["conflicting_duplicate_dates"]:
        _append_warning(
            warnings,
            "aSOPR存在冲突或无效重复日期："
            + _summarize_dates(quality["conflicting_duplicate_dates"]),
        )
    if quality["invalid_values"]:
        invalid_dates = [
            item["date"] for item in quality["invalid_values"] if item.get("date")
        ]
        _append_warning(
            warnings,
            "aSOPR最近40日存在无效值：" + _summarize_dates(invalid_dates),
        )
    if quality["source_sort_order"] not in {"ascending", "single_or_empty", "constant"}:
        _append_warning(warnings, "aSOPR原始记录未按日期升序排列")
    if any(item.get("date") is None for item in parsed.invalid_records):
        _append_warning(warnings, "aSOPR原始响应存在日期无效记录")
    invalid_after_latest = [
        item
        for item in parsed.invalid_records
        if _issue_in_window(item, latest + timedelta(days=1), requested_as_of)
    ]
    if invalid_after_latest:
        _append_warning(
            warnings,
            "aSOPR在最新有效日线之后存在无效记录："
            + _summarize_dates(
                [item["date"] for item in invalid_after_latest if item.get("date")]
            ),
        )
    conflicting_after_latest = [
        item
        for item in parsed.duplicate_dates
        if item["conflicting_or_invalid"]
        and latest < parse_date_strict(item["date"]) <= requested_as_of
    ]
    if conflicting_after_latest:
        _append_warning(
            warnings,
            "aSOPR在最新有效日线之后存在冲突或无效重复日期："
            + _summarize_dates([item["date"] for item in conflicting_after_latest]),
        )

    seven_dates = _dates(latest - timedelta(days=SEVEN_DAY_WINDOW - 1), latest)
    missing_seven = [item.isoformat() for item in seven_dates if item not in parsed.values]
    fetched_at = (
        (cache_status or {}).get("fetched_at_utc")
        or format_utc_timestamp(generated_at)
    )
    request_url = (cache_status or {}).get("request_url", endpoint)
    if missing_seven:
        seven_value: float | None = None
        seven_status = "unavailable"
        seven_reason = "7DMA需要截至当前数据日连续7个日历日的正式aSOPR值；缺失：" + "、".join(missing_seven)
        seven_warning: str | None = seven_reason
        _append_warning(warnings, seven_reason)
        seven_data_date: str | None = None
        seven_start: str | None = None
        observation_count = SEVEN_DAY_WINDOW - len(missing_seven)
    else:
        seven_value = sum(parsed.values[item] for item in seven_dates) / SEVEN_DAY_WINDOW
        seven_status = "verified"
        seven_reason = None
        seven_warning = None
        seven_data_date = latest.isoformat()
        seven_start = seven_dates[0].isoformat()
        observation_count = SEVEN_DAY_WINDOW

    non_delay_warnings = [
        item for item in warnings if not item.startswith("aSOPR相对分析截止日期延迟")
    ]
    if non_delay_warnings:
        status = "provisional"
    elif delay > 1:
        status = "delayed"
    else:
        status = "verified"
    if seven_status != "unavailable":
        seven_status = status

    freshness = {"delay_days": delay, "is_fresh": delay <= 1}
    source_name = f"{PROVIDER_NAME} / Bitcoin Data API"
    asopr = {
        "metric": "asopr",
        "status": status,
        "value": current,
        "data_date": latest.isoformat(),
        "source": source_name,
        "source_url": endpoint,
        "fetched_at": fetched_at,
        "freshness": freshness,
        "warning": list(warnings),
        "reason": None,
    }
    asopr_7dma = {
        "metric": "asopr_7dma",
        "status": seven_status,
        "value": seven_value,
        "data_date": seven_data_date,
        "source": source_name,
        "source_url": endpoint,
        "method": "derived_arithmetic_mean_7_consecutive_calendar_days",
        "window_start_date": seven_start,
        "window_end_date": latest.isoformat() if seven_value is not None else None,
        "observation_count": observation_count,
        "warning": seven_warning,
        "reason": seven_reason,
    }
    status_info = cache_status or {
        "used_cache": False,
        "status": "测试数据",
        "reason": "本地构造数据",
        "cache_file": None,
        "ttl_hours": CACHE_TTL.total_seconds() / 3600,
        "fetched_at_utc": fetched_at,
        "age_seconds": 0,
        "http_status": 200,
        "request_url": endpoint,
    }
    return {
        "generated_at_utc": format_utc_timestamp(generated_at),
        "requested_as_of": requested_as_of.isoformat(),
        "metric": "asopr",
        "status": status,
        "actual_latest_date": latest.isoformat(),
        "data_delay_days": delay,
        "asopr": asopr,
        "asopr_7dma": asopr_7dma,
        "source": {
            "provider": PROVIDER_NAME,
            "endpoint": endpoint,
            "request_url": request_url,
            "value_field": VALUE_FIELD,
            "definition": (
                "Adjusted spent output profit ratio for spent outputs with "
                "lifespan greater than one hour."
            ),
            "fetched_at_utc": fetched_at,
            "http_status": status_info.get("http_status"),
            "requires_api_key": False,
        },
        "cache_status": dict(status_info),
        "network_request_count": network_request_count,
        "quality_checks": quality,
        "warnings": warnings,
    }


def _format_value(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.6f}"


def render_markdown(result: dict[str, Any]) -> str:
    asopr = result["asopr"]
    seven = result["asopr_7dma"]
    quality = result["quality_checks"]
    warnings = result["warnings"]
    lines = [
        "## BTC aSOPR",
        "",
        f"最新：{_format_value(asopr['value'])}",
        f"数据日期：{asopr['data_date']}",
        f"状态：{asopr['status']}",
        f"来源：{asopr['source']}",
        "",
        "### aSOPR 7DMA",
        "",
    ]
    if seven["status"] == "unavailable":
        lines.extend(
            [
                "7DMA：未取得可靠值",
                f"原因：{seven['reason']}",
            ]
        )
    else:
        lines.extend(
            [
                f"7DMA：{_format_value(seven['value'])}",
                f"数据日期：{seven['data_date']}",
                f"窗口：{seven['window_start_date']} 至 {seven['window_end_date']}",
            ]
        )
    lines.extend(
        [
            "方法：同一BGeometrics正式aSOPR序列连续7个日历日的算术平均（derived）。",
            "",
            "### 数据质量",
            "",
            f"- 分析截止日期：{result['requested_as_of']}",
            f"- 最新实际数据日期：{result['actual_latest_date']}",
            f"- 数据延迟：{result['data_delay_days']}天",
            f"- 最近40日完整：{'是' if quality['is_complete'] else '否'}",
            f"- 完全相同重复日期已折叠：{len(quality['canonicalized_exact_duplicate_dates'])}日",
            f"- 缓存：{result['cache_status'].get('status', '未知')}",
            f"- 本次API请求次数：{result['network_request_count']}",
            f"- warnings：{'无' if not warnings else '；'.join(warnings)}",
            "",
            "aSOPR与7DMA仅作为链上盈利状态辅助指标，不单独构成投资建议。",
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


def determine_exit_code(result: dict[str, Any]) -> int:
    return 2 if result.get("warnings") else 0


def execute(
    *,
    requested_as_of: date,
    refresh: bool,
    output_dir: Path,
    now: datetime | None = None,
    fetcher: Callable[[str, int], Any] = fetch_json,
    endpoint: str = ASOPR_ENDPOINT,
) -> tuple[dict[str, Any], str, int]:
    now = now or utc_now()
    payload, cache_status, request_count = obtain_payload(
        requested_as_of,
        output_dir,
        refresh,
        now,
        fetcher,
        endpoint,
    )
    result = build_result(
        payload,
        requested_as_of,
        generated_at=now,
        cache_status=cache_status,
        network_request_count=request_count,
        endpoint=endpoint,
    )
    _json_path, _markdown_path, markdown = write_outputs(result, output_dir)
    return result, markdown, determine_exit_code(result)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="获取BGeometrics正式aSOPR日线并计算同源7DMA。"
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
    except (AsoprError, OSError, ValueError) as exc:
        print(f"aSOPR取数失败：{exc}", file=sys.stderr)
        print("退出码1：网络失败、JSON无效、无正式aSOPR值或核心计算失败。", file=sys.stderr)
        return 1
    print(markdown, end="")
    if exit_code == 0:
        print("退出码0：aSOPR与同源derived 7DMA成功，时效和质量正常。")
    else:
        print("退出码2：aSOPR主体可用，但存在7DMA、时效或数据质量警告。")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
