#!/usr/bin/env python3
"""获取并计算 BGeometrics CVDD 与同源 BTC Price 数据。"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CVDD_ENDPOINT = "https://api.bitcoin-data.com/v1/cvdd"
BTC_PRICE_ENDPOINT = "https://api.bitcoin-data.com/v1/btc-price"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "cvdd" / "generated"

CACHE_TTL = timedelta(hours=6)
NETWORK_TIMEOUT_SECONDS = 20
REQUEST_HISTORY_DAYS = 450
REQUIRED_HISTORY_DAYS = 365 + 7
REQUEST_SIZE = 500
MAX_FALLBACK_DAYS = 7
QUALITY_WINDOW_DAYS = 40

DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class CvddError(Exception):
    """脚本可向用户清楚说明的致命错误。"""


@dataclass
class ParsedSeries:
    """标准化后的单一时间序列及其解析质量信息。"""

    name: str
    value_field: str
    values: dict[date, float]
    raw_valid_dates: list[date]
    total_records: int
    duplicate_dates: list[dict[str, Any]]
    nonpositive_records: list[dict[str, Any]]
    invalid_records: list[dict[str, Any]]
    sort_order: str


SERIES_CONFIG = {
    "cvdd": {
        "name": "CVDD",
        "endpoint": CVDD_ENDPOINT,
        "value_field": "cvdd",
        "cache_filename": "cvdd_raw.json",
    },
    "btc_price": {
        "name": "BTC Price",
        "endpoint": BTC_PRICE_ENDPOINT,
        "value_field": "btcPrice",
        "cache_filename": "btc_price_raw.json",
    },
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


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


def reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 包含非有限数值 {value}")


def loads_json_strict(text: str, context: str) -> Any:
    try:
        return json.loads(text, parse_constant=reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CvddError(f"{context}不是有效 JSON：{exc}") from exc


def extract_records(payload: Any, series_name: str) -> list[Any]:
    """接受接口实测使用的数组，兼容常见的 data 数组外层。"""

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise CvddError(
        f"{series_name} JSON 结构无效：顶层应为记录数组，或包含 data 记录数组"
    )


def fetch_json(url: str, timeout: int = NETWORK_TIMEOUT_SECONDS) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "daily-brief-system-cvdd-fetcher/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        raise CvddError(f"HTTP 请求失败（状态码 {exc.code}）：{url}") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise CvddError(f"网络请求失败：{reason}；URL：{url}") from exc
    except (TimeoutError, OSError) as exc:
        raise CvddError(f"网络请求超时或连接失败：{exc}；URL：{url}") from exc

    if not raw:
        raise CvddError(f"接口返回空响应：{url}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CvddError(f"接口响应不是有效 UTF-8：{url}") from exc
    return loads_json_strict(text, f"接口响应（{url}）")


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
        raise CvddError(f"无法原子写入文件 {path}：{exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CvddError(f"无法序列化 JSON 文件 {path}：{exc}") from exc
    atomic_write_text(path, text + "\n")


def build_request_url(endpoint: str, start_day: date, end_day: date) -> str:
    query = urlencode(
        {
            "startday": start_day.isoformat(),
            "endday": end_day.isoformat(),
            "size": REQUEST_SIZE,
        }
    )
    return f"{endpoint}?{query}"


def cache_miss(reason: str, cache_path: Path) -> tuple[None, dict[str, Any]]:
    return None, {
        "used_cache": False,
        "status": "缓存未使用",
        "reason": reason,
        "cache_file": str(cache_path),
    }


def load_cache(
    cache_path: Path,
    source_key: str,
    required_start: date,
    required_end: date,
    now: datetime,
) -> tuple[Any | None, dict[str, Any]]:
    if not cache_path.exists():
        return cache_miss("缓存文件不存在", cache_path)

    try:
        text = cache_path.read_text(encoding="utf-8")
        envelope = loads_json_strict(text, f"缓存文件 {cache_path}")
    except (OSError, CvddError) as exc:
        return cache_miss(f"缓存无法读取或格式无效：{exc}", cache_path)

    if not isinstance(envelope, dict):
        return cache_miss("缓存顶层结构无效", cache_path)
    if envelope.get("source") != source_key:
        return cache_miss("缓存来源不匹配", cache_path)

    try:
        fetched_at = parse_utc_timestamp(envelope.get("fetched_at_utc"))
    except (TypeError, ValueError):
        return cache_miss("缓存抓取时间无效", cache_path)

    age = now.astimezone(timezone.utc) - fetched_at
    if age.total_seconds() < 0:
        return cache_miss("缓存抓取时间位于未来", cache_path)
    if age >= CACHE_TTL:
        return cache_miss("缓存已超过6小时有效期", cache_path)

    request_info = envelope.get("request")
    if not isinstance(request_info, dict):
        return cache_miss("缓存缺少请求范围", cache_path)
    try:
        cached_start = parse_date_strict(request_info.get("startday"))
        cached_end = parse_date_strict(request_info.get("endday"))
    except (TypeError, ValueError):
        return cache_miss("缓存请求范围无效", cache_path)
    if cached_start > required_start or cached_end < required_end:
        return cache_miss("缓存未覆盖本次分析所需日期范围", cache_path)

    if "raw_data" not in envelope:
        return cache_miss("缓存缺少原始返回数据", cache_path)
    raw_data = envelope["raw_data"]
    try:
        extract_records(raw_data, SERIES_CONFIG[source_key]["name"])
        parsed = parse_series(
            raw_data,
            SERIES_CONFIG[source_key]["value_field"],
            SERIES_CONFIG[source_key]["name"],
        )
    except CvddError as exc:
        return cache_miss(f"缓存原始数据结构无效：{exc}", cache_path)
    if not parsed.values:
        return cache_miss("缓存没有符合当前字段结构的有效正数记录", cache_path)

    request_url = envelope.get("request_url")
    if not isinstance(request_url, str) or not request_url:
        return cache_miss("缓存缺少请求URL", cache_path)

    return raw_data, {
        "used_cache": True,
        "status": "使用缓存",
        "cache_file": str(cache_path),
        "fetched_at_utc": format_utc_timestamp(fetched_at),
        "age_seconds": round(age.total_seconds(), 3),
        "request_url": request_url,
    }


def obtain_payloads(
    as_of: date,
    output_dir: Path,
    refresh: bool,
    now: datetime,
    fetcher: Callable[[str, int], Any] = fetch_json,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """每个源最多请求一次，并在两个网络响应都成功后写入缓存。"""

    request_start = as_of - timedelta(days=REQUEST_HISTORY_DAYS)
    required_start = as_of - timedelta(days=REQUIRED_HISTORY_DAYS)
    cache_dir = output_dir / "cache"
    payloads: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    pending: list[tuple[str, dict[str, Any], str, Path, str]] = []

    for source_key, config in SERIES_CONFIG.items():
        request_url = build_request_url(config["endpoint"], request_start, as_of)
        cache_path = cache_dir / config["cache_filename"]
        if refresh:
            payload, status = cache_miss("--refresh 强制刷新", cache_path)
        else:
            payload, status = load_cache(
                cache_path,
                source_key,
                required_start,
                as_of,
                now,
            )
        if payload is not None:
            payloads[source_key] = payload
            statuses[source_key] = status
        else:
            pending.append((source_key, config, request_url, cache_path, status["reason"]))

    downloaded: dict[str, Any] = {}
    for source_key, config, request_url, _cache_path, _reason in pending:
        payload = fetcher(request_url, NETWORK_TIMEOUT_SECONDS)
        extract_records(payload, config["name"])
        parsed = parse_series(payload, config["value_field"], config["name"])
        if not parsed.values:
            raise CvddError(
                f"{config['name']} 响应没有符合字段 {config['value_field']} 的有效正数记录"
            )
        downloaded[source_key] = payload

    fetched_at_utc = format_utc_timestamp(now)
    for source_key, config, request_url, cache_path, reason in pending:
        payload = downloaded[source_key]
        envelope = {
            "source": source_key,
            "fetched_at_utc": fetched_at_utc,
            "request_url": request_url,
            "request": {
                "startday": request_start.isoformat(),
                "endday": as_of.isoformat(),
                "size": REQUEST_SIZE,
            },
            "raw_data": payload,
        }
        atomic_write_json(cache_path, envelope)
        payloads[source_key] = payload
        statuses[source_key] = {
            "used_cache": False,
            "status": "网络请求",
            "cache_file": str(cache_path),
            "cache_miss_reason": reason,
            "fetched_at_utc": fetched_at_utc,
            "age_seconds": 0,
            "request_url": request_url,
        }

    return payloads, statuses, len(pending)


def safe_raw_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def coerce_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为数值")
    try:
        if isinstance(value, (int, float)):
            numeric = float(value)
        elif isinstance(value, str) and value.strip():
            numeric = float(value.strip())
        else:
            raise ValueError("不是数值")
    except (ValueError, OverflowError):
        raise ValueError("不是数值")
    if not math.isfinite(numeric):
        raise ValueError("数值不是有限数")
    return numeric


def determine_sort_order(dates: list[date]) -> str:
    if len(dates) < 2:
        return "insufficient_data"
    ascending = all(left <= right for left, right in zip(dates, dates[1:]))
    descending = all(left >= right for left, right in zip(dates, dates[1:]))
    if ascending and descending:
        return "constant"
    if ascending:
        return "ascending"
    if descending:
        return "descending"
    return "mixed"


def parse_series(payload: Any, value_field: str, series_name: str) -> ParsedSeries:
    records = extract_records(payload, series_name)
    raw_valid_dates: list[date] = []
    occurrence_count: dict[date, int] = {}
    positive_values: dict[date, list[float]] = {}
    nonpositive_records: list[dict[str, Any]] = []
    invalid_records: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            invalid_records.append(
                {"index": index, "date": None, "reason": "记录不是对象"}
            )
            continue

        raw_date = record.get("d")
        try:
            record_date = parse_date_strict(raw_date)
        except (TypeError, ValueError) as exc:
            invalid_records.append(
                {
                    "index": index,
                    "date": safe_raw_value(raw_date),
                    "reason": f"日期无效：{exc}",
                }
            )
            continue

        raw_valid_dates.append(record_date)
        occurrence_count[record_date] = occurrence_count.get(record_date, 0) + 1

        if value_field not in record:
            invalid_records.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "reason": f"缺少字段 {value_field}",
                }
            )
            continue
        raw_value = record[value_field]
        try:
            numeric = coerce_number(raw_value)
        except ValueError as exc:
            invalid_records.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "value": safe_raw_value(raw_value),
                    "reason": f"{value_field} 无效：{exc}",
                }
            )
            continue

        if numeric <= 0:
            nonpositive_records.append(
                {
                    "index": index,
                    "date": record_date.isoformat(),
                    "value": numeric,
                }
            )
            continue
        positive_values.setdefault(record_date, []).append(numeric)

    duplicate_dates: list[dict[str, Any]] = []
    values: dict[date, float] = {}
    for record_date, count in occurrence_count.items():
        candidates = positive_values.get(record_date, [])
        if count > 1:
            all_equal = (
                len(candidates) == count
                and all(value == candidates[0] for value in candidates[1:])
            )
            duplicate_dates.append(
                {
                    "date": record_date.isoformat(),
                    "count": count,
                    "conflicting_or_invalid": not all_equal,
                }
            )
            if all_equal:
                values[record_date] = candidates[0]
        elif len(candidates) == 1:
            values[record_date] = candidates[0]

    duplicate_dates.sort(key=lambda item: item["date"])
    nonpositive_records.sort(key=lambda item: (item["date"], item["index"]))
    return ParsedSeries(
        name=series_name,
        value_field=value_field,
        values=values,
        raw_valid_dates=raw_valid_dates,
        total_records=len(records),
        duplicate_dates=duplicate_dates,
        nonpositive_records=nonpositive_records,
        invalid_records=invalid_records,
        sort_order=determine_sort_order(raw_valid_dates),
    )


def resolve_series_point(
    values: dict[date, float],
    target_date: date,
    max_back_days: int = MAX_FALLBACK_DAYS,
) -> dict[str, Any]:
    for fallback_days in range(max_back_days + 1):
        candidate = target_date - timedelta(days=fallback_days)
        if candidate in values:
            return {
                "available": True,
                "target_date": target_date.isoformat(),
                "actual_date": candidate.isoformat(),
                "fallback_days": fallback_days,
                "cvdd": values[candidate],
            }
    return {
        "available": False,
        "target_date": target_date.isoformat(),
        "actual_date": None,
        "fallback_days": None,
        "cvdd": None,
    }


def resolve_common_point(
    cvdd_values: dict[date, float],
    price_values: dict[date, float],
    target_date: date,
    max_back_days: int = MAX_FALLBACK_DAYS,
) -> dict[str, Any]:
    for fallback_days in range(max_back_days + 1):
        candidate = target_date - timedelta(days=fallback_days)
        if candidate in cvdd_values and candidate in price_values:
            cvdd = cvdd_values[candidate]
            price = price_values[candidate]
            return {
                "available": True,
                "target_date": target_date.isoformat(),
                "actual_date": candidate.isoformat(),
                "fallback_days": fallback_days,
                "cvdd": cvdd,
                "btc_price": price,
                "price_cvdd": price / cvdd,
            }
    return {
        "available": False,
        "target_date": target_date.isoformat(),
        "actual_date": None,
        "fallback_days": None,
        "cvdd": None,
        "btc_price": None,
        "price_cvdd": None,
    }


def calculate_change(current_value: float | None, historical_value: float | None) -> dict[str, Any]:
    if current_value is None or historical_value is None:
        return {"rate": None, "percent": None, "direction": "unavailable"}
    rate = current_value / historical_value - 1
    if rate > 0:
        direction = "上升"
    elif rate < 0:
        direction = "下降"
    else:
        direction = "不变"
    return {"rate": rate, "percent": rate * 100, "direction": direction}


def dates_inclusive(start_date: date, end_date: date) -> list[date]:
    if start_date > end_date:
        return []
    day_count = (end_date - start_date).days + 1
    return [start_date + timedelta(days=offset) for offset in range(day_count)]


def filter_issue_records(
    records: list[dict[str, Any]], start_date: date, end_date: date
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for record in records:
        raw_date = record.get("date")
        try:
            record_date = parse_date_strict(raw_date)
        except (TypeError, ValueError):
            continue
        if start_date <= record_date <= end_date:
            filtered.append(record)
    return filtered


def build_series_quality(
    parsed: ParsedSeries, start_date: date, end_date: date
) -> dict[str, Any]:
    expected_dates = dates_inclusive(start_date, end_date)
    raw_dates_in_window = [
        value for value in parsed.raw_valid_dates if start_date <= value <= end_date
    ]
    raw_date_set = set(raw_dates_in_window)
    valid_dates = {
        value for value in parsed.values if start_date <= value <= end_date
    }
    return {
        "expected_date_count": len(expected_dates),
        "actual_record_count": len(raw_dates_in_window),
        "actual_unique_date_count": len(raw_date_set),
        "valid_unique_date_count": len(valid_dates),
        "missing_dates": [
            value.isoformat() for value in expected_dates if value not in raw_date_set
        ],
        "unavailable_dates": [
            value.isoformat() for value in expected_dates if value not in valid_dates
        ],
        "duplicate_dates": [
            item
            for item in parsed.duplicate_dates
            if start_date <= parse_date_strict(item["date"]) <= end_date
        ],
        "nonpositive_records": filter_issue_records(
            parsed.nonpositive_records, start_date, end_date
        ),
        "invalid_records_in_window": filter_issue_records(
            parsed.invalid_records, start_date, end_date
        ),
        "invalid_records_through_window_end": [
            item
            for item in parsed.invalid_records
            if issue_is_on_or_before(item, end_date)
        ],
        "sort_order": determine_sort_order(raw_dates_in_window),
        "total_response_records": parsed.total_records,
    }


def max_cvdd_single_day_change(
    values: dict[date, float], start_date: date, end_date: date
) -> dict[str, Any] | None:
    largest: tuple[float, date, date, float] | None = None
    for current_date in dates_inclusive(start_date + timedelta(days=1), end_date):
        previous_date = current_date - timedelta(days=1)
        if previous_date not in values or current_date not in values:
            continue
        rate = values[current_date] / values[previous_date] - 1
        candidate = (abs(rate), previous_date, current_date, rate)
        if largest is None or candidate[0] > largest[0]:
            largest = candidate
    if largest is None:
        return None
    _, previous_date, current_date, rate = largest
    if rate > 0:
        direction = "上升"
    elif rate < 0:
        direction = "下降"
    else:
        direction = "不变"
    return {
        "from_date": previous_date.isoformat(),
        "to_date": current_date.isoformat(),
        "rate": rate,
        "percent": rate * 100,
        "direction": direction,
    }


def append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def summarize_dates(items: list[str], limit: int = 12) -> str:
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + f" 等共{len(items)}日"


def issue_is_on_or_before(record: dict[str, Any], cutoff_date: date) -> bool:
    raw_date = record.get("date")
    try:
        return parse_date_strict(raw_date) <= cutoff_date
    except (TypeError, ValueError):
        # 日期本身无效时无法安全判断所属日期，仍需公开该异常。
        return True


def append_series_warnings(
    parsed: ParsedSeries, warnings: list[str], cutoff_date: date
) -> None:
    duplicate_dates = [
        item for item in parsed.duplicate_dates if issue_is_on_or_before(item, cutoff_date)
    ]
    nonpositive_records = [
        item
        for item in parsed.nonpositive_records
        if issue_is_on_or_before(item, cutoff_date)
    ]
    invalid_records = [
        item for item in parsed.invalid_records if issue_is_on_or_before(item, cutoff_date)
    ]
    if duplicate_dates:
        dates = [item["date"] for item in duplicate_dates]
        append_warning(warnings, f"{parsed.name} 存在重复日期：{summarize_dates(dates)}")
    if nonpositive_records:
        dates = [item["date"] for item in nonpositive_records]
        append_warning(warnings, f"{parsed.name} 存在非正数：{summarize_dates(dates)}")
    if invalid_records:
        append_warning(
            warnings,
            f"{parsed.name} 存在 {len(invalid_records)} 条日期或数值无效记录",
        )
    dates_through_cutoff = [
        value for value in parsed.raw_valid_dates if value <= cutoff_date
    ]
    if determine_sort_order(dates_through_cutoff) == "mixed":
        append_warning(warnings, f"{parsed.name} 原始记录未按日期排序")


def build_result(
    cvdd: ParsedSeries,
    btc_price: ParsedSeries,
    requested_as_of: date,
    now: datetime,
    source_request_urls: dict[str, str] | None = None,
    cache_statuses: dict[str, Any] | None = None,
    network_request_count: int = 0,
) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    eligible_common_dates = sorted(
        value
        for value in set(cvdd.values).intersection(btc_price.values)
        if value <= requested_as_of
    )
    if not eligible_common_dates:
        raise CvddError(
            f"截至 {requested_as_of.isoformat()}，CVDD 与 BTC Price 没有有效共同日期"
        )
    actual_latest = eligible_common_dates[-1]

    offsets = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}
    points: dict[str, dict[str, Any]] = {}
    current_cvdd_point = resolve_series_point(
        cvdd.values, actual_latest, max_back_days=0
    )
    current_price_cvdd_point = resolve_common_point(
        cvdd.values, btc_price.values, actual_latest, max_back_days=0
    )
    points["current"] = {
        "target_date": actual_latest.isoformat(),
        "cvdd_point": current_cvdd_point,
        "price_cvdd_point": current_price_cvdd_point,
    }
    for key, day_count in offsets.items():
        target = actual_latest - timedelta(days=day_count)
        points[key] = {
            "target_date": target.isoformat(),
            "cvdd_point": resolve_series_point(cvdd.values, target),
            "price_cvdd_point": resolve_common_point(
                cvdd.values, btc_price.values, target
            ),
        }

    cvdd_changes = {
        key: calculate_change(
            points["current"]["cvdd_point"]["cvdd"],
            points[key]["cvdd_point"]["cvdd"],
        )
        for key in ("30d", "90d", "365d")
    }
    price_cvdd_changes = {
        key: calculate_change(
            points["current"]["price_cvdd_point"]["price_cvdd"],
            points[key]["price_cvdd_point"]["price_cvdd"],
        )
        for key in ("7d", "30d", "90d", "365d")
    }

    quality_end = min(requested_as_of, now.date())
    quality_start = quality_end - timedelta(days=QUALITY_WINDOW_DAYS - 1)
    expected_dates = dates_inclusive(quality_start, quality_end)
    cvdd_quality = build_series_quality(cvdd, quality_start, quality_end)
    price_quality = build_series_quality(btc_price, quality_start, quality_end)
    common_dates_in_window = {
        value
        for value in set(cvdd.values).intersection(btc_price.values)
        if quality_start <= value <= quality_end
    }
    common_missing_dates = [
        value.isoformat() for value in expected_dates if value not in common_dates_in_window
    ]
    data_delay_days = (now.date() - actual_latest).days

    quality_checks = {
        "window_start": quality_start.isoformat(),
        "window_end": quality_end.isoformat(),
        "expected_date_count": QUALITY_WINDOW_DAYS,
        "cvdd": cvdd_quality,
        "btc_price": price_quality,
        "common": {
            "valid_common_date_count": len(common_dates_in_window),
            "missing_dates": common_missing_dates,
        },
        "cvdd_max_single_day_change": max_cvdd_single_day_change(
            cvdd.values, quality_start, quality_end
        ),
        "actual_latest_common_date": actual_latest.isoformat(),
        "data_delay_days": data_delay_days,
    }

    warnings: list[str] = []
    append_series_warnings(cvdd, warnings, requested_as_of)
    append_series_warnings(btc_price, warnings, requested_as_of)
    if cvdd_quality["missing_dates"]:
        append_warning(
            warnings,
            "最近40日 CVDD 缺失日期："
            + summarize_dates(cvdd_quality["missing_dates"]),
        )
    if price_quality["missing_dates"]:
        append_warning(
            warnings,
            "最近40日 BTC Price 缺失日期："
            + summarize_dates(price_quality["missing_dates"]),
        )
    if common_missing_dates:
        append_warning(
            warnings,
            "最近40日共同有效数据缺失日期：" + summarize_dates(common_missing_dates),
        )

    eligible_cvdd_dates = [value for value in cvdd.values if value <= requested_as_of]
    eligible_price_dates = [value for value in btc_price.values if value <= requested_as_of]
    if not eligible_cvdd_dates:
        raise CvddError(f"截至 {requested_as_of.isoformat()} 没有有效 CVDD 数据")
    if not eligible_price_dates:
        raise CvddError(f"截至 {requested_as_of.isoformat()} 没有有效 BTC Price 数据")
    latest_cvdd = max(eligible_cvdd_dates)
    latest_price = max(eligible_price_dates)
    if latest_cvdd != latest_price:
        append_warning(
            warnings,
            "两个序列的最新日期不一致："
            f"CVDD={latest_cvdd.isoformat()}，BTC Price={latest_price.isoformat()}；"
            f"使用最新共同日期 {actual_latest.isoformat()}",
        )
    if data_delay_days != 0:
        append_warning(
            warnings,
            f"最新共同日期相对当前UTC日期的数据延迟为 {data_delay_days} 天",
        )

    for key in ("30d", "90d", "365d"):
        point = points[key]["cvdd_point"]
        label = key.upper()
        if not point["available"]:
            append_warning(
                warnings,
                f"CVDD {label} 历史点 unavailable：目标日期 {point['target_date']} 向前7日仍无有效数据",
            )
        elif point["fallback_days"]:
            append_warning(
                warnings,
                f"CVDD {label} 历史点发生日期回退：目标 {point['target_date']}，"
                f"实际 {point['actual_date']}，向前 {point['fallback_days']} 日",
            )

    for key in ("7d", "30d", "90d", "365d"):
        point = points[key]["price_cvdd_point"]
        label = key.upper()
        if not point["available"]:
            append_warning(
                warnings,
                f"Price/CVDD {label} 历史点 unavailable：目标日期 {point['target_date']} 向前7日仍无共同数据",
            )
        elif point["fallback_days"]:
            append_warning(
                warnings,
                f"Price/CVDD {label} 历史点发生日期回退：目标 {point['target_date']}，"
                f"实际 {point['actual_date']}，向前 {point['fallback_days']} 日",
            )

    source_request_urls = source_request_urls or {
        "cvdd": CVDD_ENDPOINT,
        "btc_price": BTC_PRICE_ENDPOINT,
    }
    cache_statuses = cache_statuses or {
        "cvdd": {"used_cache": False, "status": "测试数据"},
        "btc_price": {"used_cache": False, "status": "测试数据"},
    }

    return {
        "generated_at_utc": format_utc_timestamp(now),
        "requested_as_of": requested_as_of.isoformat(),
        "actual_latest_common_date": actual_latest.isoformat(),
        "data_delay_days": data_delay_days,
        "source": {
            "provider": "BGeometrics / bitcoin-data.com",
            "cvdd": {
                "endpoint": CVDD_ENDPOINT,
                "request_url": source_request_urls["cvdd"],
                "value_field": cvdd.value_field,
            },
            "btc_price": {
                "endpoint": BTC_PRICE_ENDPOINT,
                "request_url": source_request_urls["btc_price"],
                "value_field": btc_price.value_field,
            },
        },
        "cache": {
            "ttl_hours": CACHE_TTL.total_seconds() / 3600,
            "cvdd": cache_statuses["cvdd"],
            "btc_price": cache_statuses["btc_price"],
        },
        "network_request_count": network_request_count,
        "current": points["current"],
        "7d": points["7d"],
        "30d": points["30d"],
        "90d": points["90d"],
        "365d": points["365d"],
        "cvdd_changes": cvdd_changes,
        "price_cvdd_changes": price_cvdd_changes,
        "quality_checks": quality_checks,
        "warnings": warnings,
    }


def format_number(value: float | None, decimal_places: int) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.{decimal_places}f}"


def format_change(change: dict[str, Any] | None) -> str:
    if not change or change.get("rate") is None:
        return "unavailable"
    return f"{change['percent']:+.2f}%（{change['direction']}）"


def point_date(point: dict[str, Any]) -> str:
    return point["actual_date"] if point["available"] else "unavailable"


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "### BGeometrics CVDD趋势",
        "",
        "| 时间 | 实际日期 | CVDD | 变化 |",
        "|---|---|---:|---:|",
    ]
    cvdd_rows = (
        ("当前", "current", None),
        ("30D", "30d", result["cvdd_changes"]["30d"]),
        ("90D", "90d", result["cvdd_changes"]["90d"]),
        ("365D", "365d", result["cvdd_changes"]["365d"]),
    )
    for label, key, change in cvdd_rows:
        point = result[key]["cvdd_point"]
        lines.append(
            f"| {label} | {point_date(point)} | "
            f"{format_number(point['cvdd'], 2)} | "
            f"{'—' if change is None else format_change(change)} |"
        )

    lines.extend(
        [
            "",
            "### BTC距BGeometrics CVDD",
            "",
            "| 时间 | 实际日期 | BTC价格 | Price/CVDD | 变化 |",
            "|---|---|---:|---:|---:|",
        ]
    )
    price_rows = (
        ("当前", "current", None),
        ("7D", "7d", result["price_cvdd_changes"]["7d"]),
        ("30D", "30d", result["price_cvdd_changes"]["30d"]),
        ("90D", "90d", result["price_cvdd_changes"]["90d"]),
        ("365D", "365d", result["price_cvdd_changes"]["365d"]),
    )
    for label, key, change in price_rows:
        point = result[key]["price_cvdd_point"]
        lines.append(
            f"| {label} | {point_date(point)} | "
            f"{format_number(point['btc_price'], 2)} | "
            f"{format_number(point['price_cvdd'], 4)} | "
            f"{'—' if change is None else format_change(change)} |"
        )

    quality = result["quality_checks"]
    cvdd_missing = quality["cvdd"]["missing_dates"]
    price_missing = quality["btc_price"]["missing_dates"]
    common_missing = quality["common"]["missing_dates"]
    cache = result["cache"]
    lines.extend(
        [
            "",
            "### 数据质量与来源",
            "",
            f"- 最新共同数据日期：{result['actual_latest_common_date']}；"
            f"相对当前UTC日期延迟 {result['data_delay_days']} 天。",
            f"- 最近40日缺失日期：CVDD "
            f"{summarize_dates(cvdd_missing) if cvdd_missing else '无'}；BTC Price "
            f"{summarize_dates(price_missing) if price_missing else '无'}；共同有效数据 "
            f"{summarize_dates(common_missing) if common_missing else '无'}。",
            f"- 缓存：CVDD {cache['cvdd']['status']}；"
            f"BTC Price {cache['btc_price']['status']}。",
            f"- 本次API请求次数：{result['network_request_count']}。",
            "- 警告：",
        ]
    )
    if result["warnings"]:
        lines.extend(f"  - {warning}" for warning in result["warnings"])
    else:
        lines.append("  - 无")
    return "\n".join(lines) + "\n"


def write_outputs(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path, str]:
    markdown = render_markdown(result)
    json_path = output_dir / "latest.json"
    markdown_path = output_dir / "latest.md"
    atomic_write_json(json_path, result)
    atomic_write_text(markdown_path, markdown)
    return json_path, markdown_path, markdown


def determine_exit_code(result: dict[str, Any]) -> int:
    return 2 if result.get("warnings") else 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="获取 BGeometrics CVDD 与同源 BTC Price，并生成历史对比结果。"
    )
    parser.add_argument(
        "--as-of",
        help="分析截止日期（YYYY-MM-DD）；默认使用当前UTC日期",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="忽略6小时缓存并强制重新请求两个API",
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

        payloads, cache_statuses, network_request_count = obtain_payloads(
            requested_as_of,
            output_dir,
            args.refresh,
            now,
        )
        cvdd = parse_series(
            payloads["cvdd"],
            SERIES_CONFIG["cvdd"]["value_field"],
            SERIES_CONFIG["cvdd"]["name"],
        )
        btc_price = parse_series(
            payloads["btc_price"],
            SERIES_CONFIG["btc_price"]["value_field"],
            SERIES_CONFIG["btc_price"]["name"],
        )
        source_request_urls = {
            key: cache_statuses[key]["request_url"] for key in SERIES_CONFIG
        }
        result = build_result(
            cvdd,
            btc_price,
            requested_as_of,
            now,
            source_request_urls,
            cache_statuses,
            network_request_count,
        )
        json_path, markdown_path, markdown = write_outputs(result, output_dir)
        print(markdown, end="")
        print(f"\n输出文件：{json_path}")
        print(f"输出文件：{markdown_path}")
        exit_code = determine_exit_code(result)
        if exit_code == 0:
            print("退出码 0：核心数据计算成功，未发现警告。")
        else:
            print("退出码 2：主体计算成功，但存在历史点回退、缺失或数据质量警告。")
        return exit_code
    except (CvddError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print("退出码 1：网络、JSON、数据结构、共同日期或输出发生致命错误。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
