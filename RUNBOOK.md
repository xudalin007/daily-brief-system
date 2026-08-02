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

- `exit 0`：所有已接入模块均返回0，输出结构验证通过，context成功生成。
- `exit 1`：模块调用失败、任一模块返回1或未知退出码、日期无效，或输出结构/日期校验失败。当前实现会立即停止，不能假定本次已生成新context。
- `exit 2`：没有fatal，但至少一个模块返回2；warning或暂报信息会保留，context仍会生成。

该行为可能在 `task.md` Phase 2 的 prepare fail-soft 实现中发生变化。不要提前把目标行为当作当前已完成能力。

## 4. How to Verify Freshness

每天运行后重点检查：

- `generated_at`
- `requested_as_of`
- 每个指标的 `data_date` / `timestamp`
- `source`
- `freshness` / `delay`
- `status`

不要因为文件修改时间或 `generated_at` 是今天，就认为其中每个指标的数据也是今天。`fetched_at` 也只表示抓取时间，不是指标时间。

## 5. Common Problems

### 网络失败

先查看本次命令的退出码和终端错误。若context成功生成，再检查顶层 `warnings`、`data_quality`、各模块来源元数据，以及 Funding 的provider状态，确认具体失败的数据源。不要仅凭旧 `latest` 文件判断本次请求成功。

### 缓存

有效期内使用缓存是正常行为，可减少请求并保护免费额度。怀疑上游已更新、缓存异常或需要真实验收时使用 `--refresh`。过期缓存不应被当成本次最新网络结果。

### 指标 Unavailable

MVP目标是保留该指标section，使用 `value = null`、`status = unavailable` 和明确原因，同时继续输出其他模块。当前prepare仍可能因核心模块exit 1而整体停止；该差距由 `task.md` Phase 2处理。

### Provisional

表示存在真实数据，但完整性、口径或方法学仍有限制。它不能被当作 `verified`，必须连同warning和真实日期一起使用。

### 旧 latest 文件

如果本次prepare fatal，之前生成的 `latest.json` 和 `latest.md` 可能仍然存在。必须检查本次退出码、`generated_at` 和 `requested_as_of`，不能盲读旧文件。

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
