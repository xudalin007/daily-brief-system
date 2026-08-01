#!/usr/bin/env python3
"""为每日简报生成统一、可验证的数据上下文。"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

if __package__:
    from . import fetch_ahr999, fetch_cvdd, fetch_etf_flow, fetch_funding
else:
    import fetch_ahr999
    import fetch_cvdd
    import fetch_etf_flow
    import fetch_funding


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CVDD_OUTPUT_DIR = PROJECT_ROOT / "data" / "cvdd" / "generated"
CVDD_LATEST_JSON = CVDD_OUTPUT_DIR / "latest.json"
ETF_OUTPUT_DIR = PROJECT_ROOT / "data" / "etf_flow" / "generated"
ETF_LATEST_JSON = ETF_OUTPUT_DIR / "latest.json"
AHR999_OUTPUT_DIR = PROJECT_ROOT / "data" / "ahr999" / "generated"
AHR999_LATEST_JSON = AHR999_OUTPUT_DIR / "latest.json"
FUNDING_OUTPUT_DIR = PROJECT_ROOT / "data" / "funding" / "generated"
FUNDING_LATEST_JSON = FUNDING_OUTPUT_DIR / "latest.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "daily_brief" / "generated"

MONTHLY_REFERENCE_NOTE = (
    "365D数据作为月度参考数据保留；不表示每日必须展示，"
    "最终由日报生成层决定是否显示。"
)


class PreparationError(Exception):
    """每日简报数据上下文无法安全生成。"""


@dataclass(frozen=True)
class CvddInvocation:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def invoke_cvdd(as_of: str | None, refresh: bool) -> CvddInvocation:
    """调用现有 CVDD CLI，并捕获其成功时的详细终端输出。"""

    arguments = ["--output-dir", str(CVDD_OUTPUT_DIR)]
    if as_of is not None:
        arguments.extend(["--as-of", as_of])
    if refresh:
        arguments.append("--refresh")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
        stderr_buffer
    ):
        exit_code = fetch_cvdd.main(arguments)
    return CvddInvocation(
        exit_code=exit_code,
        stdout=stdout_buffer.getvalue(),
        stderr=stderr_buffer.getvalue(),
    )


def invoke_etf_flow(as_of: str | None, refresh: bool) -> CvddInvocation:
    """调用现有 ETF Flow CLI，并捕获其详细终端输出。"""

    arguments = ["--output-dir", str(ETF_OUTPUT_DIR)]
    if as_of is not None:
        arguments.extend(["--as-of", as_of])
    if refresh:
        arguments.append("--refresh")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
        stderr_buffer
    ):
        exit_code = fetch_etf_flow.main(arguments)
    return CvddInvocation(
        exit_code=exit_code,
        stdout=stdout_buffer.getvalue(),
        stderr=stderr_buffer.getvalue(),
    )


def invoke_ahr999(as_of: str | None, refresh: bool) -> CvddInvocation:
    """调用 AHR999 CLI，并捕获其详细终端输出。"""

    arguments = ["--output-dir", str(AHR999_OUTPUT_DIR)]
    if as_of is not None:
        arguments.extend(["--as-of", as_of])
    if refresh:
        arguments.append("--refresh")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
        stderr_buffer
    ):
        exit_code = fetch_ahr999.main(arguments)
    return CvddInvocation(
        exit_code=exit_code,
        stdout=stdout_buffer.getvalue(),
        stderr=stderr_buffer.getvalue(),
    )


def invoke_funding(_as_of: str | None, refresh: bool) -> CvddInvocation:
    """调用 Funding 当前快照 CLI；该模块不伪造历史 --as-of。"""

    arguments = ["--output-dir", str(FUNDING_OUTPUT_DIR)]
    if refresh:
        arguments.append("--refresh")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
        stderr_buffer
    ):
        exit_code = fetch_funding.main(arguments)
    return CvddInvocation(
        exit_code=exit_code,
        stdout=stdout_buffer.getvalue(),
        stderr=stderr_buffer.getvalue(),
    )


def require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PreparationError(f"数据输出字段 {path} 必须是对象")
    return value


def require_key(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise PreparationError(f"数据输出缺少字段 {path}.{key}")
    return mapping[key]


def require_date(value: Any, path: str) -> str:
    try:
        return fetch_cvdd.parse_date_strict(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise PreparationError(f"数据输出字段 {path} 不是有效日期") from exc


def require_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreparationError(f"数据输出字段 {path} 必须是非空字符串")
    return value


def require_number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PreparationError(f"数据输出字段 {path} 必须是数值")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise PreparationError(f"数据输出字段 {path} 必须是有限数")
    if positive and numeric <= 0:
        raise PreparationError(f"数据输出字段 {path} 必须大于0")
    return numeric


def require_nonnegative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PreparationError(f"数据输出字段 {path} 必须是非负整数")
    return value


def normalize_cvdd_point(source: Any, path: str) -> dict[str, Any]:
    point = require_mapping(source, path)
    available = require_key(point, "available", path)
    if not isinstance(available, bool):
        raise PreparationError(f"CVDD输出字段 {path}.available 必须是布尔值")
    target_date = require_date(require_key(point, "target_date", path), f"{path}.target_date")

    if available:
        actual_date = require_date(
            require_key(point, "actual_date", path), f"{path}.actual_date"
        )
        value = require_number(
            require_key(point, "cvdd", path), f"{path}.cvdd", positive=True
        )
        fallback_days = require_nonnegative_int(
            require_key(point, "fallback_days", path), f"{path}.fallback_days"
        )
    else:
        actual_date = point.get("actual_date")
        value = point.get("cvdd")
        fallback_days = point.get("fallback_days")
        if actual_date is not None:
            actual_date = require_date(actual_date, f"{path}.actual_date")
        if value is not None:
            value = require_number(value, f"{path}.cvdd", positive=True)
        if fallback_days is not None:
            fallback_days = require_nonnegative_int(
                fallback_days, f"{path}.fallback_days"
            )

    return {
        "available": available,
        "target_date": target_date,
        "actual_date": actual_date,
        "value": value,
        "fallback_days": fallback_days,
    }


def normalize_price_cvdd_point(source: Any, path: str) -> dict[str, Any]:
    point = require_mapping(source, path)
    available = require_key(point, "available", path)
    if not isinstance(available, bool):
        raise PreparationError(f"CVDD输出字段 {path}.available 必须是布尔值")
    target_date = require_date(require_key(point, "target_date", path), f"{path}.target_date")

    if available:
        actual_date = require_date(
            require_key(point, "actual_date", path), f"{path}.actual_date"
        )
        value = require_number(
            require_key(point, "price_cvdd", path),
            f"{path}.price_cvdd",
            positive=True,
        )
        btc_price = require_number(
            require_key(point, "btc_price", path),
            f"{path}.btc_price",
            positive=True,
        )
        cvdd_value = require_number(
            require_key(point, "cvdd", path), f"{path}.cvdd", positive=True
        )
        fallback_days = require_nonnegative_int(
            require_key(point, "fallback_days", path), f"{path}.fallback_days"
        )
    else:
        actual_date = point.get("actual_date")
        value = point.get("price_cvdd")
        btc_price = point.get("btc_price")
        cvdd_value = point.get("cvdd")
        fallback_days = point.get("fallback_days")
        if actual_date is not None:
            actual_date = require_date(actual_date, f"{path}.actual_date")
        if value is not None:
            value = require_number(value, f"{path}.price_cvdd", positive=True)
        if btc_price is not None:
            btc_price = require_number(
                btc_price, f"{path}.btc_price", positive=True
            )
        if cvdd_value is not None:
            cvdd_value = require_number(
                cvdd_value, f"{path}.cvdd", positive=True
            )
        if fallback_days is not None:
            fallback_days = require_nonnegative_int(
                fallback_days, f"{path}.fallback_days"
            )

    return {
        "available": available,
        "target_date": target_date,
        "actual_date": actual_date,
        "value": value,
        "btc_price": btc_price,
        "cvdd": cvdd_value,
        "fallback_days": fallback_days,
    }


def normalize_change(source: Any, path: str, *, required: bool) -> dict[str, Any]:
    change = require_mapping(source, path)
    rate = require_key(change, "rate", path)
    percent = require_key(change, "percent", path)
    direction = require_key(change, "direction", path)

    if rate is None:
        if required:
            raise PreparationError(f"CVDD核心变化率 {path}.rate 不可用")
        normalized_rate = None
        normalized_percent = None
    else:
        normalized_rate = require_number(rate, f"{path}.rate")
        normalized_percent = require_number(percent, f"{path}.percent")
    require_text(direction, f"{path}.direction")
    return {
        "rate": normalized_rate,
        "percent": normalized_percent,
        "direction": direction,
    }


def require_available(point: dict[str, Any], path: str) -> None:
    if not point["available"] or point["value"] is None:
        raise PreparationError(f"CVDD核心数据 {path} 不可用")


def load_cvdd_output(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreparationError(f"无法读取CVDD输出 {path}：{exc}") from exc
    try:
        payload = fetch_cvdd.loads_json_strict(text, f"CVDD输出文件 {path}")
    except fetch_cvdd.CvddError as exc:
        raise PreparationError(str(exc)) from exc
    return require_mapping(payload, "root")


def load_etf_output(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreparationError(f"无法读取ETF Flow输出 {path}：{exc}") from exc
    try:
        payload = fetch_cvdd.loads_json_strict(text, f"ETF Flow输出文件 {path}")
    except fetch_cvdd.CvddError as exc:
        raise PreparationError(str(exc)) from exc
    return require_mapping(payload, "etf_flow.root")


def load_ahr999_output(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreparationError(f"无法读取AHR999输出 {path}：{exc}") from exc
    try:
        payload = fetch_ahr999.loads_json_strict(text, f"AHR999输出文件 {path}")
    except fetch_ahr999.Ahr999Error as exc:
        raise PreparationError(str(exc)) from exc
    return require_mapping(payload, "ahr999.root")


def load_funding_output(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreparationError(f"无法读取Funding输出 {path}：{exc}") from exc
    try:
        payload = fetch_funding.loads_json_strict(text, f"Funding输出文件 {path}")
    except fetch_funding.FundingError as exc:
        raise PreparationError(str(exc)) from exc
    return require_mapping(payload, "funding.root")


def require_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PreparationError(f"数据输出字段 {path} 必须是字符串数组")
    return list(value)


def normalize_optional_number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    return require_number(value, path)


def normalize_etf_row(value: Any, path: str) -> dict[str, Any]:
    row = require_mapping(value, path)
    row_date = require_date(require_key(row, "date", path), f"{path}.date")
    status = require_key(row, "status", path)
    if status not in {"complete", "provisional", "unavailable"}:
        raise PreparationError(f"ETF Flow输出字段 {path}.status 无效")
    funds_source = require_mapping(require_key(row, "funds", path), f"{path}.funds")
    funds: dict[str, float | None] = {}
    for fund, raw_value in funds_source.items():
        if not isinstance(fund, str) or not fund:
            raise PreparationError(f"ETF Flow输出字段 {path}.funds 包含无效基金名")
        funds[fund] = normalize_optional_number(raw_value, f"{path}.funds.{fund}")
    missing_core_columns = [
        fund for fund in fetch_etf_flow.CORE_FUNDS if fund not in funds
    ]
    if missing_core_columns:
        raise PreparationError(
            f"ETF Flow输出字段 {path}.funds 缺少核心基金列："
            + "、".join(missing_core_columns)
        )
    total = normalize_optional_number(
        require_key(row, "total", path), f"{path}.total"
    )
    missing_funds = require_string_list(
        require_key(row, "missing_funds", path), f"{path}.missing_funds"
    )
    missing_core_funds = require_string_list(
        require_key(row, "missing_core_funds", path),
        f"{path}.missing_core_funds",
    )
    computed_missing_funds = [fund for fund, fund_value in funds.items() if fund_value is None]
    computed_missing_core = [
        fund for fund in fetch_etf_flow.CORE_FUNDS if funds.get(fund) is None
    ]
    if (
        len(missing_funds) != len(set(missing_funds))
        or set(missing_funds) != set(computed_missing_funds)
    ):
        raise PreparationError(f"ETF Flow输出字段 {path}.missing_funds与funds不一致")
    if (
        len(missing_core_funds) != len(set(missing_core_funds))
        or set(missing_core_funds) != set(computed_missing_core)
    ):
        raise PreparationError(
            f"ETF Flow输出字段 {path}.missing_core_funds与核心基金数据不一致"
        )
    if status == "complete" and (total is None or computed_missing_core):
        raise PreparationError(f"ETF Flow完整交易日 {path} 的Total或核心基金不完整")
    if status == "provisional" and (total is None or not computed_missing_core):
        raise PreparationError(f"ETF Flow暂报交易日 {path} 的状态与数据不一致")
    if status == "unavailable" and total is not None:
        raise PreparationError(f"ETF Flow不可用交易日 {path} 仍包含可解析Total")
    return {
        "date": row_date,
        "funds": funds,
        "total": total,
        "missing_funds": missing_funds,
        "missing_core_funds": missing_core_funds,
        "status": status,
    }


def build_etf_section(
    etf_output: dict[str, Any], etf_exit_code: int
) -> tuple[str, dict[str, Any], list[str], dict[str, Any]]:
    if etf_exit_code not in (0, 2):
        raise PreparationError(f"不支持的ETF Flow退出码：{etf_exit_code}")
    requested_as_of = require_date(
        require_key(etf_output, "requested_as_of", "etf_flow.root"),
        "etf_flow.requested_as_of",
    )
    source = require_mapping(
        require_key(etf_output, "source", "etf_flow.root"), "etf_flow.source"
    )
    source_url = require_text(
        require_key(etf_output, "source_url", "etf_flow.root"),
        "etf_flow.source_url",
    )
    if source_url != fetch_etf_flow.SOURCE_URL:
        raise PreparationError("ETF Flow输出来源不是已固定的Farside页面")
    fetched_at_utc = require_text(
        require_key(etf_output, "fetched_at_utc", "etf_flow.root"),
        "etf_flow.fetched_at_utc",
    )
    try:
        fetch_etf_flow.parse_utc_timestamp(fetched_at_utc)
    except (TypeError, ValueError) as exc:
        raise PreparationError("ETF Flow输出的fetched_at_utc无效") from exc
    cache_status = require_mapping(
        require_key(etf_output, "cache_status", "etf_flow.root"),
        "etf_flow.cache_status",
    )
    network_request_count = require_nonnegative_int(
        require_key(etf_output, "network_request_count", "etf_flow.root"),
        "etf_flow.network_request_count",
    )
    latest_page_date = require_date(
        require_key(etf_output, "latest_page_date", "etf_flow.root"),
        "etf_flow.latest_page_date",
    )
    latest_complete_date = require_date(
        require_key(etf_output, "latest_complete_date", "etf_flow.root"),
        "etf_flow.latest_complete_date",
    )
    latest_row_status = require_key(
        etf_output, "latest_row_status", "etf_flow.root"
    )
    if latest_row_status not in {"complete", "provisional", "unavailable"}:
        raise PreparationError("ETF Flow输出字段 latest_row_status 无效")

    latest_page_row = normalize_etf_row(
        require_key(etf_output, "latest_page_row", "etf_flow.root"),
        "etf_flow.latest_page_row",
    )
    latest_complete_row = normalize_etf_row(
        require_key(etf_output, "latest_complete_row", "etf_flow.root"),
        "etf_flow.latest_complete_row",
    )
    previous_source = require_key(
        etf_output, "previous_complete_row", "etf_flow.root"
    )
    previous_complete_row = (
        None
        if previous_source is None
        else normalize_etf_row(previous_source, "etf_flow.previous_complete_row")
    )
    if latest_complete_row["status"] != "complete":
        raise PreparationError("ETF Flow latest_complete_row不是complete状态")
    if (
        previous_complete_row is not None
        and previous_complete_row["status"] != "complete"
    ):
        raise PreparationError("ETF Flow previous_complete_row不是complete状态")
    if latest_complete_row["date"] != latest_complete_date:
        raise PreparationError("ETF Flow latest_complete_date与完整行日期不一致")
    if latest_page_row["date"] != latest_page_date:
        raise PreparationError("ETF Flow latest_page_date与页面行日期不一致")
    if latest_page_row["status"] != latest_row_status:
        raise PreparationError("ETF Flow latest_row_status与页面行状态不一致")
    requested_date = fetch_cvdd.parse_date_strict(requested_as_of)
    latest_page_value = fetch_cvdd.parse_date_strict(latest_page_date)
    latest_complete_value = fetch_cvdd.parse_date_strict(latest_complete_date)
    if latest_page_value > requested_date:
        raise PreparationError("ETF Flow latest_page_date晚于requested_as_of")
    if latest_complete_value > latest_page_value:
        raise PreparationError("ETF Flow latest_complete_date晚于latest_page_date")
    if (
        previous_complete_row is not None
        and fetch_cvdd.parse_date_strict(previous_complete_row["date"])
        >= latest_complete_value
    ):
        raise PreparationError("ETF Flow前一完整交易日不早于最近完整交易日")

    flows_source = require_mapping(
        require_key(etf_output, "flows", "etf_flow.root"), "etf_flow.flows"
    )
    flows = {
        "latest": require_number(
            require_key(flows_source, "latest", "etf_flow.flows"),
            "etf_flow.flows.latest",
        ),
        "previous": normalize_optional_number(
            require_key(flows_source, "previous", "etf_flow.flows"),
            "etf_flow.flows.previous",
        ),
        "cumulative_3d": normalize_optional_number(
            require_key(flows_source, "cumulative_3d", "etf_flow.flows"),
            "etf_flow.flows.cumulative_3d",
        ),
        "cumulative_5d": normalize_optional_number(
            require_key(flows_source, "cumulative_5d", "etf_flow.flows"),
            "etf_flow.flows.cumulative_5d",
        ),
    }
    if not math.isclose(
        flows["latest"], latest_complete_row["total"], rel_tol=1e-12, abs_tol=1e-12
    ):
        raise PreparationError("ETF Flow最新正式流量与latest_complete_row不一致")
    if previous_complete_row is None:
        if flows["previous"] is not None:
            raise PreparationError("ETF Flow没有前一完整行但previous流量不为空")
    elif flows["previous"] is None or not math.isclose(
        flows["previous"],
        previous_complete_row["total"],
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise PreparationError("ETF Flow前一正式流量与previous_complete_row不一致")

    continuity_source = require_mapping(
        require_key(etf_output, "continuity", "etf_flow.root"),
        "etf_flow.continuity",
    )
    sign_flip = require_key(continuity_source, "sign_flip", "etf_flow.continuity")
    if not isinstance(sign_flip, bool):
        raise PreparationError("ETF Flow输出字段 continuity.sign_flip 必须是布尔值")
    continuity = {"sign_flip": sign_flip}
    for key in (
        "positive_days_3d",
        "negative_days_3d",
        "positive_days_5d",
        "negative_days_5d",
    ):
        continuity[key] = require_nonnegative_int(
            require_key(continuity_source, key, "etf_flow.continuity"),
            f"etf_flow.continuity.{key}",
        )

    provisional_source = require_key(
        etf_output, "provisional_latest", "etf_flow.root"
    )
    provisional_latest = None
    if provisional_source is not None:
        provisional = require_mapping(
            provisional_source, "etf_flow.provisional_latest"
        )
        provisional_latest = {
            "date": require_date(
                require_key(provisional, "date", "etf_flow.provisional_latest"),
                "etf_flow.provisional_latest.date",
            ),
            "partial_total": normalize_optional_number(
                require_key(
                    provisional, "partial_total", "etf_flow.provisional_latest"
                ),
                "etf_flow.provisional_latest.partial_total",
            ),
            "missing_core_funds": require_string_list(
                require_key(
                    provisional,
                    "missing_core_funds",
                    "etf_flow.provisional_latest",
                ),
                "etf_flow.provisional_latest.missing_core_funds",
            ),
            "missing_funds": require_string_list(
                require_key(
                    provisional, "missing_funds", "etf_flow.provisional_latest"
                ),
                "etf_flow.provisional_latest.missing_funds",
            ),
        }
    if latest_row_status == "provisional":
        if provisional_latest is None:
            raise PreparationError("ETF Flow最新行为provisional但缺少provisional_latest")
        if provisional_latest["date"] != latest_page_row["date"]:
            raise PreparationError("ETF Flow暂报日期与latest_page_row不一致")
        if provisional_latest["partial_total"] is None or not math.isclose(
            provisional_latest["partial_total"],
            latest_page_row["total"],
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise PreparationError("ETF Flow暂报Total与latest_page_row不一致")
        if set(provisional_latest["missing_core_funds"]) != set(
            latest_page_row["missing_core_funds"]
        ) or set(provisional_latest["missing_funds"]) != set(
            latest_page_row["missing_funds"]
        ):
            raise PreparationError("ETF Flow暂报缺失基金与latest_page_row不一致")
    elif provisional_latest is not None:
        raise PreparationError("ETF Flow最新行非provisional但仍包含provisional_latest")

    quality_checks = require_mapping(
        require_key(etf_output, "quality_checks", "etf_flow.root"),
        "etf_flow.quality_checks",
    )
    raw_warnings = require_string_list(
        require_key(etf_output, "warnings", "etf_flow.root"),
        "etf_flow.warnings",
    )
    section = {
        "source": copy.deepcopy(source),
        "source_url": source_url,
        "source_generated_at_utc": etf_output.get("generated_at_utc"),
        "fetched_at_utc": fetched_at_utc,
        "latest_page_date": latest_page_date,
        "latest_complete_date": latest_complete_date,
        "latest_row_status": latest_row_status,
        "cache_status": copy.deepcopy(cache_status),
        "network_request_count": network_request_count,
        "latest_page_row": latest_page_row,
        "latest_complete_row": latest_complete_row,
        "previous_complete_row": previous_complete_row,
        "flows": flows,
        "continuity": continuity,
        "provisional_latest": provisional_latest,
        "warnings": list(raw_warnings),
    }
    return requested_as_of, section, raw_warnings, copy.deepcopy(quality_checks)


def normalize_ahr999_history_point(source: Any, path: str) -> dict[str, Any]:
    point = require_mapping(source, path)
    available = require_key(point, "available", path)
    if not isinstance(available, bool):
        raise PreparationError(f"AHR999输出字段 {path}.available 必须是布尔值")
    target_date = require_date(
        require_key(point, "target_date", path), f"{path}.target_date"
    )
    actual_date = point.get("actual_date")
    fallback_days = point.get("fallback_days")
    ahr999 = point.get("ahr999")
    value = point.get("value")
    quantile5y = point.get("quantile5y")
    close = point.get("close")
    ma200 = point.get("ma200")
    window_kind = point.get("windowKind")

    if available:
        actual_date = require_date(actual_date, f"{path}.actual_date")
        fallback_days = require_nonnegative_int(
            fallback_days, f"{path}.fallback_days"
        )
        ahr999 = require_number(ahr999, f"{path}.ahr999", positive=True)
        value = require_number(value, f"{path}.value", positive=True)
        if not math.isclose(ahr999, value, rel_tol=1e-12, abs_tol=1e-12):
            raise PreparationError(f"AHR999输出字段 {path}.value与ahr999不一致")
        target_value = fetch_cvdd.parse_date_strict(target_date)
        actual_value = fetch_cvdd.parse_date_strict(actual_date)
        if actual_value > target_value or (target_value - actual_value).days != fallback_days:
            raise PreparationError(f"AHR999输出字段 {path} 的日期回退信息不一致")
        if fallback_days > fetch_ahr999.MAX_FALLBACK_DAYS:
            raise PreparationError(f"AHR999输出字段 {path} 回退超过7日")
    else:
        if any(item is not None for item in (actual_date, fallback_days, ahr999, value)):
            raise PreparationError(f"AHR999不可用历史点 {path} 仍包含有效值")
        actual_date = None
        fallback_days = None
        ahr999 = None
        value = None

    if quantile5y is not None:
        quantile5y = require_number(quantile5y, f"{path}.quantile5y")
        if not 0 <= quantile5y <= 1:
            raise PreparationError(f"AHR999输出字段 {path}.quantile5y超出0～1")
    if close is not None:
        close = require_number(close, f"{path}.close")
    if ma200 is not None:
        ma200 = require_number(ma200, f"{path}.ma200")
    if window_kind is not None and not isinstance(window_kind, str):
        raise PreparationError(f"AHR999输出字段 {path}.windowKind必须是字符串或null")
    return {
        "available": available,
        "target_date": target_date,
        "actual_date": actual_date,
        "fallback_days": fallback_days,
        "value": value,
        "ahr999": ahr999,
        "quantile5y": quantile5y,
        "close": close,
        "ma200": ma200,
        "windowKind": window_kind,
    }


def normalize_quantile_change(source: Any, path: str) -> dict[str, Any]:
    change = require_mapping(source, path)
    delta = require_key(change, "delta", path)
    percentage_points = require_key(change, "percentage_points", path)
    direction = require_text(
        require_key(change, "direction", path), f"{path}.direction"
    )
    if delta is None:
        if percentage_points is not None:
            raise PreparationError(
                f"AHR999输出字段 {path}.delta为空但percentage_points不为空"
            )
        normalized_delta = None
        normalized_points = None
    else:
        normalized_delta = require_number(delta, f"{path}.delta")
        normalized_points = require_number(
            percentage_points, f"{path}.percentage_points"
        )
    return {
        "delta": normalized_delta,
        "percentage_points": normalized_points,
        "direction": direction,
    }


def build_ahr999_section(
    ahr999_output: dict[str, Any], ahr999_exit_code: int
) -> tuple[str, dict[str, Any], list[str], dict[str, Any]]:
    if ahr999_exit_code not in (0, 2):
        raise PreparationError(f"不支持的AHR999退出码：{ahr999_exit_code}")
    requested_as_of = require_date(
        require_key(ahr999_output, "requested_as_of", "ahr999.root"),
        "ahr999.requested_as_of",
    )
    actual_latest_date = require_date(
        require_key(ahr999_output, "actual_latest_date", "ahr999.root"),
        "ahr999.actual_latest_date",
    )
    data_delay_days = require_nonnegative_int(
        require_key(ahr999_output, "data_delay_days", "ahr999.root"),
        "ahr999.data_delay_days",
    )
    requested_date = fetch_cvdd.parse_date_strict(requested_as_of)
    actual_date = fetch_cvdd.parse_date_strict(actual_latest_date)
    if actual_date > requested_date or (requested_date - actual_date).days != data_delay_days:
        raise PreparationError("AHR999输出的日期与data_delay_days不一致")

    source = require_mapping(
        require_key(ahr999_output, "source", "ahr999.root"), "ahr999.source"
    )
    source_url = require_text(
        require_key(ahr999_output, "source_url", "ahr999.root"),
        "ahr999.source_url",
    )
    if source_url != fetch_ahr999.SOURCE_URL or source.get("url") != source_url:
        raise PreparationError("AHR999输出来源不是已固定的AIX4U Dataset")
    fetched_at_utc = require_text(
        require_key(ahr999_output, "fetched_at_utc", "ahr999.root"),
        "ahr999.fetched_at_utc",
    )
    try:
        fetch_ahr999.parse_utc_timestamp(fetched_at_utc)
    except (TypeError, ValueError) as exc:
        raise PreparationError("AHR999输出的fetched_at_utc无效") from exc
    cache_status = require_mapping(
        require_key(ahr999_output, "cache_status", "ahr999.root"),
        "ahr999.cache_status",
    )
    used_cache = require_key(cache_status, "used_cache", "ahr999.cache_status")
    if not isinstance(used_cache, bool):
        raise PreparationError("AHR999输出字段 cache_status.used_cache 必须是布尔值")
    network_request_count = require_nonnegative_int(
        require_key(ahr999_output, "network_request_count", "ahr999.root"),
        "ahr999.network_request_count",
    )

    current_source = require_mapping(
        require_key(ahr999_output, "current", "ahr999.root"), "ahr999.current"
    )
    current = {
        "date": require_date(
            require_key(current_source, "date", "ahr999.current"),
            "ahr999.current.date",
        ),
        "close": require_number(
            require_key(current_source, "close", "ahr999.current"),
            "ahr999.current.close",
            positive=True,
        ),
        "ma200": require_number(
            require_key(current_source, "ma200", "ahr999.current"),
            "ahr999.current.ma200",
            positive=True,
        ),
        "ahr999": require_number(
            require_key(current_source, "ahr999", "ahr999.current"),
            "ahr999.current.ahr999",
            positive=True,
        ),
        "quantile5y": normalize_optional_number(
            require_key(current_source, "quantile5y", "ahr999.current"),
            "ahr999.current.quantile5y",
        ),
        "windowKind": current_source.get("windowKind"),
        "regime_code": require_text(
            require_key(current_source, "regime_code", "ahr999.current"),
            "ahr999.current.regime_code",
        ),
        "regime_zh": require_text(
            require_key(current_source, "regime_zh", "ahr999.current"),
            "ahr999.current.regime_zh",
        ),
    }
    if current["date"] != actual_latest_date:
        raise PreparationError("AHR999 current.date与actual_latest_date不一致")
    if current["quantile5y"] is not None and not 0 <= current["quantile5y"] <= 1:
        raise PreparationError("AHR999 current.quantile5y超出0～1")
    regime_pairs = {
        "bargain": "历史低估区",
        "dca": "定投区",
        "caution": "谨慎区",
        "bubble": "泡沫区",
    }
    if regime_pairs.get(current["regime_code"]) != current["regime_zh"]:
        raise PreparationError("AHR999估值区间代码与中文名称不一致")
    if current["windowKind"] is not None and not isinstance(current["windowKind"], str):
        raise PreparationError("AHR999 current.windowKind必须是字符串或null")

    history_source = require_mapping(
        require_key(ahr999_output, "history", "ahr999.root"), "ahr999.history"
    )
    history = {
        key: normalize_ahr999_history_point(
            require_key(history_source, key, "ahr999.history"),
            f"ahr999.history.{key}",
        )
        for key in ("previous", "7d", "30d")
    }
    for key, point in history.items():
        if point["actual_date"] is not None and fetch_cvdd.parse_date_strict(
            point["actual_date"]
        ) >= actual_date:
            raise PreparationError(f"AHR999历史点 {key} 不早于当前日线")

    changes_source = require_mapping(
        require_key(ahr999_output, "changes", "ahr999.root"), "ahr999.changes"
    )
    changes = {
        key: normalize_change(
            require_key(changes_source, key, "ahr999.changes"),
            f"ahr999.changes.{key}",
            required=False,
        )
        for key in ("ahr999_1d", "ahr999_7d", "ahr999_30d")
    }
    changes["quantile5y_7d"] = normalize_quantile_change(
        require_key(changes_source, "quantile5y_7d", "ahr999.changes"),
        "ahr999.changes.quantile5y_7d",
    )
    quality_checks = require_mapping(
        require_key(ahr999_output, "quality_checks", "ahr999.root"),
        "ahr999.quality_checks",
    )
    raw_warnings = require_string_list(
        require_key(ahr999_output, "warnings", "ahr999.root"),
        "ahr999.warnings",
    )
    section = {
        "source": copy.deepcopy(source),
        "source_url": source_url,
        "source_generated_at_utc": ahr999_output.get("generated_at_utc"),
        "fetched_at_utc": fetched_at_utc,
        "actual_latest_date": actual_latest_date,
        "data_delay_days": data_delay_days,
        "cache_status": copy.deepcopy(cache_status),
        "network_request_count": network_request_count,
        "current": current,
        "history": history,
        "changes": changes,
        "warnings": list(raw_warnings),
    }
    return requested_as_of, section, raw_warnings, copy.deepcopy(quality_checks)


def _funding_optional_number(value: Any, path: str) -> float | None:
    return None if value is None else require_number(value, path)


def _funding_optional_timestamp(value: Any, path: str) -> str | None:
    if value is None:
        return None
    timestamp = require_text(value, path)
    try:
        fetch_funding.parse_utc_timestamp(timestamp)
    except (TypeError, ValueError) as exc:
        raise PreparationError(f"Funding输出字段 {path} 不是有效UTC时间") from exc
    return timestamp


def normalize_funding_provider(source: Any, expected_key: str) -> dict[str, Any]:
    path = f"funding.providers.{expected_key}"
    provider = require_mapping(source, path)
    provider_key = require_text(
        require_key(provider, "provider", path), f"{path}.provider"
    )
    if provider_key != expected_key:
        raise PreparationError(f"Funding provider键 {expected_key} 与内容不一致")
    status = require_key(provider, "status", path)
    if status not in {"available", "unavailable"}:
        raise PreparationError(f"Funding输出字段 {path}.status 无效")
    provider_name = require_text(
        require_key(provider, "provider_name", path), f"{path}.provider_name"
    )
    symbol = require_text(require_key(provider, "symbol", path), f"{path}.symbol")
    fetched_at_utc = _funding_optional_timestamp(
        require_key(provider, "fetched_at_utc", path), f"{path}.fetched_at_utc"
    )
    if fetched_at_utc is None:
        raise PreparationError(f"Funding输出字段 {path}.fetched_at_utc不能为空")
    urls_source = require_mapping(require_key(provider, "urls", path), f"{path}.urls")
    urls = {
        key: require_text(value, f"{path}.urls.{key}")
        for key, value in urls_source.items()
    }
    if "current" not in urls:
        raise PreparationError(f"Funding输出字段 {path}.urls 缺少current")
    request_items = require_key(provider, "requests", path)
    if not isinstance(request_items, list):
        raise PreparationError(f"Funding输出字段 {path}.requests 必须是数组")
    requests: list[dict[str, Any]] = []
    for index, raw_request in enumerate(request_items):
        request_path = f"{path}.requests[{index}]"
        item = require_mapping(raw_request, request_path)
        http_status = item.get("http_status")
        if http_status is not None:
            http_status = require_nonnegative_int(http_status, f"{request_path}.http_status")
        requests.append(
            {
                "endpoint": require_text(
                    require_key(item, "endpoint", request_path),
                    f"{request_path}.endpoint",
                ),
                "url": require_text(
                    require_key(item, "url", request_path), f"{request_path}.url"
                ),
                "http_status": http_status,
                "fetched_at_utc": _funding_optional_timestamp(
                    require_key(item, "fetched_at_utc", request_path),
                    f"{request_path}.fetched_at_utc",
                ),
                "cache_status": require_text(
                    require_key(item, "cache_status", request_path),
                    f"{request_path}.cache_status",
                ),
                "error": item.get("error"),
            }
        )
    cache_status = copy.deepcopy(
        require_mapping(require_key(provider, "cache_status", path), f"{path}.cache_status")
    )
    used_cache = require_key(cache_status, "used_cache", f"{path}.cache_status")
    if not isinstance(used_cache, bool):
        raise PreparationError(f"Funding输出字段 {path}.cache_status.used_cache无效")
    network_request_count = require_nonnegative_int(
        require_key(provider, "network_request_count", path),
        f"{path}.network_request_count",
    )

    current_source = require_mapping(
        require_key(provider, "current", path), f"{path}.current"
    )
    current = {
        "rate": _funding_optional_number(current_source.get("rate"), f"{path}.current.rate"),
        "type": current_source.get("type"),
        "funding_time": _funding_optional_timestamp(
            current_source.get("funding_time"), f"{path}.current.funding_time"
        ),
        "next_funding_time": _funding_optional_timestamp(
            current_source.get("next_funding_time"),
            f"{path}.current.next_funding_time",
        ),
        "interval_hours": _funding_optional_number(
            current_source.get("interval_hours"), f"{path}.current.interval_hours"
        ),
        "interval_source": current_source.get("interval_source"),
    }
    if current["interval_hours"] is not None and current["interval_hours"] <= 0:
        raise PreparationError(f"Funding输出字段 {path}.current.interval_hours必须大于0")

    previous_source = require_mapping(
        require_key(provider, "previous_settled", path),
        f"{path}.previous_settled",
    )
    previous = {
        "rate": _funding_optional_number(
            previous_source.get("rate"), f"{path}.previous_settled.rate"
        ),
        "funding_time": _funding_optional_timestamp(
            previous_source.get("funding_time"),
            f"{path}.previous_settled.funding_time",
        ),
        "interval_hours": _funding_optional_number(
            previous_source.get("interval_hours"),
            f"{path}.previous_settled.interval_hours",
        ),
        "interval_source": previous_source.get("interval_source"),
    }
    if previous["interval_hours"] is not None and previous["interval_hours"] <= 0:
        raise PreparationError(
            f"Funding输出字段 {path}.previous_settled.interval_hours必须大于0"
        )

    normalized_source = require_mapping(
        require_key(provider, "normalized_8h", path), f"{path}.normalized_8h"
    )
    normalized = {
        "current_rate": _funding_optional_number(
            normalized_source.get("current_rate"),
            f"{path}.normalized_8h.current_rate",
        ),
        "previous_settled_rate": _funding_optional_number(
            normalized_source.get("previous_settled_rate"),
            f"{path}.normalized_8h.previous_settled_rate",
        ),
    }
    classification = require_text(
        require_key(provider, "classification", path), f"{path}.classification"
    )
    if classification not in {
        "negative",
        "neutral_or_healthy",
        "caution",
        "overheated",
        "unavailable",
    }:
        raise PreparationError(f"Funding输出字段 {path}.classification无效")
    reason = provider.get("reason")
    if status == "available":
        if current["rate"] is None or current["type"] not in {"estimated", "current"}:
            raise PreparationError(f"Funding可用provider {path} 缺少当前rate或type")
    else:
        reason = require_text(reason, f"{path}.reason")
        if current["rate"] is not None or normalized["current_rate"] is not None:
            raise PreparationError(f"Funding不可用provider {path} 不能包含当前数值")
    warnings = require_string_list(
        require_key(provider, "warnings", path), f"{path}.warnings"
    )
    return {
        "provider": provider_key,
        "provider_name": provider_name,
        "status": status,
        "reason": reason,
        "symbol": symbol,
        "fetched_at_utc": fetched_at_utc,
        "urls": urls,
        "requests": requests,
        "cache_status": cache_status,
        "network_request_count": network_request_count,
        "current": current,
        "previous_settled": previous,
        "normalized_8h": normalized,
        "classification": classification,
        "warnings": warnings,
    }


def build_funding_section(
    funding_output: dict[str, Any], funding_exit_code: int
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    if funding_exit_code not in (0, 2):
        raise PreparationError(f"不支持的Funding退出码：{funding_exit_code}")
    source_mode = require_text(
        require_key(funding_output, "source_mode", "funding.root"),
        "funding.source_mode",
    )
    if source_mode != fetch_funding.SOURCE_MODE:
        raise PreparationError("Funding输出source_mode无效")
    temporal_scope = require_text(
        require_key(funding_output, "temporal_scope", "funding.root"),
        "funding.temporal_scope",
    )
    if temporal_scope != "current_snapshot":
        raise PreparationError("Funding输出temporal_scope无效")
    providers_source = require_mapping(
        require_key(funding_output, "providers", "funding.root"),
        "funding.providers",
    )
    providers = {
        key: normalize_funding_provider(
            require_key(providers_source, key, "funding.providers"), key
        )
        for key in fetch_funding.PROVIDER_ORDER
    }
    reference_source = require_key(
        funding_output, "reference_funding", "funding.root"
    )
    if reference_source is None:
        raise PreparationError("Funding exit 0/2输出缺少reference_funding")
    reference = copy.deepcopy(
        require_mapping(reference_source, "funding.reference_funding")
    )
    reference_key = require_text(
        require_key(reference, "provider", "funding.reference_funding"),
        "funding.reference_funding.provider",
    )
    if reference_key not in providers or providers[reference_key]["status"] != "available":
        raise PreparationError("Funding reference指向不可用provider")
    selected_provider = providers[reference_key]
    expected_reference = {
        "provider": reference_key,
        "provider_name": selected_provider["provider_name"],
        "symbol": selected_provider["symbol"],
        "current": copy.deepcopy(selected_provider["current"]),
        "previous_settled": copy.deepcopy(selected_provider["previous_settled"]),
        "normalized_8h": copy.deepcopy(selected_provider["normalized_8h"]),
        "classification": selected_provider["classification"],
        "fetched_at_utc": selected_provider["fetched_at_utc"],
    }
    for required_key in expected_reference:
        if required_key not in reference:
            raise PreparationError(
                f"Funding reference缺少必需字段 {required_key}"
            )
    if reference != expected_reference:
        raise PreparationError(
            "Funding reference结构不完整或与对应provider标准输出不一致"
        )
    reference = expected_reference
    reference_normalized = reference["normalized_8h"]
    require_number(
        require_key(reference_normalized, "current_rate", "funding.reference_funding.normalized_8h"),
        "funding.reference_funding.normalized_8h.current_rate",
    )

    summary_source = require_mapping(
        require_key(funding_output, "summary", "funding.root"), "funding.summary"
    )
    summary = copy.deepcopy(summary_source)
    for key in ("available_provider_count", "normalized_provider_count"):
        summary[key] = require_nonnegative_int(
            require_key(summary_source, key, "funding.summary"), f"funding.summary.{key}"
        )
    for key in ("min_8h", "max_8h", "simple_mean_8h", "spread_8h"):
        summary[key] = _funding_optional_number(
            require_key(summary_source, key, "funding.summary"),
            f"funding.summary.{key}",
        )
    if summary_source.get("direction_consensus") not in {
        "all_positive",
        "all_negative",
        "mixed",
        "single_provider",
    }:
        raise PreparationError("Funding summary.direction_consensus无效")
    if summary_source.get("is_oi_weighted") is not False:
        raise PreparationError("Funding简单平均不得标记为OI-weighted")
    quality = copy.deepcopy(
        require_mapping(
            require_key(funding_output, "quality_checks", "funding.root"),
            "funding.quality_checks",
        )
    )
    warnings = require_string_list(
        require_key(funding_output, "warnings", "funding.root"), "funding.warnings"
    )
    network_request_count = require_nonnegative_int(
        require_key(funding_output, "network_request_count", "funding.root"),
        "funding.network_request_count",
    )
    section = {
        "source_mode": source_mode,
        "temporal_scope": temporal_scope,
        "source_generated_at_utc": funding_output.get("generated_at_utc"),
        "providers": providers,
        "reference_funding": reference,
        "summary": summary,
        "cache_status": copy.deepcopy(funding_output.get("cache_status", {})),
        "network_request_count": network_request_count,
        "warnings": warnings,
    }
    return section, warnings, quality


def aggregate_exit_codes(*exit_codes: int) -> int:
    if any(code not in (0, 1, 2) for code in exit_codes):
        return 1
    if 1 in exit_codes:
        return 1
    if 2 in exit_codes:
        return 2
    return 0


def build_daily_brief_context(
    cvdd_output: dict[str, Any],
    etf_output: dict[str, Any],
    ahr999_output: dict[str, Any],
    funding_output: dict[str, Any],
    *,
    cvdd_exit_code: int = 0,
    etf_exit_code: int = 0,
    ahr999_exit_code: int = 0,
    funding_exit_code: int = 0,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    if cvdd_exit_code not in (0, 2):
        raise PreparationError(f"不支持的CVDD退出码：{cvdd_exit_code}")

    requested_as_of = require_date(
        require_key(cvdd_output, "requested_as_of", "root"),
        "requested_as_of",
    )
    actual_latest = require_date(
        require_key(cvdd_output, "actual_latest_common_date", "root"),
        "actual_latest_common_date",
    )
    data_delay_days = require_nonnegative_int(
        require_key(cvdd_output, "data_delay_days", "root"),
        "data_delay_days",
    )
    source = require_mapping(require_key(cvdd_output, "source", "root"), "source")
    cache = require_mapping(require_key(cvdd_output, "cache", "root"), "cache")
    quality_checks = require_mapping(
        require_key(cvdd_output, "quality_checks", "root"), "quality_checks"
    )
    raw_warnings = require_key(cvdd_output, "warnings", "root")
    if not isinstance(raw_warnings, list) or not all(
        isinstance(item, str) for item in raw_warnings
    ):
        raise PreparationError("CVDD输出字段 warnings 必须是字符串数组")

    cvdd_points: dict[str, dict[str, Any]] = {}
    for period in ("current", "30d", "90d", "365d"):
        period_data = require_mapping(
            require_key(cvdd_output, period, "root"), period
        )
        cvdd_points[period] = normalize_cvdd_point(
            require_key(period_data, "cvdd_point", period),
            f"{period}.cvdd_point",
        )

    price_cvdd_points: dict[str, dict[str, Any]] = {}
    for period in ("current", "7d", "30d", "90d", "365d"):
        period_data = require_mapping(
            require_key(cvdd_output, period, "root"), period
        )
        price_cvdd_points[period] = normalize_price_cvdd_point(
            require_key(period_data, "price_cvdd_point", period),
            f"{period}.price_cvdd_point",
        )

    for period in ("current", "30d", "90d"):
        require_available(cvdd_points[period], f"cvdd.{period}")
    for period in ("current", "7d", "30d"):
        require_available(price_cvdd_points[period], f"price_cvdd.{period}")

    cvdd_changes_source = require_mapping(
        require_key(cvdd_output, "cvdd_changes", "root"), "cvdd_changes"
    )
    price_changes_source = require_mapping(
        require_key(cvdd_output, "price_cvdd_changes", "root"),
        "price_cvdd_changes",
    )
    cvdd_changes = {
        period: normalize_change(
            require_key(cvdd_changes_source, period, "cvdd_changes"),
            f"cvdd_changes.{period}",
            required=period in ("30d", "90d"),
        )
        for period in ("30d", "90d", "365d")
    }
    price_changes = {
        period: normalize_change(
            require_key(price_changes_source, period, "price_cvdd_changes"),
            f"price_cvdd_changes.{period}",
            required=period in ("7d", "30d"),
        )
        for period in ("7d", "30d", "90d", "365d")
    }

    network_request_count = require_nonnegative_int(
        require_key(cvdd_output, "network_request_count", "root"),
        "network_request_count",
    )
    (
        etf_requested_as_of,
        etf_section,
        etf_warnings,
        etf_quality_checks,
    ) = build_etf_section(etf_output, etf_exit_code)
    if etf_requested_as_of != requested_as_of:
        raise PreparationError(
            "CVDD与ETF Flow输出的requested_as_of不一致，拒绝合并可能陈旧的数据"
        )
    (
        ahr999_requested_as_of,
        ahr999_section,
        ahr999_warnings,
        ahr999_quality_checks,
    ) = build_ahr999_section(ahr999_output, ahr999_exit_code)
    if ahr999_requested_as_of != requested_as_of:
        raise PreparationError(
            "CVDD、ETF Flow与AHR999输出的requested_as_of不一致，"
            "拒绝合并可能陈旧的数据"
        )
    funding_section, funding_warnings, funding_quality_checks = build_funding_section(
        funding_output, funding_exit_code
    )
    if generated_at_utc is None:
        generated_at_utc = fetch_cvdd.format_utc_timestamp(fetch_cvdd.utc_now())
    else:
        require_text(generated_at_utc, "generated_at_utc")

    section = {
        "source": copy.deepcopy(source),
        "source_generated_at_utc": cvdd_output.get("generated_at_utc"),
        "actual_latest_common_date": actual_latest,
        "data_delay_days": data_delay_days,
        "cache": copy.deepcopy(cache),
        "network_request_count": network_request_count,
        "cvdd": {
            **cvdd_points,
            "change_30d": cvdd_changes["30d"],
            "change_90d": cvdd_changes["90d"],
            "change_365d": cvdd_changes["365d"],
        },
        "price_cvdd": {
            **price_cvdd_points,
            "change_7d": price_changes["7d"],
            "change_30d": price_changes["30d"],
            "change_90d": price_changes["90d"],
            "change_365d": price_changes["365d"],
        },
        "monthly_reference": {
            "period": "365d",
            "note": MONTHLY_REFERENCE_NOTE,
        },
        "warnings": list(raw_warnings),
    }
    return {
        "generated_at_utc": generated_at_utc,
        "requested_as_of": requested_as_of,
        "sections": {
            "cvdd_bgeometrics": section,
            "etf_flow": etf_section,
            "ahr999": ahr999_section,
            "funding": funding_section,
        },
        "warnings": [
            *raw_warnings,
            *etf_warnings,
            *ahr999_warnings,
            *funding_warnings,
        ],
        "data_quality": {
            "cvdd_bgeometrics": copy.deepcopy(quality_checks),
            "etf_flow": etf_quality_checks,
            "ahr999": ahr999_quality_checks,
            "funding": funding_quality_checks,
            "cache": copy.deepcopy(cache),
            "etf_cache": copy.deepcopy(etf_section["cache_status"]),
            "ahr999_cache": copy.deepcopy(ahr999_section["cache_status"]),
            "funding_cache": copy.deepcopy(funding_section["cache_status"]),
            "network_request_count": (
                network_request_count
                + etf_section["network_request_count"]
                + ahr999_section["network_request_count"]
                + funding_section["network_request_count"]
            ),
            "cvdd_network_request_count": network_request_count,
            "etf_network_request_count": etf_section["network_request_count"],
            "ahr999_network_request_count": ahr999_section[
                "network_request_count"
            ],
            "funding_network_request_count": funding_section[
                "network_request_count"
            ],
            "cvdd_exit_code": cvdd_exit_code,
            "etf_exit_code": etf_exit_code,
            "ahr999_exit_code": ahr999_exit_code,
            "funding_exit_code": funding_exit_code,
            "aggregate_exit_code": aggregate_exit_codes(
                cvdd_exit_code,
                etf_exit_code,
                ahr999_exit_code,
                funding_exit_code,
            ),
        },
    }


def format_value(value: float | None, decimal_places: int) -> str:
    return "unavailable" if value is None else f"{value:.{decimal_places}f}"


def format_change(change: dict[str, Any] | None) -> str:
    if not change or change.get("rate") is None:
        return "unavailable"
    return f"{change['percent']:+.2f}%（{change['direction']}）"


def collect_fallback_notes(section: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    groups = (
        ("CVDD", section["cvdd"], ("30d", "90d", "365d")),
        ("Price/CVDD", section["price_cvdd"], ("7d", "30d", "90d", "365d")),
    )
    for group_name, points, periods in groups:
        for period in periods:
            point = points[period]
            label = period.upper()
            if not point["available"]:
                notes.append(f"{group_name} {label} unavailable")
            elif point["fallback_days"] > 0:
                notes.append(
                    f"{group_name} {label}: {point['target_date']} → "
                    f"{point['actual_date']}（回退{point['fallback_days']}日）"
                )
    return notes


def render_context_markdown(context: dict[str, Any]) -> str:
    section = context["sections"]["cvdd_bgeometrics"]
    etf_section = context["sections"]["etf_flow"]
    ahr999_section = context["sections"]["ahr999"]
    funding_section = context["sections"]["funding"]
    cvdd = section["cvdd"]
    price_cvdd = section["price_cvdd"]
    provider = section["source"].get("provider", "BGeometrics / bitcoin-data.com")
    etf_provider = etf_section["source"].get("provider", "Farside Investors")
    ahr999_provider = ahr999_section["source"].get(
        "name", "AIX4U AHR999 Dataset"
    )

    lines = [
        "# Daily Brief Data Context",
        "",
        f"生成时间：{context['generated_at_utc']}",
        f"分析截止日期：{context['requested_as_of']}",
        f"数据源：{provider}；{etf_provider}；{ahr999_provider}；"
        "交易所公开Funding API",
        "",
        "## CVDD｜BGeometrics",
        "",
        "### 底部锚趋势",
        "",
        "| 周期 | 目标日期 | 实际日期 | CVDD | 变化率 |",
        "|---|---|---|---:|---:|",
    ]
    cvdd_rows = (
        ("当前", "current", None),
        ("30D", "30d", cvdd["change_30d"]),
        ("90D", "90d", cvdd["change_90d"]),
        ("365D（月度参考数据）", "365d", cvdd["change_365d"]),
    )
    for label, period, change in cvdd_rows:
        point = cvdd[period]
        lines.append(
            f"| {label} | {point['target_date']} | "
            f"{point['actual_date'] or 'unavailable'} | "
            f"{format_value(point['value'], 2)} | "
            f"{'—' if change is None else format_change(change)} |"
        )

    lines.extend(
        [
            "",
            "### BTC距CVDD",
            "",
            "| 周期 | 实际日期 | BTC价格 | Price/CVDD | 变化率 |",
            "|---|---|---:|---:|---:|",
        ]
    )
    price_rows = (
        ("当前", "current", None),
        ("7D", "7d", price_cvdd["change_7d"]),
        ("30D", "30d", price_cvdd["change_30d"]),
        ("90D", "90d", price_cvdd["change_90d"]),
        ("365D（月度参考数据）", "365d", price_cvdd["change_365d"]),
    )
    for label, period, change in price_rows:
        point = price_cvdd[period]
        lines.append(
            f"| {label} | {point['actual_date'] or 'unavailable'} | "
            f"{format_value(point['btc_price'], 2)} | "
            f"{format_value(point['value'], 4)} | "
            f"{'—' if change is None else format_change(change)} |"
        )

    quality = context["data_quality"]["cvdd_bgeometrics"]
    missing_dates = sorted(
        set(quality.get("cvdd", {}).get("missing_dates", []))
        | set(quality.get("btc_price", {}).get("missing_dates", []))
        | set(quality.get("common", {}).get("missing_dates", []))
    )
    fallback_notes = collect_fallback_notes(section)
    cache = section["cache"]
    warning_text = (
        "无" if not section["warnings"] else "；".join(section["warnings"])
    )
    lines.extend(
        [
            "",
            f"> {MONTHLY_REFERENCE_NOTE}",
            "",
            "### 数据质量",
            "",
            f"- 最新共同日期：{section['actual_latest_common_date']}",
            f"- 数据延迟：{section['data_delay_days']}天（相对分析截止日期）",
            f"- 日期回退：{'无' if not fallback_notes else '；'.join(fallback_notes)}",
            f"- 最近40天缺失：{'无' if not missing_dates else '；'.join(missing_dates)}",
            f"- Warnings：{warning_text}",
            f"- 数据获取：CVDD {cache.get('cvdd', {}).get('status', '未知')}；"
            f"BTC Price {cache.get('btc_price', {}).get('status', '未知')}",
            "",
            "### 口径说明",
            "",
            "本文件中的BGeometrics Price/CVDD仅用于趋势分析。",
            "不得套用Glassnode PiT CVDD的1.30/1.15/1.05/1.00阈值。",
            "Glassnode PiT CVDD属于独立指标体系。",
        ]
    )

    latest_etf = etf_section["latest_complete_row"]
    previous_etf = etf_section["previous_complete_row"]
    flows = etf_section["flows"]
    continuity = etf_section["continuity"]
    lines.extend(
        [
            "",
            "## 美国现货 BTC ETF Flow",
            "",
            f"- 最近完整交易日：{latest_etf['date']}",
            f"- 净流量：{fetch_etf_flow.format_flow(flows['latest'])}",
            f"- 前一完整交易日："
            f"{previous_etf['date'] if previous_etf is not None else 'unavailable'}；"
            f"{fetch_etf_flow.format_flow(flows['previous'])}",
            f"- 最近3个完整交易日累计："
            f"{fetch_etf_flow.format_flow(flows['cumulative_3d'])}",
            f"- 最近5个完整交易日累计："
            f"{fetch_etf_flow.format_flow(flows['cumulative_5d'])}",
            f"- 连续性："
            f"{'正负方向发生反转' if continuity['sign_flip'] else '未发生反转'}",
            f"- 3D正/负流量日：{continuity['positive_days_3d']}/"
            f"{continuity['negative_days_3d']}",
            f"- 5D正/负流量日：{continuity['positive_days_5d']}/"
            f"{continuity['negative_days_5d']}",
            "",
            "### 主要基金",
            "",
            "| ETF | Flow |",
            "|---|---:|",
        ]
    )
    for fund in fetch_etf_flow.CORE_FUNDS:
        lines.append(
            f"| {fund} | {fetch_etf_flow.format_flow(latest_etf['funds'].get(fund))} |"
        )

    provisional = etf_section["provisional_latest"]
    if provisional is not None:
        lines.extend(
            [
                "",
                "### ⚠️ 最新交易日仍为暂报",
                "",
                f"- 暂报日期：{provisional['date']}",
                f"- 当前暂报总额："
                f"{fetch_etf_flow.format_flow(provisional['partial_total'])}",
                "- 缺失核心基金："
                + ("、".join(provisional["missing_core_funds"]) or "无"),
                "- 全部缺失基金："
                + ("、".join(provisional["missing_funds"]) or "无"),
                "",
                "该暂报数据没有作为正式日流量使用。",
            ]
        )

    etf_warning_text = (
        "无"
        if not etf_section["warnings"]
        else "；".join(etf_section["warnings"])
    )
    lines.extend(
        [
            "",
            "### ETF Flow数据质量",
            "",
            f"- 页面最新交易日：{etf_section['latest_page_date']}"
            f"（{etf_section['latest_row_status']}）",
            f"- 正式采用交易日：{etf_section['latest_complete_date']}",
            f"- 最近完整日缺失基金："
            f"{'、'.join(latest_etf['missing_funds']) if latest_etf['missing_funds'] else '无'}",
            f"- Warnings：{etf_warning_text}",
            f"- 数据获取：{etf_section['cache_status'].get('status', '未知')}",
            f"- 抓取时间：{etf_section['fetched_at_utc']}",
            f"- 数据源：{etf_section['source_url']}",
        ]
    )

    current_ahr999 = ahr999_section["current"]
    history_ahr999 = ahr999_section["history"]
    ahr999_warning_text = (
        "无"
        if not ahr999_section["warnings"]
        else "；".join(ahr999_section["warnings"])
    )
    quantile_text = (
        "unavailable"
        if current_ahr999["quantile5y"] is None
        else f"{current_ahr999['quantile5y'] * 100:.2f}%"
    )
    quantile_change = ahr999_section["changes"]["quantile5y_7d"]
    quantile_change_text = (
        "unavailable"
        if quantile_change["percentage_points"] is None
        else f"{quantile_change['percentage_points']:+.2f}个百分点"
        f"（{quantile_change['direction']}）"
    )
    fallback_notes = []
    for label, key in (("前1日", "previous"), ("7D", "7d"), ("30D", "30d")):
        point = history_ahr999[key]
        if not point["available"]:
            fallback_notes.append(f"{label} unavailable")
        elif point["fallback_days"]:
            fallback_notes.append(
                f"{label}: {point['target_date']} → {point['actual_date']}"
                f"（回退{point['fallback_days']}日）"
            )
    lines.extend(
        [
            "",
            "## AHR999",
            "",
            f"- 最新完整UTC日线：{ahr999_section['actual_latest_date']}",
            f"- AHR999：{current_ahr999['ahr999']:.6f}",
            f"- 5年历史分位：{quantile_text}",
            f"- BTC收盘：${current_ahr999['close']:,.2f}",
            f"- MA200：${current_ahr999['ma200']:,.2f}",
            f"- 估值区间：{current_ahr999['regime_zh']}"
            f"（{current_ahr999['regime_code']}）",
            f"- 7D变化：{format_change(ahr999_section['changes']['ahr999_7d'])}",
            f"- 5年历史分位7D变化：{quantile_change_text}",
            "",
            "### AHR999数据质量",
            "",
            f"- 数据延迟：{ahr999_section['data_delay_days']}天"
            "（相对分析截止日期）",
            f"- 日期回退：{'无' if not fallback_notes else '；'.join(fallback_notes)}",
            f"- 最近40天是否完整："
            f"{'是' if context['data_quality']['ahr999'].get('is_complete') else '否'}",
            f"- Warnings：{ahr999_warning_text}",
            f"- 数据获取：{ahr999_section['cache_status'].get('status', '未知')}",
            f"- 抓取时间：{ahr999_section['fetched_at_utc']}",
            f"- 数据源：{ahr999_section['source_url']}",
            "",
            "AHR999是长期估值辅助指标，不单独作为买卖信号。",
        ]
    )

    funding_reference = funding_section["reference_funding"]
    funding_summary = funding_section["summary"]
    funding_warnings = (
        "无"
        if not funding_section["warnings"]
        else "；".join(funding_section["warnings"])
    )
    funding_current = funding_reference["current"]
    funding_previous = funding_reference["previous_settled"]
    lines.extend(
        [
            "",
            "## BTC Funding",
            "",
            "> 本节是抓取时点的current_snapshot，不是历史 --as-of 回放。",
            "",
            f"- Reference平台：{funding_reference['provider_name']}",
            f"- 原始Funding：{fetch_funding.format_rate(funding_current['rate'])}",
            f"- 原始周期："
            f"{fetch_funding.format_interval(funding_current['interval_hours'])}",
            f"- 8h等效："
            f"{fetch_funding.format_rate(funding_reference['normalized_8h']['current_rate'])}",
            f"- 上一期实际结算：{fetch_funding.format_rate(funding_previous['rate'])}"
            f"（{funding_previous['funding_time'] or 'unavailable'}）",
            f"- 状态：{funding_reference['classification']}",
            "",
            "### Funding各平台",
            "",
            "| 平台 | 当前/预计 | 周期 | 8h等效 | 上一期实际结算 | 状态 |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for key in fetch_funding.PROVIDER_ORDER:
        provider_data = funding_section["providers"][key]
        if provider_data["status"] == "available":
            current = provider_data["current"]
            previous = provider_data["previous_settled"]
            lines.append(
                f"| {provider_data['provider_name']} | "
                f"{fetch_funding.format_rate(current['rate'])}（{current['type']}） | "
                f"{fetch_funding.format_interval(current['interval_hours'])} | "
                f"{fetch_funding.format_rate(provider_data['normalized_8h']['current_rate'])} | "
                f"{fetch_funding.format_rate(previous['rate'])} | "
                f"{provider_data['classification']} |"
            )
        else:
            reason = str(provider_data.get("reason") or "未知原因").replace("|", "/")
            lines.append(
                f"| {provider_data['provider_name']} | unavailable | unavailable | "
                f"unavailable | unavailable | unavailable：{reason} |"
            )
    funding_range = (
        "unavailable"
        if funding_summary["min_8h"] is None or funding_summary["max_8h"] is None
        else f"{fetch_funding.format_rate(funding_summary['min_8h'])} ～ "
        f"{fetch_funding.format_rate(funding_summary['max_8h'])}"
    )
    lines.extend(
        [
            "",
            "### Funding数据质量",
            "",
            f"- 可用平台数：{funding_summary['available_provider_count']}",
            f"- 简单平均8h Funding："
            f"{fetch_funding.format_rate(funding_summary['simple_mean_8h'])}",
            f"- 区间：{funding_range}",
            f"- 方向一致性："
            f"{fetch_funding.format_consensus(funding_summary['direction_consensus'])}",
            f"- Warnings：{funding_warnings}",
            "",
            fetch_funding.COINGLASS_UPGRADE_NOTE,
        ]
    )
    return "\n".join(lines) + "\n"


def write_context_outputs(
    context: dict[str, Any], output_dir: Path
) -> tuple[Path, Path, str]:
    markdown = render_context_markdown(context)
    json_path = output_dir / "latest.json"
    markdown_path = output_dir / "latest.md"
    fetch_cvdd.atomic_write_json(json_path, context)
    fetch_cvdd.atomic_write_text(markdown_path, markdown)
    return json_path, markdown_path, markdown


def copy_markdown_to_clipboard(
    markdown: str,
    *,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., Any] | None = None,
) -> tuple[bool, str]:
    which = which or shutil.which
    runner = runner or subprocess.run
    pbcopy_path = which("pbcopy")
    if pbcopy_path is None:
        return False, "未复制到剪贴板：未找到 pbcopy。"
    try:
        runner(
            [pbcopy_path],
            input=markdown,
            text=True,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return False, f"未复制到剪贴板：{exc}"
    return True, "Markdown context copied to clipboard."


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def print_terminal_summary(context: dict[str, Any], markdown_path: Path) -> None:
    section = context["sections"]["cvdd_bgeometrics"]
    etf_section = context["sections"]["etf_flow"]
    ahr999_section = context["sections"]["ahr999"]
    funding_section = context["sections"]["funding"]
    cvdd = section["cvdd"]
    price_cvdd = section["price_cvdd"]
    warnings = context["warnings"]
    warning_summary = "None" if not warnings else f"{len(warnings)} — {'；'.join(warnings)}"
    context_path = display_path(markdown_path)
    print("Daily Brief Context ready")
    print()
    print(f"As-of: {context['requested_as_of']}")
    print(f"Latest common date: {section['actual_latest_common_date']}")
    print(f"CVDD: {format_value(cvdd['current']['value'], 2)}")
    print(f"Price/CVDD: {format_value(price_cvdd['current']['value'], 4)}")
    print(f"30D CVDD change: {format_change(cvdd['change_30d'])}")
    print(f"90D CVDD change: {format_change(cvdd['change_90d'])}")
    print(f"7D Price/CVDD change: {format_change(price_cvdd['change_7d'])}")
    print(f"30D Price/CVDD change: {format_change(price_cvdd['change_30d'])}")
    print(f"ETF latest complete date: {etf_section['latest_complete_date']}")
    print(f"ETF Flow: {fetch_etf_flow.format_flow(etf_section['flows']['latest'])}")
    print(
        "ETF Flow 3D/5D: "
        f"{fetch_etf_flow.format_flow(etf_section['flows']['cumulative_3d'])} / "
        f"{fetch_etf_flow.format_flow(etf_section['flows']['cumulative_5d'])}"
    )
    print(f"AHR999 latest date: {ahr999_section['actual_latest_date']}")
    print(f"AHR999: {ahr999_section['current']['ahr999']:.6f}")
    print(
        "AHR999 7D change: "
        f"{format_change(ahr999_section['changes']['ahr999_7d'])}"
    )
    funding_reference = funding_section["reference_funding"]
    print(f"Funding reference: {funding_reference['provider_name']}")
    print(
        "Funding 8h equivalent: "
        f"{fetch_funding.format_rate(funding_reference['normalized_8h']['current_rate'])}"
    )
    print(f"Warnings: {warning_summary}")
    print(f"Context file: {context_path}")
    print()
    print("可将以下文件提供给 ChatGPT：")
    print(context_path)


def execute_preparation(
    *,
    as_of: str | None,
    refresh: bool,
    copy_requested: bool,
    cvdd_invoker: Callable[[str | None, bool], CvddInvocation] | None = None,
    etf_invoker: Callable[[str | None, bool], CvddInvocation] | None = None,
    ahr999_invoker: Callable[[str | None, bool], CvddInvocation] | None = None,
    funding_invoker: Callable[[str | None, bool], CvddInvocation] | None = None,
    cvdd_json_path: Path | None = None,
    etf_json_path: Path | None = None,
    ahr999_json_path: Path | None = None,
    funding_json_path: Path | None = None,
    output_dir: Path | None = None,
    clipboard_copier: Callable[[str], tuple[bool, str]] | None = None,
    generated_at_utc: str | None = None,
) -> int:
    effective_as_of = (
        as_of if as_of is not None else fetch_cvdd.utc_now().date().isoformat()
    )
    if effective_as_of is not None:
        try:
            fetch_cvdd.parse_date_strict(effective_as_of)
        except ValueError:
            print(
                f"Daily Brief Context failed: 无效的 --as-of 日期 {effective_as_of}",
                file=sys.stderr,
            )
            return 1

    cvdd_invoker = cvdd_invoker or invoke_cvdd
    etf_invoker = etf_invoker or invoke_etf_flow
    ahr999_invoker = ahr999_invoker or invoke_ahr999
    funding_invoker = funding_invoker or invoke_funding
    cvdd_json_path = cvdd_json_path or CVDD_LATEST_JSON
    etf_json_path = etf_json_path or ETF_LATEST_JSON
    ahr999_json_path = ahr999_json_path or AHR999_LATEST_JSON
    funding_json_path = funding_json_path or FUNDING_LATEST_JSON
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    clipboard_copier = clipboard_copier or copy_markdown_to_clipboard

    try:
        cvdd_invocation = cvdd_invoker(effective_as_of, refresh)
    except (OSError, fetch_cvdd.CvddError, ValueError) as exc:
        print(f"Daily Brief Context failed: CVDD调用失败：{exc}", file=sys.stderr)
        return 1

    if cvdd_invocation.exit_code == 1:
        diagnostic = cvdd_invocation.stderr.strip() or cvdd_invocation.stdout.strip()
        if diagnostic:
            print(diagnostic, file=sys.stderr)
        else:
            print("Daily Brief Context failed: CVDD取数返回exit 1。", file=sys.stderr)
        return 1
    if cvdd_invocation.exit_code not in (0, 2):
        print(
            f"Daily Brief Context failed: CVDD返回未知退出码 {cvdd_invocation.exit_code}。",
            file=sys.stderr,
        )
        return 1

    try:
        etf_invocation = etf_invoker(effective_as_of, refresh)
    except (OSError, fetch_etf_flow.EtfFlowError, ValueError) as exc:
        print(f"Daily Brief Context failed: ETF Flow调用失败：{exc}", file=sys.stderr)
        return 1

    if etf_invocation.exit_code == 1:
        diagnostic = etf_invocation.stderr.strip() or etf_invocation.stdout.strip()
        if diagnostic:
            print(diagnostic, file=sys.stderr)
        else:
            print(
                "Daily Brief Context failed: ETF Flow取数返回exit 1。",
                file=sys.stderr,
            )
        return 1
    if etf_invocation.exit_code not in (0, 2):
        print(
            "Daily Brief Context failed: "
            f"ETF Flow返回未知退出码 {etf_invocation.exit_code}。",
            file=sys.stderr,
        )
        return 1

    try:
        ahr999_invocation = ahr999_invoker(effective_as_of, refresh)
    except (OSError, fetch_ahr999.Ahr999Error, ValueError) as exc:
        print(f"Daily Brief Context failed: AHR999调用失败：{exc}", file=sys.stderr)
        return 1

    if ahr999_invocation.exit_code == 1:
        diagnostic = (
            ahr999_invocation.stderr.strip() or ahr999_invocation.stdout.strip()
        )
        if diagnostic:
            print(diagnostic, file=sys.stderr)
        else:
            print(
                "Daily Brief Context failed: AHR999取数返回exit 1。",
                file=sys.stderr,
            )
        return 1
    if ahr999_invocation.exit_code not in (0, 2):
        print(
            "Daily Brief Context failed: "
            f"AHR999返回未知退出码 {ahr999_invocation.exit_code}。",
            file=sys.stderr,
        )
        return 1

    try:
        funding_invocation = funding_invoker(effective_as_of, refresh)
    except (OSError, fetch_funding.FundingError, ValueError) as exc:
        print(f"Daily Brief Context failed: Funding调用失败：{exc}", file=sys.stderr)
        return 1

    if funding_invocation.exit_code == 1:
        diagnostic = (
            funding_invocation.stderr.strip() or funding_invocation.stdout.strip()
        )
        if diagnostic:
            print(diagnostic, file=sys.stderr)
        else:
            print(
                "Daily Brief Context failed: Funding取数返回exit 1。",
                file=sys.stderr,
            )
        return 1
    if funding_invocation.exit_code not in (0, 2):
        print(
            "Daily Brief Context failed: "
            f"Funding返回未知退出码 {funding_invocation.exit_code}。",
            file=sys.stderr,
        )
        return 1

    try:
        cvdd_output = load_cvdd_output(cvdd_json_path)
        etf_output = load_etf_output(etf_json_path)
        ahr999_output = load_ahr999_output(ahr999_json_path)
        funding_output = load_funding_output(funding_json_path)
        context = build_daily_brief_context(
            cvdd_output,
            etf_output,
            ahr999_output,
            funding_output,
            cvdd_exit_code=cvdd_invocation.exit_code,
            etf_exit_code=etf_invocation.exit_code,
            ahr999_exit_code=ahr999_invocation.exit_code,
            funding_exit_code=funding_invocation.exit_code,
            generated_at_utc=generated_at_utc,
        )
        if context["requested_as_of"] != effective_as_of:
            raise PreparationError(
                "数据输出的requested_as_of与本次请求不一致，拒绝使用可能陈旧的数据"
            )
        _json_path, markdown_path, markdown = write_context_outputs(
            context, output_dir
        )
    except (
        PreparationError,
        fetch_ahr999.Ahr999Error,
        fetch_cvdd.CvddError,
        fetch_etf_flow.EtfFlowError,
        fetch_funding.FundingError,
    ) as exc:
        print(f"Daily Brief Context failed: {exc}", file=sys.stderr)
        return 1

    print_terminal_summary(context, markdown_path)
    if copy_requested:
        try:
            _copied, copy_message = clipboard_copier(markdown)
        except (OSError, subprocess.CalledProcessError) as exc:
            copy_message = f"未复制到剪贴板：{exc}"
        print(copy_message)
    return aggregate_exit_codes(
        cvdd_invocation.exit_code,
        etf_invocation.exit_code,
        ahr999_invocation.exit_code,
        funding_invocation.exit_code,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="准备每日简报生成模型所需的可靠数据上下文。"
    )
    parser.add_argument("--as-of", help="分析截止日期（YYYY-MM-DD）")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="强制刷新已接入的数据源",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="在macOS上将Markdown上下文复制到剪贴板",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    return execute_preparation(
        as_of=args.as_of,
        refresh=args.refresh,
        copy_requested=args.copy,
    )


if __name__ == "__main__":
    raise SystemExit(main())
