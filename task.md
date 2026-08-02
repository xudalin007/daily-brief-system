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

- [ ] 确认可靠数据源
- [ ] 实现 aSOPR fetcher
- [ ] 同时实现 7DMA
- [ ] 输出真实 data date
- [ ] source / freshness / quality
- [ ] cache
- [ ] tests
- [ ] 接入 prepare
- [ ] 全套测试通过
- [ ] 人工验收

必须遵循：如果 7DMA 无法可靠取得，明确：

> 7DMA：未取得可靠值

验收记录：

- 数据源：
- 最新测试值：
- 数据日期：
- 测试数：
- commit：

## Phase 2 — Prepare Fail-soft

目标：单一核心指标失败不阻断整个 context。

- [ ] 定义统一 `Unavailable` 状态
- [ ] fetch 失败仍保留 section
- [ ] 不使用旧值冒充
- [ ] warning 保留原因
- [ ] aggregate 仍可生成
- [ ] 测试单模块失败
- [ ] 测试多模块失败
- [ ] 测试正常模块仍保留
- [ ] 人工验收

验收记录：

- 模拟失败模块：
- prepare exit：
- context 是否生成：
- Unavailable 是否显示：
- commit：

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
