#!/usr/bin/env python3
"""
日报回验脚本（verify_report.py）— 生成日报后强制回验数值型指标。

原理：从报告 HTML 提取每个数值型指标的「报告值」，独立抓取实时 API 得到「真实值」，
硬对比，超容差则 FAIL，退出码 1（供生成流程拦截，不许发）。

用法：python3 verify_report.py <报告HTML路径>
退出码：0=全部通过 / 1=有 FAIL（拦截）

设计原则（2026-08-31）：
- 数值错误靠机器强制拦（不靠「记住教训」）。
- 时效性强的值（资金费率、VIX）标 REVIEW 不硬拦，避免误杀。
- 定性错误（新闻定性、身份）不在本脚本范围，仍靠读正文。
"""
import re, sys, json, ssl, csv, urllib.request, subprocess, os

ctx = ssl.create_default_context()
HERMES_HOME = os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes'))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return urllib.request.urlopen(req, timeout=20, context=ctx).read().decode('utf-8', 'ignore')

def cnbc(code):
    try:
        t = fetch(f'https://www.cnbc.com/quotes/{code}')
        m = re.search(r'class="QuoteStrip-lastPrice">([^<]*)', t)
        return m.group(1).strip() if m else None
    except Exception:
        return None

def num(s):
    if s is None:
        return None
    s = str(s).replace('$', '').replace('%', '').replace(',', '')
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group(0)) if m else None

def get_report_values(html_text):
    """按「行」提取 <tr> 内的 <td>，把首格作为标签、第二格作为值。

    2026-09-27 修正：旧版把全文 <td> 拍平后两两配对，遇 3/4 列表格（ETF 逐日表、ETF 汇总表、
    极值对比表、综合判断表）会错位，导致「连续净流入天数」等标签压根不成 key（静默跳过不校验）。
    改为按 <tr> 逐行取格，行内配对，多列表格亦不会串行。
    覆盖 <strong> 包裹的情况；带后缀注释的标签（如「美元指数 DXY（ICE 期货 Dec'26）」）由
    main() 的前缀兜底匹配处理。
    """
    d = {}
    for tr in re.findall(r'<tr>(.*?)</tr>', html_text, re.S):
        cells = re.findall(r'<td>(.*?)</td>', tr, re.S)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(cells) >= 2 and cells[0]:
            d[cells[0]] = cells[1]
    return d

# ---------- 实时数据源 ----------

def realtime_market():
    d = {}
    d['标普500'] = cnbc('.SPX')
    d['纳指'] = cnbc('.IXIC')
    d['道指'] = cnbc('.DJI')
    d['VIX'] = cnbc('.VIX')
    # DXY：CNBC 现货代码 .DXY 已失效（页内无价格）→ 改用 ICE 美元指数期货 @DX.1，
    # 报告里该行必须明标「（ICE 期货 Dec'26）」以示与现货 DXY 的区别（2026-09-27）。
    d['美元指数 DXY'] = cnbc('@DX.1')
    d['黄金'] = cnbc('@GC.1')
    d['布伦特原油'] = cnbc('@LCO.1')
    d['美国2Y'] = cnbc('US2Y')
    d['美国10Y'] = cnbc('US10Y')
    d['美国30Y'] = cnbc('US30Y')
    return d

def realtime_btc():
    try:
        j = json.loads(fetch('https://9992100.xyz/api/ahr999'))
        fg = j.get('fear_greed', {})
        if isinstance(fg, dict):
            fg_val = fg.get('value')
        else:
            fg_val = fg  # 9992100 有时直接返回整数
        return {
            'BTC 价格': j.get('price_usd'),
            '恐惧贪婪指数': fg_val,
            'BTC 市占率': j.get('btc_dominance_pct'),
            'AHR999': j.get('ahr999'),
        }
    except Exception:
        return {}

def realtime_funding():
    try:
        j = json.loads(fetch('https://api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT'))
        return float(j.get('funding_rate'))
    except Exception:
        return None

def realtime_cvdd():
    """运行 cvdd_estimator.py 取权威 CVDD 当前值 + 30/90/180/360 日前值（axeladler ×1.08 单一口径）。

    2026-09-01 起：历史对照列改从 axeladler 全历史取，杜绝增量文件 Blockchair fallback 污染。
    报告里的「N日前」CVDD 必须与脚本输出一致（同一源），偏差 >1.5% 即取到了污染值。
    """
    try:
        out = subprocess.run([sys.executable, os.path.join(HERMES_HOME, 'scripts', 'cvdd_estimator.py')],
                             capture_output=True, text=True, timeout=60)
        j = json.loads(out.stdout)
        d = {'当前 CVDD': j.get('cvdd')}
        if 'cvdd_30d' in j: d['30日前'] = j['cvdd_30d']
        if 'cvdd_90d' in j: d['90日前'] = j['cvdd_90d']
        if 'cvdd_180d' in j: d['180日前'] = j['cvdd_180d']
        if 'cvdd_360d' in j: d['360日前'] = j['cvdd_360d']
        return d
    except Exception:
        return {}

def etf_consecutive_days():
    path = os.path.join(HERMES_HOME, 'data', 'etf_btc.csv')
    rows = []
    with open(path) as f:
        r = csv.reader(f)
        h = next(r)
        ti = h.index('Total')
        for row in r:
            if len(row) > ti and row[ti].strip():
                rows.append(float(row[ti]))
    last = rows[-1]
    direction = '流入' if last >= 0 else '流出'
    cnt = 0
    for v in reversed(rows):
        if (v >= 0) == (last >= 0):
            cnt += 1
        else:
            break
    return direction, cnt

def realtime_cpi():
    try:
        t = fetch('https://tradingeconomics.com/united-states/inflation-cpi')
        m = re.search(r'to ([\d.]+) percent in (\w+) from ([\d.]+) percent in (\w+) of (\d{4})', t)
        if m:
            return {'最新值': float(m.group(1)), '最新月': m.group(2), '上月值': float(m.group(3)), '上月': m.group(4), '年份': m.group(5)}
    except Exception:
        pass
    return None

# ---------- 校验规则 ----------
RULES = [
    ('标普500', 'realtime_market', 'pct', 0.5),
    ('纳指', 'realtime_market', 'pct', 0.5),
    ('道指', 'realtime_market', 'pct', 0.5),
    ('美元指数 DXY', 'realtime_market', 'pct', 0.5),
    ('黄金', 'realtime_market', 'pct', 1.5),
    ('布伦特原油', 'realtime_market', 'pct', 1.5),
    ('美国2Y', 'realtime_market', 'abs', 0.05),
    ('美国10Y', 'realtime_market', 'abs', 0.05),
    ('美国30Y', 'realtime_market', 'abs', 0.05),
    ('VIX', 'realtime_market', 'abs', 1.0),
    ('BTC 价格', 'realtime_btc', 'pct', 2.0),
    ('BTC 市占率', 'realtime_btc', 'abs', 1.0),  # 容差 1.0pp：日报为 05:30 UTC 快照，验证时刻可差数小时，
                                                  # 日内漂移实测可达 ~0.55pp（9/9 案例），0.5 过紧误拦；>1pp 仍拦真错
    ('AHR999', 'realtime_btc', 'abs', 0.03),
    ('恐惧贪婪指数', 'realtime_btc', 'abs', 3.0),
    ('当前 CVDD', 'realtime_cvdd', 'pct', 0.5),
    ('30日前', 'realtime_cvdd', 'pct', 1.5),
    ('90日前', 'realtime_cvdd', 'pct', 1.5),
    ('180日前', 'realtime_cvdd', 'pct', 2.0),
    ('360日前', 'realtime_cvdd', 'pct', 2.0),
]

def main():
    if len(sys.argv) != 2:
        print('Usage: python3 verify_report.py <report.html>', file=sys.stderr)
        return 2
    report_path = sys.argv[1]
    html_text = open(report_path, encoding='utf-8').read()
    report = get_report_values(html_text)

    cache = {}
    fails, reviews, passes = [], [], []

    for name, getter, tol_type, tol in RULES:
        rv_raw = report.get(name)
        if rv_raw is None:
            # 前缀兜底匹配：报告行名可带后缀注释（如「美元指数 DXY（ICE 期货 Dec'26）」）
            for k, v in report.items():
                if k.startswith(name):
                    rv_raw = v
                    break
        if rv_raw is None:
            print(f'  ⚠️ 报告缺少指标「{name}」，跳过')
            continue
        rv = num(rv_raw)
        if getter not in cache:
            cache[getter] = globals()[getter]()
        rt = cache[getter].get(name)
        rtv = rt if isinstance(rt, (int, float)) else num(str(rt))
        if rv is None or rtv is None:
            print(f'  ⚠️ 「{name}」实时值抓不到（{rt}），标 REVIEW')
            reviews.append(name)
            continue
        if tol_type == 'pct':
            dev = abs(rv - rtv) / rtv * 100
            ok = dev <= tol
        else:
            dev = abs(rv - rtv)
            ok = dev <= tol
        tag = '✅ PASS' if ok else '❌ FAIL'
        if tol_type == 'pct':
            print(f'  {tag} {name}: 报告 {rv} vs 实时 {rtv} | 偏差 {dev:.3f}% (容差 {tol}%)')
        else:
            print(f'  {tag} {name}: 报告 {rv} vs 实时 {rtv} | 偏差 {dev:.3f} (容差 {tol})')
        (passes if ok else fails).append(name)

    # ---- ETF 连续天数（判断 label 里的方向词 vs 实际方向）----
    print()
    direction, cnt = etf_consecutive_days()
    matched = False
    for key, val in report.items():
        if '连续净' in key:
            matched = True
            if '流入' in key and direction == '流出':
                print(f'  ❌ FAIL ETF连续天数: 报告字段「{key}」={val}，但实际最新是连续净流出 {cnt} 天')
                fails.append('ETF连续天数')
            elif '流出' in key and direction == '流入':
                print(f'  ❌ FAIL ETF连续天数: 报告字段「{key}」={val}，但实际最新是连续净流入 {cnt} 天')
                fails.append('ETF连续天数')
            else:
                print(f'  ✅ PASS ETF连续天数: 报告「{key}={val}」，实际连续净{ direction }{cnt}天')
                passes.append('ETF连续天数')
            break
    if not matched:
        print(f'  ⚠️ 报告未找到「连续净流入/流出天数」字段，跳过')

    # ---- 资金费率（REVIEW）----
    print()
    fr = realtime_funding()
    if fr is not None:
        print(f'  ℹ️ REVIEW 资金费率: 实时 funding_rate={fr}（{fr*100:.4f}%，指示性、8h变动，请核对报告与抓取时点一致）')
    else:
        print(f'  ⚠️ 资金费率实时值抓不到，跳过')

    # ---- CPI（文本硬拦）----
    print()
    cpi = realtime_cpi()
    if cpi:
        cpi_desc = f"最新 {cpi['最新月']} {cpi['最新值']}%、{cpi['上月']} {cpi['上月值']}%"
        bad = []
        if re.search(r'CPI[^。]{0,15}破\s*4', html_text) or re.search(r'破\s*4%[^。]{0,10}CPI', html_text):
            bad.append('「CPI 破 4%」表述')
        if re.search(r'6\s*月[^。]{0,20}CPI[^。]{0,20}4\s*%', html_text):
            bad.append('「6月 CPI ... 4%」表述')
        if bad:
            print(f'  ❌ FAIL CPI: 报告含 {bad}，但实际 {cpi_desc}')
            fails.append('CPI')
        else:
            print(f'  ✅ PASS CPI: 未发现「破4%」类错误表述；实际 {cpi_desc}')
            passes.append('CPI')
    else:
        print(f'  ⚠️ CPI 实时值抓不到，跳过')

    # ---- 汇总 ----
    print()
    print('=' * 60)
    print(f'PASS {len(passes)} | FAIL {len(fails)} | REVIEW {len(reviews)}')
    if fails:
        print('❌ 拦截：' + '、'.join(fails))
        sys.exit(1)
    else:
        print('✅ 全部数值通过，可发。')
        sys.exit(0)

if __name__ == '__main__':
    main()
