#!/usr/bin/env python3
"""STH / LTH Supply（短期·长期持币量）—— checkonchain.com 免费图表自动抓取（零依赖）。

背景：STH Supply（Short Term Holder Supply，短期持币量）长期只有
bitcoinmagazinepro.com（现 LookIntoBitcoin）提供，其 Dash 数据接口返回 500，
无法自动化，只能每周人工查看。2026-09-18 发现 **charts.checkonchain.com**
（Checkmate 的免费公开图表库）有同口径数据且可直接解析：

    页面: https://charts.checkonchain.com/btconchain/supply/breakdown_lthsth_0/
    数据: https://charts-cdn.checkonchain.com/.../breakdown_lthsth_0_light.html
          自包含 Plotly HTML，y 轴为 base64(float64) 编码，全部离线可解析。

口径：STH = 持币年龄 < 155 天的币（与 Glassnode / LookIntoBitcoin 同阈值）。
校验：2026-09-18 STH 3,523,888 + LTH 16,561,979 = 20,085,867 BTC，与当前
      比特币流通量（约 2000 万枚）吻合 ✅

用法:
    python3 sth_supply.py            # 人类可读输出
    python3 sth_supply.py --json     # JSON（供 fetch_all.py / 日报调用）
"""
from __future__ import annotations

import base64
import gzip
import json
import struct
import sys
import urllib.request
from datetime import datetime, timezone

CHART_URL = ("https://charts-cdn.checkonchain.com/btconchain/supply/"
             "breakdown_lthsth_0/breakdown_lthsth_0_light.html")
SOURCE_PAGE = ("https://charts.checkonchain.com/btconchain/supply/"
               "breakdown_lthsth_0/breakdown_lthsth_0_light.html")
SOURCE_NAME = "checkonchain.com (Checkmate, 免费图表)"
TIMEOUT = 45


def fetch_html(timeout: int = TIMEOUT) -> str:
    """抓图表 HTML（gzip 传输，约 300KB / 2~3 秒；不压缩约 1.9MB / 18 秒）。"""
    req = urllib.request.Request(CHART_URL, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _extract_traces(html: str) -> list:
    """从 Plotly.newPlot(...) 的 HTML 里取出 traces 数组（括号配对，避免正则脆断）。"""
    i = html.find("newPlot")
    if i < 0:
        raise RuntimeError("页面里没有 Plotly.newPlot —— 结构可能变了")
    start = html.find("[", i)
    depth = 0
    instr = False
    esc = False
    for j in range(start, len(html)):
        ch = html[j]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        else:
            if ch == '"':
                instr = True
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth == 0:
                    return json.loads(html[start:j + 1])
    raise RuntimeError("traces 数组括号不闭合")


def _decode(v):
    """Plotly 3.x 的列式编码：{"dtype":"f8","bdata":"<base64>"} → python list。"""
    if isinstance(v, list):
        return v
    if isinstance(v, dict) and "bdata" in v:
        fmts = {"f8": ("d", 8), "f4": ("f", 4), "i8": ("q", 8), "i4": ("i", 4)}
        dtype = v.get("dtype", "")
        if dtype not in fmts:
            raise RuntimeError(f"未知 dtype: {dtype}")
        fmt, size = fmts[dtype]
        b = base64.b64decode(v["bdata"])
        n = len(b) // size
        return list(struct.unpack("<%d%s" % (n, fmt), b[:n * size]))
    return []


def _series(traces: list, name: str) -> tuple[list, list]:
    for t in traces:
        if t.get("name") == name:
            return _decode(t.get("x")), _decode(t.get("y"))
    raise RuntimeError(f"图表里没有序列: {name}")


def _pct(cur: float, old: float) -> float | None:
    if not old:
        return None
    return round((cur - old) / old * 100, 2)


def fetch_sth_supply() -> dict:
    """返回 STH/LTH Supply 最新值 + 7日/30日变化（供 fetch_all.py 调用）。

    ⚠️ 未完整点防护（2026-09-24）：checkonchain 供给图会在**当日中途**追加「今天」的点，
    该点是当日尚未走完的瞬时值，会被后续修订，且与日报其它「上一完整日」口径不一致
    （实测同日 STH 30日变化 +1.94% vs 上一完整日 +2.54%）。故若最后一点日期 == 今日(UTC)，
    回退到上一个完整日，并在输出里标 dropped_partial_point 供编译时说明。
    """
    traces = _extract_traces(fetch_html())
    xs, sth = _series(traces, "STH Total")
    _, lth = _series(traces, "LTH Total")
    _, price = _series(traces, "Price")

    if not sth:
        raise RuntimeError("STH 序列为空")

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dropped = None
    if str(xs[-1])[:10] >= today_utc and len(sth) >= 2:
        dropped = str(xs[-1])[:10]
        xs, sth, lth = xs[:-1], sth[:-1], lth[:-1]
        if price and len(price) > len(sth):
            price = price[:-1]

    latest_date = str(xs[-1])[:10]
    out = {
        "sth_supply": round(sth[-1], 2),
        "sth_date": latest_date,
        "sth_7d_change_pct": _pct(sth[-1], sth[-8]) if len(sth) > 7 else None,
        "sth_30d_change_pct": _pct(sth[-1], sth[-31]) if len(sth) > 30 else None,
        "lth_supply": round(lth[-1], 2),
        "lth_30d_change_pct": _pct(lth[-1], lth[-31]) if len(lth) > 30 else None,
        "supply_total": round(sth[-1] + lth[-1], 2),
        "sth_pct_of_total": round(sth[-1] / (sth[-1] + lth[-1]) * 100, 2) if (sth[-1] + lth[-1]) else None,
        "price": round(price[-1], 2) if price else None,
        "dropped_partial_point": dropped,
        "source": SOURCE_NAME,
        "source_url": SOURCE_PAGE,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return out


def main() -> int:
    try:
        d = fetch_sth_supply()
    except Exception as exc:
        print(json.dumps({"error": str(exc), "source": SOURCE_NAME}, ensure_ascii=False))
        return 1
    if "--json" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(f"📊 STH / LTH Supply（短期·长期持币量）  {d['sth_date']}")
    print(f"  短期持币量 STH Supply : {d['sth_supply']:,.0f} BTC"
          f"  (7日 {d['sth_7d_change_pct']:+.2f}% | 30日 {d['sth_30d_change_pct']:+.2f}%)")
    print(f"  长期持币量 LTH Supply : {d['lth_supply']:,.0f} BTC"
          f"  (30日 {d['lth_30d_change_pct']:+.2f}%)")
    print(f"  合计（≈流通量）      : {d['supply_total']:,.0f} BTC"
          f" | STH 占比 {d['sth_pct_of_total']}%")
    print(f"  价格: ${d['price']:,.0f}   来源: {d['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
