#!/usr/bin/env python3
"""Estimate CVDD (标准口径) — 主源 axeladlerjr.com × 1.08 校准。

CVDD = Cumulative (Value of CDD) / Market Age in Days

主数据源（权威，2026-08-27 起）：
- axeladlerjr.com chart-data.json（完整历史，每日更新）× 1.08 校准因子
  = LookIntoBitcoin.com 标准口径（Philip Swift / Willy Woo 生态）。
  ⚠️ axeladler 本身系统性偏低 ~7.4%，不 ×1.08 会方向性错误。
- Blockchair API 仅作 fallback（cdd_24h 口径偏高 ~60%，增量累加会漂移）。

⚠️ 来源标注必须写「LookIntoBitcoin 校准（axeladler × 1.08）」，严禁标「Blockchair stats」——
Blockchair 只是 fallback 且数值偏高，标它属于张冠李戴（2026-08-30 已踩坑）。

Usage: python3 cvdd_estimator.py
Output: JSON with current CVDD, Price/CVDD, 30d/90d/180d/360d tracking, source
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

SEED_FILE = os.path.expanduser("~/.hermes/data/cvdd_seed.json")
HISTORY_FILE = os.path.expanduser("~/.hermes/data/cvdd_history.json")

# Bitcoin genesis date
BTC_GENESIS = datetime(2009, 1, 3, tzinfo=timezone.utc)

# CDD scaling factor (Blockchair CDD in raw units, divide by 1e6 for CVDD)
CDD_SCALE = 1e6

# 权威 CVDD 源：axeladlerjr.com（标准 Willy Woo 定义，完整链上数据）。
# 页面 <meta name="description"> 直接给出「base line now at $XX,XXX as of YYYY-MM-DD」。
# ⚠️ 2026-08-27 实测：axeladlerjr 的 CVDD 系统性偏低 ~7.4%（其 CDD 累计口径偏小），
#    vs LookIntoBitcoin.com（Philip Swift / Willy Woo 生态，标准口径）8-24: $49,063。
#    换算因子 = 49063 / 45416 = 1.0803。因此标准 CVDD = axeladler 值 × 1.0803。
#    LookIntoBitcoin 的 Dash API（_dash-update-component）返回 500 无法自动化，
#    故用 axeladler meta（抓取稳定）× 校准因子近似标准值。因子需定期用 LookIntoBitcoin 复核。
AXELADLER_URL = "https://axeladlerjr.com/charts/bitcoin-cvdd/"
AXELADLER_DATA_URL = "https://axeladlerjr.com/charts/bitcoin-cvdd/chart-data.json"
CALIBRATION_FILE = os.path.expanduser("~/.hermes/data/cvdd_calibration.json")
DEFAULT_CALIBRATION_FACTOR = 1.08  # fallback（LookIntoBitcoin 标准口径）

def load_calibration_factor():
    """从配置文件读校准因子（axeladler → LookIntoBitcoin 标准口径）。失败返回默认值。"""
    try:
        with open(CALIBRATION_FILE) as f:
            cfg = json.load(f)
        return float(cfg.get("factor", DEFAULT_CALIBRATION_FACTOR))
    except Exception:
        return DEFAULT_CALIBRATION_FACTOR

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def get_market_age():
    """Days since Bitcoin genesis"""
    now = datetime.now(timezone.utc)
    return (now - BTC_GENESIS).days

def fetch_axeladler_cvdd():
    """抓 axeladlerjr.com chart-data.json 的最新 CVDD 原始值 + 日期，× 校准因子。

    chart-data.json 含完整历史（2009-10-06 至今，6170 天，每日更新），
    字段：date/price/cvdd/cvdd_2_5x/cvdd_5x/price_cvdd_ratio。
    返回 {"cvdd": float(标准口径), "date": "YYYY-MM-DD"} 或 None（抓取失败）。
    """
    try:
        req = urllib.request.Request(AXELADLER_DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        if isinstance(data, list) and data:
            latest = data[-1]
            raw = float(latest["cvdd"])
            return {"cvdd": round(raw * load_calibration_factor(), 2), "date": latest["date"]}
    except Exception:
        pass
    return None

_AXELADLER_HISTORY_CACHE = None

def fetch_axeladler_history():
    """抓 axeladlerjr.com chart-data.json 完整历史，返回 [{date, cvdd(×1.08), price}]。

    用于 30/90/180/360 日前历史对照。⚠️ 历史对照必须用这个源，严禁用增量 history 文件——
    增量文件曾混入 Blockchair fallback（cdd_24h 口径偏高 ~60% 且逐日不稳），导致「30日前」
    锚值出现 +$610 假跳（2026-09-01 踩坑）。axeladler 全历史单一口径、单调平滑。
    """
    global _AXELADLER_HISTORY_CACHE
    if _AXELADLER_HISTORY_CACHE is not None:
        return _AXELADLER_HISTORY_CACHE
    try:
        req = urllib.request.Request(AXELADLER_DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        factor = load_calibration_factor()
        out = []
        for d in data:
            raw = float(d.get("cvdd") or 0)
            if raw <= 0:
                continue
            out.append({
                "date": d["date"],
                "cvdd": raw * factor,
                "price": float(d.get("price") or 0),
            })
        _AXELADLER_HISTORY_CACHE = out
        return out
    except Exception:
        return None

def _axeladler_at(hist, days_ago):
    """从 axeladler 全历史里取 N 天前的 cvdd/price（±1 天容差）。"""
    if not hist:
        return None
    now = datetime.now(timezone.utc)
    for e in hist:
        try:
            dt = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if abs((now - dt).days - days_ago) <= 1:
            return e
    return None

def get_blockchair_data():
    """Get CDD and BTC price from Blockchair"""
    data = fetch("https://api.blockchair.com/bitcoin/stats")
    stats = data["data"]
    return {
        "cdd_24h": stats["cdd_24h"],
        "btc_price": stats["market_price_usd"],
        "timestamp": stats["best_block_time"]
    }

def load_seed():
    """Load the known CVDD seed value"""
    if os.path.exists(SEED_FILE):
        with open(SEED_FILE) as f:
            return json.load(f)
    return None

def save_seed(seed):
    with open(SEED_FILE, 'w') as f:
        json.dump(seed, f, indent=2)

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history[-365:], f, indent=2)  # Keep 1 year

def estimate_cvdd(price_override=None):
    """Estimate current CVDD.
    price_override: 若传入，用这个 BTC 价覆盖 Blockchair 取数，保证与全篇统一价一致。"""
    # ⚠️ Blockchair 在墙内常超时/被限流：它是 fallback 而非主源，失败不应拖垮整个脚本
    # （2026-09-18 踩坑：fetch_all.py 调用本脚本 30s 超时，整个 CVDD 段无数据）。
    # 权威源是 axeladler ×1.08，不依赖 Blockchair；仅当 axeladler 也失败时才需要 cdd。
    try:
        bc = get_blockchair_data()
        cdd = bc["cdd_24h"]
        btc_price = bc["btc_price"]
    except Exception as e:
        print(f"⚠️ Blockchair 取数失败（{type(e).__name__}），改用 axeladler 权威源", file=sys.stderr)
        bc = None
        cdd = 0.0
        btc_price = 0.0
    if price_override is not None:
        btc_price = float(price_override)
    market_age = get_market_age()

    seed = load_seed()

    if seed is None:
        # No seed yet — need manual initialization
        print("No CVDD seed found. Initialize with known value.", file=sys.stderr)
        return {
            "error": "No seed. Initialize with: echo '{\"cvdd\":<value>,\"date\":\"YYYY-MM-DD\",\"cumulative_vdd\":<value>}' > ~/.hermes/data/cvdd_seed.json",
            "market_age": market_age
        }

    # Calculate days since seed
    seed_date = datetime.fromisoformat(seed["date"]).replace(tzinfo=timezone.utc)
    days_since_seed = (datetime.now(timezone.utc) - seed_date).days

    if days_since_seed < 0:
        return {"error": "Seed date is in the future"}

    # Load history and check what we've already recorded
    history = load_history()
    recorded_dates = {h["date"] for h in history}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 权威源优先：axeladlerjr.com 当天基线值（比 Blockchair 增量累加更准）
    axeladler = fetch_axeladler_cvdd()

    if axeladler:
        # 用权威值覆盖/记录今天（增量累加会因 Blockchair cdd_24h 口径偏大而每天漂移）
        current_cvdd = axeladler["cvdd"]
        cumulative_vdd = current_cvdd * market_age  # 反推，保持单调递增
        if today_str in recorded_dates:
            for h in history:
                if h["date"] == today_str:
                    h["cvdd"] = round(current_cvdd, 2)
                    h["cumulative_vdd"] = round(cumulative_vdd, 2)
                    h["btc_price"] = round(btc_price, 2)
                    h["market_age"] = market_age
                    h["source"] = "LookIntoBitcoin-calibrated"
        else:
            history.append({
                "date": today_str,
                "cvdd": round(current_cvdd, 2),
                "cumulative_vdd": round(cumulative_vdd, 2),
                "cdd_24h": cdd,
                "btc_price": round(btc_price, 2),
                "market_age": market_age,
                "source": "LookIntoBitcoin-calibrated",
            })
        save_history(history)
    elif today_str not in recorded_dates:
        # fallback：Blockchair cdd_24h 增量累加
        daily_vdd = cdd * btc_price / CDD_SCALE
        if history:
            cumulative_vdd = history[-1]["cumulative_vdd"] + daily_vdd
        else:
            cumulative_vdd = seed["cumulative_vdd"] + daily_vdd
        current_cvdd = cumulative_vdd / market_age
        history.append({
            "date": today_str,
            "cvdd": round(current_cvdd, 2),
            "cumulative_vdd": round(cumulative_vdd, 2),
            "cdd_24h": cdd,
            "btc_price": round(btc_price, 2),
            "market_age": market_age
        })
        save_history(history)
    else:
        # 今天已记录（无权威值），沿用已有记录
        current_cvdd = history[-1]["cvdd"]

    # Get historical values for 30d/90d/180d/360d comparison
    cvdd_30d = None
    cvdd_90d = None
    cvdd_180d = None
    cvdd_360d = None
    btc_30d = None
    btc_90d = None
    btc_180d = None
    btc_360d = None

    # ⚠️ 历史对照优先用 axeladler 全历史（单一口径×1.08），杜绝增量文件混入 Blockchair fallback 污染
    axeladler_hist = fetch_axeladler_history()
    if axeladler_hist:
        for days, key_cvdd, key_btc in [(30, "cvdd_30d", "btc_30d"), (90, "cvdd_90d", "btc_90d"),
                                         (180, "cvdd_180d", "btc_180d"), (360, "cvdd_360d", "btc_360d")]:
            a = _axeladler_at(axeladler_hist, days)
            if a:
                if key_cvdd == "cvdd_30d": cvdd_30d = a["cvdd"]; btc_30d = a["price"]
                elif key_cvdd == "cvdd_90d": cvdd_90d = a["cvdd"]; btc_90d = a["price"]
                elif key_cvdd == "cvdd_180d": cvdd_180d = a["cvdd"]; btc_180d = a["price"]
                elif key_cvdd == "cvdd_360d": cvdd_360d = a["cvdd"]; btc_360d = a["price"]

    # 回退：axeladler 抓不到时用增量 history 文件
    if None in (cvdd_30d, cvdd_90d, cvdd_180d, cvdd_360d):
        for h in history:
            date = datetime.fromisoformat(h["date"]).replace(tzinfo=timezone.utc)
            days_ago = (datetime.now(timezone.utc) - date).days
            if 29 <= days_ago <= 31 and cvdd_30d is None:
                cvdd_30d = h["cvdd"]; btc_30d = h.get("btc_price")
            if 89 <= days_ago <= 91 and cvdd_90d is None:
                cvdd_90d = h["cvdd"]; btc_90d = h.get("btc_price")
            if 179 <= days_ago <= 181 and cvdd_180d is None:
                cvdd_180d = h["cvdd"]; btc_180d = h.get("btc_price")
            if 359 <= days_ago <= 361 and cvdd_360d is None:
                cvdd_360d = h["cvdd"]; btc_360d = h.get("btc_price")

    # Calculate Price/CVDD
    price_cvdd = btc_price / current_cvdd

    result = {
        "cvdd": round(current_cvdd, 2),
        "btc_price": round(btc_price, 2),
        "price_cvdd": round(price_cvdd, 4),
        "market_age_days": market_age,
        "daily_cvdd_change": round(cdd * btc_price / CDD_SCALE / market_age, 2),
        "cdd_24h_millions": round(cdd / 1e6, 2),
        "data_date": today_str,
        "source": "LookIntoBitcoin-calibrated (axeladler × 1.08)",
        "interval": (
            "离极端底部仍有距离" if price_cvdd > 1.30 else
            "重点观察区" if price_cvdd > 1.15 else
            "底部区域" if price_cvdd > 1.05 else
            "极端底部"
        )
    }

    # Add 30d/90d comparison if available
    if cvdd_30d:
        result["cvdd_30d"] = round(cvdd_30d, 2)
        result["cvdd_30d_change_pct"] = round((current_cvdd / cvdd_30d - 1) * 100, 2)
        if btc_30d:
            result["price_cvdd_30d"] = round(btc_30d / cvdd_30d, 4)
            result["price_cvdd_30d_change_pct"] = round((price_cvdd / (btc_30d / cvdd_30d) - 1) * 100, 2)

    if cvdd_90d:
        result["cvdd_90d"] = round(cvdd_90d, 2)
        result["cvdd_90d_change_pct"] = round((current_cvdd / cvdd_90d - 1) * 100, 2)

    if cvdd_180d:
        result["cvdd_180d"] = round(cvdd_180d, 2)
        result["cvdd_180d_change_pct"] = round((current_cvdd / cvdd_180d - 1) * 100, 2)
        if btc_180d:
            result["btc_price_180d"] = round(btc_180d, 2)

    if cvdd_360d:
        result["cvdd_360d"] = round(cvdd_360d, 2)
        result["cvdd_360d_change_pct"] = round((current_cvdd / cvdd_360d - 1) * 100, 2)
        if btc_360d:
            result["btc_price_360d"] = round(btc_360d, 2)

    result["trend"] = (
        "↑上移" if cvdd_30d and current_cvdd > cvdd_30d * 1.001 else
        "↓回落" if cvdd_30d and current_cvdd < cvdd_30d * 0.999 else
        "→横盘(数据不足)" if not cvdd_30d else "→横盘"
    )

    return result

def init_seed(cvdd_value, seed_date, btc_price_at_seed):
    """Initialize CVDD tracking with a known seed value"""
    age = (datetime.fromisoformat(seed_date).replace(tzinfo=timezone.utc) - BTC_GENESIS).days
    cumulative_vdd = cvdd_value * age

    seed = {
        "cvdd": cvdd_value,
        "date": seed_date,
        "cumulative_vdd": round(cumulative_vdd, 2),
        "btc_price": btc_price_at_seed,
        "source": "Glassnode Studio web search"
    }
    save_seed(seed)

    # Initialize history with seed
    history = [{
        "date": seed_date,
        "cvdd": round(cvdd_value, 2),
        "cumulative_vdd": round(cumulative_vdd, 2),
        "btc_price": btc_price_at_seed,
        "market_age": age
    }]
    save_history(history)

    return seed

if __name__ == "__main__":
    args = sys.argv[1:]
    # --price <btc价>：传统一价进去，保证 CVDD 段与全篇 BTC 价一致
    if "--price" in args:
        idx = args.index("--price")
        price_override = float(args[idx + 1]) if idx + 1 < len(args) else None
        result = estimate_cvdd(price_override)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if args and args[0] == "init" and len(args) >= 4:
        cvdd = float(args[1])
        date = args[2]
        price = float(args[3]) if len(args) > 3 else 0
        seed = init_seed(cvdd, date, price)
        print(f"Seed initialized: CVDD=${cvdd:,.2f} on {date}")
    elif args and args[0] == "status":
        seed = load_seed()
        if seed:
            print(json.dumps(seed, indent=2))
        else:
            print("No seed found")
    else:
        result = estimate_cvdd()
        print(json.dumps(result, indent=2))
