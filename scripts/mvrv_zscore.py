#!/usr/bin/env python3
"""获取 MVRV Z-Score 数据。
数据源：Coin Metrics 免费 API（无需 key）+ HUT1994/bitcoin-mvrv-zscore 历史 CSV（jsDelivr CDN）
用法: python3 mvrv_zscore.py
"""
import json, urllib.request, os, sys
from datetime import datetime, timezone

HISTORY_CSV = os.path.expanduser("~/.hermes/data/mvrv_history.json")
CM_API = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
JSDELIVR_CSV = "https://cdn.jsdelivr.net/gh/HUT1994/bitcoin-mvrv-zscore@main/btc_mvrv_dataset.csv"

def fetch_coinmetrics(limit=10):
    """从 Coin Metrics 免费 API 获取最新 MVRV 数据"""
    params = f"?assets=btc&metrics=PriceUSD,SplyCur,CapMVRVCur&frequency=1d&page_size={limit}"
    url = CM_API + params
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    results = []
    for item in data.get("data", []):
        ts = item["time"].replace("Z", "+00:00")
        # 处理纳秒精度: 2026-07-29T00:00:00.000000000+00:00
        if "." in ts:
            ts = ts[:ts.index(".")] + ts[ts.index("+00:00"):]
        dt = datetime.fromisoformat(ts)
        results.append({
            "date": dt.strftime("%Y-%m-%d"),
            "price": float(item["PriceUSD"]),
            "supply": float(item["SplyCur"]),
            "mvrv_ratio": float(item["CapMVRVCur"]),
        })
    return sorted(results, key=lambda x: x["date"])

def fetch_history_csv():
    """从 jsDelivr CDN 获取历史 CSV（含 MarketCap 用于计算 Z-Score）"""
    req = urllib.request.Request(JSDELIVR_CSV, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode("utf-8")
    lines = text.strip().split("\n")
    header = lines[0].strip().split(",")
    # time,PriceUSD,SplyCur,CapMVRVCur,MarketCap,RealizedCap,RealizedPrice
    history = []
    for line in lines[1:]:
        parts = line.strip().split(",")
        if len(parts) >= 7:
            history.append({
                "date": parts[0],
                "price": float(parts[1]),
                "supply": float(parts[2]),
                "mvrv_ratio": float(parts[3]),
                "market_cap": float(parts[4]),
                "realized_cap": float(parts[5]),
            })
    return history

def calc_zscore(history):
    """计算扩张窗口标准差 Z-Score（与原始定义一致）"""
    market_caps = [h["market_cap"] for h in history]
    mcs = []
    mean_so_far = 0
    m2_so_far = 0
    for i, mc in enumerate(market_caps):
        n = i + 1
        delta = mc - mean_so_far
        mean_so_far += delta / n
        delta2 = mc - mean_so_far
        m2_so_far += delta * delta2
        if n > 1:
            expanding_std = (m2_so_far / (n - 1)) ** 0.5
        else:
            expanding_std = 0
        mcs.append((mc, mean_so_far, expanding_std))

    # Z-Score = (MarketCap - RealizedCap) / expanding_stdev(MarketCap)
    results = []
    for h, (mc, mean, std) in zip(history, mcs):
        if std > 0:
            zscore = (h["market_cap"] - h["realized_cap"]) / std
        else:
            zscore = 0
        results.append({
            "date": h["date"],
            "mvrv_ratio": h["mvrv_ratio"],
            "mvrv_zscore": round(zscore, 4),
            "market_cap": h["market_cap"],
            "realized_cap": h["realized_cap"],
            "price": h["price"],
        })
    return results

def main():
    # 1. 加载历史（本地缓存优先）
    history_raw = None
    if os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV) as f:
            history_raw = json.load(f)

    if not history_raw:
        print("首次加载历史数据（jsDelivr CDN）...", file=sys.stderr)
        history_raw = fetch_history_csv()
        os.makedirs(os.path.dirname(HISTORY_CSV), exist_ok=True)
        with open(HISTORY_CSV, "w") as f:
            json.dump(history_raw, f)

    # 2. 获取最新数据
    print("获取 Coin Metrics 最新数据...", file=sys.stderr)
    latest = fetch_coinmetrics(10)

    # 3. 检查最新日期并合并
    last_hist_date = history_raw[-1]["date"] if history_raw else "2010-01-01"
    new_entries = [l for l in latest if l["date"] > last_hist_date]

    if new_entries:
        for entry in new_entries:
            # 计算 MarketCap 和 RealizedCap
            mc = entry["price"] * entry["supply"]
            rc = mc / entry["mvrv_ratio"]
            history_raw.append({
                "date": entry["date"],
                "price": entry["price"],
                "supply": entry["supply"],
                "mvrv_ratio": entry["mvrv_ratio"],
                "market_cap": mc,
                "realized_cap": rc,
            })
        with open(HISTORY_CSV, "w") as f:
            json.dump(history_raw, f)

    # 4. 计算 Z-Score
    results = calc_zscore(history_raw)
    latest_result = results[-1]

    # 5. 区间判断（口径 2026-08-30 修正：0~1 是「合理偏低」不是「低估」，Z<0 才是低估）
    z = latest_result["mvrv_zscore"]
    if z < 0:
        interval = "低估"
        light = "🟢"
    elif z < 1.0:
        interval = "合理偏低"
        light = "🟢"
    elif z < 3.0:
        interval = "合理"
        light = "🟡"
    elif z < 5.0:
        interval = "高估"
        light = "🟠"
    else:
        interval = "泡沫"
        light = "🔴"

    output = {
        "mvrv_ratio": latest_result["mvrv_ratio"],
        "mvrv_zscore": latest_result["mvrv_zscore"],
        "btc_price": round(latest_result["price"], 2),
        "market_cap_b": round(latest_result["market_cap"] / 1e9, 2),
        "realized_cap_b": round(latest_result["realized_cap"] / 1e9, 2),
        "data_date": latest_result["date"],
        "interval": interval,
        "light": light,
        "history_days": len(results),
        "latest_days": len(new_entries),
        "source": "Coin Metrics 免费 API + jsDelivr CDN (HUT1994/bitcoin-mvrv-zscore)",
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
