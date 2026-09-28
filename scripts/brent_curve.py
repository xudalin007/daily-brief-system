#!/usr/bin/env python3
"""布伦特原油双合约报价：近月 + 主力（最活跃）+ 月差。

## 为什么需要
ICE 布伦特的「近月」和「主力」现在会分家：近月 Nov'26 将于 9/30 到期且被挤仓
（104.38，成交 26 万手），主力已经换到 Dec'26（97.62，成交 50 万手）。
- 媒体/交易所结算口径（Reuters、TradingEconomics、CNBC @LCO.1）引用的是**近月**；
- 行情平台（TradingView BRN1!、新浪 hf_OIL、东方财富「当月连续」）默认显示的是**主力**。
两边都没错，差 6.5%，日报必须两个都列才不会让人误解。

## 数据源（GFW 直连，无需 key）
东方财富 push2（`112.B26X` = 布伦特原油 2026-11，`112.B26Z` = 2026-12 …）：
    https://push2.eastmoney.com/api/qt/stock/get?secid=112.<code>&fields=f43,f57,f58,f60,f86,f169,f170,f47
字段：f43 最新价 / f60 昨收 / f169 涨跌额 / f170 涨跌幅% / f47 成交量 / f86 时间戳
合约代码由搜索接口发现：searchapi.eastmoney.com/api/suggest/get?input=布伦特原油&type=14

校验：近月价应与 CNBC `@LCO.1`（页内 JSON `last`）一致（实测 104.38 vs 104.32，差 settle/last）。

用法：
    python3 brent_curve.py            # 人类可读
    python3 brent_curve.py --json     # 供日报脚本消费
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
SUGGEST = ("https://searchapi.eastmoney.com/api/suggest/get?input=%E5%B8%83%E4%BC%A6%E7%89%B9%E5%8E%9F%E6%B2%B9"
           "&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=30")
QUOTE = ("https://push2.eastmoney.com/api/qt/stock/get?secid=112.{code}"
         "&fields=f43,f57,f58,f60,f86,f169,f170,f47&invt=2&fltt=2")
LETTER = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


def curl(url: str) -> str:
    r = subprocess.run(["curl", "-s", "-L", "-A", UA, "--max-time", "20", url],
                       capture_output=True, timeout=40)
    return r.stdout.decode("utf-8", errors="replace")


def discover_codes() -> list[str]:
    """从东财搜索接口发现布伦特各月份合约代码（B26X=26年11月 …）。"""
    txt = curl(SUGGEST)
    codes = []
    for m in re.finditer(r'"Code":"(B\d{2}[FGHJKMNQUVXZ])"[^}]*?"Name":"布伦特原油(\d{4})"', txt):
        codes.append(m.group(1))
    return sorted(set(codes))


def month_of(code: str) -> tuple[int, int]:
    """B26X → (2026, 11)。"""
    yy = 2000 + int(code[1:3])
    return yy, LETTER[code[3]]


def quote(code: str) -> dict | None:
    raw = curl(QUOTE.format(code=code))
    try:
        d = json.loads(raw)["data"]
    except Exception:
        return None
    if not d or d.get("f43") in (None, "-"):
        return None
    y, m = month_of(code)
    return {"code": code, "month": f"{y}-{m:02d}", "label": f"{['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m]}'{str(y)[2:]}",
            "last": float(d["f43"]), "prev": float(d["f60"]) if d.get("f60") not in (None, "-") else None,
            "chg": float(d["f169"]) if d.get("f169") not in (None, "-") else None,
            "pct": float(d["f170"]) if d.get("f170") not in (None, "-") else None,
            "vol": int(d["f47"]) if str(d.get("f47", "")).isdigit() else None,
            "ts": datetime.fromtimestamp(d["f86"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if d.get("f86") else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    codes = discover_codes()
    if not codes:
        print("❌ 未发现合约代码", file=sys.stderr)
        return 1
    qs = [q for q in (quote(c) for c in codes) if q]
    if not qs:
        print("❌ 未取到报价", file=sys.stderr)
        return 1
    qs.sort(key=lambda q: q["month"])
    traded = [q for q in qs if (q["vol"] or 0) > 0] or qs
    front = traded[0]                                   # 近月：有成交的最早月份
    main = max(traded, key=lambda q: q["vol"] or 0)      # 主力：成交量最大
    spread = front["last"] - main["last"] if front["code"] != main["code"] else None
    out = {"front": front, "main": main, "spread": spread, "all": qs,
           "src": "eastmoney push2 (112.B26X/B26Z)", "note": "近月=有成交的最早月份；主力=成交量最大"}

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"布伦特 近月 {front['label']}: {front['last']}（{front['chg']:+} / {front['pct']:+.2f}%）成交 {front['vol']:,}")
    print(f"布伦特 主力 {main['label']}: {main['last']}（{main['chg']:+} / {main['pct']:+.2f}%）成交 {main['vol']:,}")
    print(f"月差（近月−主力）: {spread:+.2f}" if spread is not None else "近月＝主力，无月差")
    print(f"数据时间 {front['ts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
