#!/usr/bin/env python3
"""重启 gateway 后：自检 + 主动给用户发一条「回来了」。

为什么需要它：本机 gateway 跑在 tmux 里（非 systemd），重启期间 agent 进程被杀，
所以「告知用户」这件事必须由一个**独立于 gateway 的脚本**来做 —— 本脚本在重启
之后运行，用 gateway 自己那套 `send_weixin_direct`（iLink 直发）把消息送出去。

用法：
    python3 post_restart_notify.py --dry-run   # 只自检、不发（验证导入/凭据/配置）
    python3 post_restart_notify.py             # 自检 + 真发

自检内容：gateway 进程在不在、weixin 是否重连成功、新参数是否已生效。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
REPO = os.path.join(HERMES_HOME, "hermes-agent")
CONFIG = os.path.join(HERMES_HOME, "config.yaml")
ACCOUNTS = os.path.join(HERMES_HOME, "weixin", "accounts")

# 本脚本 import gateway 包；上游 agent/redact.py 用占有量词（Python 3.11+ 语法），
# 系统 python3（3.10）会 import 失败（re.error: multiple repeat）→ 强制用 venv 解释器重跑
_VENV_PY = os.path.join(HERMES_HOME, "hermes-agent", "venv", "bin", "python3")
if sys.version_info < (3, 11) and os.path.exists(_VENV_PY) and not os.environ.get("_PRN_REEXEC"):
    os.execve(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]],
              {**os.environ, "_PRN_REEXEC": "1"})
GATEWAY_LOG = os.path.join(HERMES_HOME, "logs", "gateway.log")
CHAT_ID = os.environ.get("HERMES_CHAT_ID", "").strip()


def sh(cmd: str, timeout: int = 30) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"<{exc}>"


def gateway_up() -> bool:
    return "hermes gateway run" in sh("ps aux | grep 'hermes gateway run' | grep -v grep")


def wait_gateway(max_wait: int = 180) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if gateway_up() and "hermes" in sh("tmux ls 2>&1"):
            return True
        time.sleep(3)
    return False


def weixin_reconnected(max_age: int = 900) -> bool:
    """gateway.log 里最近 max_age 秒内有没有 `Connected account=` 行（= 重启后重连成功）。

    注意：不能用「脚本启动时间」当基线 —— 重启脚本会先把旧进程杀掉再拉新，
    新连接行往往**早于**本脚本的启动时刻，用启动时间比会误判成「没重连」。
    """
    cutoff = time.time() - max_age
    try:
        with open(GATEWAY_LOG, encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-400:]
    except Exception:
        return False
    for line in reversed(tail):
        if "Connected account=" in line:
            try:
                t = time.mktime(time.strptime(line[:19], "%Y-%m-%d %H:%M:%S"))
            except Exception:
                continue
            return t >= cutoff
    return False


def config_state() -> dict:
    try:
        import yaml  # type: ignore
        with open(CONFIG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        extra = ((cfg.get("platforms") or {}).get("weixin") or {}).get("extra") or {}
        return {
            "typing_indicator": extra.get("typing_indicator"),
            "send_chunk_delay_seconds": extra.get("send_chunk_delay_seconds"),
            "send_chunk_retry_delay_seconds": extra.get("send_chunk_retry_delay_seconds"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def credentials() -> tuple[str, str, str]:
    """取当前活跃账号的 (account_id, token, base_url)。

    坑：凭据文件里**没有** account_id 字段 —— account_id 就是文件名去掉后缀
    （如 `example@im.bot.json`）。而「哪个号是活的」不能看 .json 的 mtime
    （凭据是 7 月扫码时写的），要看同名 `.context-tokens.json` / `.sync.json`
    谁最新（长轮询会一直更新它们）。
    """
    def base_of(name: str) -> str:
        for suf in (".context-tokens.json", ".sync.json", ".json"):
            if name.endswith(suf):
                return name[: -len(suf)]
        return name

    newest = None
    for name in os.listdir(ACCOUNTS):
        p = os.path.join(ACCOUNTS, name)
        m = os.path.getmtime(p)
        if newest is None or m > newest[0]:
            newest = (m, base_of(name), p)
    if newest is None:
        raise RuntimeError("accounts 目录里没有凭据文件")
    _, account_id, _ = newest
    cred_path = os.path.join(ACCOUNTS, f"{account_id}.json")
    with open(cred_path, encoding="utf-8") as f:
        data = json.load(f)
    return (account_id, str(data.get("token") or ""),
            str(data.get("base_url") or "https://ilinkai.weixin.qq.com"))


def send(message: str, dry_run: bool) -> str:
    if not CHAT_ID:
        raise RuntimeError("Set HERMES_CHAT_ID to the recipient chat ID before sending")
    sys.path.insert(0, REPO)
    import asyncio
    from gateway.platforms.weixin import send_weixin_direct  # type: ignore

    account_id, token, base_url = credentials()
    if dry_run:
        return (f"DRY-RUN: 会发给 {CHAT_ID}\n凭据: account_id={account_id[:8]}… "
                f"token={'有' if token else '无'} base_url={base_url}\n内容:\n{message}")
    res = asyncio.run(send_weixin_direct(
        extra={"account_id": account_id, "base_url": base_url},
        token=token, chat_id=CHAT_ID, message=message, media_files=None,
    ))
    return json.dumps(res, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", type=int, default=180)
    args = ap.parse_args()

    t0 = time.time()
    up = wait_gateway(args.wait)
    reconnected = weixin_reconnected() if up else False
    cfg = config_state()

    if args.dry_run:
        print(f"gateway 进程: {'✅ 在跑' if up else '❌ 没起来'}")
        print(f"weixin 重连: {'✅ 已重连' if reconnected else '⚠️ 未见新连接行'}")
        print(f"微信参数: {cfg}")
        print(send("（dry-run 占位）", dry_run=True))
        return 0

    ok = up and reconnected
    msg = ("✅ 我回来了～ 重启完成\n"
           f"gateway: {'在跑 ✅' if up else '异常 ❌'}\n"
           f"微信通道: {'已重连 ✅' if reconnected else '未见重连日志 ⚠️'}\n"
           f"新参数: typing_indicator={cfg.get('typing_indicator')}, "
           f"chunk_delay={cfg.get('send_chunk_delay_seconds')}s, "
           f"retry_delay={cfg.get('send_chunk_retry_delay_seconds')}s\n"
           "（关掉输入心跳 + 拉长退避，明早 9:00 待办和 13:30 日报应该就稳了）"
           if ok else
           "⚠️ 重启后自检没完全通过\n"
           f"gateway: {'在跑' if up else '没起来 ❌'}\n"
           f"微信重连: {'已重连' if reconnected else '未见日志 ⚠️'}\n"
           f"参数: {cfg}")
    out = send(msg, dry_run=False)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
