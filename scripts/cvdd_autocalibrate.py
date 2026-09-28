#!/usr/bin/env python3
"""CVDD 换算因子自动校准（标准口径 LookIntoBitcoin ÷ axeladler 原始值）。

背景：本机口径是 `标准 CVDD = axeladler chart-data.json 原始值 × 因子`。
以前因子靠人工：用户每周从 LookIntoBitcoin 截一个锚点值 → `cvdd_calibrate.py`。
2026-09-20 起 `litb_chart.py` 能用 Windows Edge + CDP 自动读到标准值，
于是这一步也自动化（本脚本）。

策略（防抖 + 可追溯）：
- 每次运行都往 `~/.hermes/data/cvdd_calibration_log.jsonl` 记一条互校记录
- **只有 |漂移| > 0.5% 才真正改因子**（避免日噪声把口径带跑），改动前备份 .bak
- 因子未变时 **stdout 为空**（cron no_agent 模式下静默，不打扰）；变了才输出一行
- 时区/日期：取 LITB 序列最后一个数据点（UTC 日）

用法:
    python3 cvdd_autocalibrate.py             # 正常：变了才说话
    python3 cvdd_autocalibrate.py --verbose   # 无论变没变都打印互校结果
    python3 cvdd_autocalibrate.py --force     # 忽略阈值强制更新（人工排查用）
    python3 cvdd_autocalibrate.py --dry-run   # 只算不写
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timezone

DATA_DIR = os.path.expanduser("~/.hermes/data")
CALIB_FILE = os.path.join(DATA_DIR, "cvdd_calibration.json")
LOG_FILE = os.path.join(DATA_DIR, "cvdd_calibration_log.jsonl")
LITB_SCRIPT = os.path.expanduser("~/.hermes/scripts/litb_chart.py")
AXELADLER_URL = "https://axeladlerjr.com/charts/bitcoin-cvdd/"
THRESHOLD_PCT = 0.5


def fetch_litb_cvdd() -> tuple[float, str]:
    """LookIntoBitcoin 标准 CVDD（浏览器 CDP 自动抓取）→ (值, 日期)"""
    spec = importlib.util.spec_from_file_location("litb_chart_local", LITB_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = mod.fetch_chart("cvdd", days=8)
    traces = {t.get("name"): t for t in data.get("traces", [])}
    cv = traces.get("CVDD") or {}
    last = cv.get("last") or [None, None]
    if not isinstance(last[1], (int, float)):
        raise RuntimeError("LITB 图表没解析出 CVDD 值")
    return float(last[1]), str(last[0] or "")[:10]


def fetch_axeladler_raw() -> tuple[float, str]:
    """axeladler meta 原始值（不乘因子）→ (值, 日期)"""
    req = urllib.request.Request(AXELADLER_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", "ignore")
    m = re.search(r"base line now at \$([\d,]+) as of (\d{4}-\d{2}-\d{2})", html)
    if not m:
        raise RuntimeError("axeladler 页面里没找到 base line 文本")
    return float(m.group(1).replace(",", "")), m.group(2)


def load_calib() -> dict:
    if os.path.exists(CALIB_FILE):
        try:
            with open(CALIB_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def append_log(entry: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    calib = load_calib()
    old_factor = float(calib.get("factor", 1.08))

    litb, litb_date = fetch_litb_cvdd()
    ax_value, ax_date = fetch_axeladler_raw()
    implied = litb / ax_value
    drift_pct = (implied / old_factor - 1) * 100
    changed = args.force or abs(drift_pct) > THRESHOLD_PCT

    entry = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "litb_cvdd": litb,
        "litb_date": litb_date,
        "axeladler_raw": ax_value,
        "axeladler_date": ax_date,
        "old_factor": old_factor,
        "implied_factor": round(implied, 6),
        "drift_pct": round(drift_pct, 4),
        "action": ("dry-run" if args.dry_run else ("update" if changed else "keep")),
        "threshold_pct": THRESHOLD_PCT,
    }

    if changed and not args.dry_run:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(CALIB_FILE):
            shutil.copy2(CALIB_FILE, CALIB_FILE + ".bak")
        calib.update({
            "factor": round(implied, 6),
            "last_calibrated": litb_date,
            "litb_cvdd": litb,
            "axeladler_cvdd": ax_value,
            "note": ("标准 CVDD (LookIntoBitcoin 口径) = axeladler meta 值 × factor。"
                     "由 cvdd_autocalibrate.py 自动维护（阈值 ±0.5%，日志 "
                     "cvdd_calibration_log.jsonl）。"),
        })
        with open(CALIB_FILE, "w", encoding="utf-8") as f:
            json.dump(calib, f, ensure_ascii=False, indent=2)

    append_log(entry)

    if changed and not args.dry_run:
        print(f"⚠️ CVDD 换算因子已自动更新：{old_factor:.6f} → {implied:.6f}"
              f"（漂移 {drift_pct:+.3f}%，超阈值 ±{THRESHOLD_PCT}%）")
        print(f"   标准值 {litb:,.2f}（{litb_date}）÷ axeladler {ax_value:,.2f}（{ax_date}）")
        print("   旧因子已备份到 cvdd_calibration.json.bak")
    elif args.verbose or args.dry_run:
        print(f"✅ CVDD 因子无需调整：现行 {old_factor:.6f}，推算 {implied:.6f}"
              f"（漂移 {drift_pct:+.3f}%，阈值 ±{THRESHOLD_PCT}%）")
        print(f"   标准值 {litb:,.2f}（{litb_date}）| axeladler {ax_value:,.2f}（{ax_date}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
