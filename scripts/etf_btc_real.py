#!/usr/bin/env python3
"""美国现货 BTC ETF —— **真实 BTC 数量**（不靠 USD÷价格折算）。

数据源：charts.checkonchain.com 的 ETF 图表区（免费、GFW 直连、自包含 Plotly HTML）：
  - `btconchain/etfs/etf_flows_0/`   → 各基金**每日净流入（单位：BTC 枚）** + Total Net Flow
  - `btconchain/etfs/etf_balance_0/` → 各基金**持币余额（BTC 枚）** + Total ETF AUM

为什么需要它：Farside 只给美元流量，「折合BTC」只能 USD÷当日价估算。
checkonchain 直接给份额/持仓口径的**真实枚数**，是更硬的口径。

口径与校验（2026-09-23 实测）：
  - 各基金求和 == 源自带的 Total（flows 7,123.6 == 7,123.6；balance 11 只合计 1,293,087 == Total ETF AUM ✅）
  - 与 Farside 的美元流量逐日对照：9/17 $159.5M、9/18 $433.0M **完全一致**；
    9/21~22 差 $61.7M/$99.0M —— 恰好等于新发的 **MSBT**（checkonchain 暂未覆盖该基金）
  - 隐含价校验：USD流量 ÷ BTC流量 = 当日现货价（9/17 $76,459 vs 现货 $76,387，差 0.1%）✅

覆盖：11 只主要基金（GBTC/IBIT/FBTC/ARKB/BITB/BTCO/HODL/BRRR/EZBC/BTCW/BTC）。
`--fill-gap` 会用 Farside 美元缺口 ÷ 当日价补上未覆盖基金（当前就是 MSBT），
让 BTC 与美元两列口径一致。

用法:
    python3 etf_btc_real.py            # 人类可读
    python3 etf_btc_real.py --json     # JSON（供 fetch_all.py / 日报）
    python3 etf_btc_real.py --fill-gap # 补齐未覆盖基金
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

CDN = "https://charts-cdn.checkonchain.com/"
FLOWS_PATH = "btconchain/etfs/etf_flows_0/etf_flows_0_light.html"
BALANCE_PATH = "btconchain/etfs/etf_balance_0/etf_balance_0_light.html"
SOURCE = "checkonchain.com ETF 图表（真实份额/持仓口径）"
FUNDS = ["GBTC", "IBIT", "FBTC", "ARKB", "BITB", "BTCO", "HODL", "BRRR", "EZBC", "BTCW", "BTC"]

_sth = None


def _helpers():
    """复用 sth_supply.py 里的 checkonchain HTML 解析（traces 提取 + base64 解码）。"""
    global _sth
    if _sth is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sth_supply.py")
        spec = importlib.util.spec_from_file_location("sth_supply_local", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _sth = mod
    return _sth


def fetch_series(path: str, timeout: int = 60) -> dict:
    h = _helpers()
    req = urllib.request.Request(CDN + path, headers={"User-Agent": "Mozilla/5.0",
                                                      "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
    traces = h._extract_traces(raw.decode("utf-8", "replace"))
    return {t.get("name"): (h._decode(t.get("x")), h._decode(t.get("y")))
            for t in traces if t.get("name")}


def _sum_funds(series: dict) -> list:
    total = None
    for n in FUNDS:
        if n not in series:
            continue
        y = series[n][1]
        total = list(y) if total is None else [a + b for a, b in zip(total, y)]
    return total or []


def farside_usd_totals() -> dict:
    """Farside（经 jsDelivr）逐日美元总流量，单位百万美元；用于补齐覆盖缺口。"""
    import csv
    import io
    cache = os.path.expanduser("~/.hermes/data/etf_btc.csv")
    if not os.path.exists(cache):
        return {}
    out = {}
    for r in csv.DictReader(io.StringIO(open(cache, encoding="utf-8").read())):
        t = (r.get("Total") or "").strip()
        if not t:
            continue
        try:
            d = datetime.strptime(r["Date"].strip(), "%d %b %Y").strftime("%Y-%m-%d")
            out[d] = float(t)
        except Exception:
            continue
    return out


def _date(xs, i) -> str:
    """支持负索引（-1 = 最新）；越界返回空串"""
    if not xs:
        return ""
    try:
        return str(xs[i])[:10]
    except IndexError:
        return ""


def _load_mvrv() -> list:
    try:
        return json.load(open(os.path.expanduser("~/.hermes/data/mvrv_history.json"), encoding="utf-8"))
    except Exception:
        return []


def fetch_etf_btc(days: int = 5, fill_gap: bool = False) -> dict:
    fl = fetch_series(FLOWS_PATH)
    ba = fetch_series(BALANCE_PATH)
    xf, flow = fl["Total Net Flow"]
    xb = ba["IBIT"][0] if "IBIT" in ba else next(iter(ba.values()))[0]
    holdings = _sum_funds(ba)

    def win(n):
        return sum(flow[max(0, len(flow) - n):])

    out = {
        "date": _date(xf, -1),
        "flow_btc_1d": round(flow[-1], 1),
        "flow_btc_5d": round(win(5), 1),
        "flow_btc_30d": round(win(30), 1),
        "flow_btc_all": round(sum(flow), 1),
        "holdings_btc": round(holdings[-1], 0),
        "holdings_date": _date(xb, -1),
        "holdings_btc_30d_ago": round(holdings[-31], 0) if len(holdings) > 30 else None,
        "holdings_btc_30d_change": round(holdings[-1] - holdings[-31], 0) if len(holdings) > 30 else None,
        "holdings_btc_90d_change": round(holdings[-1] - holdings[-91], 0) if len(holdings) > 90 else None,
        "by_fund_btc": {n: round(ba[n][1][-1], 1) for n in FUNDS if n in ba},
        "recent_days": [
            {"date": _date(xf, i), "btc": round(flow[i], 1)} for i in range(max(0, len(flow) - days), len(flow))
        ],
        "source": SOURCE,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "funds_covered": len([n for n in FUNDS if n in ba]),
    }

    if fill_gap:
        # 未覆盖基金的美元缺口 ÷ 当日价 → 折合 BTC 补上
        far = farside_usd_totals()
        try:
            fl1 = fetch_series("btconchain/etfs/etf_flows_1/etf_flows_1_light.html")
            xu, usd_flow = fl1["Total Net Flow"]
        except Exception:
            usd_flow, xu = None, None
        prices = {}
        try:
            for x in json.load(open(os.path.expanduser("~/.hermes/data/mvrv_history.json"))):
                prices[x["date"]] = x.get("price")
        except Exception:
            pass
        gaps = []
        for i in range(max(0, len(flow) - 30), len(flow)):
            d = _date(xf, i)
            if not d or d not in far or usd_flow is None:
                continue
            gap_usd = (far[d] - usd_flow[i] / 1e6) * 1e6  # 美元
            if abs(gap_usd) < 1_000_000:
                continue
            px = prices.get(d)
            gaps.append({"date": d, "gap_usd": round(gap_usd), "gap_btc": round(gap_usd / px, 1) if px else None})
        if gaps:
            out["uncovered_gap"] = gaps
            out["fill_note"] = ("未覆盖基金（如新发 MSBT）按 Farside 美元缺口 ÷ 当日价折算后补入，"
                                "口径与美元列一致但属估算")
            for key, n in (("flow_btc_1d", 1), ("flow_btc_5d", 5), ("flow_btc_30d", 30)):
                add = sum(g["gap_btc"] or 0 for g in gaps[-n:])
                out[key] = round(out[key] + add, 1)

    # ── 历史极值（全历史，用于与当下对比）──
    def _argmax(seq):
        return max(range(len(seq)), key=lambda i: seq[i]) if seq else None

    def _argmin(seq):
        return min(range(len(seq)), key=lambda i: seq[i]) if seq else None

    hi, lo, hp = _argmax(flow), _argmin(flow), _argmax(holdings)
    w30 = [sum(flow[k:k + 30]) for k in range(max(0, len(flow) - 29))]
    w30i = _argmax(w30)
    cur30 = win(30)
    out["extremes"] = {
        "inflow_peak_btc": {"date": _date(xf, hi), "btc": round(flow[hi], 1)},
        "outflow_peak_btc": {"date": _date(xf, lo), "btc": round(flow[lo], 1)},
        "holdings_peak": {
            "date": _date(xb, hp), "btc": round(holdings[hp], 0),
            "now": round(holdings[-1], 0),
            "now_vs_peak_pct": round(holdings[-1] / holdings[hp] * 100, 2) if holdings[hp] else None,
            "days_since": len(holdings) - 1 - hp,
            "is_now": hp == len(holdings) - 1,
        },
        "flow30d_peak_btc": {
            "start": _date(xf, w30i), "end": _date(xf, w30i + 29),
            "btc": round(w30[w30i], 1), "now_30d": round(cur30, 1),
            "now_pct": round(cur30 / w30[w30i] * 100, 1) if w30 and w30[w30i] else None,
        },
        "history_days": len(flow),
        "history_start": _date(xf, 0),
    }

    # ── ETF 持币占 BTC 流通量的比例（流通量取自 mvrv_history.json 的 supply 字段）──
    mvrv = _load_mvrv()
    supply = {x["date"]: x.get("supply") for x in mvrv if x.get("supply")}
    price_map = {x["date"]: x.get("price") for x in mvrv if x.get("price")}
    share = {}   # date -> 占比 %
    for i in range(len(holdings)):
        d = _date(xb, i)
        s = supply.get(d)
        if d and s and holdings[i]:
            share[d] = holdings[i] / s * 100
    if share:
        dates = sorted(share)
        pk_date = max(dates, key=lambda d: share[d])
        out["supply_share"] = {
            "now": round(share.get(_date(xb, -1)) or share[dates[-1]], 3),
            "date": dates[-1],
            "pct_30d_ago": round(share.get(_date(xb, -31)), 3) if _date(xb, -31) in share else None,
            "pct_90d_ago": round(share.get(_date(xb, -91)), 3) if _date(xb, -91) in share else None,
            "peak": {"date": pk_date, "pct": round(share[pk_date], 3),
                     "price": price_map.get(pk_date), "btc": round(holdings[dates.index(pk_date)], 0)},
            "supply_now": round(supply.get(_date(xb, -1), 0), 0),
        }
    else:
        out["supply_share"] = None

    # 逐日映射（如已补齐则含补齐量）—— 全历史，供 etf_flow_summary.py 按交易日对齐累加/找极值
    gap_by_date = {g["date"]: (g["gap_btc"] or 0.0) for g in (out.get("uncovered_gap") or [])}
    out["daily_map"] = {
        _date(xf, i): round(flow[i] + gap_by_date.get(_date(xf, i), 0.0), 1)
        for i in range(len(flow))
    }
    out["daily_map_filled"] = bool(gap_by_date)
    return out


def main() -> int:
    fill = "--fill-gap" in sys.argv
    try:
        d = fetch_etf_btc(fill_gap=fill)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc), "source": SOURCE}, ensure_ascii=False))
        return 1
    if "--json" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    scope = "（已补齐未覆盖基金）" if fill else f"（{d['funds_covered']} 只基金）"
    print(f"📊 美国现货 ETF 真实 BTC 数量  {d['date']}{scope}")
    print(f"  当日净流入 : {d['flow_btc_1d']:>+10,.1f} 枚")
    print(f"  近5日累计  : {d['flow_btc_5d']:>+10,.1f} 枚")
    print(f"  近30日累计 : {d['flow_btc_30d']:>+10,.1f} 枚")
    print(f"  合计持币   : {d['holdings_btc']:>10,.0f} 枚"
          f"（30日 {d['holdings_btc_30d_change']:+,.0f} | 90日 {d['holdings_btc_90d_change']:+,.0f}）")
    print("  最近几日:")
    for r in d["recent_days"]:
        print(f"    {r['date']}  {r['btc']:>+10,.1f} 枚")
    if d.get("uncovered_gap"):
        print("  未覆盖基金缺口（已折算补入）:")
        for g in d["uncovered_gap"][-4:]:
            print(f"    {g['date']}  ${g['gap_usd']/1e6:,.1f}M ≈ {g['gap_btc']:,.1f} 枚")
    print(f"  来源: {d['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
