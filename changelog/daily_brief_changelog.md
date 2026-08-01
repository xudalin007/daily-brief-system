# 每日简报 CHANGELOG

规则：只记录已经正式固定的变化；新条目只追加，不覆盖历史；无法确认更早日期时不虚构日期。

## 2026-07-31 — v1.0 基线建立

- 建立独立 `daily_brief_rules.md` 作为 Source of Truth。
- 明确：历史聊天或旧提示词若与规则库冲突，以规则库最新版为准。
- 明确：“固定方案 / 固定一下 / 就按这个方案” = 更新正式规则 + 追加 CHANGELOG。
- 当前日报以主聊天手动触发为主，典型指令“今天日报”。

## 2026-07-30 — CVDD模块升级

原方案主要展示最新 PiT CVDD、当前 Price/CVDD 和固定阈值。

正式升级为：
- PiT CVDD：当前、30日前、90日前；计算30D/90D变化率；判断底部锚上移/横盘/回落。
- Price/CVDD：当前、7日前、30日前；计算7D/30D变化率；判断快速接近/缓慢接近/稳定/缓慢远离/快速远离CVDD。
- 每月第一份日报增加 PiT CVDD 365D变化。
- 强制标注数据来源及日期/时间。
- 缓存、延迟或日期无法确认的数据不得称为“今日最新”。
- 普通 Glassnode CVDD、Bitbo仅作交叉核对，不与PiT CVDD混用历史阈值。

升级目的：同时回答“长期底部锚自己怎么走”和“BTC离底部锚越来越近还是越来越远”。

## 基线之前的现行规则

以下内容已经进入v1.0正式规则，但本CHANGELOG不虚构其更早的精确固定日期：
- MVRV
- STH-MVRV
- AHR999及AIX4U取数优先级
- 美国现货BTC ETF Flow
- Funding官方交易所API回退机制
- aSOPR + 7DMA
- SSR Oscillator
- Brent / 美国国债收益率
- LTH Sell-Side Risk Ratio、AVIV、Pi Cycle Top候选规则

未来如有必要，可依据历史聊天单独做一次“历史变更回填”，并明确哪些日期已确认、哪些只是近似。

## 2026-08-01 — CVDD升级为双轨数据体系

原方案：

- 主要依赖Glassnode PiT CVDD同时承担趋势与底部距离判断，但公开历史数据获取不稳定。

新方案：

1. BGeometrics CVDD成为每日趋势主源。
2. 使用同源 `btc-price` 计算BGeometrics Price/CVDD。
3. 固定展示CVDD 30D/90D趋势，每月第一份日报增加365D。
4. Price/CVDD固定展示7D/30D趋势，可额外展示90D。
5. Glassnode PiT独立保留为历史极端底部距离锚。
6. 原有1.30、1.15、1.05、1.00监控区间只适用于Glassnode PiT口径。
7. BGeometrics与Glassnode PiT两套数据禁止混算、拼接历史序列或共用未经验证的阈值。
8. 目标日期缺失时向前寻找最近有效日，并明确标注原目标日期和实际使用日期。
9. Bitbo暂不作为自动备用源，只保留为人工交叉核对候选。

修改目的：

- 解决Glassnode PiT公开历史序列无法稳定获得的问题，同时保留PiT历史极端底部判断价值，并建立可稳定每日计算的CVDD趋势体系。

## 2026-08-01 — Refine ETF Flow freshness and completeness rules

原规则：

- 报告最近完整美国交易日数据；当天数据不完整时标注；缺失不得写成0；优先观察连续性与多日累计。
- 未明确单个基金缺失与整日完整性的区别，也未固定核心基金、交易日状态、暂报回退、累计窗口和新鲜度字段。

新规则：

1. Farside Investors `https://farside.co.uk/btc/` 固定为主源；当前页面与搜索摘要、搜索缓存或旧抓取结果冲突时，以当前页面为准。
2. `-`、空白和缺失单元格永远不等于0；单个次要基金缺失不自动否决整日完整性。
3. 以 IBIT、FBTC、BITB、ARKB、GBTC、BTC 六个核心基金及可解析的 `Total` 判断交易日状态：核心基金完整时为 `complete`，核心基金缺失时为 `provisional`，`Total` 不可解析时为 `unavailable`。
4. 最新交易日为 `provisional` 时，正式日报回退到最近 `complete` 日，并可另行提示暂报总额与缺失基金；没有可靠 `complete` 日时写“未取得可靠完整值”。
5. 正式比较和累计只使用 `complete` 交易日：记录最近完整日、前一完整日、最近3个和5个完整交易日累计，以及连续完整日的 `sign_flip`；不增加投资阈值。
6. 固定记录 `fetched_at_utc`、`source_url`、`latest_page_date`、`latest_complete_date`、`latest_row_status`。

修改原因：

- 此前日报曾因旧缓存错过7月31日的 `-265.4M`，因此必须优先核对当前 Farside 页面并明确区分页面最新日期与正式采用日期。
- 进一步验证发现，Farside 历史最终数据也可能包含单个基金的 `-`；因此不能使用“有 `-` = 整日不完整”的过度简单规则，必须区分单元格完整性与交易日完整性。
