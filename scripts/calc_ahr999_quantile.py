#!/usr/bin/env python3
"""Calculate AHR999 quantile5y (5年分位)，直接从 mvrv_history.json 算，永远最新。

口径（2026-08-30 修正，与 9992100.xyz / lovexw/ahr999-free 一致）：
- AHR999 = (price / 200日定投成本) × (price / 指数增长估值)
- 200日定投成本 = 过去200日调和平均 = 200 / Σ(1/price)  （定投成本，非简单算术平均）
- 指数增长估值 = 10^(5.84 × log10(币龄天数) - 17.01)  （幂律，非1.6指数）
- 币龄 = 距 2009-01-03 的天数 + 1

5年分位 = 当前值在最近 5 年（1826 天）历史分布中的位置。
直接从 mvrv_history.json（Coin Metrics 价格，每日更新）计算，无需维护中间文件。
"""
import json, sys, os, math
from datetime import datetime, timezone

MV_PATH = os.path.expanduser("~/.hermes/data/mvrv_history.json")
WINDOW_DAYS = 365 * 5
GENESIS = datetime(2009, 1, 3, tzinfo=timezone.utc)

def days_since_genesis(date_str):
    y, m, d = map(int, date_str.split('-'))
    return (datetime(y, m, d, tzinfo=timezone.utc) - GENESIS).days + 1

def index_growth(age_days):
    return 10 ** (5.84 * math.log10(age_days) - 17.01)

def build_history():
    """从 mvrv 价格算完整 AHR999 历史，返回 [(ts_ms, ahr999), ...]"""
    mvrv = json.load(open(MV_PATH))
    rows = sorted(mvrv, key=lambda d: d['date'])
    dates = [d['date'] for d in rows]
    prices = [d['price'] for d in rows]
    invs = [1 / p for p in prices]
    s = 0.0
    out = []
    for i in range(len(prices)):
        s += invs[i]
        if i >= 200:
            s -= invs[i - 200]
        if i >= 199:
            dca = 200 / s
            age = days_since_genesis(dates[i])
            iv = index_growth(age)
            a = (prices[i] / dca) * (prices[i] / iv)
            ts = int(datetime.strptime(dates[i], '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)
            out.append([ts, round(a, 4)])
    return out

def percentile(sorted_data, p):
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_data):
        return sorted_data[f] + c * (sorted_data[f+1] - sorted_data[f])
    return sorted_data[f]

def quantile(value, history):
    if not history:
        return 0.0
    rank = sum(1 for v in history if v <= value)
    return rank / len(history)

if __name__ == "__main__":
    data = build_history()
    full = sorted([x[1] for x in data])
    n_total = len(data)
    latest_ts = max(x[0] for x in data)
    cutoff_ts = latest_ts - WINDOW_DAYS * 86400 * 1000
    recent = sorted([x[1] for x in data if x[0] >= cutoff_ts])
    n_5y = len(recent)

    print(f"分位数窗口: 最近 5 年 = {n_5y} 天（{datetime.fromtimestamp(cutoff_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d')} 起）")
    print(f"（全历史 {n_total} 天仅存档，分位数不用全历史）")
    print(f"5年分布: min={recent[0]:.4f}, max={recent[-1]:.4f}")
    print(f"关键分位数（5年）:")
    for p in [5, 10, 25, 50, 75, 90, 95]:
        print(f"  {p:2d}th: {percentile(recent, p):.4f}")

    if len(sys.argv) > 1:
        value = float(sys.argv[1])
        q = quantile(value, recent)
        print(f"\nAHR999 = {value:.4f} → quantile5y = {q:.4f} ({q*100:.1f}th percentile)")
    else:
        print("\nUsage: python3 calc_ahr999_quantile.py <ahr999_value>")
