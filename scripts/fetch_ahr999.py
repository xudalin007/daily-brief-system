#!/usr/bin/env python3
"""获取、校验并整理 AIX4U AHR999 完整历史日线。"""

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
from urllib.request import Request, urlopen


SOURCE_URL = "https://ahr999.aix4u.com/datasets/ahr999.json"
SOURCE_NAME = "AIX4U AHR999 Dataset"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ahr999" / "generated"

CACHE_TTL = timedelta(hours=6)
NETWORK_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_FALLBACK_DAYS = 7
QUALITY_WINDOW_DAYS = 40

DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class Ahr999Error(Exception):
    """AHR999 无法安全获取或计算时向用户报告的致命错误。"""


@dataclass
class ParsedDataset:
    """标准化的 AHR999 日线以及解析过程中发现的质量信息。"""

    records: dict[date, dict[str, Any]]
    date_counts: dict[date, int]
    date_sequence: list[date]
    total_records: int
    duplicate_dates: list[dict[str, Any]]
    invalid_date_records: list[dict[str, Any]]
    invalid_ahr999_records: list[dict[str, Any]]
    invalid_close_records: list[dict[str, Any]]
    nonpositive_close_records: list[dict[str, Any]]
    invalid_ma200_records: list[dict[str, Any]]
    nonpositive_ma200_records: list[dict[str, Any]]
    invalid_quantile5y_records: list[dict[str, Any]]
    sort_order: str


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
        raise Ahr999Error(f"{context}不是有效 JSON：{exc}") from exc


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
        raise Ahr999Error(f"无法原子写入文件 {path}：{exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise Ahr999Error(f"无法序列化 JSON 文件 {path}：{exc}") from exc
    atomic_write_text(path, text + "\n")


def fetch_json(url: str, timeout: int = NETWORK_TIMEOUT_SECONDS) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "daily-brief-system-ahr999-fetcher/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise Ahr999Error(f"HTTP 请求失败（状态码 {exc.code}）：{url}") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise Ahr999Error(f"网络请求失败：{reason}；URL：{url}") from exc
    except (TimeoutError, OSError) as exc:
        raise Ahr999Error(f"网络请求超时或连接失败：{exc}；URL：{url}") from exc

    if not raw:
        raise Ahr999Error(f"接口返回空响应：{url}")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise Ahr999Error(
            f"接口响应超过安全上限 {MAX_RESPONSE_BYTES} 字节：{url}"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Ahr999Error(f"接口响应不是有效 UTF-8：{url}") from exc
    return loads_json_strict(text, f"接口响应（{url}）")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def _safe_raw_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
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


def parse_dataset(payload: Any) -> ParsedDataset:
    """按 AIX4U 实际顶层数组结构解析完整历史数据。"""

    if not isinstance(payload, list):
        raise Ahr999Error("AIX4U JSON结构无效：顶层必须是记录数组")
    if not payload:
        raise Ahr999Error("AIX4U JSON结构无效：记录数组为空")

    dated_rows: dict[date, list[dict[str, Any]]] = {}
    date_counts: dict[date, int] = {}
    valid_date_sequence: list[date] = []
    invalid_date_records: list[dict[str, Any]] = []
    invalid_ahr999_records: list[dict[str, Any]] = []
    invalid_close_records: list[dict[str, Any]] = []
    nonpositive_close_records: list[dict[str, Any]] = []
    invalid_ma200_records: list[dict[str, Any]] = []
    nonpositive_ma200_records: list[dict[str, Any]] = []
    invalid_quantile5y_records: list[dict[str, Any]] = []

    for index, raw_record in enumerate(payload):
        if not isinstance(raw_record, dict):
            invalid_date_records.append(
                {"index": index, "value": _safe_raw_value(raw_record), "reason": "记录不是对象"}
            )
            continue
        try:
            record_date = parse_date_strict(raw_record.get("date"))
        except (TypeError, ValueError) as exc:
            invalid_date_records.append(
                {
                    "index": index,
                    "value": _safe_raw_value(raw_record.get("date")),
                    "reason": str(exc),
                }
            )
            continue

        valid_date_sequence.append(record_date)
        date_counts[record_date] = date_counts.get(record_date, 0) + 1

        ahr999 = _number(raw_record.get("ahr999"))
        close = _number(raw_record.get("close"))
        ma200 = _number(raw_record.get("ma200"))
        raw_quantile = raw_record.get("quantile5y")
        quantile5y = _number(raw_quantile)

        issue_base = {"index": index, "date": record_date.isoformat()}
        if ahr999 is None or ahr999 <= 0:
            invalid_ahr999_records.append(
                {**issue_base, "value": _safe_raw_value(raw_record.get("ahr999"))}
            )
        if close is None:
            invalid_close_records.append(
                {**issue_base, "value": _safe_raw_value(raw_record.get("close"))}
            )
        elif close <= 0:
            nonpositive_close_records.append({**issue_base, "value": close})
        if ma200 is None:
            invalid_ma200_records.append(
                {**issue_base, "value": _safe_raw_value(raw_record.get("ma200"))}
            )
        elif ma200 <= 0:
            nonpositive_ma200_records.append({**issue_base, "value": ma200})

        if raw_quantile is not None and (
            quantile5y is None or not 0 <= quantile5y <= 1
        ):
            invalid_quantile5y_records.append(
                {**issue_base, "value": _safe_raw_value(raw_quantile)}
            )
            quantile5y = None

        normalized = {
            "date": record_date.isoformat(),
            "close": close,
            "ma200": ma200,
            "ahr999": ahr999,
            "quantile5y": quantile5y,
            "windowKind": (
                raw_record.get("windowKind")
                if isinstance(raw_record.get("windowKind"), str)
                else None
            ),
        }
        dated_rows.setdefault(record_date, []).append(normalized)

    records: dict[date, dict[str, Any]] = {}
    duplicate_dates: list[dict[str, Any]] = []
    for record_date, rows in dated_rows.items():
        if len(rows) > 1:
            all_equal = all(row == rows[0] for row in rows[1:])
            all_valid = all(
                row["ahr999"] is not None and row["ahr999"] > 0 for row in rows
            )
            duplicate_dates.append(
                {
                    "date": record_date.isoformat(),
                    "count": len(rows),
                    "conflicting_or_invalid": not (all_equal and all_valid),
                }
            )
            if all_equal and all_valid:
                records[record_date] = rows[0]
        elif rows[0]["ahr999"] is not None and rows[0]["ahr999"] > 0:
            records[record_date] = rows[0]

    if not records:
        raise Ahr999Error("AIX4U数据中没有合法有限正数的AHR999记录")

    duplicate_dates.sort(key=lambda item: item["date"])
    return ParsedDataset(
        records=records,
        date_counts=date_counts,
        date_sequence=valid_date_sequence,
        total_records=len(payload),
        duplicate_dates=duplicate_dates,
        invalid_date_records=invalid_date_records,
        invalid_ahr999_records=invalid_ahr999_records,
        invalid_close_records=invalid_close_records,
        nonpositive_close_records=nonpositive_close_records,
        invalid_ma200_records=invalid_ma200_records,
        nonpositive_ma200_records=nonpositive_ma200_records,
        invalid_quantile5y_records=invalid_quantile5y_records,
        sort_order=_sort_order(valid_date_sequence),
    )


def _cache_miss(reason: str, cache_path: Path) -> tuple[None, dict[str, Any], None]:
    return None, {
        "used_cache": False,
        "status": "缓存未使用",
        "reason": reason,
        "cache_file": str(cache_path),
    }, None


def load_cache(
    cache_path: Path,
    now: datetime,
) -> tuple[Any | None, dict[str, Any], datetime | None]:
    if not cache_path.exists():
        return _cache_miss("缓存文件不存在", cache_path)
    try:
        envelope = loads_json_strict(cache_path.read_text(encoding="utf-8"), "缓存文件")
    except (OSError, Ahr999Error) as exc:
        return _cache_miss(f"缓存不可用：{exc}", cache_path)
    if not isinstance(envelope, dict):
        return _cache_miss("缓存结构无效", cache_path)
    if envelope.get("source_url") != SOURCE_URL:
        return _cache_miss("缓存来源URL不匹配", cache_path)
    if "raw_data" not in envelope:
        return _cache_miss("缓存缺少原始JSON", cache_path)
    try:
        fetched_at = parse_utc_timestamp(envelope.get("fetched_at_utc"))
    except (TypeError, ValueError):
        return _cache_miss("缓存抓取时间无效", cache_path)
    age = now.astimezone(timezone.utc) - fetched_at
    if age < timedelta(0):
        return _cache_miss("缓存抓取时间晚于当前时间", cache_path)
    if age >= CACHE_TTL:
        return _cache_miss("缓存已过期（有效期6小时）", cache_path)
    try:
        parse_dataset(envelope["raw_data"])
    except Ahr999Error as exc:
        return _cache_miss(f"缓存原始数据无效：{exc}", cache_path)
    return envelope["raw_data"], {
        "used_cache": True,
        "status": "使用缓存",
        "reason": "缓存仍在6小时有效期内",
        "cache_file": str(cache_path),
    }, fetched_at


def obtain_payload(
    output_dir: Path,
    refresh: bool,
    now: datetime,
    fetcher: Callable[[str], Any] = fetch_json,
) -> tuple[Any, datetime, dict[str, Any], int]:
    cache_path = output_dir / "cache" / "ahr999_raw.json"
    cache_reason = "--refresh强制刷新" if refresh else "缓存未检查"
    if not refresh:
        cached, cache_status, fetched_at = load_cache(cache_path, now)
        if cached is not None and fetched_at is not None:
            return cached, fetched_at, cache_status, 0
        cache_reason = cache_status["reason"]

    payload = fetcher(SOURCE_URL)
    parse_dataset(payload)
    fetched_at = now.astimezone(timezone.utc)
    atomic_write_json(
        cache_path,
        {
            "fetched_at_utc": format_utc_timestamp(fetched_at),
            "source_url": SOURCE_URL,
            "request_url": SOURCE_URL,
            "raw_data": payload,
        },
    )
    return payload, fetched_at, {
        "used_cache": False,
        "status": "网络获取",
        "reason": cache_reason,
        "cache_file": str(cache_path),
    }, 1


def classify_regime(value: float) -> tuple[str, str]:
    if value < 0.45:
        return "bargain", "历史低估区"
    if value < 1.20:
        return "dca", "定投区"
    if value < 3.00:
        return "caution", "谨慎区"
    return "bubble", "泡沫区"


def resolve_history_point(
    records: dict[date, dict[str, Any]], target_date: date
) -> dict[str, Any]:
    for fallback_days in range(MAX_FALLBACK_DAYS + 1):
        candidate_date = target_date - timedelta(days=fallback_days)
        record = records.get(candidate_date)
        if record is not None:
            return {
                "available": True,
                "target_date": target_date.isoformat(),
                "actual_date": candidate_date.isoformat(),
                "fallback_days": fallback_days,
                "value": record["ahr999"],
                "ahr999": record["ahr999"],
                "quantile5y": record["quantile5y"],
                "close": record["close"],
                "ma200": record["ma200"],
                "windowKind": record["windowKind"],
            }
    return {
        "available": False,
        "target_date": target_date.isoformat(),
        "actual_date": None,
        "fallback_days": None,
        "value": None,
        "ahr999": None,
        "quantile5y": None,
        "close": None,
        "ma200": None,
        "windowKind": None,
    }


def calculate_ratio_change(current: float, point: dict[str, Any]) -> dict[str, Any]:
    historical = point.get("ahr999") if point.get("available") else None
    if historical is None or historical <= 0:
        return {"rate": None, "percent": None, "direction": "unavailable"}
    rate = current / historical - 1
    direction = "上升" if rate > 0 else "下降" if rate < 0 else "不变"
    return {"rate": rate, "percent": rate * 100, "direction": direction}


def calculate_quantile_change(
    current_quantile: float | None, point: dict[str, Any]
) -> dict[str, Any]:
    historical = point.get("quantile5y") if point.get("available") else None
    if current_quantile is None or historical is None:
        return {"delta": None, "percentage_points": None, "direction": "unavailable"}
    delta = current_quantile - historical
    direction = "上升" if delta > 0 else "下降" if delta < 0 else "不变"
    return {
        "delta": delta,
        "percentage_points": delta * 100,
        "direction": direction,
    }


def _dates_inclusive(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _issues_in_window(
    issues: list[dict[str, Any]], start: date, end: date
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for issue in issues:
        try:
            issue_date = parse_date_strict(issue.get("date"))
        except (TypeError, ValueError):
            continue
        if start <= issue_date <= end:
            selected.append(dict(issue))
    return selected


def build_quality_checks(
    parsed: ParsedDataset,
    actual_latest_date: date,
    data_delay_days: int,
) -> dict[str, Any]:
    start = actual_latest_date - timedelta(days=QUALITY_WINDOW_DAYS - 1)
    expected_dates = _dates_inclusive(start, actual_latest_date)
    missing_dates = [
        item.isoformat() for item in expected_dates if parsed.date_counts.get(item, 0) == 0
    ]
    duplicate_dates = [
        item
        for item in parsed.duplicate_dates
        if start <= parse_date_strict(item["date"]) <= actual_latest_date
    ]
    invalid_ahr999 = _issues_in_window(
        parsed.invalid_ahr999_records, start, actual_latest_date
    )
    invalid_close = _issues_in_window(
        parsed.invalid_close_records, start, actual_latest_date
    )
    nonpositive_close = _issues_in_window(
        parsed.nonpositive_close_records, start, actual_latest_date
    )
    invalid_ma200 = _issues_in_window(
        parsed.invalid_ma200_records, start, actual_latest_date
    )
    nonpositive_ma200 = _issues_in_window(
        parsed.nonpositive_ma200_records, start, actual_latest_date
    )
    invalid_quantile = _issues_in_window(
        parsed.invalid_quantile5y_records, start, actual_latest_date
    )
    window_sort_order = _sort_order(
        [item for item in parsed.date_sequence if start <= item <= actual_latest_date]
    )
    actual_record_count = sum(parsed.date_counts.get(item, 0) for item in expected_dates)
    unique_date_count = sum(1 for item in expected_dates if parsed.date_counts.get(item, 0))
    complete = not any(
        (
            missing_dates,
            duplicate_dates,
            invalid_ahr999,
            invalid_close,
            nonpositive_close,
            invalid_ma200,
            nonpositive_ma200,
            invalid_quantile,
            window_sort_order == "unsorted",
        )
    )
    return {
        "window_start": start.isoformat(),
        "window_end": actual_latest_date.isoformat(),
        "expected_date_count": QUALITY_WINDOW_DAYS,
        "actual_record_count": actual_record_count,
        "unique_date_count": unique_date_count,
        "missing_dates": missing_dates,
        "duplicate_dates": duplicate_dates,
        "invalid_ahr999_records": invalid_ahr999,
        "invalid_close_records": invalid_close,
        "nonpositive_close_records": nonpositive_close,
        "invalid_ma200_records": invalid_ma200,
        "nonpositive_ma200_records": nonpositive_ma200,
        "invalid_quantile5y_records": invalid_quantile,
        "latest_date": actual_latest_date.isoformat(),
        "data_delay_days": data_delay_days,
        "sort_order": window_sort_order,
        "full_dataset_sort_order": parsed.sort_order,
        "is_complete": complete,
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
    fetched_at: datetime | None = None,
    cache_status: dict[str, Any] | None = None,
    network_request_count: int = 0,
) -> dict[str, Any]:
    parsed = parse_dataset(payload)
    eligible_dates = [item for item in parsed.records if item <= requested_as_of]
    if not eligible_dates:
        raise Ahr999Error(
            f"不晚于 {requested_as_of.isoformat()} 的范围内没有有效AHR999记录"
        )
    actual_latest = max(eligible_dates)
    current_record = parsed.records[actual_latest]
    if current_record["close"] is None or current_record["close"] <= 0:
        raise Ahr999Error(f"最新有效AHR999记录 {actual_latest} 缺少合法正数close")
    if current_record["ma200"] is None or current_record["ma200"] <= 0:
        raise Ahr999Error(f"最新有效AHR999记录 {actual_latest} 缺少合法正数ma200")

    data_delay_days = (requested_as_of - actual_latest).days
    previous = resolve_history_point(parsed.records, actual_latest - timedelta(days=1))
    point_7d = resolve_history_point(parsed.records, actual_latest - timedelta(days=7))
    point_30d = resolve_history_point(parsed.records, actual_latest - timedelta(days=30))
    regime_code, regime_zh = classify_regime(current_record["ahr999"])
    quality = build_quality_checks(parsed, actual_latest, data_delay_days)
    warnings: list[str] = []

    if data_delay_days > 1:
        _append_warning(
            warnings,
            f"最新完整AHR999日线相对分析截止日期延迟{data_delay_days}天",
        )

    invalid_after_latest = [
        issue
        for issue in parsed.invalid_ahr999_records
        if actual_latest < parse_date_strict(issue["date"]) <= requested_as_of
    ]
    if invalid_after_latest:
        _append_warning(
            warnings,
            "分析截止日期前存在更新但AHR999无效的日线："
            + _summarize_dates([item["date"] for item in invalid_after_latest]),
        )

    ambiguous_duplicates_after_latest = [
        item
        for item in parsed.duplicate_dates
        if item["conflicting_or_invalid"]
        and actual_latest < parse_date_strict(item["date"]) <= requested_as_of
    ]
    if ambiguous_duplicates_after_latest:
        _append_warning(
            warnings,
            "分析截止日期前存在冲突或无效的重复日线："
            + _summarize_dates(
                [item["date"] for item in ambiguous_duplicates_after_latest]
            ),
        )

    if current_record["quantile5y"] is None:
        _append_warning(warnings, "当前记录quantile5y缺失或超出0～1有效范围")
    elif point_7d["available"] and point_7d["quantile5y"] is None:
        _append_warning(
            warnings,
            f"7D实际日期 {point_7d['actual_date']} 的quantile5y缺失或无效，"
            "无法计算7D分位变化",
        )

    for label, point in (("前1日", previous), ("7D", point_7d), ("30D", point_30d)):
        if not point["available"]:
            _append_warning(
                warnings,
                f"{label}目标日期 {point['target_date']} 向前7日内无有效AHR999记录",
            )
        elif point["fallback_days"]:
            _append_warning(
                warnings,
                f"{label}目标日期 {point['target_date']} 回退{point['fallback_days']}日，"
                f"实际使用 {point['actual_date']}",
            )

    if quality["missing_dates"]:
        _append_warning(
            warnings,
            "最近40个完整日历日缺失日期："
            + _summarize_dates(quality["missing_dates"]),
        )
    if quality["duplicate_dates"]:
        _append_warning(
            warnings,
            "最近40个完整日历日存在重复日期："
            + _summarize_dates([item["date"] for item in quality["duplicate_dates"]]),
        )
    if quality["sort_order"] == "unsorted":
        _append_warning(warnings, "最近40个完整日历日的原始记录顺序混乱")
    issue_messages = (
        ("invalid_ahr999_records", "AHR999无效"),
        ("invalid_close_records", "close无效"),
        ("nonpositive_close_records", "close非正数"),
        ("invalid_ma200_records", "ma200无效"),
        ("nonpositive_ma200_records", "ma200非正数"),
        ("invalid_quantile5y_records", "quantile5y超出0～1或无效"),
    )
    for key, label in issue_messages:
        if quality[key]:
            _append_warning(
                warnings,
                f"最近40个完整日历日存在{label}："
                + _summarize_dates([item["date"] for item in quality[key]]),
            )
    if parsed.invalid_date_records:
        _append_warning(
            warnings,
            f"原始数据存在{len(parsed.invalid_date_records)}条无效日期记录",
        )

    generated_at = generated_at or utc_now()
    fetched_at = fetched_at or generated_at
    cache_status = cache_status or {
        "used_cache": False,
        "status": "本地数据",
        "reason": "直接构造结果",
        "cache_file": None,
    }
    return {
        "generated_at_utc": format_utc_timestamp(generated_at),
        "requested_as_of": requested_as_of.isoformat(),
        "actual_latest_date": actual_latest.isoformat(),
        "data_delay_days": data_delay_days,
        "source": {"name": SOURCE_NAME, "url": SOURCE_URL},
        "source_url": SOURCE_URL,
        "fetched_at_utc": format_utc_timestamp(fetched_at),
        "cache_status": dict(cache_status),
        "network_request_count": network_request_count,
        "current": {
            **current_record,
            "regime_code": regime_code,
            "regime_zh": regime_zh,
        },
        "history": {
            "previous": previous,
            "7d": point_7d,
            "30d": point_30d,
        },
        "changes": {
            "ahr999_1d": calculate_ratio_change(current_record["ahr999"], previous),
            "ahr999_7d": calculate_ratio_change(current_record["ahr999"], point_7d),
            "ahr999_30d": calculate_ratio_change(current_record["ahr999"], point_30d),
            "quantile5y_7d": calculate_quantile_change(
                current_record["quantile5y"], point_7d
            ),
        },
        "quality_checks": quality,
        "warnings": warnings,
    }


def format_number(value: float | None, decimal_places: int) -> str:
    return "unavailable" if value is None else f"{value:,.{decimal_places}f}"


def format_percent(value: float | None, decimal_places: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{decimal_places}f}%"


def format_change(change: dict[str, Any]) -> str:
    if change.get("percent") is None:
        return "unavailable"
    return f"{change['percent']:+.2f}%（{change['direction']}）"


def render_markdown(result: dict[str, Any]) -> str:
    current = result["current"]
    history = result["history"]
    quality = result["quality_checks"]
    warnings = result["warnings"]
    fallback_happened = any(
        point["fallback_days"] not in (None, 0) for point in history.values()
    )
    lines = [
        "## AHR999",
        "",
        f"分析截止日期：{result['requested_as_of']}",
        "",
        f"最新完整UTC日线：{result['actual_latest_date']}",
        "",
        f"抓取时间（UTC）：{result['fetched_at_utc']}",
        "",
        f"数据源：{result['source']['name']}（{result['source_url']}）",
        "",
        f"AHR999：{format_number(current['ahr999'], 6)}",
        "",
        f"5年历史分位：{format_percent(None if current['quantile5y'] is None else current['quantile5y'] * 100)}",
        "",
        f"BTC收盘：${format_number(current['close'], 2)}",
        "",
        f"MA200：${format_number(current['ma200'], 2)}",
        "",
        f"估值区间：{current['regime_zh']}（{current['regime_code']}）",
        "",
        "辅助趋势：",
        "",
        f"- 1D变化：{format_change(result['changes']['ahr999_1d'])}",
        f"- 7D变化：{format_change(result['changes']['ahr999_7d'])}",
        f"- 30D变化：{format_change(result['changes']['ahr999_30d'])}",
        "- 5年历史分位7D变化："
        + (
            "unavailable"
            if result["changes"]["quantile5y_7d"]["percentage_points"] is None
            else f"{result['changes']['quantile5y_7d']['percentage_points']:+.2f}个百分点"
            f"（{result['changes']['quantile5y_7d']['direction']}）"
        ),
        "",
        "历史点：",
        "",
        "| 周期 | 目标日期 | 实际日期 | AHR999 | 回退天数 |",
        "|---|---|---|---:|---:|",
    ]
    for label, key in (("前1日", "previous"), ("7D", "7d"), ("30D", "30d")):
        point = history[key]
        fallback = "unavailable" if point["fallback_days"] is None else str(point["fallback_days"])
        lines.append(
            f"| {label} | {point['target_date']} | {point['actual_date'] or 'unavailable'} | "
            f"{format_number(point['ahr999'], 6)} | {fallback} |"
        )
    lines.extend(
        [
            "",
            "数据质量：",
            "",
            f"- 数据延迟：{result['data_delay_days']}天（相对分析截止日期 {result['requested_as_of']}）",
            f"- 是否发生日期回退：{'是' if fallback_happened else '否'}",
            f"- 最近40天是否完整：{'是' if quality['is_complete'] else '否'}",
            f"- 是否使用缓存：{'是' if result['cache_status']['used_cache'] else '否'}",
            f"- warnings：{'无' if not warnings else '；'.join(warnings)}",
            "",
            "AHR999是长期估值辅助指标，不单独作为买卖信号。",
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
    fetcher: Callable[[str], Any] = fetch_json,
) -> tuple[dict[str, Any], str, int]:
    now = now or utc_now()
    payload, fetched_at, cache_status, request_count = obtain_payload(
        output_dir, refresh, now, fetcher
    )
    result = build_result(
        payload,
        requested_as_of,
        generated_at=now,
        fetched_at=fetched_at,
        cache_status=cache_status,
        network_request_count=request_count,
    )
    _json_path, _markdown_path, markdown = write_outputs(result, output_dir)
    return result, markdown, determine_exit_code(result)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="获取并整理AIX4U AHR999日线数据。")
    parser.add_argument("--as-of", help="分析截止日期（YYYY-MM-DD）")
    parser.add_argument(
        "--refresh", action="store_true", help="忽略6小时缓存并强制重新下载"
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
        requested_as_of = (
            parse_date_strict(args.as_of)
            if args.as_of is not None
            else utc_now().date()
        )
        _result, markdown, exit_code = execute(
            requested_as_of=requested_as_of,
            refresh=args.refresh,
            output_dir=args.output_dir,
        )
    except (Ahr999Error, OSError, ValueError) as exc:
        print(f"AHR999取数失败：{exc}", file=sys.stderr)
        print("退出码1：网络失败、JSON无效、无有效记录或核心计算失败。", file=sys.stderr)
        return 1

    print(markdown, end="")
    if exit_code == 0:
        print("退出码0：核心数据成功，时效和数据质量正常。")
    else:
        print("退出码2：主体计算成功，但存在时效、历史回退或数据质量警告。")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
