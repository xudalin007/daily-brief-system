#!/usr/bin/env python3
"""Minimal markdown-to-HTML converter. No dependencies."""
import re, sys

def convert(text):
    lines = text.split('\n')
    out = []
    in_table = False
    in_code = False
    in_list = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # Code block
        if line.startswith('```'):
            if in_code:
                out.append('</code></pre>')
                in_code = False
            else:
                out.append('<pre><code>')
                in_code = True
            i += 1
            continue

        if in_code:
            out.append(escape_html(line))
            i += 1
            continue

        # Horizontal rule
        if line.strip() == '---':
            if in_table:
                out.append('</tbody></table>')
                in_table = False
            if in_list:
                out.append('</ul>' if in_list != 'ol' else '</ol>')
                in_list = False
            out.append('<hr>')
            i += 1
            continue

        # Headers
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            if in_table:
                out.append('</tbody></table>')
                in_table = False
            if in_list:
                out.append('</ul>' if in_list != 'ol' else '</ol>')
                in_list = False
            level = len(m.group(1))
            text = inline(m.group(2))
            out.append(f'<h{level}>{text}</h{level}>')
            i += 1
            continue

        # Table rows
        if '|' in line and line.strip().startswith('|'):
            if not in_table:
                out.append('<table><thead>')
                in_table = 'header'

            cells = [c.strip() for c in line.strip().split('|')[1:-1]]

            # Check if separator row
            if all(re.match(r'^[-:]+$', c) for c in cells):
                out.append('</thead><tbody>')
                in_table = 'body'
            elif in_table == 'header':
                out.append('<tr>' + ''.join(f'<th>{inline(c)}</th>' for c in cells) + '</tr>')
            else:
                out.append('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in cells) + '</tr>')
            i += 1
            continue

        # Blockquote
        if line.startswith('>'):
            if in_table:
                out.append('</tbody></table>')
                in_table = False
            if in_list:
                out.append('</ul>' if in_list != 'ol' else '</ol>')
                in_list = False
            text = inline(re.sub(r'^>\s*', '', line))
            out.append(f'<blockquote>{text}</blockquote>')
            i += 1
            continue

        # Unordered list
        if re.match(r'^-\s+', line):
            if in_table:
                out.append('</tbody></table>')
                in_table = False
            if not in_list:
                out.append('<ul>')
                in_list = 'ul'
            elif in_list != 'ul':
                out.append('</ol><ul>')
                in_list = 'ul'
            text = inline(re.sub(r'^-\s+', '', line))
            out.append(f'<li>{text}</li>')
            i += 1
            continue

        # Ordered list
        m = re.match(r'^(\d+)\.\s+', line)
        if m:
            if in_table:
                out.append('</tbody></table>')
                in_table = False
            if not in_list:
                out.append('<ol>')
                in_list = 'ol'
            elif in_list != 'ol':
                out.append('</ul><ol>')
                in_list = 'ol'
            text = inline(line[m.end():])
            out.append(f'<li>{text}</li>')
            i += 1
            continue

        # Blank line: keep any open list intact (a blank line between numbered
        # items must NOT break the <ol>, otherwise each item becomes its own
        # <ol> and numbering restarts at 1). But a blank line DOES end a table
        # (markdown tables are contiguous; a blank line between two adjacent
        # tables must close the first one, otherwise the second table's header
        # row is swallowed as a body row and its separator row re-emits
        # </thead><tbody>, breaking tag balance).
        if not line.strip():
            if in_table:
                out.append('</tbody></table>')
                in_table = False
            i += 1
            continue

        # Regular paragraph (non-empty, non-list line)
        if in_table:
            out.append('</tbody></table>')
            in_table = False
        if in_list:
            out.append('</ul>' if in_list != 'ol' else '</ol>')
            in_list = False

        text = inline(line)
        out.append(f'<p>{text}</p>')
        i += 1

    if in_table:
        out.append('</tbody></table>')
    if in_list:
        out.append('</ul>' if in_list != 'ol' else '</ol>')

    return '\n'.join(out)

def validate_html(body):
    """Sanity-check that table/list tags are balanced. Returns list of problems."""
    problems = []
    for tag in ('table', 'thead', 'tbody', 'tr', 'ul', 'ol'):
        opens = len(re.findall(rf'<{tag}\b', body))
        closes = len(re.findall(rf'</{tag}>', body))
        if opens != closes:
            problems.append(f'<{tag}>: {opens} open vs {closes} close')
    return problems

def inline(text):
    # 先转义 HTML 特殊字符，防止 "<-2.0" ">5" "≤1.05" 这类区间符号
    # 被浏览器当成标签吞掉内容（2026-08-31 SSR 段"落在「<-2.0 极端低位」"被吞根因）
    text = escape_html(text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Emoji stay as-is
    return text

def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

css = '''
body { font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; color: #333; line-height: 1.6; background: #fff; }
h1 { font-size: 1.6em; border-bottom: 2px solid #f0ad4e; padding-bottom: 8px; }
h2 { font-size: 1.3em; margin-top: 24px; color: #444; }
h3 { font-size: 1.1em; color: #555; }
h4 { font-size: 1em; color: #666; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 0.9em; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
th { background: #f5f5f5; font-weight: 600; }
tr:nth-child(even) { background: #fafafa; }
blockquote { border-left: 4px solid #f0ad4e; margin: 10px 0; padding: 8px 16px; background: #fff8e1; color: #555; }
hr { border: none; border-top: 1px solid #eee; margin: 20px 0; }
code { background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
pre code { display: block; padding: 12px; overflow-x: auto; background: #f8f8f8; border: 1px solid #eee; border-radius: 4px; }
strong { color: #222; }
'''

if __name__ == '__main__':
    md_file = sys.argv[1] if len(sys.argv) > 1 else '/dev/stdin'
    with open(md_file) as f:
        content = f.read()

    # Strip the cron job header + skill template (everything before ## Response)
    m = re.search(r'^## Response\s*\n', content, re.MULTILINE)
    if m:
        content = content[m.end():]

    html_body = convert(content)
    problems = validate_html(html_body)
    if problems:
        print('⚠️ HTML 结构校验失败（标签不平衡），已中止，不生成损坏文件：', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        sys.exit(1)
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日中文简报</title>
<style>{css}</style>
</head>
<body>
{html_body}
</body>
</html>'''

    out_file = sys.argv[2] if len(sys.argv) > 2 else '/tmp/daily_briefing.html'
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'Saved: {out_file}')
