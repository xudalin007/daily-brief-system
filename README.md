# Daily Brief System

Daily Brief System 是一个长期维护的“每日关注简报”规则与状态管理系统。它用于集中保存已经确认的正式规则、规则变更历史和阶段性状态，让每日简报能够在不同时间、不同聊天线程中保持一致。

## 1. 项目目的

本项目服务于长期生成中文每日关注简报，内容覆盖人工智能与科技、比特币与加密市场、宏观经济与投资，以及国内外重大新闻。

随着使用时间增长，单个聊天可能变得很长，也可能出现上下文混乱、旧规则与新规则冲突、历史修改难以追踪等问题。本项目通过把正式规则、变更记录和阶段性状态分别保存到固定文件中，降低对聊天上下文和模型记忆的依赖，使半年或一年后仍能快速理解并继续维护这套简报系统。

## 2. 核心原则

- `rules/daily_brief_rules.md` 是唯一正式规则（Source of Truth）。
- 聊天中的旧讨论、旧提示词或历史内容如果与 `rules/daily_brief_rules.md` 冲突，以该文件的最新版本为准。
- 当用户确认“固定方案 / 固定一下 / 就按这个方案”时，需要：
  1. 更新 `rules/daily_brief_rules.md`；
  2. 在 `changelog/daily_brief_changelog.md` 中追加对应的变更记录。
- CHANGELOG 只追加新记录，不修改或删除历史记录；无法确认的历史日期不应虚构。
- `checkpoints/` 用于保存阶段性状态快照，帮助核对规则并在新聊天中恢复当前状态。
- `reports/` 用于未来保存每日简报。
- `scripts/` 用于未来存放自动化工具。

具体的简报内容、指标、数据口径和判断方法不在 README 中重复定义，均以 `rules/daily_brief_rules.md` 为准。

## 3. 目录结构

```text
daily-brief-system/
├── README.md
├── rules/
│   └── daily_brief_rules.md
├── changelog/
│   └── daily_brief_changelog.md
├── checkpoints/
│   └── checkpoint_2026-07.md
├── reports/
│   └── 2026/
│       └── 07/
└── scripts/
```

各目录和文件的作用如下：

- `README.md`：项目入口说明，介绍项目目的、维护原则、目录结构和操作流程。
- `rules/daily_brief_rules.md`：唯一正式规则源，保存当前有效的简报规则、数据口径和判断原则。
- `changelog/daily_brief_changelog.md`：正式规则的变更历史，只追加，不覆盖已有记录。
- `checkpoints/`：存放按月生成的状态快照。
- `checkpoints/checkpoint_2026-07.md`：2026 年 7 月的系统状态快照，也是当前最新 checkpoint。
- `reports/`：未来的日报归档目录。
- `reports/2026/07/`：预留用于保存 2026 年 7 月的日报。
- `scripts/`：预留用于存放校验、checkpoint 生成、状态查看等自动化工具；当前尚无脚本。

## 4. 日常工作流程

标准流程如下：

```text
讨论新指标或规则
→ 确认方案
→ 固定方案
→ 更新 daily_brief_rules.md
→ 追加 daily_brief_changelog.md
→ 继续生成日报
```

讨论过程本身不等于正式规则。只有方案被明确固定后，才更新正式规则并追加变更记录。日常生成简报时，直接按照 `rules/daily_brief_rules.md` 的最新内容执行；当前典型触发指令为“今天日报”。

## 5. Monthly Checkpoint

每月生成一个状态快照，文件命名格式为：

```text
checkpoint_YYYY-MM.md
```

Checkpoint 用于保存当月结束时的系统状态，包括当前目标、已固定内容、重要数据原则、未解决事项、运行方式和新线程恢复信息。它是状态摘要，不替代正式规则。

新聊天启动时，应优先提供：

1. `rules/daily_brief_rules.md`
2. `changelog/daily_brief_changelog.md`
3. 最新的 `checkpoint_YYYY-MM.md`

## 6. 新线程恢复流程

当现有聊天过长，需要开启新聊天时，按照以下步骤恢复：

1. 找到 `rules/daily_brief_rules.md`。
2. 找到 `changelog/daily_brief_changelog.md`。
3. 在 `checkpoints/` 中选择最新的 `checkpoint_YYYY-MM.md`。
4. 将以上三个文件提供给新聊天。
5. 在新聊天的第一条消息中明确正式规则源和恢复方式。

建议使用以下开场说明：

> 继续每日简报。以 daily_brief_rules.md 为唯一正式规则；CHANGELOG 只追加；最新 checkpoint 用于恢复当前状态。若旧聊天内容与 rules 冲突，以 rules 最新版为准。

完成恢复后，可以继续生成日报、讨论调整方案；新规则只有在用户明确固定后，才进入 rules 和 changelog。

## 7. 当前状态

- 当前基线版本：v1.0
- 当前 checkpoint：2026-07
- Git：尚未初始化
- 自动化脚本：尚未建立

## 8. 后续计划

以下事项仅为后续计划，本阶段尚未实施：

- 初始化 Git
- 建立首次基线提交
- 创建 validate 工具
- 创建 checkpoint 工具
- 创建 status 工具
- 未来考虑日报自动归档
