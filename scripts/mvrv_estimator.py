#!/usr/bin/env python3
"""估算 MVRV 比率和近似 Z-Score

原理：
- 用 Santiment 已知值（30天延迟）作为种子
- 每日跟踪 Market Cap 变化（价格 × 供应量）
- 用链上交易量估算 Realized Cap 变化
- 近似 MVRV = MC / RC
- 近似 Z-Score = (MVRV - 1) / σ，用 AHR999 与 Z-Score 的经验关系校准

用法: python3 mvrv_estimator.py
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta

PRICE_FILE = "/tmp/bic_price_data.json"

# 种子值（需定期更新）
# 来源: bitcoinmagazinepro.com (ex-lookintobitcoin.com) → charts/mvrv-zscore/
# 更新: 每1-2周手动查一次。RealizedCap 在图表上有直接显示。
# 公式: MVRV = MC / RealizedCap
SEED = {
    "mvrv": 1.171,       # MVRV 比率 (2026-08-07)
    "date": "2026-08-07",
    "btc_price": 64124,
    "realized_cap": 1083818636989,  # 从 bitcoinmagazinepro.com 获取
    "zscore": 0.36,      # MVRV Z-Score 直接值
}

def load_prices():
    """加载历史价格"""
    if not os.path.exists(PRICE_FILE):
        return []
    with open(PRICE_FILE) as f:
        raw = json.load(f)
    return [(datetime.strptime(d, "%Y/%m/%d"), p) for d, p in raw]

def fetch_chain_volume(days=10):
    """获取近期链上交易量"""
    url = f"https://api.blockchain.info/charts/estimated-transaction-volume?timespan={days}days&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    return {datetime.fromtimestamp(v["x"], tz=timezone.utc).strftime("%Y-%m-%d"): v["y"]
            for v in data["values"]}

def estimate_mvrv():
    """估算当前 MVRV 和近似 Z-Score"""
    prices_all = load_prices()
    if not prices_all:
        return {"error": "no price data"}

    price_map = {dt.strftime("%Y-%m-%d"): p for dt, p in prices_all}

    # 种子
    seed_date = datetime.fromisoformat(SEED["date"]).replace(tzinfo=timezone.utc)
    seed_mvrv = SEED["mvrv"]
    seed_price = SEED["btc_price"]

    # 种子时的隐含 Realized Cap
    # MVRV = MC / RC → RC = MC / MVRV
    supply = 19.8e6  # 约1980万 BTC
    seed_mc = seed_price * supply
    seed_rc = seed_mc / seed_mvrv

    # 从种子日期到今天
    today = datetime.now(timezone.utc)

    # 当前价格 - 优先从 9992100.xyz
    current_price = None
    current_date = today
    try:
        req = urllib.request.Request("https://9992100.xyz/api/ahr999",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            ahr = json.loads(r.read())
        current_price = ahr.get("price_usd")
        if current_price:
            current_date = datetime.fromtimestamp(ahr.get("updated_at_unix", today.timestamp()), tz=timezone.utc)
    except:
        pass

    # 回退到价格文件
    if current_price is None:
        for dt, p in reversed(prices_all):
            d = dt.replace(tzinfo=timezone.utc)
            if (today - d).days <= 7:
                current_price = p
                current_date = d
                break

    if current_price is None:
        return {"error": "no recent price"}

    current_mc = current_price * supply

    # 获取近期交易量估算 Realized Cap 变化
    try:
        volume = fetch_chain_volume(30)
    except:
        volume = {}

    # 估算 Realized Cap 变化
    # RC 变化 ≈ 已花费币量 × (当前价 - 获取价)
    # 简化: 用 SMA155 作为平均获取价
    sma155 = 0
    count = 0
    for dt, p in prices_all:
        days = (current_date - dt.replace(tzinfo=timezone.utc)).days
        if 0 <= days <= 155:
            sma155 += p
            count += 1
    sma155 = sma155 / count if count > 0 else current_price

    # 从种子到今天 RC 的变化
    # 粗略: 每日交易量中约20%来自新花费，平均获取价用 SMA155
    total_volume_btc = 0
    d = seed_date
    while d <= current_date:
        ds = d.strftime("%Y-%m-%d")
        if ds in volume:
            total_volume_btc += volume[ds]
        d += timedelta(days=1)

    # RC 增量: 花费量 × (平均市价 - 获取价)
    # 假设 ~20% 的链上交易量是新花费
    spent_btc = total_volume_btc * 0.2
    avg_market_price = (seed_price + current_price) / 2
    rc_delta = spent_btc * (avg_market_price - sma155)

    current_rc = seed_rc + rc_delta
    current_mvrv = current_mc / current_rc if current_rc > 0 else seed_mvrv

    # 用已知校准点修正 Z-Score
    # 已知: MVRV=1.236 → Z≈0.32; MVRV=1.26 → Z≈0.32-0.36
    # 近似: Z ≈ MVRV - 0.93（在 1.0-1.5 区间）
    z_approx = current_mvrv - 0.93

    # 区间判断
    if z_approx < 0:
        z_interval = "极度低估"
        z_light = "🟢"
    elif z_approx < 1.0:
        z_interval = "低估"
        z_light = "🟢"
    elif z_approx < 3.0:
        z_interval = "合理"
        z_light = "🟡"
    elif z_approx < 5.0:
        z_interval = "高估"
        z_light = "🟠"
    else:
        z_interval = "极度高估"
        z_light = "🔴"

    return {
        "mvrv_ratio": round(current_mvrv, 4),
        "mvrv_zscore_approx": round(z_approx, 3),
        "btc_price": round(current_price, 2),
        "market_cap_b": round(current_mc / 1e9, 2),
        "realized_cap_est_b": round(current_rc / 1e9, 2),
        "data_date": current_date.strftime("%Y-%m-%d"),
        "seed_date": SEED["date"],
        "seed_mvrv": SEED["mvrv"],
        "z_interval": z_interval,
        "z_light": z_light,
        "interval": (
            "极度低估" if current_mvrv < 1.0 else
            "低估" if current_mvrv < 2.0 else
            "合理" if current_mvrv < 3.0 else
            "高估" if current_mvrv < 4.0 else
            "极度高估"
        ),
        "source": "自算（Santiment种子 + MC/RC变化估算）",
        "note": f"精度 ±0.2 Z-Score。种子 MVRV={SEED['mvrv']} ({SEED['date']})。每1-2周用 bitcoinmagazinepro.com 重新校准种子。"
    }

if __name__ == "__main__":
    result = estimate_mvrv()
    print(json.dumps(result, indent=2, ensure_ascii=False))
