#!/usr/bin/env python3
"""重建 AHR999 完整历史数据集（幂律口径）。

口径与 9992100.xyz / lovexw/ahr999-free 一致：
- AHR999 = (price / 200日定投成本) × (price / 指数增长估值)
- 200日定投成本 = 200 / Σ(1/price)   （过去200日调和平均 = 定投成本）
- 指数增长估值 = 10^(5.84 × log10(币龄天数) - 17.01)   （幂律）
- 币龄 = 距 2009-01-03 的天数 + 1

输入：~/.hermes/data/mvrv_history.json（Coin Metrics 价格，date+price）
输出：~/.hermes/data/ahr999_history.json（[[ts_ms, ahr999], ...]）

用法：python3 ~/.hermes/scripts/rebuild_ahr999_history.py
"""
import json, math, os, shutil
from datetime import datetime, timezone

MV_PATH = os.path.expanduser("~/.hermes/data/mvrv_history.json")
AHR_PATH = os.path.expanduser("~/.hermes/data/ahr999_history.json")

GENESIS = datetime(2009, 1, 3, tzinfo=timezone.utc)

def days_since_genesis(date_str):
    y, m, d = map(int, date_str.split('-'))
    return (datetime(y, m, d, tzinfo=timezone.utc) - GENESIS).days + 1

def index_growth(age_days):
    """指数增长估值（幂律）"""
    return 10 ** (5.84 * math.log10(age_days) - 17.01)

def main():
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

    # 备份旧数据再覆盖
    if os.path.exists(AHR_PATH):
        shutil.copy(AHR_PATH, AHR_PATH.replace('.json', '.bak.json'))
    with open(AHR_PATH, 'w') as f:
        json.dump(out, f)

    print(f"重建完成: {len(out)} 天 AHR999 历史")
    print(f"范围: {dates[199]} ~ {dates[-1]}")
    print(f"末值: AHR999={out[-1][1]:.4f}")

if __name__ == "__main__":
    main()
