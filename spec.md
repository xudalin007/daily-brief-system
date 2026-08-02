# Daily Brief MVP Specification

本文件只定义 MVP 工程实现边界，不重新定义、修改或覆盖业务规则。`rules/daily_brief_rules.md` v1.2 仍是唯一正式业务规则；如有冲突，以 rules 为准。

## 1. 项目目的

本项目不是为了构建一个工程上无限完善的数据平台。

核心目的是让“每日关注简报”每天能够稳定取得最新、可靠、可验证的数据，减少临时网页搜索、缓存旧值、日期不清、来源变化和不同日期口径不一致的问题。

最终使用方式仍然是用户在主聊天发送：

> 今天日报

数据底座负责：

> 自动取数 → 校验 → 缓存 → 标准化 → 标注真实数据日期、来源和质量状态 → prepare 聚合

ChatGPT 负责：

> 结合数据包以及当天 AI、宏观和新闻信息 → 事实 → 分析 → 判断 → 灯号 → 最终日报

## 2. Source of Truth

优先级如下：

1. `rules/daily_brief_rules.md`：唯一正式业务规则。
2. `spec.md`：工程实现规格，不得修改业务规则。
3. `task.md`：当前开发任务和验收状态。
4. `changelog/daily_brief_changelog.md`：只记录正式规则历史变化。
5. `checkpoints/`：记录阶段运行状态。

`AGENTS.md` 属于开发工作规范，不属于业务规则。

## 3. 当前架构

不重新设计现有架构，继续使用真实链路：

> External Sources → fetchers → cache / normalized data → `prepare_daily_brief.py` → daily brief context → ChatGPT / Codex / 人工生成最终日报

## 4. MVP Definition of Done

第一版投入真实日常使用必须满足以下条件。

### A. 规则

- 仓库 rules 已是 v1.2。
- 三文件规则基线已经同步并提交。

### B. BTC 核心数据底座

现有稳定模块继续工作：

- CVDD
- ETF Flow
- AHR999
- Funding
- MVRV
- STH-MVRV

新增：

- aSOPR
- aSOPR 7DMA

SSR：

- 如果存在与 v1.2 兼容的可靠数据源，则正常输出。
- 如果无法可靠取得，则输出 `Unavailable / 未取得可靠值`。
- SSR 不得阻塞整份日报。

### C. Fail-soft

这是 MVP 硬性要求。单个指标无法取得可靠值时：

- 不猜测。
- 不拿旧值冒充当天值。
- 不导致整个 prepare 失败。
- 在最终 context 中保留该指标。
- 明确标记 `Unavailable / 未取得可靠值`。
- 其他正常模块继续输出。

只有真正破坏整个数据包可信度的系统级错误才允许 fatal。

### D. 时间与来源

核心数据应尽量包含：

- `value`
- `data_date / timestamp`
- `source`
- `freshness / delay`
- `quality status`

不得把 `fetched_at`、`generated_at` 或网页访问时间当作数据日期。

### E. 实战验收

必须至少完成一次真实当日端到端运行：

> fetch → prepare → context → ChatGPT 生成“今天日报”

验收要求：

- 数据可以实际使用。
- 一个可模拟的模块失败不会阻断日报。
- `Unavailable` 正确展示。
- 没有旧值冒充新值。
- 日报符合 `daily_brief_rules.md` v1.2。

完成这一项后：

> MVP 状态 = Operational / 可投入日常使用

## 5. MVP 明确不要求

以下内容不得阻塞 MVP 上线：

- Brent 自动化
- UST Yield 自动化
- LTH Sell-Side Risk Ratio
- AVIV
- Pi Cycle Top
- AI 新闻自动抓取
- 新闻自动聚类
- 完整自动日报生成器
- GitHub Actions
- CI
- Scheduled Task
- 自动 push
- README 全面重写
- 性能优化
- 并行 fetch
- UI

这些内容进入 Post-MVP。

## 6. MVP 原则

> 日报实际可用性优先于工程完整性。

任何问题判断优先级时，首先问：

> 它是否会导致今天的日报无法可靠生成？

如果不会，原则上不阻塞 MVP。
