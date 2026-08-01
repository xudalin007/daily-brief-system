#!/usr/bin/env python3
"""获取并计算 Farside 美国现货 BTC ETF Flow 数据。"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SOURCE_URL = "https://farside.co.uk/btc/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "etf_flow" / "generated"

CACHE_TTL = timedelta(minutes=15)
NETWORK_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

CORE_FUNDS = ("IBIT", "FBTC", "BITB", "ARKB", "GBTC", "BTC")
KNOWN_FUNDS = (
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

DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
DISPLAY_DATE_PATTERN = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\Z"
)
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


class EtfFlowError(Exception):
    """ETF Flow 无法安全获取或计算时向用户报告的错误。"""


@dataclass
class ParsedPage:
    """从 Farside HTML 标准化得到的交易日记录。"""

    rows: list[dict[str, Any]]
    headers: list[str]
    fund_columns: dict[str, int]
    duplicate_dates: list[str]
    table_count: int


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


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def parse_page_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    if DATE_PATTERN.fullmatch(text):
        try:
            return parse_date_strict(text)
        except ValueError:
            return None
    matched = DISPLAY_DATE_PATTERN.fullmatch(text)
    if not matched:
        return None
    day_text, month_text, year_text = matched.groups()
    month = MONTHS.get(month_text.lower())
    if month is None:
        return None
    try:
        return date(int(year_text), month, int(day_text))
    except ValueError:
        return None


def parse_flow_value(value: Any) -> float | None:
    """解析 Farside 以百万美元计的数值；缺失符号永不转换为0。"""

    text = clean_text(value)
    if text.lower() in {"", "-", "–", "—", "n/a", "na", "null"}:
        return None

    parenthesized = text.startswith("(") and text.endswith(")")
    if parenthesized:
        text = clean_text(text[1:-1])
    elif text.startswith("(") or text.endswith(")"):
        return None

    normalized = text.replace(",", "").replace("$", "").replace("£", "")
    if normalized.lower().endswith("m"):
        normalized = normalized[:-1]
    normalized = clean_text(normalized)
    if not NUMBER_PATTERN.fullmatch(normalized):
        return None
    try:
        numeric = float(normalized)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(numeric):
        return None
    if parenthesized:
        numeric = -abs(numeric)
    return numeric


class _TableCollector(HTMLParser):
    """只收集顶层 table/tr/th/td，保留动态列顺序。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_colspan = 1

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        if tag == "table":
            if self._table_depth == 0:
                self._table = []
            self._table_depth += 1
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell_parts = []
            attributes = {key.lower(): value for key, value in attrs}
            try:
                self._cell_colspan = max(
                    1, int(attributes.get("colspan") or "1")
                )
            except ValueError:
                self._cell_colspan = 1
        elif tag == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._table_depth == 1 and self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            if self._table_depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
                self._row = None
                self._cell_parts = None
            if self._table_depth > 0:
                self._table_depth -= 1
            return
        if self._table_depth != 1:
            return
        if tag in {"th", "td"} and self._cell_parts is not None:
            if self._row is not None:
                value = clean_text("".join(self._cell_parts))
                self._row.extend([value] * self._cell_colspan)
            self._cell_parts = None
            self._cell_colspan = 1
        elif tag == "tr" and self._row is not None:
            if self._table is not None and self._row:
                self._table.append(self._row)
            self._row = None
            self._cell_parts = None


def normalize_header(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", clean_text(value).upper())


def _date_column_counts(table: list[list[str]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for row in table:
        for index, value in enumerate(row):
            if parse_page_date(value) is not None:
                counts[index] = counts.get(index, 0) + 1
    return counts


def _discover_header_columns(
    table: list[list[str]], date_column: int, first_data_index: int
) -> tuple[dict[str, int], int | None, list[str]]:
    header_rows = table[:first_data_index]
    total_column: int | None = None
    for row in header_rows:
        for index, value in enumerate(row):
            token = normalize_header(value)
            if token == "TOTAL" and total_column is None:
                total_column = index

    # Farside may add or reorder funds. Choose the header row containing the
    # largest number of known tickers, then retain every ticker-like column in
    # that row rather than restricting parsing to today's fixed fund list.
    fund_header_row = max(
        header_rows,
        key=lambda row: sum(normalize_header(value) in KNOWN_FUNDS for value in row),
        default=[],
    )
    fund_columns: dict[str, int] = {}
    for index, value in enumerate(fund_header_row):
        token = normalize_header(value)
        if (
            index != date_column
            and index != total_column
            and token not in {"", "DATE", "TOTAL", "FEE"}
            and re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", token)
        ):
            fund_columns.setdefault(token, index)
    headers = [
        fund
        for fund, _index in sorted(fund_columns.items(), key=lambda item: item[1])
    ]
    return fund_columns, total_column, headers


def _parse_candidate_table(
    table: list[list[str]], table_count: int
) -> ParsedPage | None:
    date_counts = _date_column_counts(table)
    if not date_counts:
        return None
    date_column = max(date_counts, key=lambda index: (date_counts[index], -index))
    data_indices = [
        index
        for index, row in enumerate(table)
        if date_column < len(row) and parse_page_date(row[date_column]) is not None
    ]
    if not data_indices:
        return None
    first_data_index = min(data_indices)
    fund_columns, total_column, headers = _discover_header_columns(
        table, date_column, first_data_index
    )
    if total_column is None or any(fund not in fund_columns for fund in CORE_FUNDS):
        return None

    ordered_funds = [
        fund
        for fund, _index in sorted(fund_columns.items(), key=lambda item: item[1])
    ]
    rows: list[dict[str, Any]] = []
    occurrence_count: dict[str, int] = {}
    for row in table:
        if date_column >= len(row):
            continue
        row_date = parse_page_date(row[date_column])
        if row_date is None:
            continue
        funds: dict[str, float | None] = {}
        for fund in ordered_funds:
            column = fund_columns[fund]
            raw_value = row[column] if column < len(row) else ""
            funds[fund] = parse_flow_value(raw_value)
        raw_total = row[total_column] if total_column < len(row) else ""
        total = parse_flow_value(raw_total)
        missing_funds = [fund for fund in ordered_funds if funds[fund] is None]
        missing_core_funds = [fund for fund in CORE_FUNDS if funds[fund] is None]
        if total is None:
            status = "unavailable"
        elif missing_core_funds:
            status = "provisional"
        else:
            status = "complete"
        date_text = row_date.isoformat()
        occurrence_count[date_text] = occurrence_count.get(date_text, 0) + 1
        rows.append(
            {
                "date": date_text,
                "funds": funds,
                "total": total,
                "missing_funds": missing_funds,
                "missing_core_funds": missing_core_funds,
                "status": status,
            }
        )

    if not rows:
        return None
    rows.sort(key=lambda item: item["date"])
    duplicate_dates = sorted(
        value for value, count in occurrence_count.items() if count > 1
    )
    return ParsedPage(
        rows=rows,
        headers=["Date", *ordered_funds, "Total"],
        fund_columns=copy.deepcopy(fund_columns),
        duplicate_dates=duplicate_dates,
        table_count=table_count,
    )


def parse_farside_html(html_text: str) -> ParsedPage:
    if not isinstance(html_text, str) or not html_text.strip():
        raise EtfFlowError("Farside 页面为空或不是有效文本")
    parser = _TableCollector()
    try:
        parser.feed(html_text)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise EtfFlowError(f"Farside HTML解析失败：{exc}") from exc

    candidates: list[ParsedPage] = []
    for table in parser.tables:
        candidate = _parse_candidate_table(table, len(parser.tables))
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        raise EtfFlowError(
            "未找到包含日期、核心基金和Total且具有有效记录的Farside ETF表格"
        )
    return max(candidates, key=lambda candidate: len(candidate.rows))


def reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 包含非有限数值 {value}")


def loads_json_strict(text: str, context: str) -> Any:
    try:
        return json.loads(text, parse_constant=reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EtfFlowError(f"{context}不是有效JSON：{exc}") from exc


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
        raise EtfFlowError(f"无法原子写入文件 {path}：{exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EtfFlowError(f"无法序列化JSON文件 {path}：{exc}") from exc
    atomic_write_text(path, text + "\n")


def fetch_html(url: str, timeout: int = NETWORK_TIMEOUT_SECONDS) -> str:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "daily-brief-system-etf-fetcher/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise EtfFlowError(f"HTTP请求失败（状态码 {exc.code}）：{url}") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise EtfFlowError(f"网络请求失败：{reason}；URL：{url}") from exc
    except (TimeoutError, OSError) as exc:
        raise EtfFlowError(f"网络请求超时或连接失败：{exc}；URL：{url}") from exc

    if not raw:
        raise EtfFlowError(f"Farside返回空响应：{url}")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise EtfFlowError(
            f"Farside响应超过{MAX_RESPONSE_BYTES}字节安全上限：{url}"
        )
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError) as exc:
        raise EtfFlowError(f"Farside响应字符编码无效：{charset}") from exc


def cache_miss(reason: str, cache_path: Path) -> tuple[None, dict[str, Any], None]:
    return None, {
        "used_cache": False,
        "status": "缓存未使用",
        "reason": reason,
        "cache_file": str(cache_path),
    }, None


def load_cache(
    cache_path: Path, now: datetime
) -> tuple[str | None, dict[str, Any], str | None]:
    if not cache_path.exists():
        return cache_miss("缓存文件不存在", cache_path)
    try:
        envelope = loads_json_strict(
            cache_path.read_text(encoding="utf-8"), f"缓存文件 {cache_path}"
        )
    except (OSError, EtfFlowError) as exc:
        return cache_miss(f"缓存无法读取或格式无效：{exc}", cache_path)
    if not isinstance(envelope, dict):
        return cache_miss("缓存顶层结构无效", cache_path)
    if envelope.get("source_url") != SOURCE_URL:
        return cache_miss("缓存来源URL不匹配", cache_path)
    try:
        fetched_at = parse_utc_timestamp(envelope.get("fetched_at_utc"))
    except (TypeError, ValueError):
        return cache_miss("缓存抓取时间无效", cache_path)
    age = now.astimezone(timezone.utc) - fetched_at
    if age.total_seconds() < 0:
        return cache_miss("缓存抓取时间位于未来", cache_path)
    if age >= CACHE_TTL:
        return cache_miss("缓存已超过15分钟有效期", cache_path)
    raw_html = envelope.get("raw_html")
    if not isinstance(raw_html, str) or not raw_html:
        return cache_miss("缓存缺少原始HTML", cache_path)
    try:
        parse_farside_html(raw_html)
    except EtfFlowError as exc:
        return cache_miss(f"缓存HTML结构无效：{exc}", cache_path)
    fetched_at_text = format_utc_timestamp(fetched_at)
    return raw_html, {
        "used_cache": True,
        "status": "使用缓存",
        "cache_file": str(cache_path),
        "fetched_at_utc": fetched_at_text,
        "age_seconds": round(age.total_seconds(), 3),
        "source_url": SOURCE_URL,
    }, fetched_at_text


def obtain_html(
    output_dir: Path,
    refresh: bool,
    now: datetime,
    fetcher: Callable[[str, int], str] = fetch_html,
) -> tuple[str, dict[str, Any], str, int]:
    cache_path = output_dir / "cache" / "farside_btc_raw.json"
    if refresh:
        html_text, cache_status, fetched_at = cache_miss(
            "--refresh 强制刷新", cache_path
        )
    else:
        html_text, cache_status, fetched_at = load_cache(cache_path, now)
    if html_text is not None and fetched_at is not None:
        return html_text, cache_status, fetched_at, 0

    html_text = fetcher(SOURCE_URL, NETWORK_TIMEOUT_SECONDS)
    parse_farside_html(html_text)
    fetched_at = format_utc_timestamp(now)
    atomic_write_json(
        cache_path,
        {
            "fetched_at_utc": fetched_at,
            "source_url": SOURCE_URL,
            "raw_html": html_text,
        },
    )
    return html_text, {
        "used_cache": False,
        "status": "网络请求",
        "cache_file": str(cache_path),
        "cache_miss_reason": cache_status["reason"],
        "fetched_at_utc": fetched_at,
        "age_seconds": 0,
        "source_url": SOURCE_URL,
    }, fetched_at, 1


def _append_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _count_directions(rows: list[dict[str, Any]]) -> tuple[int, int]:
    positive = sum(1 for row in rows if row["total"] > 0)
    negative = sum(1 for row in rows if row["total"] < 0)
    return positive, negative


def _sum_complete_rows(
    rows: list[dict[str, Any]], count: int
) -> tuple[float | None, list[dict[str, Any]]]:
    selected = rows[-count:]
    if len(selected) < count:
        return None, selected
    return sum(row["total"] for row in selected), selected


def build_result(
    parsed_page: ParsedPage,
    requested_as_of: date,
    now: datetime,
    cache_status: dict[str, Any] | None = None,
    fetched_at_utc: str | None = None,
    network_request_count: int = 0,
) -> dict[str, Any]:
    if not isinstance(requested_as_of, date):
        raise EtfFlowError("requested_as_of必须是日期")
    now = now.astimezone(timezone.utc)
    eligible_source_rows = [
        row
        for row in parsed_page.rows
        if parse_date_strict(row["date"]) <= requested_as_of
    ]
    if not eligible_source_rows:
        raise EtfFlowError(
            f"截至 {requested_as_of.isoformat()}，Farside页面没有可用交易日记录"
        )

    rows_by_date: dict[str, dict[str, Any]] = {}
    for row in eligible_source_rows:
        rows_by_date[row["date"]] = copy.deepcopy(row)
    eligible_rows = sorted(rows_by_date.values(), key=lambda item: item["date"])
    latest_page_row = eligible_rows[-1]
    complete_rows = [row for row in eligible_rows if row["status"] == "complete"]
    if not complete_rows:
        raise EtfFlowError(
            f"截至 {requested_as_of.isoformat()}，当前Farside页面没有可靠complete交易日；"
            "未取得可靠完整值"
        )
    latest_complete_row = complete_rows[-1]
    previous_complete_row = complete_rows[-2] if len(complete_rows) >= 2 else None

    cumulative_3d, rows_3d = _sum_complete_rows(complete_rows, 3)
    cumulative_5d, rows_5d = _sum_complete_rows(complete_rows, 5)
    positive_3d, negative_3d = _count_directions(rows_3d)
    positive_5d, negative_5d = _count_directions(rows_5d)
    latest_total = latest_complete_row["total"]
    previous_total = (
        previous_complete_row["total"] if previous_complete_row is not None else None
    )
    sign_flip = bool(
        previous_total is not None
        and (
            (latest_total > 0 and previous_total < 0)
            or (latest_total < 0 and previous_total > 0)
        )
    )

    warnings: list[str] = []
    eligible_duplicate_dates = [
        value
        for value in parsed_page.duplicate_dates
        if parse_date_strict(value) <= requested_as_of
    ]
    if eligible_duplicate_dates:
        _append_warning(
            warnings,
            "ETF Flow存在重复交易日：" + "、".join(eligible_duplicate_dates),
        )
    if latest_page_row["status"] == "provisional":
        _append_warning(
            warnings,
            "ETF Flow最新交易日仍为暂报："
            f"{latest_page_row['date']}；缺失核心基金："
            + "、".join(latest_page_row["missing_core_funds"]),
        )
    elif latest_page_row["status"] == "unavailable":
        _append_warning(
            warnings,
            f"ETF Flow最新交易日 {latest_page_row['date']} 的Total不可用",
        )
    if previous_complete_row is None:
        _append_warning(warnings, "ETF Flow缺少前一个complete交易日")
    if cumulative_3d is None:
        _append_warning(warnings, "ETF Flow不足3个complete交易日，3D累计不可用")
    if cumulative_5d is None:
        _append_warning(warnings, "ETF Flow不足5个complete交易日，5D累计不可用")

    provisional_latest = None
    if latest_page_row["status"] == "provisional":
        provisional_latest = {
            "date": latest_page_row["date"],
            "partial_total": latest_page_row["total"],
            "missing_core_funds": list(latest_page_row["missing_core_funds"]),
            "missing_funds": list(latest_page_row["missing_funds"]),
        }

    cache_status = copy.deepcopy(
        cache_status
        or {
            "used_cache": False,
            "status": "测试数据",
            "source_url": SOURCE_URL,
        }
    )
    if fetched_at_utc is None:
        fetched_at_utc = format_utc_timestamp(now)
    else:
        try:
            parse_utc_timestamp(fetched_at_utc)
        except (TypeError, ValueError) as exc:
            raise EtfFlowError("fetched_at_utc不是有效UTC时间戳") from exc

    latest_page_date = latest_page_row["date"]
    latest_complete_date = latest_complete_row["date"]
    return {
        "generated_at_utc": format_utc_timestamp(now),
        "fetched_at_utc": fetched_at_utc,
        "requested_as_of": requested_as_of.isoformat(),
        "source_url": SOURCE_URL,
        "source": {
            "provider": "Farside Investors",
            "url": SOURCE_URL,
            "unit": "USD millions",
        },
        "cache_status": cache_status,
        "network_request_count": network_request_count,
        "latest_page_date": latest_page_date,
        "latest_complete_date": latest_complete_date,
        "latest_row_status": latest_page_row["status"],
        "latest_page_row": copy.deepcopy(latest_page_row),
        "latest_complete_row": copy.deepcopy(latest_complete_row),
        "previous_complete_row": copy.deepcopy(previous_complete_row),
        "flows": {
            "latest": latest_total,
            "previous": previous_total,
            "cumulative_3d": cumulative_3d,
            "cumulative_5d": cumulative_5d,
        },
        "continuity": {
            "sign_flip": sign_flip,
            "positive_days_3d": positive_3d,
            "negative_days_3d": negative_3d,
            "positive_days_5d": positive_5d,
            "negative_days_5d": negative_5d,
        },
        "provisional_latest": provisional_latest,
        "quality_checks": {
            "html_table_count": parsed_page.table_count,
            "parsed_row_count": len(parsed_page.rows),
            "eligible_row_count": len(eligible_rows),
            "complete_row_count": len(complete_rows),
            "provisional_row_count": sum(
                row["status"] == "provisional" for row in eligible_rows
            ),
            "unavailable_row_count": sum(
                row["status"] == "unavailable" for row in eligible_rows
            ),
            "duplicate_dates": eligible_duplicate_dates,
            "fund_headers": [
                fund
                for fund, _index in sorted(
                    parsed_page.fund_columns.items(), key=lambda item: item[1]
                )
            ],
            "complete_dates_3d": [row["date"] for row in rows_3d],
            "complete_dates_5d": [row["date"] for row in rows_5d],
            "page_delay_days": (
                requested_as_of - parse_date_strict(latest_page_date)
            ).days,
            "latest_page_date": latest_page_date,
            "latest_complete_date": latest_complete_date,
            "latest_row_status": latest_page_row["status"],
        },
        "rows": copy.deepcopy(eligible_rows),
        "warnings": warnings,
    }


def format_flow(value: float | None) -> str:
    if value is None:
        return "unavailable"
    if value > 0:
        return f"+${value:.1f}M"
    if value < 0:
        return f"-${abs(value):.1f}M"
    return "$0.0M"


def render_markdown(result: dict[str, Any]) -> str:
    latest = result["latest_complete_row"]
    previous = result["previous_complete_row"]
    continuity = result["continuity"]
    lines = [
        "## 美国现货 BTC ETF Flow",
        "",
        "最近完整交易日：",
        "",
        latest["date"],
        "",
        "净流量：",
        "",
        format_flow(result["flows"]["latest"]),
        "",
        "前一完整交易日：",
        "",
        previous["date"] if previous is not None else "unavailable",
        format_flow(result["flows"]["previous"]),
        "",
        "3交易日累计：",
        format_flow(result["flows"]["cumulative_3d"]),
        "",
        "5交易日累计：",
        format_flow(result["flows"]["cumulative_5d"]),
        "",
        "连续性：",
        "正负方向发生反转" if continuity["sign_flip"] else "未发生反转",
        "",
        "主要基金：",
        "",
        "| ETF | Flow |",
        "|---|---:|",
    ]
    for fund in CORE_FUNDS:
        lines.append(f"| {fund} | {format_flow(latest['funds'].get(fund))} |")

    provisional = result["provisional_latest"]
    if provisional is not None:
        lines.extend(
            [
                "",
                "### ⚠️ 最新交易日仍为暂报",
                "",
                f"暂报日期：{provisional['date']}",
                f"当前暂报总额：{format_flow(provisional['partial_total'])}",
                "缺失核心基金："
                + ("、".join(provisional["missing_core_funds"]) or "无"),
                "全部缺失基金："
                + ("、".join(provisional["missing_funds"]) or "无"),
                "",
                "该暂报数据没有作为正式日流量使用。",
            ]
        )

    missing_funds = latest["missing_funds"]
    lines.extend(
        [
            "",
            "### 数据质量与来源",
            "",
            f"- 页面最新交易日：{result['latest_page_date']}（{result['latest_row_status']}）",
            f"- 正式采用交易日：{result['latest_complete_date']}",
            f"- 最近完整日缺失基金：{'、'.join(missing_funds) if missing_funds else '无'}",
            f"- 缓存：{result['cache_status'].get('status', '未知')}。",
            f"- 抓取时间：{result['fetched_at_utc']}。",
            f"- 数据源：{result['source_url']}",
            "- 警告：",
        ]
    )
    if result["warnings"]:
        lines.extend(f"  - {warning}" for warning in result["warnings"])
    else:
        lines.append("  - 无")
    return "\n".join(lines) + "\n"


def write_outputs(
    result: dict[str, Any], output_dir: Path
) -> tuple[Path, Path, str]:
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
        description="获取Farside美国现货BTC ETF Flow并生成连续性指标。"
    )
    parser.add_argument(
        "--as-of",
        help="分析截止日期（YYYY-MM-DD）；默认使用当前UTC日期",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="忽略15分钟缓存并强制重新请求Farside页面",
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
        html_text, cache_status, fetched_at, network_request_count = obtain_html(
            output_dir, args.refresh, now
        )
        parsed_page = parse_farside_html(html_text)
        result = build_result(
            parsed_page,
            requested_as_of,
            now,
            cache_status=cache_status,
            fetched_at_utc=fetched_at,
            network_request_count=network_request_count,
        )
        json_path, markdown_path, markdown = write_outputs(result, output_dir)
        print(markdown, end="")
        print(f"\n输出文件：{json_path}")
        print(f"输出文件：{markdown_path}")
        exit_code = determine_exit_code(result)
        if exit_code == 0:
            print("退出码 0：ETF Flow核心数据计算成功，未发现警告。")
        else:
            print("退出码 2：ETF Flow主体计算成功，但存在暂报或数据质量警告。")
        return exit_code
    except (EtfFlowError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print(
            "退出码 1：网络、HTML、缓存、可靠完整交易日或输出发生致命错误。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
