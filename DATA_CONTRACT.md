# Daily Brief Data Contract

## 1. Purpose

`rules/daily_brief_rules.md` 决定日报需要什么，是唯一正式业务规则。

本文件只规定数据如何在以下链路中可靠传递：

> fetcher → prepare → final context

本文件不得重新定义业务指标、阈值或数据源优先级。如与 rules 冲突，以 rules 为准。

## 2. Common Module Status

模块应逐步统一使用以下语义状态。现有大小写或模块内部状态可以在 MVP 期间兼容，但不得改变这些含义。

### verified

数据源、指标口径和数据日期均可可靠确认。

### delayed

数据本身可靠，但相对请求截止时间存在已知延迟；必须保留真实数据日期和延迟信息。

### provisional

存在真实数据，但仍有口径、完整性或方法学限制，不允许伪装成 `verified`。

### unavailable

无法取得符合正式规则的可靠值。

`unavailable` 时：

- `value = null`
- 必须提供 `reason`
- 不得拿旧值冒充本次值
- 不得使用 proxy 冒充正式指标
- 不得阻塞其他模块

## 3. Common Fields

核心模块在指标语义合理的情况下，应逐步统一提供：

- `metric`
- `status`
- `value`
- `data_date`
- `timestamp`，用于指标本身是实时型的情况
- `source`
- `source_url` / `source_id`，现有代码适合时提供
- `fetched_at`
- `freshness` / `delay`
- `warning`
- `reason`

必须明确：

> `data_date` / `timestamp` ≠ `fetched_at` / `generated_at`

网页访问时间、抓取时间和文件生成时间不得冒充指标本身的数据时间。

MVP 不要求所有现有模块立即一次性重构成完全相同的 schema，允许逐步兼容。

## 4. Daily vs Realtime Metrics

日线指标优先使用 `data_date`，例如：

- MVRV
- STH-MVRV
- AHR999
- aSOPR
- SSR
- CVDD

实时或结算型指标应使用 `timestamp`、结算时间和周期，例如 Funding。

ETF 使用最近可靠的美国完整交易日，并保留页面最新日期与正式采用日期的区别。

## 5. Unavailable Contract

结构示意：

```json
{
  "metric": "...",
  "status": "unavailable",
  "value": null,
  "data_date": null,
  "source": "...",
  "reason": "..."
}
```

最终 prepare 必须保留该 section。`unavailable` 是明确的数据质量结果，不应被省略、替换为零或替换为其他指标。

## 6. Prepare Contract

prepare 的职责是：

- 聚合所有正式模块
- 验证输入结构
- 保留 warning
- 保留 `unavailable` section
- 不因一个指标不可用而丢弃整个 context
- 不把旧 `latest` 文件误认为本次运行成功
- 输出本次 `generated_at`
- 输出本次 `requested_as_of`

只有真正破坏整个数据包可信度的系统级错误才允许 fatal。

## 7. Compatibility

MVP 不要求现有所有模块立即大规模重写。

后续实现 aSOPR 和 prepare fail-soft 时，应首先遵守本 contract；已有模块随后逐步统一状态和字段。
