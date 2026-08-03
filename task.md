# Daily Brief MVP Tasks

本文件只跟踪当前 MVP 开发任务和验收状态，不定义业务规则。如与 `rules/daily_brief_rules.md` 冲突，以 rules 为准。

## Phase 0 — Rule Baseline

- [x] v1.2 rules / changelog / checkpoint 已同步
- [x] 人工验收通过
- [x] commit：`Update daily brief rules baseline to v1.2`
- [x] git status clean

验收记录区：

- rules baseline commit：`26d0467abc9137d453a0b351bad63312370ecd6e`
- MVP spec/task commit：`9b73b9c6cde0dd66dc3631dfce0a6a7d819ac6ee`
- git status baseline：通过
- 验收结果：人工验收通过

## Phase 1 — aSOPR + 7DMA

- [x] 确认可靠数据源
- [x] 实现 aSOPR fetcher
- [x] 同时实现 7DMA
- [x] 输出真实 data date
- [x] source / freshness / quality
- [x] cache
- [x] tests
- [x] 接入 prepare
- [x] 全套测试通过
- [x] 人工验收

必须遵循：如果 7DMA 无法可靠取得，明确：

> 7DMA：未取得可靠值

验收记录：

- 数据源：BGeometrics / Bitcoin Data API 正式 `GET /v1/asopr`（字段 `asopr`，无需 API Key）
- 最新测试值：aSOPR `1.010700`；同源 derived 7DMA `1.003586`
- 数据日期：aSOPR 与 7DMA 均为 `2026-08-02`；相对 requested_as_of 延迟 `0` 天
- 测试数：aSOPR 专项 `24`；prepare 专项 `64`；全套 `261`，全部通过
- prepare 结果：真实刷新成功包含 `sections.asopr`；aSOPR 为 `verified`；整体 exit 2 仅因既有 SSR 警告；随后普通运行总网络请求为 `0`
- 人工验收：PASS
- 人工验收数据：aSOPR `1.010700`；7DMA `1.003586`；data_date `2026-08-02`；status `verified`
- commit：`e107a01`

## Phase 2 — Prepare Fail-soft

目标：单一核心指标失败不阻断整个 context。

- [x] 定义统一 `Unavailable` 状态
- [x] fetch 失败仍保留 section
- [x] 不使用旧值冒充
- [x] warning 保留原因
- [x] aggregate 仍可生成
- [x] 测试单模块失败
- [x] 测试多模块失败
- [x] 测试正常模块仍保留
- [ ] 人工验收

验收记录：

- fail-soft 实现方式：prepare 聚合层依次执行全部正式模块；单模块调用、读取或质量校验失败时不读取其旧 `latest`，而是标准化为 `Unavailable`，其他模块继续。
- Unavailable schema：公共字段包含 `metric`、`status`、`availability`、`value: null`、`data_date: null`、`source`、`reason`、`warning`、`requested_as_of`、`fetched_at`。
- aggregate exit 语义：全部正常且无重要 warning 为 `0`；context 已生成但含 provisional / delayed / unavailable / warning 为 `2`；仅系统级 context 生成或写出失败为 `1`。
- 模拟失败模块：逐项覆盖 CVDD、ETF、AHR999、Funding、MVRV、SSR、aSOPR；另覆盖双模块及 ETF + Funding + SSR 三模块同时失败。
- prepare exit：指标级失败为 `2`；模拟最终写出失败为 `1`。
- context 是否生成：是；指标级失败时所有七个 section 均保留，后续模块继续执行，正常模块值不丢失。
- Unavailable 是否显示：是；JSON 与 Markdown 均显示原因，失败模块 `value` / `data_date` 为 `null`，旧值 sentinel 未进入新 context。
- 测试：prepare 专项 `70` 项；全套 `267` 项，全部通过。
- 真实刷新：成功生成 context；整体 exit `2` 仅因既有 SSR Provisional warning，其余模块均为 `verified`。
- 人工验收：待本轮验收
- commit：本提交（`Implement fail-soft Daily Brief preparation`）

## Phase 3 — SSR MVP 处理

目标不是追求完美方法学，而是不阻塞日报。

先确认当前可靠来源是否与 v1.2 兼容。

如果兼容：

- [ ] 正式接入
- [ ] 标注真实日期和来源
- [ ] 验证口径
- [ ] tests
- [ ] Verified

如果仍不兼容：

- [ ] 保留当前研究结果
- [ ] 正式日报字段输出 `Unavailable`
- [ ] 不执行方向判断
- [ ] 不执行极端分类
- [ ] 不阻塞 prepare
- [ ] tests

二选一完成即可通过 MVP。

验收记录：

- 最终方案：
- 状态：
- 原因：
- commit：

## Phase 4 — End-to-End MVP Acceptance

真实运行：

```bash
python3 scripts/prepare_daily_brief.py --refresh
```

然后使用真实生成的数据包完成一次：

> 今天日报

检查：

- [ ] CVDD
- [ ] ETF
- [ ] AHR999
- [ ] Funding
- [ ] MVRV
- [ ] STH-MVRV
- [ ] aSOPR
- [ ] aSOPR 7DMA
- [ ] SSR 或 Unavailable
- [ ] 实际 data date
- [ ] source
- [ ] quality 状态
- [ ] 无旧值冒充
- [ ] 单模块失败不阻断
- [ ] 最终日报符合 v1.2

如果全部通过：

> MVP = OPERATIONAL

记录：

- 验收日期：
- prepare 结果：
- 日报结果：
- 已知非阻塞问题：
- 最终 commit：

## Post-MVP Backlog

以下仅记录，不在本轮开发：

- PiT CVDD 进一步增强（如仍有缺口）
- Brent
- UST Yield
- AHR999 fallback
- 统一 quality schema 进一步完善
- upstream contract tests
- AI / news 自动化
- 完整日报生成层
- CI
- scheduling
- README 更新
- LTH Sell-Side Risk Ratio
- AVIV
- Pi Cycle Top
