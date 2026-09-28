# Hermes 每日中文简报

这是从 Hermes 迁移包提取的日报流水线：一个 `daily-briefing` skill、数据抓取与报告校验脚本、三项定时任务示例。生成流程涵盖加密市场、宏观行情、AI 新闻和重大新闻；实际报告由 Hermes agent 按 skill 编写，再由 `md2html.py` 转成 HTML，经 `verify_report.py` 回验。

## 运行条件

- Hermes Agent（迁移包使用 v0.21.2）、Python 3.11+、`curl`。
- Windows + WSL2 和 Windows Edge：`litb_chart.py` 与 `puell_multiple.py` 的 LookIntoBitcoin 数据路径依赖 Edge CDP，以及 WSL 中的 `powershell.exe`、`wslpath`。
- 能访问各数据源的网络连接。新闻与行情接口可能改变；运行前应检查实际输出。
- 自己配置 Hermes 模型、API key 和可选的微信连接。本仓库不含这些凭据。

这些脚本沿用 Hermes 的 `~/.hermes` 目录布局；不是可直接在任意目录运行的独立 Python 包。`cron_jobs.example.json` 默认使用 `local` 投递，创建任务前检查时区、时间、模型和投递目标。示例中的 `/mnt/d/Desktop/每日简报` 是 WSL 报告目录，按自己的环境调整 skill 与 cron 提示中的路径。

## 安装

```bash
mkdir -p ~/.hermes/scripts ~/.hermes/skills/productivity/daily-briefing ~/.hermes/data
cp scripts/* ~/.hermes/scripts/
cp skills/productivity/daily-briefing/SKILL.md ~/.hermes/skills/productivity/daily-briefing/
cp -n data/cvdd_seed.json data/cvdd_calibration.json ~/.hermes/data/
chmod +x ~/.hermes/scripts/cvdd_autocalibrate.sh
```

`cvdd_seed.json` 和 `cvdd_calibration.json` 是 2026 年的历史起点。校准脚本会在安装机上更新因子；先核对数据源与结果，再启用定时任务。其余 `data/` 文件是运行时缓存，由脚本下载或生成，不随源码发布。

补发功能调用 Hermes 的微信 gateway。仅在需要微信补发时，在运行该任务的环境里设置 `HERMES_CHAT_ID` 为自己的目标 chat ID；不要写进源码或提交到 Git。首次接入微信需在本机完成登录。没有此变量，直发脚本会明确报错。

## 建立任务

```bash
python3 restore_cron_jobs.py --dry-run
python3 restore_cron_jobs.py --jobs ./cron_jobs.example.json
```

第二条命令会实际调用 `hermes cron create`，请在检查示例后执行。示例包含 13:05 CVDD 校准、13:30 日报和每 15 分钟补发失败投递。时间按 Hermes 的运行时区解释。要改投递目标，可在复制出的本地 `cron_jobs.json` 中修改，或在创建时使用 `--deliver`。补发任务在没有微信连接时可先从本地副本中移除。

## 验证

离线检查：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/test_md2html.py
python3 -m compileall -q scripts restore_cron_jobs.py
python3 restore_cron_jobs.py --dry-run
```

在目标 WSL 环境中完成配置后，逐项运行 `fetch_all.py`、`etf_flow_summary.py`、`brent_curve.py`、`puell_multiple.py`，检查数据来源和日期。生成报告后以 `python3 ~/.hermes/scripts/verify_report.py <报告.html>` 回验；缺失、抓取失败或标记为 REVIEW 的项目还需人工确认。macOS 上的离线检查不能证明 Edge CDP、Hermes cron、微信投递或实时数据源可用。

### macOS 冒烟结果（2026-09-28）

在 Darwin、Python 3.14.6 上，`brent_curve.py --json`、`fetch_news.py` 的 `crypto` / `ai` / `macro` 模式、`ssr_oscillator.py`、`puell_multiple.py --no-litb --json` 均取得非空实时结果；`md2html.py` 命令行也成功生成 HTML。`litb_chart.py puell_multiple --json` 因没有 Windows `powershell.exe` 返回错误，这是当前实现的 macOS 限制。以上测试未覆盖完整的 `fetch_all.py`、Hermes cron、微信投递或整份报告回验。

## 发布范围

仓库只放日报所需代码、skill、非凭据数据种子与任务示例。原始迁移压缩包、个人记忆、微信账户文件、运行日志、报告和历史缓存不属于源码。发布到 GitHub 前还需选定许可证，并确认第三方数据与 skill 内容适合公开。
