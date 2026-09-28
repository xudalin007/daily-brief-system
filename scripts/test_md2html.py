#!/usr/bin/env python3
"""md2html 回归测试 —— 防止「表格粘连」bug 复发。

背景（2026-08-24）：md2html 状态机在「表格后跟普通段落/列表」时不关闭
<table>，导致相邻两个表格粘成一个损坏的 HTML（A/B 表列数对不上、<p> 嵌进
<table>、多余 </thead>）。已修复 + 加标签平衡自检。本脚本固化回归用例。

运行：python3 ~/.hermes/scripts/test_md2html.py
退出码：0 = 全部通过，非 0 = 存在回归。
"""
import importlib.util
import sys
from pathlib import Path

MD2HTML = str(Path(__file__).with_name("md2html.py"))


def load_module():
    spec = importlib.util.spec_from_file_location("md2html", MD2HTML)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check(name, cond):
    print(f"  {'✓' if cond else '✗ 回归！'} {name}")
    return cond


def main():
    m = load_module()
    ok = True

    # 用例 1：表格 → 段落 → 表格（本次 bug 根因）
    md = (
        "**A. 底部锚趋势：**\n\n"
        "| 项目 | CVDD | 变化率 |\n|------|------|------|\n"
        "| 当前 | $50,588 | — |\n| 360日前 | $39,778 | +27.18% |\n\n"
        "**B. BTC距CVDD：**\n\n"
        "| 项目 | 数值 |\n|------|------|\n| 当前Price/CVDD | 1.52 |\n"
    )
    body = m.convert(md)
    ok &= check("表格→段落→表格：两个 <table> 各自独立闭合",
                body.count("<table>") == 2 and body.count("</table>") == 2)
    ok &= check("转换结果标签平衡（validate_html 通过）",
                len(m.validate_html(body)) == 0)
    ok &= check("B 表头是 <th> 而非 <td>（共 3+2=5 个 th）", body.count("<th>") == 5)

    # 用例 2：表格 → 列表（同类 bug，列表分支曾同样漏关 table）
    md2 = "| a | b |\n|---|---|\n| 1 | 2 |\n\n- 列表项\n"
    body2 = m.convert(md2)
    ok &= check("表格→列表：table 正确闭合", len(m.validate_html(body2)) == 0)

    # 用例 3：validate_html 能拦截损坏结构（模拟旧 bug 的多余 </thead>）
    broken = (
        "<table><thead><tr><th>x</th></tr></thead><tbody><tr><td>y</td></tr>"
        "<p>bad</p></thead><tbody></tbody></table>"
    )
    ok &= check("validate_html 拦截损坏标签", len(m.validate_html(broken)) > 0)

    print("\n" + ("✅ 全部通过" if ok else "❌ 存在回归，请检查 md2html.py"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
