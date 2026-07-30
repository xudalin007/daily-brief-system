# 每日简报 Monthly Checkpoint

- Checkpoint：2026-07
- 生成日期：2026-07-31
- 用途：线程过长、新线程启动、规则核对时快速恢复
- 正式规则源：`daily_brief_rules.md`

## 1. 当前目标

长期维护一份中文每日简报，覆盖AI与科技、BTC与加密市场、宏观经济与投资、国内外重大新闻。重点是最近24小时真正重要的变化、趋势与影响、投资利好/利空、工具与机会，并严格区分事实/分析/推测。

## 2. 当前BTC指标状态

| 指标 | 角色 | 状态 |
|---|---|---|
| MVRV | 长期估值 | 固定 |
| STH-MVRV | 短期持有人盈亏 | 固定 |
| AHR999 | 长周期辅助估值 | 固定 |
| 美国现货BTC ETF Flow | 机构真实资金 | 固定 |
| Funding | 杠杆情绪 | 固定 |
| aSOPR + 7DMA | 已实现盈亏行为 | 固定 |
| SSR Oscillator | 稳定币相对购买力 | 固定 |
| PiT CVDD + Price/CVDD | 长期底部锚与距离 | 固定新版 |
| Brent / UST Yield | 宏观与再通胀风险 | 固定按需 |

候选：
- LTH Sell-Side Risk Ratio：高价值，暂不强制每日
- AVIV Ratio：等待稳定可靠数据
- Pi Cycle Top：只在顶部风险接近时使用

## 3. CVDD当前正式结构

PiT CVDD：当前、30日前、90日前、30D/90D变化率、上移/横盘/回落。

Price/CVDD：当前、7日前、30日前、7D/30D变化率、接近/远离趋势。

每月首份日报：增加PiT CVDD 365D变化。

Price/CVDD区间：
- >1.30：离极端底部仍明显有距离
- 1.15–1.30：重点观察
- 1.05–1.15：历史重要底部区
- ≤1.05：极端底部区
- ≈1.00：贴近CVDD

## 4. 数据可靠性原则

- 新鲜数据尽量使用可靠、可验证来源并标日期/时间。
- 无可靠值写“未取得可靠值”。
- 不用旧值冒充当天值。
- ETF未完整时不能写成0。
- Funding某交易所失败时改用其他官方源。
- Glassnode公开页面可能缓存，尤其CVDD必须确认对应日期。
- 不同CVDD口径不得混用历史阈值。

## 5. 当前未解决项

1. 暂无CoinGlass API Key：Funding依赖OKX等官方交易所API；理想升级方向是CoinGlass OI加权Funding。
2. LTH Sell-Side Risk Ratio自动稳定取数仍不够理想：保持候选/周度增强，不伪造精确值。
3. AVIV暂不固定加入：只有数据稳定且有明显增量价值时再加入。
4. Glassnode部分公开页面可能延迟/缓存：以数据日期而非访问日期判断新鲜度。

## 6. 当前运行方式

- 在同一主聊天中持续运行和讨论。
- 用户发“今天日报”即可生成。
- 为避免多个聊天结果，当前不依赖Scheduled Task作为主要入口。
- 新规则只有在正式固定后才进入rules和changelog。

## 7. 新线程恢复方法

当当前聊天过长，需要开启“每日简报 2 / 3 / ...”时：

1. 把以下三个文件提供给新聊天：
   - `daily_brief_rules.md`
   - `daily_brief_changelog.md`
   - 最新 `checkpoint_YYYY-MM.md`
2. 第一条消息写：
   “继续每日简报。以daily_brief_rules.md为唯一正式规则；CHANGELOG只追加；最新checkpoint用于恢复当前状态。聊天中的旧讨论若冲突，以rules最新版为准。”
3. 继续日报、讨论、固定新规则、更新规则库、追加CHANGELOG，并每月生成新Checkpoint。

这样无需依赖模型完整记住多年聊天历史。
