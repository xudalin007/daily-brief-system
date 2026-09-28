#!/usr/bin/env python3
"""补发「未送达」的定时任务内容（delivery_outcome='failed'）。

## 为什么需要
2026-09-22（日报）和 2026-09-26（下午待办提醒）两次因 **iLink 限流**投递失败：
日志显示连续 8 次 `rate limited`、每次退避 24s，最后 `ret=-2 errmsg=prepare failed`，
引擎记 `delivery_outcome='failed'` —— 内容就此丢掉，用户什么都没收到。

本脚本每 15 分钟扫一次 executions.db，把最近 N 小时内失败的运行**用 iLink 直发补回**：
- 只处理 `delivery_outcome='failed'`（引擎已确认没送达 → 不会重复发）
- 正文取该次运行的 cron 输出文件里的 `## Response` 段；去掉 `MEDIA:` 行（直发只支持文本）
- 前置一行「⚠️ 补发（原定 HH:MM，微信限流未送达）」
- 长文本分片发送（每片 ≤1200 字，间隔 6 秒），避免自己再撞限流
- 发送失败则不记账，下一轮重试；超过 MAX_AGE_H 小时的不再补发（避免半夜补发清晨的提醒）
- 记账文件：~/.hermes/data/resend_state.json

## 注意
**stdout 必须保持为空**（本脚本挂在 no_agent cron 上，stdout 会被当消息投递 → 会重复发）。
日志一律写 ~/.hermes/logs/resend_failed.log。

用法:
    python3 resend_failed_deliveries.py --dry-run   # 只打印将补发什么，不发
    python3 resend_failed_deliveries.py             # 真发（cron 用）
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.join(HOME, ".hermes"))

# 关键：本脚本要 import gateway 包，而上游 agent/redact.py 用了**占有量词**（Python 3.11+ 语法），
# 系统 python3 是 3.10 → import 直接炸（re.error: multiple repeat）。网关自己跑在 venv 里（3.11+），
# 所以这里强制用 venv 解释器重跑一遍自己。
_VENV_PY = os.path.join(HERMES_HOME, "hermes-agent", "venv", "bin", "python3")
if sys.version_info < (3, 11) and os.path.exists(_VENV_PY) and not os.environ.get("_RESEND_REEXEC"):
    os.execve(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]],
              {**os.environ, "_RESEND_REEXEC": "1"})
DB = os.path.join(HERMES_HOME, "cron", "executions.db")
OUT_DIR = os.path.join(HERMES_HOME, "cron", "output")
STATE = os.path.join(HERMES_HOME, "data", "resend_state.json")
LOG = os.path.join(HERMES_HOME, "logs", "resend_failed.log")
MAX_AGE_H = 6.0
CHUNK = 1200
CHUNK_DELAY = 6


def log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _parse_iso(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def find_response_text(job_id: str, finished_at: str) -> str | None:
    """在该 job 的输出目录里找与 finished_at 最近的文件，抽 `## Response` 段。"""
    d = os.path.join(OUT_DIR, job_id)
    if not os.path.isdir(d):
        return None
    fin = _parse_iso(finished_at)
    best, best_delta = None, None
    for p in glob.glob(os.path.join(d, "*.md")):
        mt = datetime.fromtimestamp(os.path.getmtime(p)).astimezone()
        delta = abs((mt - fin).total_seconds()) if fin else 0
        if best_delta is None or delta < best_delta:
            best, best_delta = p, delta
    if not best or (best_delta is not None and best_delta > 1800):
        return None
    lines = open(best, encoding="utf-8").read().splitlines()
    idx = next((n for n, l in enumerate(lines) if l.startswith("## Response")), None)
    if idx is None:
        return None
    body = [l for l in lines[idx + 1:] if not l.strip().startswith("MEDIA:")]
    return "\n".join(body).strip() or None


def failed_runs(hours: float) -> list[dict]:
    if not os.path.exists(DB):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    out = []
    for r in con.execute("SELECT * FROM executions WHERE delivery_outcome='failed' "
                         "ORDER BY rowid DESC LIMIT 30"):
        d = dict(r)
        fin = _parse_iso(str(d.get("finished_at") or ""))
        if fin is None or fin < cutoff:
            continue
        out.append(d)
    con.close()
    return out


def _notifier():
    """复用 post_restart_notify 的凭据 + 直发实现（同一套 iLink 通道）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "post_restart_notify.py")
    spec = importlib.util.spec_from_file_location("prn_local", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hours", type=float, default=MAX_AGE_H)
    args = ap.parse_args()

    st = load_state()
    runs = failed_runs(args.hours)
    if not runs:
        return 0                      # 静默：无失败

    mod = None if args.dry_run else _notifier()
    for d in runs:
        eid = d["id"]
        if st.get(eid):
            continue
        job_id = d["job_id"]
        body = find_response_text(job_id, str(d.get("finished_at") or ""))
        if not body:
            st[eid] = "no_body"
            log(f"{eid} {job_id} 找不到正文，跳过")
            continue
        when = _parse_iso(str(d.get("claimed_at") or ""))
        head = f"⚠️ 补发（原定 {when.strftime('%m-%d %H:%M') if when else '?'}，微信限流未送达）\n\n"
        text = head + body
        if args.dry_run:
            print(f"--- 将补发 {eid} ({job_id}) ---\n{text[:400]}…")
            continue
        ok = True
        for i in range(0, len(text), CHUNK):
            part = text[i:i + CHUNK]
            try:
                res = mod.send(part, dry_run=False)
                try:
                    sent_ok = bool(json.loads(str(res)).get("success"))
                except Exception:
                    sent_ok = '"success": true' in str(res).replace("'", '"').lower()
                if not sent_ok:
                    ok = False
                    log(f"{eid} 分片发送未成功: {str(res)[:200]}")
                    break
            except Exception as exc:
                ok = False
                log(f"{eid} 分片发送异常: {str(exc)[:200]}")
                break
            time.sleep(CHUNK_DELAY)
        if ok:
            st[eid] = f"resent:{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            log(f"{eid} {job_id} 补发成功（{len(text)} 字）")
        else:
            log(f"{eid} {job_id} 补发失败，下轮重试")
    if not args.dry_run:
        save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
