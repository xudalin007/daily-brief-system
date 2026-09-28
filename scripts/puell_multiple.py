#!/usr/bin/env python3
"""Puell Multiple —— 解决 bgeometrics「滞后 7 天」问题。

## 问题（2026-09-25 定位）
bgeometrics 的 /v1/puell-multiple 数据日期比当天晚 **约 7 天**（同一家的 realized-price
也停在同一日，属源侧滞后，不是我们抓取失败）。用它做「短期持有人状态」判断 = 拿一周前的数据看今天。

## 解决
Puell 的定义是公开可复算的：**当日发行价值(USD) / 365日移动平均(当日发行价值)**。
两条免费路线都只滞后约 1 天：

1. **LookIntoBitcoin（首选，展示口径）**：LITB 是 Puell 的参考站点，数据到前一日。
   走本机浏览器 CDP（`litb_chart.py`，与 CVDD 同一套），slug = `puell_multiple`。
2. **本地自算（交叉校验 + 回退）**：Coin Metrics 社区版免费接口的 `IssTotUSD`
   （我们已在用它算 MVRV）→ 自己算 365 日均值。纯 HTTP、5 秒、零浏览器依赖。

## 口径校验（2026-09-25 实测，真实数据，近 200 天）
| 对比 | 位移 | 平均|偏差| | 中位 | >2% 天数 |
|---|---|---|---|---|
| 自算 vs LITB | 同日 | **0.087%** | 0.065% | 0/200 |
| 自算 vs bgeometrics | **同日** | 0.830% | 0.606% | 13/200 |
| LITB vs bgeometrics | LITB 日期 = bgeo +1 天 | 0.843% | 0.623% | 15/199 |

结论：三者同一定义；**LITB 的日期标注比 bgeo/Coin Metrics 晚一天**（LITB 的 d 日点
= 用 d-1 日发行数据算出的值）。本脚本以 **LITB 的日期/数值为准**（用户核对 LITB 时能对上），
同时用自算值在同日期上互校，偏差 >0.5% 就报警（防止 LITB 改口径我们不知道）。

用法:
    python3 puell_multiple.py            # 最新值 + 7/30 日变化 + 自算互校
    python3 puell_multiple.py --json
    python3 puell_multiple.py --no-litb  # 强制走本地自算（不启动 Edge）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

CM_URL = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc"
          "&metrics=IssTotUSD,PriceUSD&frequency=1d&page_size=10000&start_time=2021-01-01")
WIN = 365
SOURCE_LITB = "lookintobitcoin.com（浏览器 CDP 自动，Puell 参考站点）"
SOURCE_CM = "Coin Metrics IssTotUSD ÷ 365日均值（本地自算；与 LITB 定义同日偏差 0.087%）"


def _litb_series(days: int = 40) -> dict[str, float]:
    """LITB Puell 序列（浏览器 CDP，约 20s）→ {date: value}"""
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, os.path.join(here, "litb_chart.py"), "puell_multiple",
                        "--json", "--days", str(days)], capture_output=True, text=True, timeout=200)
    d = json.loads(r.stdout)
    tr = next(t for t in d["traces"] if t["name"] == "Puell Multiple")
    return {str(k): float(v) for k, v in tr["series"]}


def _cm_rows() -> list[tuple[str, float, float]]:
    req = urllib.request.Request(CM_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.loads(r.read().decode())
    rows = []
    for x in payload.get("data", []):
        try:
            rows.append((x["time"][:10], float(x["IssTotUSD"]), float(x.get("PriceUSD") or 0)))
        except Exception:
            continue
    rows.sort(key=lambda t: t[0])
    return rows


def _compute(rows: list[tuple[str, float, float]], label_shift: int = 0) -> dict[str, float]:
    """Puell = 发行价值 / 365日均值。label_shift=1 → LITB 口径（日期标在发行日次日）。"""
    if len(rows) < WIN + 5:
        raise RuntimeError(f"数据不足（{len(rows)} 天）")
    out = {}
    for i in range(WIN - 1, len(rows)):
        ma = sum(r[1] for r in rows[i - WIN + 1:i + 1]) / WIN
        if not ma:
            continue
        d = rows[i][0]
        if label_shift:
            d = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        out[d] = rows[i][1] / ma
    return out


def _zone(pm: float) -> str:
    return ("过热" if pm >= 4 else "偏热" if pm >= 2 else "正常" if pm >= 1
            else "受压" if pm >= 0.5 else "投降区")


def _pct(cur: float | None, old: float | None) -> float | None:
    return None if (cur is None or not old) else round((cur - old) / old * 100, 2)


def fetch_puell(use_litb: bool = True) -> dict:
    rows = _cm_rows()
    cm_litb = _compute(rows, 1)      # LITB 口径（日期 = 发行日 +1）
    cm_raw = _compute(rows, 0)       # Coin Metrics / bgeometrics 口径

    litb: dict[str, float] = {}
    litb_err = None
    if use_litb:
        try:
            litb = _litb_series()
        except Exception as exc:
            litb_err = str(exc)[:120]

    if litb:
        dates = sorted(litb)
        primary_date, primary_val = dates[-1], litb[dates[-1]]
        series = litb
        source = SOURCE_LITB
        fallback = None
    else:                             # 回退：本地自算（LITB 口径），日期标注同 LITB 规则
        dates = sorted(cm_litb)
        primary_date, primary_val = dates[-1], cm_litb[dates[-1]]
        series = cm_litb
        source = SOURCE_CM
        fallback = ("LITB 抓取失败，已回退本地自算（LITB 口径）"
                    + (f"：{litb_err}" if litb_err else ""))

    out = {
        "puell_multiple": round(primary_val, 4),
        "puell_date": primary_date,
        "puell_7d_ago": round(series[dates[-8]], 4) if len(dates) > 7 else None,
        "puell_30d_ago": round(series[dates[-31]], 4) if len(dates) > 30 else None,
        "puell_7d_change_pct": _pct(primary_val, series[dates[-8]]) if len(dates) > 7 else None,
        "puell_30d_change_pct": _pct(primary_val, series[dates[-31]]) if len(dates) > 30 else None,
        "zone": _zone(primary_val),
        "source": source,
        "fallback": fallback,
        "cm_litb_date": sorted(cm_litb)[-1],
        "cm_litb_value": round(cm_litb[sorted(cm_litb)[-1]], 4),
        "cm_raw_date": sorted(cm_raw)[-1],
        "cm_raw_value": round(cm_raw[sorted(cm_raw)[-1]], 4),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # 交叉校验：同一日期上 自算(LITB 口径) vs LITB
    if litb and primary_date in cm_litb:
        dev = (cm_litb[primary_date] / primary_val - 1) * 100
        out["cm_check_date"] = primary_date
        out["cm_check_value"] = round(cm_litb[primary_date], 4)
        out["cm_check_dev_pct"] = round(dev, 3)
        out["cm_check_ok"] = abs(dev) <= 0.5
    return out


def main() -> int:
    use_litb = "--no-litb" not in sys.argv
    try:
        d = fetch_puell(use_litb=use_litb)
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:300], "source": "puell_multiple"}, ensure_ascii=False))
        return 1
    if "--json" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(f"📊 Puell Multiple  {d['puell_date']}  =  {d['puell_multiple']}  → {d['zone']}")
    print(f"  7日/30日 : {d['puell_7d_ago']} ({d['puell_7d_change_pct']:+.2f}%) | "
          f"{d['puell_30d_ago']} ({d['puell_30d_change_pct']:+.2f}%)")
    print(f"  来源     : {d['source']}")
    if d.get("cm_check_dev_pct") is not None:
        ok = "✅" if d.get("cm_check_ok") else "⚠️ 超阈值"
        print(f"  自算互校 : {d['cm_check_date']} 自算 {d['cm_check_value']} vs LITB "
              f"{d['puell_multiple']}  偏差 {d['cm_check_dev_pct']:+.3f}% {ok}")
    print(f"  本地领先值: {d['cm_litb_date']} = {d['cm_litb_value']}（自算，通常比 LITB 早一天可算）")
    if d.get("fallback"):
        print(f"  ⚠️ {d['fallback']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
