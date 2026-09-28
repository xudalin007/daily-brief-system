---
name: daily-briefing
description: 每日中文简报：AI科技/加密/宏观/要闻。GFW v2.3.10，官方源优先。
version: 2.5.0
---

# 每日中文简报生成（GFW 适配版）

## ⚠️ GFW 网络环境须知

本 skill 针对**中国 GFW 墙内 WSL 环境**优化。

**可用数据源（已验证 2026-08-08）：**
- ✅ 9992100.xyz `/api/ahr999` — BTC价格 + 200日定投成本(gma200_usd) + AHR999 + 市占率(btc_dominance_pct) + Fear&Greed
- ⚠️ api.bgeometrics.com `/v1/puell-multiple` — **已弃用为主源（源侧滞后约 7 天）**，改用 `puell_multiple.py`（LITB 值 + Coin Metrics 自算互校，仅 1 天滞后）
- ✅ api.bgeometrics.com `/v1/realized-price` — Realized Price 每日历史（可自算MVRV Ratio）
- ✅ api.bgeometrics.com `/v1/reserve-risk` — Reserve Risk 每日历史
- ❌ api.bgeometrics.com `/v1/sopr` — GFW阻断
- ✅ **checkonchain.com** — STH/LTH Supply（短期·长期持币量），`sth_supply.py` 自动抓取（自包含 Plotly HTML，gzip 后约 300KB/3s，免费无需 key）。2026-09-18 起替代原 LookIntoBitcoin 手动路线
- ✅ CVDD — axeladlerjr.com `chart-data.json` × **1.08** 校准到标准口径 LookIntoBitcoin（cvdd_estimator.py 抓完整历史 JSON）+ Blockchair fallback
- ✅ Gate.io API — 永续合约资金费率（8h周期）
- ✅ Huobi API — BTC现货行情
- ✅ CNBC — 原油 + 美国国债收益率
- ✅ DeFiLlama API — 稳定币市值（SSR计算）
- ✅ jsDelivr CDN — AHR999历史数据集 + BTC ETF流量CSV（haturatu/crypto-etf-flow）
- ✅ 央视新闻 JSONP 接口 — 宏观/时政中文要闻，官方一手源（`news_{1-7}.jsonp`，7栏目×500条实时，无反爬）
- ✅ BlockBeats 快讯 — 加密中文（解析服务端渲染的 HTML）
- ✅ Cointelegraph RSS — 加密英文（需翻译）
- ✅ arxiv cs.AI RSS — AI/科技英文（需翻译）
- ❌ 搜狗新闻 — 对宏观词（宏观经济/美联储/人工智能）返回验证码页，已弃用
- ✅ cmd.exe /c curl — 走 Windows 网络栈（VPN可用时备用）

> 💡 **方法论（2026-08-21 血泪经验）：中文官方新闻源别找 RSS，找网页版/App 的 JSON 接口。**
> 新华网/人民网/新浪的 RSS 端点全是废弃旧 feed（pubDate 停在 2018~2025，内容不更新）；搜狗对宏观词反爬（验证码）。而央视新闻网页版自己用的 JSONP 接口（`news.cctv.com/2019/07/gaiban/cmsdatainterface/page/news_N.jsonp`）无反爬、实时、字段齐全（title/brief/focus_date/url）。下次找中文新闻源，先看官网/App 的 XHR 网络请求里的 jsonp/json 接口，不要只试 RSS。

**已弃用（延迟太大）：**
- ⚠️ Santiment GraphQL — MVRV/STH-MVRV/Network P/L 有 ~30天延迟，已替换为 lookintobitcoin.com 实时数据

**被 GFW 阻断（不得使用）：**
- ❌ Binance / OKX / Bybit — DNS/TCP阻断
- ❌ CoinGecko 直连 — 阻断
- ❌ CoinMarketCap 直连 — 阻断
- ❌ Yahoo Finance — 频繁限流
- ❌ GitHub / HuggingFace 直连 — 阻断（但 jsDelivr CDN 可用）

**STH Supply 自动化结论（2026-09-18 更新，推翻 8-10 旧结论）：**
- ❌ bitcoinmagazinepro.com / LookIntoBitcoin — Dash 数据接口 `_dash-update-component` 各种 payload 均返回 500（8月、9月两次验证），不可用。
- ✅ **charts.checkonchain.com** — 免费公开图表库（642 张），STH/LTH Supply 在同口径图表 `btconchain/supply/breakdown_lthsth_0/`（页面 → iframe 指向 charts-cdn，自包含 Plotly HTML）。数据 `y` 为 `{"dtype":"f8","bdata":"<base64>"}`，离线 base64+struct 解码即得全历史（6470 天）。脚本：`python3 ~/.hermes/scripts/sth_supply.py`
- 口径校验：STH + LTH ≈ 流通量（2026-09-18：3,523,888 + 16,561,979 = 20,085,867 BTC ✅）

**CVDD 标准口径也已自动化（2026-09-20）：**
- 旧状态：`cvdd_estimator.py`（axeladler chart-data.json × 因子 + Blockchair）自动跑，但「LookIntoBitcoin 标准值」只能每周人工截图校准。
- 现在：`litb_chart.py` 用 **Windows 自带 Edge 无头 + CDP** 读 Plotly 的 `gd.data`，拿到完整历史序列（5869 点）。
  - 关键细节：LITB 图表页顶层**没有** plotly div（图在 iframe 里）→ 要直接开 Dash app 页 `/django_plotly_dash/app/<slug>/`
  - WSL 访问不到 Windows 的 9222（NAT）→ CDP 客户端（PowerShell + ClientWebSocket）也放 Windows 侧跑，结果写文件由 WSL 读回；脚本把 4 个桥接文件（launch/eval/kill.ps1 + expr.js）实时写到 Windows TEMP
  - PowerShell 脚本必须 **纯 ASCII**（无 BOM 的 UTF-8 会被 PS 5.1 按 GBK 读，中文注释会把后面的 `$edge` 读成 null）；bash 里传 `$env:TEMP` 要用**单引号**，否则被 bash 先展开成空
- 已并入 `fetch_all.py` 的 `cvdd_litb` 段（8 源，全程 22 秒）：2026-09-20 标准值 49,509.57 vs 我们的 axeladler×因子 49,503.5，**差 0.012%** ✅
- 日报今后可同时列「标准值 / 我们的值」；换算因子由 `cvdd_autocalibrate.py` **自动维护**（cron 每天 13:05，早于日报）：每次记一条互校日志 `cvdd_calibration_log.jsonl`，**漂移 >0.5% 才改因子**（改前备份 .bak、改后推送一行提示），未变时静默 → **人工每周校准环节已彻底取消**

---

## 📰 信息来源可信度分级（v2.3.10 新增）

**优先使用高可信度来源，标注来源时体现可信度层级：**

### 🥇 一级：官方一手来源（优先使用）
- **公司官方**：OpenAI Blog、DeepSeek API docs、Anthropic Research、各项目GitHub Release Notes
- **政府/监管**：中国证监会、央行、美联储FOMC声明、SEC filings、央视新闻、新华社
- **交易所官方API**：Gate.io、Huobi、DeFiLlama、Santiment（链上数据一手方）
- **国际组织**：世界黄金协会、IMF、世界银行

### 🥈 二级：权威数据聚合（可用）
- CNBC（宏观报价）、Farside（ETF数据，通过jsDelivr CDN）、9992100.xyz（AHR999）
- 经济日报、财新、Caixin

### 🥉 三级：行业媒体/二次传播（交叉验证后可用）
- PANews、BlockBeats、CoinDesk、The Block、新浪科技、网易科技、腾讯网、东方财富
- ⚠️ 三级来源不得作为唯一来源，至少两个独立来源交叉验证

### 标注规范
- 每条数据标明来源 + 可信度层级，如「来源：Gate.io API 🥇」
- 三级来源必须注明「⚠️ 待交叉验证」
- 无法确认来源的传闻不得入报

---

## 简报输出模板（v2.3.10 锁定）

严格按以下结构输出，不得偏离：

```
# 📰 每日中文简报 — YYYY年M月D日（周X）

**数据截至：YYYY-MM-DD HH:MM UTC | 编译环境：GFW墙内WSL**

---

## 一、🤖 人工智能与科技

### 🔥 本周最值得关注

每条约150字：标题加粗 + 一句话描述 + 来源标注
以「📊 趋势判断：」段落结尾

---

## 二、₿ 比特币与加密市场

### 📊 BTC核心指标体系

快照表格：BTC价格、恐惧贪婪、市占率

> ⚠️ **BTC 价格全篇统一（2026-08-30 修正，2026-08-31 强化）**：核心表、CVDD 段、ETF 段、各分析段落里的 BTC 价格，
> 必须用**同一个源、同一个时间戳**（用 9992100.xyz 的 price_usd 为准，或 mvrv 最新价），
> 严禁各处用不同取数时间的价格（曾出现核心表 $78,909 vs CVDD 段 $78,890 对不上）。
> ⚠️ **CVDD 段「同期BTC价」是独立取数的坑（2026-08-31 踩坑）**：cvdd_estimator.py 输出的 btc_price 是它自己
> 从 Blockchair 取的（$78,424），和 9992100 的 price_usd（$78,447）对不上，违反了「统一时间戳」承诺。
> 编译时 CVDD 段的「同期BTC价」**必须用 9992100 的 price_usd 覆盖**，不能照抄脚本输出的 btc_price；
> Price/CVDD 也按统一价重算（78447/49176.59=1.595，和脚本 1.5947 基本一致，但为自洽必须统一）。
> 更好的做法：改 cvdd_estimator.py 让它接受 `--price` 参数，直接传统一价进去。

#### ① MVRV Ratio — 长期估值主指标
表格：数值、数据日期、来源、所处区间 + 「> **分析：**」段落

#### ② Puell Multiple — 矿工压力/短期持有人方向代用
表格：Puell Multiple + MVRV Z-Score 双指标联合判断 + 「> **分析：**」段落
+ **STH Supply（短期持币量）自动行**：取 `fetch_all.py`/`sth_supply.py` 的 `sth_supply` 段 —— 填「STH 值 + 7日/30日变化 + LTH 对比」（不再有“本周待查”手动项）

#### ③ AHR999 — 长期定投/极端估值辅助指标
表格：AHR999值、数据时间、来源、5年分位、5年窗口（1826天）、区间
+ 「> **分析：**」段落

#### ④ 美国现货BTC ETF净流入/流出
最近5天逐日表 + 汇总表（5/30交易日累计、上市以来累计、**ETF 合计持币**、连续流入/流出天数、资金与价格关系，**每一行都同时给「美元」与「BTC 数量」两列**）+ **历史极值对比表**（单日流入/流出峰值、30交易日累计峰值、持仓峰值 → 与当下对比）+ 来源标注 + 分析段落

#### ⑤ BTC永续合约资金费率
表格：当前费率、上期结算、平台、下次结算、区间
+ 分析段落（**双向判断**：正费率看多头是否过度杠杆，负费率看空头是否过度杠杆，缺一侧即漏判）

#### ⑥ SSR Oscillator — 稳定币相对购买力
表格：SSR Oscillator、当前SSR、200日SMA、BTC市值、稳定币MC、来源、区间
+ 分析段落

#### ⑦ CVDD — 长期底部距离锚

**A. 底部锚趋势：**
表格：当前CVDD、30日前、90日前、180日前、360日前、变化率、趋势判断、来源

**B. BTC距CVDD：**
表格：当前Price/CVDD、同期BTC价、30日前Price/CVDD、变化率、距离判断、区间

+ 「> **综合判断：**」两句话总结

#### ⑧ 宏观指标
表格：美股三大指数（标普/纳指/道指）、美元指数 DXY（ICE 期货 Dec'26，用 @DX.1）、VIX、黄金（COMEX Dec'26）、**布伦特原油（近月＋主力＋月差）**、
美国 2Y/10Y/30Y 国债收益率、CME FedWatch 加息概率（2026 加息周期，见宏观背景）

---

### 🟡 综合判断：X灯（一句话定性）

| 维度 | 信号 | 判断 |
|------|------|------|
| 长期估值（MVRV Z-Score） | ... | 🟢/🟡/🟠/🔴 |
| 矿工压力（Puell Multiple） | ... | ... |
| 短期持有人（Puell+MVRV） | ... | ... |
| 定投估值（AHR999） | ... | ... |
| 市场情绪（F&G） | ... | ... |
| 稳定币购买力（SSR） | ... | ... |
| 底部距离（CVDD） | ... | ... |
| ETF资金流（美元/折合BTC） | ... | ... |
| 杠杆结构（资金费率） | ... | ... |

> ⚠️ 9 个维度缺一不可，尤其「定投估值（AHR999）」——2026-08-30 曾从表里掉落但「本日要点」又引用它，前后矛盾。

> **综合结论：** 2-3句话总结

---

### 📰 本周加密要闻

| 新闻 | 来源 | 影响 |
表格，每行一条新闻，标注偏多/偏空/中性

> **关键信号：** 一句话提炼

---

## 三、🌍 宏观经济与投资

### 🔥 核心事件
编号列表，每条含来源+日期

### 📊 投资影响矩阵

| 事件 | 对BTC | 对黄金 | 对美股 | 对A股 |
表格

> **风险提示：** 关键尾部风险说明

---

## 四、📌 国内外重大新闻

| 新闻 | 来源 | 日期 |
紧凑表格，5-6条

---

## 📋 本日要点总结

编号列表 4-5 条核心结论

> ⚠️ **免责声明：** 本简报仅供信息参考，不构成任何投资建议。

---

*数据来源汇总 | 编译时间*
```

---

## 板块2：比特币与加密市场（核心）

#### ① MVRV Z-Score — 长期估值主指标 ✅（v2.3.7 自动化）

**🎯 数据源：Coin Metrics 免费 API + jsDelivr CDN**（自动，无需手动）

```bash
python3 ~/.hermes/scripts/mvrv_zscore.py
```
- Coin Metrics 端点：`community-api.coinmetrics.io/v4/timeseries/asset-metrics`（免费，GFW可直连）
- 历史数据：`HUT1994/bitcoin-mvrv-zscore` → jsDelivr CDN（5865天，2010至今）
- 首次运行下载完整历史（~500KB），之后每日增量更新
- 本地缓存：`~/.hermes/data/mvrv_history.json`
- 精度验证：vs bitcoinmagazinepro.com 相差 <0.05 Z-Score

**区间**（标准口径，2026-08-30 修正，严禁只挑偏多口径）：
- MVRV Z-Score：<0 低估 | 0~1 合理偏低 | 1~3 合理 | 3~5 高估 | >5 泡沫
  ⚠️ 0~1 是「合理偏低」不是「低估」；Z<0 才是低估。
- MVRV Ratio 分位交叉验证：用 mvrv_history.json 的 mvrv_ratio 算历史分位
  （历史 25分位≈1.28、中位≈1.72、75分位≈2.23）。
  当前 Ratio 分位 <25% = 低估；25~50% = 合理偏低；50~75% = 合理；>75% = 高估。
- ⚠️ Z-Score 与 Ratio 两口径结论必须一致才写「低估/高估」；分歧时写「合理/分歧」。
  严禁只呈现偏多的那个口径（会系统性偏乐观，2026-08-30 已踩坑：Z 0.83 标「低估」，
  但 Ratio 1.47 分位 37% 实为「合理偏低」）。

> ⚠️ **MVRV「溢价%」必须同源同日（2026-09-02 踩坑）**：严禁拿「实时价 ÷ 昨日 RP」算溢价。
9/2 版写「BTC 均价较 Realized Price 高约 47.6%」= $77,693（9/2 的 9992100 实时价）÷ $52,647
（9/1 的 bgeometrics RP），与同段表格 MVRV Ratio 1.458（=+45.8%，Coin Metrics 同日同步口径）
同页打架，且制造「价格跌、溢价反升」的表观矛盾（真实同口径序列：9/1 = 78,886/52,672.5 = +49.8%
→ 9/2 = 77,693/52,647.4 = +47.6%，溢价在降）。铁律：算溢价%用同一数据源同一日期（直接报
Coin Metrics mvrv_ratio−1 最稳），或价与 RP 同取一数据日期并标注；严禁跨源跨日混算。另：RP
单日微动（±0.05% 级）可能真实——9/1 RP 较 8/31 −$25 由两独立源（bgeometrics 与 Coin Metrics
realized cap）同幅确认，属 8/31 高位换手推升后的回落 + 回调日亏损盘换手压低 realized 价值，
别拿「净流入行情 RP 应缓升」的长期规律否定单日数据方向。

**报告格式**：
```
| 项目 | 内容 |
|------|------|
| **MVRV Z-Score** | **<值>** |
| 数据日期 | YYYY-MM-DD（~2天延迟） |
| 来源 | lookintobitcoin.com 🥇 |
| 所处区间 | **<区间>** |
```

### 指标2：Puell Multiple + MVRV Z-Score — 短期持有人状态双指标联合判断 ✅（v2.3.8 更新）

**🎯 数据源（全部自动化）：**
- **Puell Multiple**：`~/.hermes/scripts/puell_multiple.py`（2026-09-25 换源）
  - 首选 **LookIntoBitcoin 真值**（Edge+CDP，slug `puell_multiple`）→ 1 天滞后（原来 bgeometrics 是 7 天）
  - 同日期用 **Coin Metrics `IssTotUSD` ÷ 365 日均值**本地自算互校（实测同日偏差 0.054%；200 天均值 0.087%，>0.5% 就在 JSON 里 `cm_check_ok=false` 报警）
  - LITB 抓不到时自动回退自算，`fallback` 字段会说明
  - **日期口径**：LITB 的 d 日值 = 用 d−1 日发行数据算出的值（比 bgeometrics/Coin Metrics 的标注晚一天）。**日报一律用 LITB 的日期与数值**，便于用户开 LITB 对得上
  - 想要领先一天的值：JSON 里的 `cm_litb_date` / `cm_litb_value`（自算，通常比 LITB 早一天可算）
  - fetch_all.py 里 Puell 与 CVDD 都走 LITB(Edge)，**共用同一个 CDP 端口，必须串行**（已改成池后串行；fetch_all 总耗时 ≈40s）
- **MVRV Z-Score**：Coin Metrics 社区API + jsDelivr CDN（`python3 ~/.hermes/scripts/mvrv_zscore.py`）
- **STH Supply**：charts.checkonchain.com（`python3 ~/.hermes/scripts/sth_supply.py`；已并入 `fetch_all.py` 的 `sth_supply` 段）✅ 2026-09-18 起全自动

**STH Supply 读数规则（短期持币量 = 持币 <155 天的币）：**
- STH 上升 + 价格滞涨 → 短期筹码堆积，潜在抛压增加（🟠）
- STH 下降 + LTH 上升 → 筹码被长期锁定，结构转好（🟢）
- 2026-09-18 基线：STH 3,523,888 BTC（占流通 17.54%，30日 +3.47%）；LTH 16,561,979 BTC（30日 -0.63%）

**Puell + MVRV 仍是主判断**（STH Supply 作第三重确认，不单独下结论）

**为什么 Puell + MVRV 可以替代 STH Supply：**
- Puell Multiple 反映矿工盈利压力，矿工投降通常与短期持有人抛售同步
- MVRV Z-Score 反映整体市场估值，低估值区间短期持有人浮亏严重
- 两者同向信号可推断短期持有人筹码状态，无需单独 STH Supply

**Puell Multiple 区间**：<0.5极度受压（矿工投降） | 0.5~1.0受压 | 1.0~2.0正常 | >2.0过热

**联合判断逻辑：**
- MVRV Z < 0.5 + Puell < 0.8 → 短期持有人浮亏 + 矿工受压 = 筹码积累信号（🟢）
- MVRV Z > 3 + Puell > 2 → 短期持有人暴利 + 矿工套现 = 派发信号（🔴）
- 单指标偏离 → 分歧，需观察（🟡）
- STH Supply 自动值作为第三重确认（不许再写“本周待查”）

**报告格式**：
```
| 项目 | 内容 |
|------|------|
| **Puell Multiple** | **<值>** |
| **MVRV Z-Score** | **<值>** |
| STH Supply（短期持币量，自动）| **<值> BTC**（7日/30日变化；LTH 对照） |
| 数据日期 | YYYY-MM-DD |
| 来源 | LookIntoBitcoin（Edge+CDP 自动）+ Coin Metrics 自算互校 🥇 |
| 短期持有人判断 | **<积累/派发/分歧>** |
```

### 指标3：AHR999（含 quantile5y ✅）

**数据源**：
- **实时值**：9992100.xyz `/api/ahr999`（含 ahr999 + gma200_usd + index_growth_valuation_usd + coin_age_days）
- **quantile5y计算**：`python3 ~/.hermes/scripts/calc_ahr999_quantile.py <ahr999值>`
  - 历史数据集：**自算，直接从 mvrv_history.json 价格实时计算**（2010-07 至今，约 5688 天），无需中间文件、永远最新

**公式**（幂律口径，2026-08-30 修正，与 9992100 实时值一致 <0.1%）：
- AHR999 = (price / 200日定投成本) × (price / 指数增长估值)
- 200日定投成本 = 200 / Σ(1/price)（过去200日**调和平均**=定投成本，非简单算术平均）
- 指数增长估值 = 10^(5.84 × log10(币龄天数) - 17.01)（**幂律**，非1.6指数）
- 币龄 = 距 2009-01-03 的天数 + 1
- 公式来源：lovexw/ahr999-free 的 app.js，与 9992100 实时 API 交叉验证

> ⚠️ **历史数据集坑（2026-08-30）**：旧源 `zed-wong/ahr999-mixin`（3449天）已于 2024-12-09 停更，且用的是旧口径（1.6指数），与当前 9992100 幂律口径不一致。已弃用，改自算幂律口径完整历史。5年分位必须用**幂律口径 + 完整数据**，旧数据会低估分位约 9 个百分点（如 0.52 旧报 18.3%，新口径实为 27.8%）。

**区间**（作者原版，2026-08-30 修正）：<0.45 抄底 | 0.45~1.2 定投 | >1.2 谨慎/观望

### 指标4：BTC ETF 净流入/流出 ✅（v2.3.9 升级：带历史维度）

**🎯 免费数据源（2026-07-31起）**：
- **首选（美元）**：`haturatu/crypto-etf-flow` GitHub 仓库 → jsDelivr CDN
  - 数据来源：Farside.co.uk（每30分钟自动抓取）
  - 含逐只ETF明细（IBIT/FBTC/GBTC/ARKB/BITB...）+ Total 汇总（单位百万美元）
  - 数据范围：2024/1/11 至今
- **BTC 数量（2026-09-23 起，真实份额口径）**：`charts.checkonchain.com/btconchain/etfs/`
  - `etf_flows_0` = 各基金每日净流入（BTC 枚）+ `etf_balance_0` = 各基金持币余额（BTC 枚）
  - 脚本 `etf_btc_real.py`（被 etf_flow_summary.py 自动调用；也可单独跑，支持 `--json` / `--fill-gap`）
  - 覆盖 11 只主要基金；未覆盖的新发基金（MSBT）按 Farside 美元缺口补齐
- **BTC 价格**：`~/.hermes/data/mvrv_history.json`（date + price 字段，用于估算回退）

```bash
python3 ~/.hermes/scripts/etf_flow_summary.py
```

脚本自动：purge jsDelivr 缓存 → 下载最新 CSV（失败回退本地缓存 `~/.hermes/data/etf_btc.csv`）→ 输出带历史维度的 markdown，直接粘贴到日报。

**金额单位**（中文习惯）：≥1亿美元用「亿」（两位小数），<1亿用「万」（整数）。例：+$2.38亿、-$6110万。

**BTC 数量列（2026-09-23 上线，真实口径）**：脚本每一行都多给一列 BTC 数量——逐日与各区间累计都有，
另加一行「ETF 合计持币」，最后还有一张**历史极值对比表**（用户 2026-09-23 要求）：
单日净流入/流出峰值、30 交易日累计流入峰值（美元与 BTC 两口径）、ETF 合计持币峰值，每个都给「时间 + 极值 + 当下=峰值的百分之几」。

> ⚠️ **「未固化日」大坑（2026-09-24 用户拓出并已定位根因，务必遵守）**：**Farside 的行是「渐进填充」的** ——
> 行先出现、先只填 1~2 只基金，**Total 列 = 已披露部分之和**（不是最终值），随各基金陆续披露才补齐。
> 硬证据（haturatu 镜像仓库提交历史，每 30 分钟一次）：09/23 行 → 08:00CST 全空(Total=0.0) →
> **07:30CST 只填了 MSBT（Total=$3,240万）** → 15:30CST 补齐 IBIT/FBTC/ARKB 后 Total=$346.9M。
> 日报 13:30 跑时正好拿到「只有 MSBT」那版 → 报出 +$3,240万 / +384枚（真值 +$3.47亿 / +4,108枚，差 10.7 倍）。
> 它能混过去是因为隐含价正常（美元与枚数同步偏小）、旧防线只防 Total==0。
>
> **脚本已双重防护**（etf_flow_summary.py）：① 行未填满（IBIT/FBTC 空 或 有效基金数 ≤3）→ 判未固化；
> ② 独立佐证：checkonchain `holdings_date < flow_date`（余额图晚固化）。命中则把该日**剔出所有累计/极值**
> （保证「表内各行之和 == 累计行」永不漂移），只输出一行 `⏳ … 原因：…`。
> **编译要求**：⏳ 行必须原样保留；**未固化日的数字严禁写进摘要或表格**（摘要只能引已固化值）。
> 回放验证：用 07:30 那版真实 CSV 跑脚本 → 09/23 被正确剔除 ✅。

> ⚠️ **STH/LTH 当日点同样未固化（2026-09-24 修）**：checkonchain 供给图会在当日中途追加「今天」的点，
> 那是当日瞬时值（同日 STH 30日变化 +1.94% vs 上一完整日 +2.54%）。脚本已自动回退到上一个完整日，
> 并在 JSON 里输出 `dropped_partial_point`；若日报里看到该字段非空，不必在正文写，知悉即可。

> ⏰ **时机提醒**：Farside 把前一交易日补齐大约要拖到**当日 15:30 CST 前后**（美东收盘 +19h），
> 而日报固定 13:30 跑 —— 所以「最新一交易日」经常落在未固化区。这不是 bug 而是时间窗，脚本会按上面规则处理；
> 若哪天用户要求「ETF 必须是当日最终值」，可把 ETF 部分挪到 16:00 后单独出。

> ⚠️ **两个口径坑（写的时候就错了）**：① 30 交易日窗口必须**按 Farside 交易日对齐**——用自然日窗口会变成 ~21 个交易日，与美元列对不上；
> ② 「当前即历史最高」时直接写「当前即历史最高 ✅」，不要硬拍一个「= 100%」的百分比。

> ✅ **口径（与旧的「折合BTC」不同，别搞混）**：BTC 数量来自 **checkonchain ETF 图表**（`etf_flows_0` / `etf_balance_0`），
> 是**真实份额/持仓口径的枚数**，不是 USD÷价格折算。校验链（2026-09-23 实测）：
> ① 11 只基金逐只求和 == 图自带 Total（持仓 1,293,087 == Total ETF AUM）；
> ② 与 Farside 美元流量逐日对照：9/17 $159.5M、9/18 $433.0M 完全一致，差额恰为新发的 **MSBT**（该源未收录）；
> ③ 隐含价（USD÷BTC）= 当日现货价（9/17 $76,459 vs 现货 $76,387）；
> ④ 新真口径 vs 旧折算：9/22 8,272 vs 8,291 枚（0.2%）、9/21 11,507 vs 11,548（0.4%）——旧口径不偏，但**报告以真实口径为准**。
>
> - 带 **≈ 前缀**的行 = 该日缺真实数据、退回「USD÷当日价」折算估算（脚本自动标记，编译时保留 ≈）。
> - 「ETF 合计持币」是**持仓存量**（不带正负号）；其 30/90 日变化才是增量。
> - 未覆盖基金（当前只有新发的 MSBT）由脚本按 Farside 美元缺口 ÷ 当日价补齐，使两列口径一致；注释行会自报。
> - 真实口径抓取失败时脚本自动退化为折算估算，并在注释行以 ⚠️ 提示——**若看到该警告，编译时务必保留警告**。
> - 严禁把「合计持币」写成「净流入累计」（存量 ≠ 流量）；也严禁把 BTC 数量写成「新增持仓」（含份额申赎与托管变动，只能叫净流入/净买入）。

**报告格式**（脚本 stdout 直接输出，粘贴即可）：
> ⚠️ **表格上方可能自动输出数据缺口说明行（2026-09-08 新增）**：脚本内置美股交易日历（NYSE 假日+夏令时+收盘状态），
> 当数据末行不是最近交易日时自动在表格顶部输出醒目注释（如「09/07(一) 劳动节休市；09/08(二) 美东未收盘」），
> 让读者明白非数据缺失。**该行是脚本自动生成，编译时原样保留、禁止删除或重复手写**；
> 纯周末空窗或数据新鲜时不输出该行（安静模式）。若输出「ETF 数据滞后」警告 = Farside 源真的没更新，需核对/重跑。

> ⚠️ **Farside 占位空值坑（2026-09-09 踩坑，重大）**：日报 05:30 UTC 编译（= 美东前一日 21:30）时，
> 最新美股交易日（如 9/8）的行**已存在但 Total 列是空的**——Farside 最终值要等美东收盘后数小时~次日才发布，
> 上游先用占位行。旧版脚本把空 Total 解析成 0.0，日报把 9/8 报成「净流为零 + 连续净流入 4 天」，
> 实际最终为 **-$4660万 净流出**（GBTC -$6550万/FBTC -$1710万 主导，IBIT +$1070万 独木难支）。
> 已修（etf_flow_summary.py 双防线）：①Total 空 → 跳过该行；②末行恰为 0.0 → 剔除并输出醒目占位警告。
> 编译时若看到占位警告行 = 数据未出，当天 ETF 表只到上一真实交易日（宁可缺一天不可假 0）。
> 教训二：**「净流为零」类异常值不许用外部消息硬凑解释**（曾编「Strategy 未买入 BTC 印证 ETF 零流入」——
> 企业财库购币与 ETF 申赎是两条独立渠道，只能旁证不能互为因果）。怀疑占位 → 拉原始 CSV 末尾行核对
> （`curl -s "https://cdn.jsdelivr.net/gh/haturatu/crypto-etf-flow@main/etf_btc.csv" | tail`，Total 列空=未发布）。
```
| 日期 | 净流量 | 净流入BTC |
|------|--------|-----------|
| 09/16 | -$2.96亿 | -3,909枚 |
| 09/17 | +$1.59亿 | +2,086枚 |
| 09/18 | +$4.33亿 | +5,329枚 |
| 09/21 | +$9.99亿 | +1.15万枚 |
| 09/22 | +$7.15亿 | +8,272枚 |

| 汇总 | 数值 | BTC 数量 |
|------|------|----------|
| 近5交易日累计净流入 | **+$20.10亿** | **+2.33万枚** |
| 近30交易日累计净流入 | **+$48.46亿** | **+6.02万枚** |
| 前30交易日（对照） | +$4.40亿 | +3,903枚 |
| 上市以来累计净流入 | +$569.43亿 | +67.12万枚 |
| ETF 合计持币 | — | **129.31万枚**（30日 +3.44万枚｜90日 +4.21万枚） |
| ETF 持币占 BTC 流通量 | — | **6.44%**（30日前 6.27%｜90日前 6.24%） |
| 连续净流入天数 | 4 天 | — |
| 近30交易日 BTC涨跌 | +35.7% | — |
| 资金与价格关系 | 同步 | — |

| 历史极值对比（2024-01-11 以来 986 天） | 时间（当时 BTC 价） | 极值 | 当下对比 |
|------|------|------|----------|
| 单日净流入峰值 | 2024-11-07（$75,972） | **+$13.74亿 / +1.79万枚** | 当日 +$7.15亿（$86,205）= **52.0%** |
| 单日净流出峰值 | 2025-02-25（$88,770） | -$11.14亿 / -1.17万枚 | 当日为净流入 +$7.15亿（$86,205） |
| 30交易日累计流入峰值 | 2024-11-06~2024-12-17（$75,696→$106,116） | **+$132.06亿** | 近30交易日 +$48.46亿（$86,205）= **36.7%** |
| 30交易日累计流入峰值（BTC） | 2024-02-05~2024-03-15（$42,619→$69,425） | **+18.13万枚** | 近30交易日 +6.02万枚（$86,205）= **33.2%** |
| ETF 合计持币峰值 | 2025-10-09（$121,603） | **137.52万枚** | 现在 129.31万枚（$86,205） = **94.03%**（距峰值 -8.21万枚） |
| ETF 持币占流通量峰值 | 2025-10-09（$121,603） | **6.90%** | 现在 6.44%（= 峰值的 93.3%） |

> 「ETF 持币占 BTC 流通量」= checkonchain 持仓（BTC）÷ `mvrv_history.json` 的 supply 字段（流通量，约 2008.8 万枚）。
> 分母是**动态流通量**（每天新发约 450 枚），所以占比下降未必是抛售；要区分「占比回落」与「持币减少」。

> 极值表里的「当时 BTC 价」取自 `~/.hermes/data/mvrv_history.json`（脚本自动查）；窗口型极值显示「起点价→终点价」，
> 当下对比列同时给当日价——作用是判断**同一资金量在不同价位的含义不同**（例：30日 BTC 峰值 18.13万枚发生在 $42k~$69k，
> 而现在 6.02万枚发生在 $86k，美元口径上反而更“贵”）。分析段落可以引用这个对比，但**不要自行换算或外推**。
```

**分析要点**：单日噪音大，看累计趋势；「前30日对照」与「近30日」对比看资金流拐点（流出转流入=偏多，流入转流出=偏空）；「资金与价格关系」出现「背离」（资金流入但价格跌 / 资金流出但价格涨）也是拐点信号，需重点提示。

> ⚠️ **「连续净流入/流出天数」口径（2026-08-31 修正）**：必须严格照抄脚本输出，**不得手动改写**。
> 脚本输出「连续净流出天数 1 天」= 最新交易日是流出，当前连续流出 1 天。严禁改写成「连续净流入天数 9 天（8/28 首次转流出）」——
> 因为「9 天」指的是转流出**之前**的连涨段（8/17~8/27），一旦 8/28 转流出，连续净流入天数就归零了，
> 把「9 天」塞进「连续净流入天数」字段会与「8/28 转流出」自相矛盾（8/28 不可能既是第 9 个流入日又是转流出日）。
> 若确实要提「此前连涨 9 日」，单独写一句「8/28 结束此前连续 9 日净流入」，别改字段名和数值。

### 指标5：BTC 永续合约资金费率 ✅

**主数据源：Gate.io**（GFW可直连）
```bash
# 当前资金费率 + 结算周期
curl -s "https://api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT"
# 返回: funding_rate, funding_interval(28800=8h), mark_price

# 近期历史（最近3次结算）
curl -s "https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&limit=3"
# 返回: last_funding_rate(实际结算值)
```

**解读（8h口径，双向对称）**：
- **正费率** = 多头向空头付费，正值越高多头越拥挤（判断"多头是否过度杠杆"）
  - 0%~0.03% 健康 | 0.03%~0.05% 警惕（多头开始拥挤）| >0.05% 过热（多头过度杠杆）
- **负费率** = 空头向多头付费，负值越深空头越拥挤（判断"空头是否过度杠杆"）
  - -0.03%~0% 健康 | -0.05%~-0.03% 警惕（空头开始拥挤）| <-0.05% 过热（空头过度杠杆）
- 同一指标两面：正费率看多头、负费率看空头，缺一侧即漏判另一方向的杠杆失衡。

### 指标6：SSR Oscillator ✅

**🎯 计算方法**：`python3 ~/.hermes/scripts/ssr_oscillator.py`
- BTC MC历史：blockchain.info API（200天）
- 稳定币MC历史：DeFiLlama stablecoincharts/all（3166天）
- 公式：SSR Oscillator = (当前SSR - 200D_SMA) / 200D_std

**输出**：ssr, ssr_oscillator, ssr_200d_sma, interpretation

**解读**（标准 Glassnode 口径，SSR = BTC 市值 / 稳定币市值）：
- >2.0 = 极端高位（稳定币购买力极弱，BTC被高估）
- 0.5~2.0 = 偏高
- -0.5~0.5 = 平衡
- -2.0~-0.5 = 偏低
- <-2.0 = 极端低位（稳定币购买力极强，BTC被低估）

> ⚠️ 非Glassnode原版（免费数据自算），方向一致但数值不可直接对比。
> ⚠️ **定义方向（2026-08-30 修正）**：SSR = BTC市值 ÷ 稳定币市值（**不是** 稳定币÷BTC）。SSR 高 = 稳定币购买力弱 = BTC 相对高估（bearish）；SSR 低 = 购买力强 = BTC 低估（bullish）。曾写反导致「极端高位」误判为「BTC被低估」，属方向性错误。

---

### 指标7：CVDD（长期底部距离锚） ✅

**🎯 获取策略（v2.3.11）：**

**A. 每日权威值（自动，主源）**：`python3 ~/.hermes/scripts/cvdd_estimator.py`
- 脚本内置 `fetch_axeladler_cvdd()`，每天抓 axeladlerjr.com `chart-data.json`（完整历史 2009 至今，每日更新）取最新 `cvdd` × **1.08 校准因子** = 标准 CVDD（LookIntoBitcoin 口径）
- ⚠️ axeladler 本身系统性偏低 7.4%，不 ×1.08 会方向性错误（因子有双重历史验证：8-24 锚点 1.0800 + 2022-11 FTX 底部 1.077）；Blockchair `cdd_24h` 增量偏高 ~60%，仅作 fallback
- 输出：cvdd, price_cvdd, cvdd_30d/90d/180d/360d, source
- ⚠️ **来源标注必须写「LookIntoBitcoin 校准（axeladler × 1.08）」**（脚本 source 字段已带），
  严禁标「Blockchair stats」——Blockchair 只是 fallback 且 cdd_24h 偏高 ~60%，标它属于张冠李戴（2026-08-30 已踩坑：数值是 axeladler×1.08 却标 Blockchair）。

**B. 校准反推（手动）**：`python3 ~/.hermes/scripts/cvdd_backfill_calibrated.py`
- 用 CVDD 权威源（抓 axeladlerjr `chart-data.json` ×1.08 即标准口径；交叉验证可人工看 lookintobitcoin.com 悬停取锚点）作为 180d/360d 锚点
- 锚点间线性插值

**C. 块级反推（高风险）**：`python3 ~/.hermes/scripts/cvdd_progressive.py`
- Blockchair blocks API 按天聚合 CDD
- ⚠️ blocks API 需要 API key，极易触发 430 全站封禁

**区间**：>1.30有距离 | 1.15-1.30观察 | 1.05-1.15底部 | ≤1.05极端

> ⚠️ **口径陷阱（2026-08-24 踩坑）**：CVDD 的「30日前」是 **30 个自然日**（cvdd_estimator 按自然日累积），而指标④ ETF 脚本输出的「近30交易日 BTC涨跌」是 **30 个交易日**（约 42 自然日）。两者涨幅不同（当天分别 +21% vs +26%），**严禁把 ETF 的 30交易日涨幅套用到 CVDD 的「30日」**，否则会和 Price/CVDD 的 30 日变化自相矛盾。CVDD 板块若要写「30日 BTC 涨幅」，用 mvrv_history.json 算 30 自然日涨幅，或从 Price/CVDD 涨幅 + CVDD 涨幅反推。

> ⚠️ **历史对照列取数（2026-09-01 踩坑，重大）**：CVDD 的「30/90/180/360 日前」锚值**必须直接从 axeladler 全历史取**（`cvdd_estimator.py` 已自动这么做），严禁手动翻 `cvdd_history.json` 增量文件——该文件曾被 Blockchair fallback 污染（source=`blockchair-precise`，cdd_24h 偏高 ~60% 且逐日不稳），导致「30日前」锚值单日假跳 +$610（$45,429→$46,039，正确 ~$48,669）、「底部锚一年 +32%」被高估（实际 +23.3%）。判据：30日前 CVDD 月环比涨幅应 ~1%（CVDD 单调慢涨，~$8/天），若报告里月环比 >3% 或单日跳幅 >$100，说明取到了污染值，必须重跑 `python3 ~/.hermes/scripts/rebuild_cvdd_history.py` 重建历史再取。

### 指标8：宏观指标 ✅

**数据源：CNBC quotes**（GFW可直连），抓全以下指标，不再只报原油+10Y：
```bash
# 布伦特原油 / 10Y / 2Y / 30Y
curl -s -o /tmp/brent.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/@LCO.1"
curl -s -o /tmp/us10y.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/US10Y"
curl -s -o /tmp/us2y.html  -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/US2Y"
curl -s -o /tmp/us30y.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/US30Y"
# 美股三大指数（⚠️ 必须用现货指数代码，严禁 @ 期货代码）
curl -s -o /tmp/spx.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/.SPX"    # 标普500 现货
curl -s -o /tmp/ndx.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/.IXIC"   # 纳指综合 现货
curl -s -o /tmp/dji.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/.DJI"    # 道指 现货
# DXY / VIX / 黄金（⚠️ VIX 用现货 .VIX，黄金/原油是 COMEX/NYMEX 期货用 @ 代码属正常）
curl -s -o /tmp/dxy.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/DXY"
curl -s -o /tmp/vix.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/.VIX"
curl -s -o /tmp/gold.html -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/@GC.1"
# 提取：grep -oP 'class="QuoteStrip-lastPrice">[^<]*' /tmp/xxx.html
```
> ⚠️ **周末/假日报表口径（2026-09-27 用户指出）**：周六、周日跑日报时，美股/美债的最后数据是**上一个交易日（周五）收盘**，商品也是**周五最后报价**（美东周五傍晚 = 北京时间周六凌晨）。**来源行严禁写「9/26 商品」这类日期**——会被读成「周六有交易」。正确写法：
> `CNBC quotes（美股/美债＝9/25(周五)收盘；商品＝9/25 最后报价，周末休市）`；涨跌幅 `0.00%` 必须注明「休市无变动」，不能留空/标缺失。同理：周一跑日报时若美东尚未收盘，写「上一交易日收盘」。

> ⚠️ **CNBC 周末「UNCH / 0.00%」陷阱（2026-09-27 用户拓出，真实错误）**：周末（或美市休市日）跑日报时，CNBC 会把 `previous_day_closing` 重置为上一交易日收盘 → 涨跌字段返回 **`UNCH`**（页内 JSON：`"change":"UNCH"`），报告如果照抄就会把「周五 -2.14%」写成 **0.00%**。
> 实测：09/27 日报把布伦特写成 `$104.32（0.00%）`，而 ICE 官方 Nov26 合约为 104.380/-2.083、Reuters 结算 104.32（-2.28，-2.1%）→ 错。
> **规则**：① 涨跌字段若为 `UNCH`/空/`0.00%` 而当日是周末或休市日，**必须另找涨跌**（ICE 官方页合约表、Reuters/新闻站结算价，或「本日 close − 上一交易日 close」自己算）；② 宁可不写涨跌，也不能写 0.00%（假跌等于假信号）。
> **查页内 JSON 的方法**：`grep -o '"change"[^,]*'`、`"previous_day_closing"`、`"last_time"`——比抓 DOM 样式类稳（`QuoteStrip-changeUp/Down` 在部分页面根本不出值）。

> ⚠️ **DXY 口径（2026-09-27）**：CNBC `/quotes/DXY` 页面**已没有任何价格**（DOM 与页内 JSON 都为空，符号失效）→ 日报里那个 `101.035` **无法复现、来源不明**，且周六→周日从 101.04 微到 101.035（休市本不该动）。可用的替代：CNBC `@DX.1`（ICE 美元指数 **期货 Dec**）= 100.765，change -0.253（-0.25%）——注意**期货≠现货 DXY**。
> **规则**：DXY 要么用 `@DX.1` 并明标「美元指数（ICE 期货 Dec'26）」，要么不用；**禁止写来源不明或跨休市日会漂移的数字**。

> ⚠️ **布伦特/黄金的近月换月坑（2026-09-27）**：ICE 布伦特 **主力成交常已在次月**（09/25 实测 Nov26 成交 26.4 万手、Dec26 50.4 万手），部分行情 App 的「连续合约 BRN1!」会**提前换月到 Dec**→ 用户手机上看 97.44，与日报的 Nov26 104.32 差 7 美元。
> 日报引用时**标清合约月份**（如「布伦特（近月 Nov'26）」）；用户质疑价格不一致时，先去 ICE 官方合约表对月份再回答。同理黄金是 COMEX **Dec'26** 合约。

> ⚠️ **布伦特必须双合约（2026-09-27 用户定口径 ③）**：近月与主力会分家（近月临到期被挤，主力已换月），只列一个数一定被用户/行情平台质疑。**执行 `python3 ~/.hermes/scripts/brent_curve.py`**（东方财富 push2，自动发现合约、按成交量定主力），⑧ 段布伦特行写成：
> ```
> | 布伦特原油 | 近月 Nov'26 $104.38（−2.22 / **−2.08%**）｜主力 Dec'26 $97.62（−2.60 / −2.59%）｜月差 **+6.76** |
> ```
> 规则：① 近月=有成交的最早月份（媒体/交易所结算口径，可用 CNBC `@LCO.1` 校验）；主力=成交量最大（TradingView BRN1!／新浪 hf_OIL／东财「当月连续」都跟它，也是用户手机上看到的数）；② **月差 = 近月 − 主力**，正数=backwardation（近月贵，现货紧张），负数=contango；③ 两合约同一时写一个值；④ 数据源可用性：ICE 官方页需 JS（curl 拿不到）、TradingView/Barchart/Investing 均被墙或 403 → **东财 push2 是墙内唯一稳定可自动化的双合约源**。

- **CME FedWatch 加息概率**：CME 页面 JS 渲染无法直抓，改用搜狗搜「FedWatch 加息概率」或
  Cointelegraph 报道里引用的概率（如「July Fed rate-hike odds near 40%」）。至少给出「加息 vs 降息」方向。

> ⚠️ 黄金、DXY、2Y 是**加息周期关键信号**（加息预期→黄金跌、美元涨、短端收益率升），
> 缺这些会把宏观方向判断缺一块（2026-08-30 只报原油+10Y，漏了 Warsh 讲话后黄金-3%、美元升）。

> ⚠️ **美债收益率「新高」措辞（2026-09-01 修正）**：严禁写「20年新高」——这是夸大。历史高点（FRED 免费直连，`https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10` / `DGS30`）：10Y 2006 年以来最高 5.26%（2007-06-12）、2023-10-19 曾 4.98%；30Y 2006 年以来最高 5.35%（2007-06-12）。正确措辞分层：**10Y 4.78% ≈ 近 1.5 年高位**（2025 年 1 月以来最高，仍低于 2023-10 的 4.98%）；**30Y 5.28% ≈ 2007 年以来高位（约 17 年）**，且 8/17 刚触 5.31% 近期峰值（比现在还略高）。「20年新高」对两者都错，改成「维持高位 / 多年高位」并区分 10Y 与 30Y 的不同时间尺度。

> ⚠️ **「X 年新高/首次」一律用历史序列核验，别一律顺着收敛（2026-09-02 日债案例）**：日本 10Y
破 3% 标「近 30 年首次」——FRED `https://fred.stlouisfed.org/graph/fredgraph.csv?id=IRLTLT01JPM156N`
（IMF 月度）实证：最后一次月度 ≥3% 是 **1996-08（3.209%）**，1997-01 已 2.63%，2006-06 峰值仅
1.90%、2008-01 1.43%，此后从未接近 3% → 2026 破 3% 距上次正好约 30 年，「近 30 年首次」成立，
甚至可精确为「1996 年以来首次」。1990s 初（1990-95）虽 >3%（6.4%→4.6%），但那是连续高位段、
1996 年中才跌破——「首次升至」指重新突破，不受高位段起点影响。与美债「20 年新高」性质相反
（美债 2007 有 5.26% 更高点 = 夸大、该收敛；日债上次 ≥3% 恰在 30 年前 = 表述精确，不要被
「1990s 初也 >3%」诱导向下调年限——用户提的「2008 年以来/十几年来」反而错，2008 年日债仅 1.4%）。
核验法：FRED 拉全历史 → 找最后一次 ≥ 目标水平的日期 → 距今间隔 = 真实年限 → 再定措辞。

---

## 综合判断：红绿灯系统

> ⚠️ **F&G 高位退烧要串进综合判断（2026-09-02 采纳）**：F&G 连续 2+ 天在贪婪区高位反复
（69→62→69→63，60+ 但磨顶回落）时，综合判断须点一句「情绪从贪婪高位退烧中」，并与短期策略
呼应（自洽于「回踩确认后再看多」的谨慎逻辑），不能只标静态值「63 贪婪」。

> 🟢 绿灯（看多）| 🟡 黄灯（谨慎偏多）| 🟠 橙灯（谨慎偏空）| 🔴 红灯（看空）
>
> 判断依据：
> - 🟢 绿灯：估值处于历史低估区间、情绪极度恐惧、链上指标显示积累
> - 🟡 黄灯：多数指标偏多但存在短期风险因素（ETF流出、宏观压制等）
> - 🟠 橙灯：多数指标偏空或估值偏高
> - 🔴 红灯：估值极度高估、情绪狂热、链上显示派发

---

## 执行步骤

### 第1批：并行获取实时数据
```bash
# 9992100.xyz（AHR999 + BTC + F&G + GMA200）
curl -s "https://9992100.xyz/api/ahr999" -o /tmp/ahr999.json

# Gate.io 资金费率
curl -s "https://api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT"
curl -s "https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&limit=3"

# BTC ETF 流量（含历史维度汇总，脚本输出完整 markdown）
python3 ~/.hermes/scripts/etf_flow_summary.py

# 宏观（CNBC）
curl -s -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/@LCO.1" -o /tmp/brent.html
curl -s -A "Mozilla/5.0" --max-time 15 "https://www.cnbc.com/quotes/US10Y" -o /tmp/us10y.html
grep -oP 'class="QuoteStrip-lastPrice">[^<]*' /tmp/brent.html
grep -oP 'class="QuoteStrip-lastPrice">[^<]*' /tmp/us10y.html
```

### 第2批：链上估值指标（自动化）
替代原 Santiment（30天延迟）：
- **MVRV Z-Score**：`python3 ~/.hermes/scripts/mvrv_zscore.py`（Coin Metrics 免费 API + HUT1994 jsDelivr，见指标1）
- **Puell Multiple**：`puell_multiple.py`（LITB + Coin Metrics 自算互校，见指标2；**不要**再直接用 bgeometrics 的 Puell，它滞后 7 天）

### 第3批：自算指标
```bash
# AHR999 quantile5y
python3 ~/.hermes/scripts/calc_ahr999_quantile.py $(python3 -c "import json; print(json.load(open('/tmp/ahr999.json'))['ahr999'])")

# SSR Oscillator
python3 ~/.hermes/scripts/ssr_oscillator.py

# CVDD（含 30/90/180/360d）
python3 ~/.hermes/scripts/cvdd_estimator.py
```

### 第4批：新闻获取（用脚本，严禁现场写 curl 管道）
- 加密新闻：`python3 ~/.hermes/scripts/fetch_news.py crypto`（BlockBeats 中文快讯 + Cointelegraph 英文 RSS，需翻译）
- AI/科技新闻：`python3 ~/.hermes/scripts/fetch_news.py ai`（arxiv cs.AI 英文标题，需翻译）

> ⚠️ **AI 板块同主线合并（2026-09-02 采纳）**：同日多条新闻若属同一因果主线（例：KOSPI −4%
半导体抛售 + 日央行委员「AI 推动全球加息」+ 全球债熊/日债 10Y 破 3% =「利率压制 AI 半导体」
一体三面），合并成一条主线综述，细节收进趋势判断段，腾出槽位给独立叙事（如数据中心电力/资本
开支瓶颈）。判别：同一因果链 A→B→C 上的新闻算一条主线，只保留一条 + 一句展开。
- 美股科技财报（财报季必做）：`python3 ~/.hermes/scripts/fetch_earnings.py 英伟达 微软 谷歌`
  （搜狗搜财报；⚠️ 搜狗反爬单次有效，被反爬时改用 delegate 子代理分摊搜索，或手动查）。
  ⚠️ 财报季（1/4/7/10月 + 后一个月）必须覆盖龙头财报——这是本周最大的 AI/科技事件，
  不能只报行业媒体快讯（2026-08-30 漏了英伟达 Q2 FY2027 财报：营收 $962亿、同比 +106%、
  Q3 指引 $1080亿、财报前连跌 7 日，其「业绩好但被利率压制」与 BTC 同源）。

**📅 财报季日历 + 龙头名单（agent 财报季必查）**：
- 财报季月份：**1月 / 4月 / 7月 / 10月**（美企财报集中月），及各自后一个月
- 龙头名单（AI/科技相关，按优先级）：英伟达 NVDA、微软 MSFT、谷歌 GOOGL、AMD、台积电 TSM、Meta、苹果 AAPL
- 兜底策略：搜狗被反爬 → delegate 子代理搜 → 仍失败则标「财报待查」并提示用户手动补
- ⚠️ 财报季若 AI 板块没出现任何龙头财报，等于漏了本周最大的科技事件，必须检查
- 宏观/要闻：`python3 ~/.hermes/scripts/fetch_news.py macro`（央视新闻中文要闻，含宏观政策/时政经济/科技社会，官方一手源）
- ⚠️ 严禁在 terminal 现场写 `curl ... | python3/sed/grep` 等管道命令抓新闻——会触发 tirith 审批导致日报卡顿（8/20、8/21 已两次踩坑）。新闻抓取一律走 fetch_news.py 脚本。

> ⚠️ **宏观背景（2026 版，编译宏观/利率内容前必读）**：2026 年美国是**加息周期**（不是降息）——6 月已现「7月加息概率40%」、8/6 Fed 理事 Cook「若去通胀停滞将支持加息」、7/9「AI热潮引发通胀担忧」。美联储主席是 **Kevin Warsh**（2025年特朗普提名、2026年5月上任，**不是「理事」**，8/28 Jackson Hole 是其主席任内首秀）。写宏观/利率内容时方向是「加息/鹰派」不是「降息/鸽派」，风险提示一律写「加息预期」而非「降息预期」（2026-08-30 曾把方向写反）。

> ⚠️ **美国 CPI 数据口径（2026-08-31 修正，重要）**：美国 CPI 同比**没有破 4%**。实测 TradingEconomics：2026年7月 CPI 同比 **3.4%**、6月 **3.5%**，且**连续第二个月放缓**，低于 2023年5月高点 4.2%（当时是伊朗战争能源冲击所致，现已回落）。「6月 CPI 同比破 4%」是把 2023年5月高点 4.2% 或能源分项涨幅（汽油 +24.6% yoy、燃料油 +39.1% yoy）误记成了整体 CPI。严禁再写「CPI 破 4%」——否则「加息」论断会被高估，市场反应也对不上（CPI 真到 4% 加息是板上钉钉，市场会剧烈反应）。写宏观背景用「CPI 3.4~3.5% 且放缓，但 Warsh 鹰派立场 + 加息预期仍在」这类准确表述。

> ⚠️ **新闻定性坑（2026-08-30 Warsh 事件）**：编译新闻**必须读正文，不能只看标题推断市场影响**。案例：Cointelegraph 标题"Warsh downplays softer inflation prints"，日报曾写"美联储理事 Warsh 淡化通胀→暗示不急于降息，中性偏谨慎，四资产全中性"——错三处：①身份错（Warsh 是美联储主席非理事）；②方向错（2026 是加息周期，Warsh 讲话是鹰派：坚持2%目标、淡化通胀降温、废除前瞻指引，非「中性」）；③"美股/加密承压"与正文相反（正文 "US stocks avoided losses"，美股涨0.5%）。教训：标题给「说了什么」，正文才给「市场怎么反应」，两者必须分开核对；且先确认宏观方向（加息/降息）再定性，否则会把最大的宏观主线事件打成「中性」漏掉。

> ⚠️ **地缘新闻单源降权规则（2026-08-31 伊朗-霍尔木兹事件）**：央视单源 + 强市场含义（如「海峡完全关闭」「击落美军无人机」级别）的新闻，**默认降一档权重、标⚠️，等第二个独立源（🥈🥉级西方媒体）确认再进投资矩阵**。判别信号：①单源；②叙事幅度与市场反应对不上（如「海峡完全关闭」但布伦特只 +2.7% 到 $90.6，真封锁应是两位数跳空）；③是当天新增的头号事件、报告把大量判断压在它上面。符合任一即降权。案例：8/31「伊朗称霍尔木兹海峡上空击落美军MQ-9无人机」确系央视多条报道（8/28美军清雷、8/30副外长称海峡关闭、8/31击落无人机，属持续升级的地缘紧张，事件真实），但「完全关闭」是伊朗单方说法，且油价反应幅度不匹配，应标「⚠️ 单源待交叉验证」而非直接当定论写进风险提示核心链条。

### 第5批：编译输出
严格按模板输出，标注数据源和延迟。

先运行 `python3 ~/.hermes/scripts/brief_paths.py YYYY-MM-DD --ensure`（把日期换成报告日），读取返回的 `markdown` 和 `html` **绝对路径**。在任何系统上都使用这两个路径，不自行拼接 `/mnt/d` 或桌面路径。先把正文写入 `markdown`，再运行 `python3 ~/.hermes/scripts/md2html.py <markdown绝对路径> <html绝对路径>`。

### 第6批：数值回验（⚠️ 强制，不可跳过）

**生成 HTML 后、交付前，必须跑回验脚本**，用独立实时 API 硬对比报告里的数值型指标，对不上就重抓修复，不许带着 FAIL 发出去：

运行 `python3 ~/.hermes/scripts/verify_report.py <html绝对路径>`，其中路径取自第5批 `brief_paths.py` 的输出。

- 退出码 0 = 全部数值通过，可发；退出码 1 = 有 FAIL，**必须修完再发**。
- 脚本自动校验：三大指数（现货代码）、VIX、DXY、黄金、原油、2Y/10Y/30Y 国债、BTC 价格/市占率/AHR999/F&G、ETF 连续净流入/流出天数、CPI「破4%」错误表述。
- 时效性强的值（资金费率）标 REVIEW 不硬拦，人工核对抓取时点即可。
- 这是机器强制校验，不依赖「记住教训」——2026-08-31 实测：纳指抓成期货(11.6%偏差)、ETF 天数写反、CPI 破4% 三个错全被拦截，13 个正确值零误杀。
- 脚本只拦数值错，**定性错（新闻定性、人物身份）仍靠读正文**，两者都要过。

## 📤 交付规范

1. 确认第5批生成的 HTML 路径是绝对路径且文件存在；`MEDIA:` 后面直接接这个路径。

> ⚠️ **md2html 表格粘连坑（2026-08-24 已修复+固化）**：markdown 里两个相邻表格若用普通段落（如 `**B. xxx：**`）分隔，旧版 md2html 会因「普通段落/列表」分支不关闭 `<table>` 导致 A/B 两表粘连、渲染错乱（数据看起来「缺失」）。现已修复：表格/列表分支都补上 table 关闭逻辑、统一 in_list 状态值（ul 列表曾误输出 `</ol>`），并加了标签平衡自检（`<table>/<thead>/<tbody>/<tr>/<ul>/<ol>` 配对，不平衡则 exit 1 报警、不生成损坏文件）。改脚本后跑 `python3 ~/.hermes/scripts/test_md2html.py` 确认无回归。生成 markdown 时相邻表格仍建议留空行或加 `---` 分隔（双保险）。

2. 最终回复（按 Hermes 当前投递配置发送）必须同时包含两部分，缺一不可：
   (a) ≤2000字中文摘要（简短核心结论：红绿灯、BTC价格、关键指标、今日要闻，不贴全文）
   (b) 单独一行 `MEDIA:` 加第5批取得的 HTML 绝对路径；不要输出占位符。
3. 没有 MEDIA: 行 = 用户收不到 HTML = 失败。禁止把完整 markdown 全文当最终回复；禁止只列文件路径而不带 MEDIA: 行。
