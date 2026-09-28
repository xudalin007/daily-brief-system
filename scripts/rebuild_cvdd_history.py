#!/usr/bin/env python3
"""重建 cvdd_history.json：从 axeladler chart-data.json 全历史 ×1.08 校准，消除 Blockchair fallback 污染。

背景：2026-07-29 ~ 08-23 的历史条目由 Blockchair cdd_24h 增量反推（source=blockchair-precise / 缺 source），
Blockchair cdd_24h 口径偏高 ~60% 且逐日不稳，导致「30日前」锚值出现 +$610 的假跳（7/31 $45,429 → 8/1 $46,039）。
正确做法：全历史统一从 axeladler ×1.08 取，单一口径、单调平滑。

用法：python3 rebuild_cvdd_history.py [--keep N]   # 默认保留最近 365 天
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta

HISTORY_FILE = os.path.expanduser("~/.hermes/data/cvdd_history.json")
CALIBRATION_FILE = os.path.expanduser("~/.hermes/data/cvdd_calibration.json")
AXELADLER_DATA_URL = "https://axeladlerjr.com/charts/bitcoin-cvdd/chart-data.json"
BTC_GENESIS = datetime(2009, 1, 3, tzinfo=timezone.utc)

def load_factor():
    try:
        return float(json.load(open(CALIBRATION_FILE))["factor"])
    except Exception:
        return 1.08

def main():
    keep = 365
    if "--keep" in sys.argv:
        keep = int(sys.argv[sys.argv.index("--keep") + 1])

    req = urllib.request.Request(AXELADLER_DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=25).read())
    factor = load_factor()

    rebuilt = []
    for d in data:
        ds = d["date"]
        raw = float(d["cvdd"])
        if raw <= 0:
            continue
        cvdd = raw * factor
        dt = datetime.fromisoformat(ds).replace(tzinfo=timezone.utc)
        age = (dt - BTC_GENESIS).days
        rebuilt.append({
            "date": ds,
            "cvdd": round(cvdd, 2),
            "cumulative_vdd": round(cvdd * age, 2),
            "btc_price": round(float(d.get("price") or 0), 2),
            "market_age": age,
            "source": "LookIntoBitcoin-calibrated (axeladler × 1.08)",
        })

    rebuilt = rebuilt[-keep:]

    # 备份旧文件
    bak = HISTORY_FILE + ".contaminated_bak"
    if os.path.exists(HISTORY_FILE):
        os.replace(HISTORY_FILE, bak)
        print(f"旧文件已备份 → {bak}")

    with open(HISTORY_FILE, "w") as f:
        json.dump(rebuilt, f, indent=2)

    print(f"重建完成：{len(rebuilt)} 天 ({rebuilt[0]['date']} ~ {rebuilt[-1]['date']})，factor={factor}")
    print(f"最新 CVDD = ${rebuilt[-1]['cvdd']:,.2f} @ {rebuilt[-1]['date']}")

    # 校验：报告 30/90/180/360 日前值
    now = datetime.now(timezone.utc)
    print("\n=== 重建后历史对照（正确值）===")
    for label, days in [("30日前", 30), ("90日前", 90), ("180日前", 180), ("360日前", 360)]:
        target = now - timedelta(days=days)
        best = None
        for e in rebuilt:
            dt = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc)
            if abs((now - dt).days - days) <= 1:
                best = e
                break
        if best:
            pc = best["btc_price"] / best["cvdd"] if best["cvdd"] else 0
            print(f"  {label} ({best['date']}): CVDD=${best['cvdd']:,.2f}  BTC=${best['btc_price']:,.2f}  Price/CVDD={pc:.4f}")

if __name__ == "__main__":
    main()
