#!/usr/bin/env python3
"""
日报新闻抓取脚本 —— 固化稳定新闻源的抓取，替代子任务现场写 curl 管道。
避免触发 tirith "管道到解释器" 审批（那是日报卡顿的根因）。

用法：
    python3 fetch_news.py              # 抓全部（crypto + ai + macro）
    python3 fetch_news.py crypto       # 只抓加密
    python3 fetch_news.py ai           # 只抓 AI/科技
    python3 fetch_news.py macro        # 只抓宏观/要闻

输出：结构化 markdown 到 stdout，供日报 LLM 读取后写简报。
依赖：仅 Python 标准库。

已验证源（2026-08-21）：
    BlockBeats 快讯        —— 解析服务端渲染的 HTML（中文，带正文+时间）
    Cointelegraph RSS      —— 正则解析 <item>（英文，需日报 LLM 翻译）
    arxiv cs.AI RSS        —— 正则解析 <item>（英文，需翻译）
    央视新闻 JSONP 接口     —— 官方媒体实时接口（中文，标题+摘要+时间+URL，无反爬）

注意：搜狗对宏观词返回验证码；新华网/人民网/新浪 RSS 是废弃旧 feed
    （pubDate 停在 2018~2025）。这些已弃用，宏观/要闻改用央视新闻接口。
"""
import urllib.request
import re
import html
import json
import ssl
import sys

ctx = ssl.create_default_context()
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode('utf-8', 'ignore')


def strip_html(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    return html.unescape(re.sub(r'\s+', ' ', s)).strip()


# ============ 加密新闻 ============
def blockbeats(limit=12):
    """BlockBeats 中文快讯。返回 [{time,title,content}]"""
    data = fetch('https://www.theblockbeats.info/newsflash')
    items = []
    for section in re.split(r'<div class="news-flash-item-top"[^>]*>', data)[1:]:
        title = re.search(r'<div class="news-flash-title-text[^>]*>(.*?)</div>', section, re.S)
        content = re.search(r'<div class="news-flash-item-content"[^>]*>(.*?)</div>', section, re.S)
        clock = re.search(r'class="news-flash-title"[^>]*>\s*(\d{1,2}:\d{2})', section, re.S)
        if title:
            items.append({
                'time': clock.group(1) if clock else '',
                'title': strip_html(title.group(1)),
                'content': strip_html(content.group(1))[:180] if content else '',
            })
        if len(items) >= limit:
            break
    return items[:limit]


def cointelegraph(limit=15):
    """Cointelegraph RSS（英文，需日报 LLM 翻译）。返回 [{date,title}]"""
    data = fetch('https://cointelegraph.com/rss')
    items = []
    for it in re.findall(r'<item>(.*?)</item>', data, re.S):
        t = re.search(r'<title>(.*?)</title>', it, re.S)
        d = re.search(r'<pubDate>(.*?)</pubDate>', it, re.S)
        if t:
            items.append({
                'date': d.group(1)[:16] if d else '?',
                'title': html.unescape(t.group(1)).strip(),
            })
    return items[:limit]


# ============ AI / 科技 ============
def arxiv_ai(limit=12):
    """arxiv cs.AI RSS（英文，需翻译）。返回 [title]"""
    data = fetch('https://export.arxiv.org/rss/cs.AI')
    items = []
    for it in re.findall(r'<item>(.*?)</item>', data, re.S):
        t = re.search(r'<title>(.*?)</title>', it, re.S)
        if t:
            items.append(html.unescape(t.group(1)).strip())
    return items[:limit]


# ============ 宏观 / 要闻（央视新闻 JSONP 接口） ============
# 栏目：1=要闻(含宏观政策) 2=时政经济 3=科技/社会 4=医疗 5=时政 6=时政国际 7=生活
def cctv(channel=1, limit=15):
    """央视新闻实时 JSONP 接口。返回 [{time,title,brief,url}]"""
    url = ('https://news.cctv.com/2019/07/gaiban/cmsdatainterface/page/'
           f'news_{channel}.jsonp')
    data = fetch(url)
    d = json.loads(data[data.find('(') + 1:data.rfind(')')])
    items = []
    for it in d.get('data', {}).get('list', []):
        items.append({
            'time': (it.get('focus_date') or '')[:16],
            'title': it.get('title', ''),
            'brief': (it.get('brief') or '')[:150],
            'url': it.get('url', ''),
        })
    return items[:limit]


# ============ 汇总输出 ============
def run(scope):
    if scope in ('crypto', 'all'):
        print('## 加密新闻（BlockBeats 中文快讯）')
        try:
            bb = blockbeats()
            for it in bb:
                print(f"- [{it['time']}] {it['title']}")
                if it['content']:
                    print(f"  {it['content']}")
            if not bb:
                print("  (无数据)")
        except Exception as e:
            print(f"  [BlockBeats 抓取失败] {e}")

        print('\n## 加密新闻（Cointelegraph 英文 RSS，供交叉验证/翻译）')
        try:
            for it in cointelegraph():
                print(f"- {it['date']} | {it['title']}")
        except Exception as e:
            print(f"  [Cointelegraph 抓取失败] {e}")

    if scope in ('ai', 'all'):
        print('\n## AI/科技（arxiv cs.AI 英文，供翻译）')
        try:
            for t in arxiv_ai():
                print(f"- {t}")
        except Exception as e:
            print(f"  [arxiv 抓取失败] {e}")

    if scope in ('macro', 'all'):
        print('\n## 宏观/要闻（央视新闻，中文，官方一手源）')
        for ch, name in [(1, '要闻（含宏观政策）'), (2, '时政经济'), (3, '科技/社会')]:
            print(f'\n### 央视·{name}')
            try:
                for it in cctv(ch):
                    print(f"- [{it['time']}] {it['title']}")
                    if it['brief']:
                        print(f"  {it['brief']}")
            except Exception as e:
                print(f"  [央视{name}抓取失败] {e}")


if __name__ == '__main__':
    scope = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if scope not in ('all', 'crypto', 'ai', 'macro'):
        print(f'未知参数: {scope}（可选 all/crypto/ai/macro）', file=sys.stderr)
        sys.exit(1)
    run(scope)
