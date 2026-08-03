# Daily Brief Runbook

## 1. Normal Daily Run

正常入口：

```bash
python3 scripts/prepare_daily_brief.py
```

默认运行使用当前 UTC 日期作为 `requested_as_of`，依次调用当前已接入模块；各模块在缓存有效时优先使用缓存，成功后聚合并写入 context。

强制刷新：

```bash
python3 scripts/prepare_daily_brief.py --refresh
```

`--refresh` 会把强制刷新请求传给已接入模块，绕过其有效缓存并重新访问数据源。

指定分析截止日期：

```bash
python3 scripts/prepare_daily_brief.py --as-of YYYY-MM-DD
```

`--as-of` 用于支持该参数的日线和交易日模块。Funding 当前始终是实时快照，不伪造历史 Funding，因此历史 `--as-of` 运行会同时包含当前 Funding 快照。

复制 Markdown：

```bash
python3 scripts/prepare_daily_brief.py --copy
```

`--copy` 在 macOS 存在 `pbcopy` 时复制最终 Markdown context；没有 `pbcopy` 或复制失败时只给出提示，不因此改变数据准备结果。

## 2. Output

当前统一输出：

- `data/daily_brief/generated/latest.json`
- `data/daily_brief/generated/latest.md`

`latest.json` 用于机器校验和结构化消费；`latest.md` 可直接提供给 ChatGPT、Codex 或人工分析。在主聊天中结合该数据包发送“今天日报”，最终日报仍按 `rules/daily_brief_rules.md` 生成。

## 3. Exit Codes

以下记录当前 prepare 的实际行为：

- `exit 0`：所有正式模块正常、没有重要warning，输出结构验证通过，context成功生成。
- `exit 1`：prepare或最终context生成/写出发生系统级fatal，或输入/聚合契约已无法保证整个数据包可信。不能仅凭磁盘上的旧`latest`判断本次成功。
- `exit 2`：context成功生成，但至少存在`provisional`、`delayed`、`unavailable`或非致命warning。单个指标失败不会阻断其他模块；失败section保留原因和空值。

## 4. How to Verify Freshness

每天运行后重点检查：

- `generated_at_utc`
- `requested_as_of`
- `aggregate_status` / `aggregate_exit_code`
- 每个指标的 `data_date` / `timestamp`
- `source`
- `freshness` / `delay`
- `status`

不要因为文件修改时间或 `generated_at_utc` 是今天，就认为其中每个指标的数据也是今天。`fetched_at` 也只表示抓取时间，不是指标时间。

## 5. Common Problems

### 网络失败

先查看本次命令的退出码和终端错误。若context成功生成，再检查顶层 `warnings`、`data_quality`、各模块来源元数据，以及 Funding 的provider状态，确认具体失败的数据源。不要仅凭旧 `latest` 文件判断本次请求成功。

### 缓存

有效期内使用缓存是正常行为，可减少请求并保护免费额度。怀疑上游已更新、缓存异常或需要真实验收时使用 `--refresh`。过期缓存不应被当成本次最新网络结果。

### 指标 Unavailable

当前prepare会保留该指标section，使用 `value = null`、`status = unavailable` 和明确原因，同时继续执行并输出其他模块。失败模块的旧`latest`不会被读取来冒充本次值。

### Provisional

表示存在真实数据，但完整性、口径或方法学仍有限制。它不能被当作 `verified`，必须连同warning和真实日期一起使用。

### 旧 latest 文件

如果本次prepare fatal，之前生成的 `latest.json` 和 `latest.md` 可能仍然存在。必须检查本次退出码、`generated_at_utc`、`requested_as_of`和`aggregate_exit_code`，不能盲读旧文件。`latest.json`是本次成功写出的权威标记；若终端明确提示“context已写出，但终端摘要失败”，仍应按exit 1处理并人工核对该文件的`generated_at_utc`。

### Funding

Funding当前是实时/结算快照。即使使用历史 `--as-of`，Funding仍代表当前请求时点，不代表历史截止日。历史分析必须显式说明该时间语义限制。

## 6. End-to-End Daily Workflow

1. 运行 `python3 scripts/prepare_daily_brief.py`，必要时增加 `--refresh`。
2. 检查退出码、日期、来源、状态和warning。
3. 打开或读取 `data/daily_brief/generated/latest.md`；需要结构化核验时读取`latest.json`。
4. 在主聊天发送“今天日报”，并提供本次有效context。
5. 按 `rules/daily_brief_rules.md` 生成最终日报。

## 7. MVP Acceptance Run

最终MVP验收入口见 `task.md` 的 **Phase 4 — End-to-End MVP Acceptance**。按该阶段执行一次真实`--refresh`、fail-soft模拟和“今天日报”验收，不在本文件复制整份检查表。
