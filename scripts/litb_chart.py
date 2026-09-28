#!/usr/bin/env python3
"""LookIntoBitcoin 图表数据自动抓取（本机 Chromium CDP 或 WSL + Edge）。

为什么这么绕：LITB 图表数据走 Django Dash 接口，从 WSL 直接 POST
`_dash-update-component` 一律 500（`_dash-layout` 也不含数据），只能让真浏览器
去渲染。本机没装 Chromium/Playwright，但 Windows 自带 Edge —— 用无头 Edge
开远程调试端口，在页面里执行 JS 读 Plotly 的 `gd.data`，就能拿到完整历史序列。
WSL 到 Windows 的 9222 端口不通（NAT），所以 CDP 客户端也放在 Windows 侧
（PowerShell + ClientWebSocket），结果写文件再由本脚本读回。

用法:
    python3 litb_chart.py cvdd                 # 最新 CVDD + 价格
    python3 litb_chart.py cvdd --json
    python3 litb_chart.py cvdd --days 30       # 附带最近 30 天序列
支持任意 LITB Dash 图表 slug（cvdd、short-term-holder-supply 等）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
APP_URL = "https://www.lookintobitcoin.com/django_plotly_dash/app/{slug}/"

LAUNCH_PS1 = r"""
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$profile = "$env:TEMP\edge-cdp-profile"
$url = $args[0]
if (Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
    Where-Object { $_.CommandLine -like "*edge-cdp-profile*" }) {
    Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
        Where-Object { $_.CommandLine -like "*edge-cdp-profile*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 700
}
Start-Process -FilePath $edge -ArgumentList @(
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--remote-debugging-port=9222", "--remote-allow-origins=*",
    "--user-data-dir=$profile", $url
) -WindowStyle Hidden
Start-Sleep -Seconds 4
try {
    $v = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 5
    Write-Output ("CDP_OK " + $v.Browser)
} catch { Write-Output ("CDP_FAIL " + $_.Exception.Message) }
"""

EVAL_PS1 = r"""
$ErrorActionPreference = "Stop"
$outFile = "$env:TEMP\cdp_result.json"
try {
    $targets = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/list" -TimeoutSec 8
} catch {
    '{"error":"cdp_unreachable"}' | Out-File -Encoding utf8 $outFile
    Write-Output "CDP_UNREACHABLE"; exit 1
}
$page = $targets | Where-Object { $_.type -eq "page" -and $_.url -like "*lookintobitcoin*" } | Select-Object -First 1
if (-not $page) { $page = $targets | Where-Object { $_.type -eq "page" } | Select-Object -First 1 }
if (-not $page) {
    '{"error":"no_page_target"}' | Out-File -Encoding utf8 $outFile
    Write-Output "NO_PAGE"; exit 1
}
$exprFile = "$env:TEMP\litb_expr.js"
$lines = Get-Content $exprFile
$script:js = [string]($lines -join "`n")
$ws = New-Object System.Net.WebSockets.ClientWebSocket
$ct = [System.Threading.CancellationToken]::None
$ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl, $ct).Wait(15000) | Out-Null
function Send-Ws([string]$msg) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($msg)
    $seg = New-Object System.ArraySegment[byte] -ArgumentList @(,$bytes)
    $ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $ct).Wait(15000) | Out-Null
}
function Recv-Ws() {
    $buf = New-Object byte[] 1048576
    $sb = New-Object System.Text.StringBuilder
    do {
        $seg = New-Object System.ArraySegment[byte] -ArgumentList @(,$buf)
        $res = $ws.ReceiveAsync($seg, $ct).Result
        $sb.Append([System.Text.Encoding]::UTF8.GetString($buf, 0, $res.Count)) | Out-Null
    } while (-not $res.EndOfMessage)
    return $sb.ToString()
}
$payload = @{ id = 1; method = "Runtime.evaluate"; params = @{ expression = $script:js; returnByValue = $true; awaitPromise = $false } } | ConvertTo-Json -Depth 6 -Compress
Send-Ws $payload
$resp = Recv-Ws
$ws.Dispose()
try {
    $val = ($resp | ConvertFrom-Json).result.result.value
    if (-not $val) { '{"error":"eval_no_value"}' | Out-File -Encoding utf8 $outFile; Write-Output "CDP_EVAL_NO_VALUE"; exit 1 }
    $val | Out-File -Encoding utf8 $outFile
    Write-Output ("CDP_EVAL_OK len=" + $val.Length)
} catch { Write-Output ("CDP_PARSE_FAIL " + $_.Exception.Message) }
"""

KILL_PS1 = r"""
Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
    Where-Object { $_.CommandLine -like "*edge-cdp-profile*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Output "EDGE_CLOSED"
"""

# 页面里执行的 JS：读 Plotly 图表的完整数据（x 轴比 y 轴多 100 天未来补位，故按 y 长度对齐）
JS_EXPR = r"""
(() => {
  const gd = document.querySelector('.js-plotly-plot');
  if (!gd) return JSON.stringify({error: 'no_plotly_div', title: document.title});
  const iso = (v) => (v instanceof Date) ? v.toISOString() : String(v);
  const out = {now: new Date().toISOString(), traces: []};
  for (const t of gd.data || []) {
    const xs = t.x || [], ys = t.y || [];
    let lastIdx = -1;
    for (let i = ys.length - 1; i >= 0; i--) {
      const v = ys[i];
      if (v !== null && v !== undefined && !(typeof v === 'number' && isNaN(v))) { lastIdx = i; break; }
    }
    const at = (i) => (i >= 0 && i < ys.length) ? [iso(xs[i]), ys[i]] : null;
    const series = [];
    for (let i = Math.max(0, ys.length - DAYS); i < ys.length; i++) {
      if (xs[i] !== undefined) series.push([iso(xs[i]).slice(0, 10), ys[i]]);
    }
    out.traces.push({
      name: t.name, points: ys.length, lastIdx: lastIdx,
      last: at(lastIdx), prev: at(lastIdx - 1), prev7: at(lastIdx - 7),
      series: series
    });
  }
  return JSON.stringify(out);
})()
"""


def sh(cmd, timeout=180):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, errors="replace")


def win_temp() -> str:
    # 注意：外层是 bash，PowerShell 的 $env:TEMP 必须用单引号包，否则会被 bash 先展开成空
    r = sh("powershell.exe -NoProfile -Command '$env:TEMP'")
    return r.stdout.strip().replace("\r", "")


def win_path(wsl_path: str) -> str:
    return sh(f'wslpath -w "{wsl_path}"').stdout.strip()


def write_bridge(tmp: str, days: int) -> None:
    files = {
        "litb_launch.ps1": LAUNCH_PS1,
        "litb_eval.ps1": EVAL_PS1,
        "litb_kill.ps1": KILL_PS1,
        "litb_expr.js": JS_EXPR.replace("DAYS", str(days)),
    }
    for name, content in files.items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            f.write(content)


def to_wsl(win: str) -> str:
    """C:\\Users\\new\\AppData\\Local\\Temp -> /mnt/c/Users/new/AppData/Local/Temp"""
    p = win.strip().replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = f"/mnt/{p[0].lower()}{p[2:]}"
    return p


def _fetch_chart_wsl(slug: str, days: int, render_wait: int) -> dict:
    tmp_win = win_temp()
    if not tmp_win:
        raise RuntimeError("拿不到 Windows TEMP 路径（powershell 不可用？）")
    tmp = to_wsl(tmp_win)
    os.makedirs(tmp, exist_ok=True)
    write_bridge(tmp, days)
    launch = tmp_win.rstrip("\\") + "\\litb_launch.ps1"
    ev = tmp_win.rstrip("\\") + "\\litb_eval.ps1"
    kill = tmp_win.rstrip("\\") + "\\litb_kill.ps1"
    url = APP_URL.format(slug=slug)

    r = sh(f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{launch}" "{url}"')
    if "CDP_OK" not in r.stdout:
        raise RuntimeError(f"Edge/CDP 启动失败: {r.stdout.strip()[:200]} {r.stderr.strip()[:200]}")
    time.sleep(render_wait)
    r = sh(f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{ev}"')
    if "CDP_EVAL_OK" not in r.stdout:
        raise RuntimeError(f"CDP 取值失败: {r.stdout.strip()[:200]} {r.stderr.strip()[:200]}")
    with open(os.path.join(tmp, "cdp_result.json"), encoding="utf-8-sig") as f:
        data = json.load(f)
    sh(f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{kill}"')
    data["chart"] = slug
    data["source"] = "lookintobitcoin.com (WSL Edge CDP)"
    return data


def find_native_browser() -> str | None:
    """Find a local Chrome/Edge binary; LITB_BROWSER_PATH takes precedence."""
    override = os.environ.get("LITB_BROWSER_PATH", "").strip()
    if override:
        path = shutil.which(override) or override
        if not os.path.isfile(path):
            raise RuntimeError(f"LITB_BROWSER_PATH does not exist: {override}")
        return path

    system = platform.system()
    candidates: list[str] = []
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    elif system == "Windows":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.extend([
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                ])
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            path = shutil.which(name)
            if path:
                candidates.append(path)
    return next((p for p in candidates if os.path.isfile(p)), None)


def _fetch_chart_native(browser: str, slug: str, days: int) -> dict:
    """Launch a private headless browser and read Plotly through local CDP."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js 22.4+ is required for native Chromium CDP")
    helper = Path(__file__).with_name("cdp_eval.mjs")
    if not helper.is_file():
        raise RuntimeError(f"CDP helper missing: {helper}")

    with tempfile.TemporaryDirectory(prefix="litb-cdp-") as profile:
        profile_path = Path(profile)
        expression = profile_path / "expression.js"
        expression.write_text(JS_EXPR.replace("DAYS", str(days)), encoding="utf-8")
        command = [
            browser, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0", "--remote-allow-origins=*",
            f"--user-data-dir={profile}", APP_URL.format(slug=slug),
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            port_file = profile_path / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not port_file.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Browser exited before CDP started ({process.returncode})")
                time.sleep(0.2)
            if not port_file.exists():
                raise RuntimeError("Browser did not open a CDP port")
            port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
            result = subprocess.run([node, str(helper), str(port), str(expression)],
                                    capture_output=True, text=True, timeout=80)
            if result.returncode:
                raise RuntimeError(f"CDP chart read failed: {result.stderr.strip()[-250:]}")
            data = json.loads(result.stdout)
            data["chart"] = slug
            data["source"] = "lookintobitcoin.com (local Chromium CDP)"
            return data
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def fetch_chart(slug: str = "cvdd", days: int = 30, render_wait: int = 14) -> dict:
    """WSL keeps the Windows Edge bridge; elsewhere use a native Chromium."""
    mode = os.environ.get("LITB_BROWSER_MODE", "auto").lower()
    if mode not in {"auto", "native", "wsl"}:
        raise ValueError("LITB_BROWSER_MODE must be auto, native or wsl")
    if mode == "wsl" or (mode == "auto" and is_wsl()):
        return _fetch_chart_wsl(slug, days, render_wait)
    browser = find_native_browser()
    if browser:
        return _fetch_chart_native(browser, slug, days)
    if mode == "auto" and platform.system() == "Linux" and shutil.which("powershell.exe"):
        return _fetch_chart_wsl(slug, days, render_wait)
    raise RuntimeError("No local Chrome/Edge found; set LITB_BROWSER_PATH or use WSL mode")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    slug = args[0] if args else "cvdd"
    days = 30
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
    try:
        data = fetch_chart(slug, days)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)[:300], "chart": slug}, ensure_ascii=False))
        return 1
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"📊 LookIntoBitcoin / {slug}  （{data.get('now','')[:16]} 抓取）")
    for t in data.get("traces", []):
        last = t.get("last") or [None, None]
        prev = t.get("prev") or [None, None]
        chg = ""
        if isinstance(last[1], (int, float)) and isinstance(prev[1], (int, float)) and prev[1]:
            chg = f"（较前一日 {(last[1]/prev[1]-1)*100:+.3f}%）"
        print(f"  {t.get('name')}: {last[1]:,.2f}  @ {last[0]}{chg}  [共 {t.get('points')} 点]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
