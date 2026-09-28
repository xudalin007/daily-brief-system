#!/usr/bin/env python3
"""抓美股科技龙头财报（财报季用）。搜狗搜中文财经报道，输出结构化 markdown。

用法:
    python3 fetch_earnings.py                 # 默认英伟达/微软/谷歌
    python3 fetch_earnings.py 英伟达 AMD      # 指定公司

⚠️ 搜狗单次会话约限 1~2 次有效查询（后续触发 anti-spider），
   多公司请优先排最关键的（英伟达），或分多个会话/子代理分摊。
"""
import urllib.request
import urllib.parse
import re
import html
import ssl
import sys
import time

ctx = ssl.create_default_context()
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode('utf-8', 'ignore')


def strip_html(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    return html.unescape(re.sub(r'\s+', ' ', s)).strip()


def search_earnings(company):
    """搜狗搜「<公司> 财报」，返回 {company,title,summary} 或 None"""
    q = urllib.parse.quote(f'{company} 财报 营收 同比 指引')
    try:
        data = fetch(f'https://www.sogou.com/web?query={q}')
    except Exception as e:
        return {'company': company, 'title': None, 'summary': f'[抓取异常] {e}'}

    if '验证码' in data or 'antispider' in data.lower():
        return {'company': company, 'title': None, 'summary': '[被搜狗反爬拦截]'}

    titles = re.findall(r'vr-title">(.*?)</a>', data)
    summaries = re.findall(r'cacheresult_summary[^>]*>(.*?)</div>', data)
    if not titles:
        return {'company': company, 'title': None, 'summary': '[无搜索结果]'}

    return {
        'company': company,
        'title': strip_html(titles[0]),
        'summary': strip_html(summaries[0])[:250] if summaries else '',
    }


def main():
    companies = sys.argv[1:] if len(sys.argv) > 1 else ['英伟达', '微软', '谷歌']
    print('## 美股科技龙头财报（搜狗搜索，供财报季参考）')
    for comp in companies:
        r = search_earnings(comp)
        if r and r.get('title'):
            print(f"\n- **{comp}**：{r['title']}")
            if r.get('summary') and not r['summary'].startswith('['):
                print(f"  {r['summary']}")
        else:
            note = r.get('summary', '') if r else ''
            print(f"\n- **{comp}**：（抓取失败{(' - ' + note) if note else ''}，请手动查）")
        time.sleep(1)  # 避免触发反爬


if __name__ == '__main__':
    main()
