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
