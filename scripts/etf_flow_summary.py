#!/usr/bin/env python3
"""
ETF 资金流汇总脚本 — 为每日简报指标④生成带历史维度的输出。

数据源：
- ETF 流量：haturatu/crypto-etf-flow (jsDelivr CDN)，2024/1/11 至今，Total 单位百万美元
- BTC 价格：~/.hermes/data/mvrv_history.json (date + price 字段)

输出：markdown 片段，可直接嵌入日报指标④。

用法：python3 ~/.hermes/scripts/etf_flow_summary.py
"""
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

ETF_CSV_URL = "https://cdn.jsdelivr.net/gh/haturatu/crypto-etf-flow@main/etf_btc.csv"
ETF_PURGE_URL = "https://purge.jsdelivr.net/gh/haturatu/crypto-etf-flow@main/etf_btc.csv"
ETF_CACHE = os.path.expanduser("~/.hermes/data/etf_btc.csv")


def fetch_etf_csv():
    """下载最新 ETF CSV，失败则回退本地缓存。"""
    # 1) 先触发 jsDelivr 缓存刷新
    try:
        urllib.request.urlopen(urllib.request.Request(ETF_PURGE_URL), timeout=8).read()
    except Exception:
        pass
    # 2) 下载
    try:
        req = urllib.request.Request(ETF_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", errors="replace")
        os.makedirs(os.path.dirname(ETF_CACHE), exist_ok=True)
        with open(ETF_CACHE, "w") as f:
            f.write(raw)
        return raw, "jsDelivr(实时)"
    except Exception as e:
        # 回退本地缓存
        if os.path.exists(ETF_CACHE):
            with open(ETF_CACHE) as f:
                raw = f.read()
            return raw, f"本地缓存(下载失败: {str(e)[:50]})"
        print(f"❌ ETF 数据获取失败且无缓存: {e}", file=sys.stderr)
        sys.exit(1)


def parse_etf(raw):
    """解析 CSV，返回按日期升序的 [(date_str_iso, datetime, total_million, info), ...]

    info = {"n": 已填基金数, "core_empty": IBIT/FBTC 单元格是否为空}
    —— 用于识别 Farside「渐进填充」的半成品行（2026-09-24 踩坑，见 main()）
    """
    rows = list(csv.DictReader(io.StringIO(raw)))
    out = []
    for r in rows:
        try:
            d = datetime.strptime(r["Date"].strip(), "%d %b %Y")
            t_str = (r.get("Total") or "").strip()
            if not t_str:
                # Total 空 = Farside 占位行（当日数据尚未发布）。
                # 严禁把空值当 0 上报——9/9 曾把 9/8 占位行当「净流为零」发进日报，
                # 实际当日最终为 -$4660万（Farside 通常在美东收盘后数小时~次日才出最终值）。
                continue
            funds = {k: (v or "").strip() for k, v in r.items() if k not in ("Date", "Total")}
            info = {
                "n": sum(1 for v in funds.values() if v != ""),
                "core_empty": funds.get("IBIT", "") == "" or funds.get("FBTC", "") == "",
            }
            out.append((d.strftime("%Y-%m-%d"), d, float(t_str), info))
        except Exception:
            continue
    out.sort(key=lambda x: x[1])
    return out


def load_btc_prices():
    """从 mvrv_history.json 读 BTC 日价格，返回 {date_iso: price}"""
    path = os.path.expanduser("~/.hermes/data/mvrv_history.json")
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path))
        if isinstance(data, list):
            return {x["date"]: x.get("price") for x in data if x.get("price")}
        return {}
    except Exception:
        return {}


def fmt_money(m):
    """m 单位百万美元，输出中文金额：+$X.XX亿 或 +$XXXX万"""
    usd = m * 1_000_000
    sign = "+" if usd >= 0 else "-"
    a = abs(usd)
    if a >= 100_000_000:  # >= 1亿美元
        return f"{sign}${a/100_000_000:.2f}亿"
    return f"{sign}${a/10_000:.0f}万"


def btc_amount(million, price):
    """USD 百万流量 ÷ 当日 BTC 价 → 折合 BTC 枚（None = 该日无价格）

    说明：这是「按当日价折算的净买入量」，不是交易所披露的持仓增量；
    但口径与市场惯例一致（Farside 的 US$m ÷ 当日价），日报里标注清楚。
    """
    if not price or price <= 0:
        return None
    return million * 1_000_000 / price


def fmt_btc(b, signed: bool = True):
    """BTC 枚 → +8,408枚 / +4.62万枚；None → —

    signed=False 用于**存量**（如合计持币），不显示正号。
    """
    if b is None:
        return "—"
    sign = ("+" if b >= 0 else "-") if signed else ("-" if b < 0 else "")
    a = abs(b)
    if a >= 10_000:
        return f"{sign}{a/10_000:.2f}万枚"
    return f"{sign}{a:,.0f}枚"


def load_etf_btc_real():
    """真实 BTC 数量口径（checkonchain ETF 图表，含未覆盖基金补齐）。

    失败返回 None —— 调用方退化为「USD÷当日价」的折合估算（旧口径）。
    """
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_btc_real.py")
        spec = importlib.util.spec_from_file_location("etf_btc_real_local", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.fetch_etf_btc(days=8, fill_gap=True)
    except Exception:
        return None


# ---------- 美股交易日历（自动解释数据缺口，2026-09-08 新增） ----------

WEEKDAY_CN = "一二三四五六日"


def _nth_weekday(year, month, weekday, n):
    """某月第 n 个 weekday（Mon=0）。例：9 月第 1 个周一 = _nth_weekday(y,9,0,1)"""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    """某月最后一个 weekday（Mon=0）。例：5 月最后周一 = _last_weekday(y,5,0)"""
    last = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year):
    """Anonymous Gregorian Computus：任意年复活节（周日）"""
    a = year % 19
    b, c = year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def us_holidays(year):
    """NYSE 休市日 → {date: 中文名}。固定假日逢周六前移至周五、逢周日后移至周一。"""
    h = {}

    def add(d, name):
        if d.weekday() == 5:      # 周六 → 前移周五
            d -= timedelta(days=1)
        elif d.weekday() == 6:    # 周日 → 后移周一
            d += timedelta(days=1)
        h[d] = name

    add(date(year, 1, 1), "元旦")
    add(_nth_weekday(year, 1, 0, 3), "马丁·路德·金纪念日")
    add(_nth_weekday(year, 2, 0, 3), "总统日")
    add(_easter_sunday(year) - timedelta(days=2), "耶稣受难日")
    add(_last_weekday(year, 5, 0), "阵亡将士纪念日")
    add(date(year, 6, 19), "六月节")
    add(date(year, 7, 4), "独立日")
    add(_nth_weekday(year, 9, 0, 1), "劳动节")
    add(_nth_weekday(year, 11, 3, 4), "感恩节")
    add(date(year, 12, 25), "圣诞节")
    return h


def us_eastern_now():
    """当前美东时间（含夏令时：3 月第 2 个周日 ~ 11 月第 1 个周日为 EDT=UTC-4）"""
    now = datetime.now(timezone.utc)
    dst_start = _nth_weekday(now.year, 3, 6, 2)
    dst_end = _nth_weekday(now.year, 11, 6, 1)
    off = -4 if dst_start <= now.date() < dst_end else -5
    return now + timedelta(hours=off)


def freshness_note(last_d):
    """ETF 末行日期(last_d, date) 对比美股日历，返回醒目 markdown 说明行；数据新鲜/纯周末空窗返回 None。

    触发场景（示例：末行 09/04 周五，美东 09/08 周二凌晨未收盘）→ 输出：
    「截至 09/04(五) = 最近一个美股交易日：09/07(一) 劳动节休市；09/08(二) 美东未收盘…」
    让读者一眼明白不是数据缺失。若存在「交易日该有却无」的日期 → 输出数据源滞后警告。
    """
    et = us_eastern_now()
    today_et = et.date()
    closed = et.hour >= 16  # 美东 16:00 收盘
    hol = {}
    for y in range(today_et.year - 1, today_et.year + 2):
        hol.update(us_holidays(y))

    items = []  # (标签如 09/07(一), 原因, 是否数据源滞后)
    cur = last_d + timedelta(days=1)
    while cur <= today_et:
        tag = f"{cur.month:02d}/{cur.day:02d}(周{WEEKDAY_CN[cur.weekday()]})"
        if cur.weekday() >= 5:
            items.append((tag, "周末休市", False))
        elif cur in hol:
            items.append((tag, f"{hol[cur]}休市", False))
        elif cur == today_et and not closed:
            items.append((tag, "美东未收盘", False))
        else:
            items.append((tag, "Farside源未更新", True))
        cur += timedelta(days=1)
    if not items:
        return None

    stale = [i for i in items if i[2]]
    if stale:
        return (f"> ⚠️ **ETF 数据滞后**：Farside 源最新仅到 {last_d.month:02d}/{last_d.day:02d}"
                f"（周{WEEKDAY_CN[last_d.weekday()]}），其后 {len(stale)} 个交易日数据尚未出现"
                f"（正常应于美东收盘后约 1~2 小时更新），请核对源或稍后重跑。")
    # 无数据源滞后：看是否有「日历类」缺口（假日/未收盘），纯周末空窗不解释
    calendar_events = [i for i in items if i[1] not in ("周末休市",)]
    if not calendar_events:
        return None
    wk = [i for i in items if i[1] == "周末休市"]
    parts = []
    if wk:
        parts.append(f"{wk[0][0]}~{wk[-1][0]} 周末休市" if len(wk) > 1 else f"{wk[0][0]} 周末休市")
    parts += [f"{t} {reason}" for t, reason, _ in calendar_events]
    return (f"> ⚠️ **ETF 数据截至 {last_d.month:02d}/{last_d.day:02d}"
            f"（周{WEEKDAY_CN[last_d.weekday()]}）＝最近一个美股交易日**：{'；'.join(parts)}。"
            f"Farside 仅含美股交易日，表中无缺失，最新一日数据待美东收盘后更新。")


def main():
    raw, src = fetch_etf_csv()
    etf = parse_etf(raw)

    # 双保险：末行 Total 恰为 0.0 → 疑似上游占位（真实全 0 交易日极罕见），剔除并警告，
    # 避免「净流为零」假象污染连续天数/累计值
    dropped = []
    while etf and etf[-1][2] == 0.0:
        dropped.append(etf.pop()[1])
    if dropped:
        tags = "、".join(d.strftime("%m/%d") for d in dropped)
        print(f"> ⚠️ **ETF 最新行（{tags}）Total=0，疑似上游占位未发布**，已剔除、按上一真实交易日计。"
              f"请核对 Farside 源或稍后重跑。")
        print()

    if len(etf) < 5:
        print(f"❌ ETF 数据不足({len(etf)}行)", file=sys.stderr)
        sys.exit(1)

    prices = load_btc_prices()

    # ── BTC 真实口径 + 未固化检测（2026-09-24 踩坑）──────────────────────────────
    # checkonchain 的「持仓余额」序列比「流量」序列晚固化：余额还没推进到最新流量日时，
    # 该日流量是**当日未完成累计**，会被上游大幅修订（实测 09/23 曾为 $3,240万/384枚，
    # 数小时后固化为 $3.47亿/4,108枚，10.7 倍）。检测到即把该日剔出所有累计/极值，
    # 只留一行提示，保证「表内各行之和 == 累计行」永不漂移。
    real = load_etf_btc_real()
    dmap = (real or {}).get("daily_map") or {}
    prov, prov_reason = None, None
    # A. 行未填满：Farside 的行是「渐进填充」——行先出现（可能只有 1 只基金披露、Total=该只的数），
    #    随各基金陆续披露才更新到最终值。实测 09/23 在 13:42 只有 MSBT 填了（Total=$3,240万），
    #    15:30 补齐为 $346.9M（IBIT 166.3 + FBTC 143.2 + ARKB 5.0 + MSBT 32.4）——差 10.7 倍。
    if etf:
        _iso, _d, _tot, _info = etf[-1]
        if _info.get("core_empty") or _info.get("n", 99) <= 3:
            prov = _iso
            prov_reason = (f"Farside 行未填满（仅 {_info.get('n')} 只基金有值，IBIT/FBTC 尚未披露），"
                           f"Total={_tot:g}（百万美元）只是已披露部分之和，仍会大幅上调")
    # B. 独立佐证：checkonchain 持仓余额序列尚未推进到该日（余额图晚于流量图固化）
    if real:
        fd, hd = real.get("date"), real.get("holdings_date")
        if fd and hd and hd < fd:
            if prov is None:
                prov, prov_reason = fd, "checkonchain 持仓序列尚未推进到该日（余额图晚固化）"
            elif prov == fd:
                prov_reason += "；checkonchain 持仓序列亦未推进到该日"
    settled = [r for r in etf if r[0] != prov] if prov else etf
    if prov and len(settled) < 5:
        settled, prov, prov_reason = etf, None, None   # 剥完不足5行就不剥了

    # 最近5个交易日逐日（已固化）
    last5 = settled[-5:]

    # 汇总（只用已固化数据）
    sum_5 = sum(x[2] for x in settled[-5:])
    sum_30 = sum(x[2] for x in settled[-30:])
    sum_prev30 = sum(x[2] for x in settled[-60:-30]) if len(settled) >= 60 else None
    sum_all = sum(x[2] for x in settled)

    # 连续净流入/流出天数（从最新往前，0 视为中断；用已固化数据）
    consecutive = 0
    sign = None
    for _, _, t, _i in reversed(settled):
        if t == 0:
            break
        s = 1 if t > 0 else -1
        if sign is None:
            sign, consecutive = s, 1
        elif s == sign:
            consecutive += 1
        else:
            break
    flow_word = "净流入" if sign == 1 else "净流出"

    # 30交易日区间 BTC 涨跌幅对照
    price_note = ""
    btc_chg = None
    if prices:
        end_date = settled[-1][0]
        start_date = settled[-30][0]
        p_end = prices.get(end_date)
        p_start = prices.get(start_date)
        if p_end and p_start:
            btc_chg = (p_end - p_start) / p_start * 100
            # 判断资金-价格关系
            if sum_30 > 0 and btc_chg < 0:
                relation = "背离（资金流入但价格下跌）"
            elif sum_30 < 0 and btc_chg > 0:
                relation = "背离（资金流出但价格上涨）"
            else:
                relation = "同步"
            price_note = f"{relation}"

    # 输出 markdown（表头前先打数据缺口说明，有则醒目提示，无则安静）
    note = freshness_note(settled[-1][1].date())
    if note:
        print(note)
        print()

    # ── BTC 数量列：真实口径（checkonchain）；real 已在上面加载 ──
    def btc_for_day(iso, million):
        """→ (BTC 值, 是否真实口径)"""
        if iso in dmap:
            return dmap[iso], True
        return btc_amount(million, prices.get(iso)), False

    def btc_window(rows):
        total, missing, any_est = 0.0, 0, False
        for iso, _d, t, _i in rows:
            v, is_real = btc_for_day(iso, t)
            if v is None:
                missing += 1
                continue
            total += v
            any_est = any_est or (not is_real)
        return total, missing, any_est

    btc5, miss5, est5 = btc_window(settled[-5:])
    btc30, miss30, est30 = btc_window(settled[-30:])
    btc_prev30, _miss_p30, est_p30 = btc_window(settled[-60:-30]) if len(settled) >= 60 else (None, 0, False)
    btc_all_fallback, miss_all, _ = btc_window(settled)
    real_all = (real or {}).get("flow_btc_all")
    if real_all is not None and prov:
        # 未固化日不能进「上市以来累计」的 BTC 口径（否则与美元口径不一致）
        real_all = real_all - (dmap.get(prov) or 0)
    btc_all = real_all if real_all is not None else btc_all_fallback

    def cell(v, is_real):
        """真实口径直接给；折算估算加 ≈ 前缀（保持可辨识）"""
        if v is None:
            return "—"
        s = fmt_btc(v)
        return s if is_real else f"≈{s}"

    print("| 日期 | 净流量 | 净流入BTC |")
    print("|------|--------|-----------|")
    for iso, d, t, _info in last5:
        v, is_real = btc_for_day(iso, t)
        print(f"| {d.strftime('%m/%d')} | {fmt_money(t)} | {cell(v, is_real)} |")

    if prov:
        pv = next((r for r in etf if r[0] == prov), None)
        pv_usd = pv[2] if pv else None
        pv_btc = dmap.get(prov)
        print(f"> ⏳ **{prov[5:].replace('-', '/')}（最新交易日）数据尚未固化，未计入任何累计/极值**："
              f"上游当前美元值 {fmt_money(pv_usd) if pv_usd is not None else '—'}"
              f"（枚数与总额均会随披露大幅上调）。原因：{prov_reason}；"
              f"明日日报将补入固化值。")

    print()
    print("| 汇总 | 数值 | BTC 数量 |")
    print("|------|------|----------|")
    print(f"| 近5交易日累计净流入 | **{fmt_money(sum_5)}** | **{cell(btc5, not est5)}** |")
    print(f"| 近30交易日累计净流入 | **{fmt_money(sum_30)}** | **{cell(btc30, not est30)}** |")
    if sum_prev30 is not None:
        print(f"| 前30交易日（对照） | {fmt_money(sum_prev30)} | {cell(btc_prev30, not est_p30)} |")
    print(f"| 上市以来累计净流入 | {fmt_money(sum_all)} | {cell(btc_all, real_all is not None)} |")
    if real:
        hold = real.get("holdings_btc")
        ch30 = real.get("holdings_btc_30d_change")
        ch90 = real.get("holdings_btc_90d_change")
        hd = real.get("holdings_date") or ""
        flow_d = real.get("date") or ""
        hd_short = f"（{hd[5:].replace('-', '/')}）" if hd else ""
        # 持仓余额序列常比流量晚一天发布 —— 行内标日期，避免与流量行混读
        print(f"| ETF 合计持币{hd_short} | — | **{fmt_btc(hold, signed=False)}**"
              f"（30日 {fmt_btc(ch30)}｜90日 {fmt_btc(ch90)}） |")
        if hd and flow_d and hd != flow_d and not prov:
            print(f"> ⚠️ 持仓余额序列比流量晚一天发布：上表**流量截至 {flow_d[5:]}**，"
                  f"**持仓/占流通量截至 {hd[5:]}**（同源 checkonchain，余额图滞后于流量图）。")
        ss = real.get("supply_share") or {}
        if ss.get("now") is not None:
            d30 = ss.get("pct_30d_ago")
            d90 = ss.get("pct_90d_ago")
            tail = "（30日前 {}%｜90日前 {}%）".format(f"{d30:.2f}" if d30 is not None else "—",
                                                       f"{d90:.2f}" if d90 is not None else "—")
            print(f"| ETF 持币占 BTC 流通量 | — | **{ss['now']:.2f}%**{tail} |")
    print(f"| 连续{flow_word}天数 | {consecutive} 天 | — |")
    if btc_chg is not None:
        print(f"| 近30交易日 BTC涨跌 | {btc_chg:+.1f}% | — |")
        print(f"| 资金与价格关系 | {price_note} | — |")

    print()
    if real:
        fill = "，已按 Farside 美元缺口折算补齐未覆盖基金（如新发 MSBT）" if real.get("daily_map_filled") else ""
        print(f"> ℹ️ BTC 数量＝**真实份额/持仓口径**（checkonchain ETF 图表，{real.get('funds_covered')} 只基金{fill}），"
              f"非 USD÷价格折算；带 ≈ 的日子表示缺真实数据、退回折算估算。")
    else:
        print("> ⚠️ BTC 数量为 **USD÷当日价 折算估算**（checkonchain 真实口径抓取失败），非份额数据，谨慎引用。")

    # ── 历史极值对比（USD=Farside 全历史；BTC=checkonchain 全历史）──
    vals = [t for _, _, t, _i in settled]
    ex = (real or {}).get("extremes") or {}
    if vals:
        hi_i = max(range(len(vals)), key=lambda i: vals[i])
        lo_i = min(range(len(vals)), key=lambda i: vals[i])
        w30 = [sum(vals[k:k + 30]) for k in range(max(0, len(vals) - 29))]
        wi = max(range(len(w30)), key=lambda i: w30[i]) if w30 else None
        cur = vals[-1]
        pk_in = ex.get("inflow_peak_btc") or {}
        pk_out = ex.get("outflow_peak_btc") or {}
        hp = ex.get("holdings_peak") or {}
        wb = ex.get("flow30d_peak_btc") or {}

        print()
        print(f"| 历史极值对比（{ex.get('history_start', '')} 以来 {ex.get('history_days', '')} 天） | 时间（当时 BTC 价） | 极值 | 当下对比 |")
        print("|------|------|------|----------|")

        def px(iso):
            """该日 BTC 价（~/.hermes/data/mvrv_history.json），无则 —"""
            p = prices.get(iso)
            return f"${p:,.0f}" if p else "—"

        cur_date = settled[-1][0]
        cell_in = fmt_money(vals[hi_i]) + (f" / {fmt_btc(pk_in.get('btc'))}" if pk_in else "")
        now_in = (f"当日 {fmt_money(cur)}（{px(cur_date)}）= **{cur / vals[hi_i] * 100:.1f}%**"
                  if vals[hi_i] else "—")
        print(f"| 单日净流入峰值 | {settled[hi_i][0]}（{px(settled[hi_i][0])}） | **{cell_in}** | {now_in} |")

        cell_out = fmt_money(vals[lo_i]) + (f" / {fmt_btc(pk_out.get('btc'))}" if pk_out else "")
        if cur < 0:
            now_out = f"当日 {fmt_money(cur)}（{px(cur_date)}）= **{cur / vals[lo_i] * 100:.1f}%**"
        else:
            now_out = f"当日为净流入 {fmt_money(cur)}（{px(cur_date)}）"
        print(f"| 单日净流出峰值 | {settled[lo_i][0]}（{px(settled[lo_i][0])}） | {cell_out} | {now_out} |")

        if wi is not None:
            win_start, win_end = settled[wi][0], settled[wi + 29][0]
            now_30 = (f"近30交易日 {fmt_money(sum_30)}（{px(cur_date)}）= **{sum_30 / w30[wi] * 100:.1f}%**"
                      if w30[wi] else "—")
            print(f"| 30交易日累计流入峰值 | {win_start}~{win_end}"
                  f"（{px(win_start)}→{px(win_end)}） | **{fmt_money(w30[wi])}** | {now_30} |")

        # BTC 口径的 30 交易日窗口峰值：与美元口径**同样按 Farside 交易日对齐**（别用自然日窗口，会算成 ~21 个交易日，两边对不上）
        if dmap:
            bseq = [dmap.get(iso) for iso, _d, _t, _i in settled]
            bw = []
            for k in range(max(0, len(bseq) - 29)):
                seg = [v for v in bseq[k:k + 30] if v is not None]
                bw.append(sum(seg) if len(seg) >= 25 else None)
            if any(v is not None for v in bw):
                bwi = max(range(len(bw)), key=lambda i: (bw[i] if bw[i] is not None else -1e18))
                if bw[bwi] is not None:
                    b_start, b_end = settled[bwi][0], settled[bwi + 29][0]
                    now_b = (f"近30交易日 {fmt_btc(btc30)}（{px(cur_date)}）= **{btc30 / bw[bwi] * 100:.1f}%**"
                             if bw[bwi] else "—")
                    print(f"| 30交易日累计流入峰值（BTC） | {b_start}~{b_end}"
                          f"（{px(b_start)}→{px(b_end)}） | **{fmt_btc(bw[bwi])}** | {now_b} |")

        if hp:
            if hp.get("is_now"):
                now_hp = "当前即历史最高 ✅"
            else:
                gap = (hp.get("now") or 0) - (hp.get("btc") or 0)
                hd2 = real.get("holdings_date") or ""
                tag = f"{hd2[5:]}，" if hd2 else ""
                now_hp = (f"现在 {fmt_btc(hp.get('now'), signed=False)}（{tag}{px(hd2)}）"
                          f" = **{hp.get('now_vs_peak_pct')}%**（距峰值 {fmt_btc(gap)}）")
            print(f"| ETF 合计持币峰值 | {hp.get('date')}（{px(hp.get('date'))}） | "
                  f"**{fmt_btc(hp.get('btc'), signed=False)}** | {now_hp} |")

        # ETF 持币占流通量峰值（占流通量比例的历史最高，含当时币价）
        ss = (real or {}).get("supply_share") or {}
        sspk = ss.get("peak") or {}
        if sspk.get("pct") is not None and ss.get("now") is not None:
            pkp = f"${sspk.get('price'):,.0f}" if sspk.get("price") else "—"
            ratio = f"{ss['now'] / sspk['pct'] * 100:.1f}%" if sspk.get("pct") else "—"
            print(f"| ETF 持币占流通量峰值 | {sspk.get('date')}（{pkp}） | "
                  f"**{sspk['pct']:.2f}%** | 现在 {ss['now']:.2f}%（= 峰值的 {ratio}） |")
        print(f"> 极值表「当下」＝最近**已固化**交易日（{settled[-1][0][5:].replace('-', '/')}）；未固化日不计入任何累计与极值。")

    if sum_prev30 is not None:
        if sum_30 > 0 and sum_prev30 < 0:
            momo = "由流出转流入（拐点）"
        elif sum_30 < 0 and sum_prev30 > 0:
            momo = "由流入转流出（拐点）"
        elif sum_30 > sum_prev30:
            momo = "流入加速" if sum_30 > 0 else "流出减缓"
        else:
            momo = "流入减速" if sum_30 > 0 else "流出加速"
        print(f"> 资金流环比：近30日({fmt_money(sum_30)}) vs 前30日({fmt_money(sum_prev30)})，{momo}")

    print()
    print(f"> 数据源：Farside(jsDelivr) 🥈 | 数据截至 {settled[-1][0]} | {src}")


if __name__ == "__main__":
    main()
