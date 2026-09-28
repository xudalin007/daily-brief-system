#!/bin/bash
# CVDD 换算因子自动校准（no_agent 定时任务，每天 13:05 —— 早于 13:30 日报）
# 静默设计：因子没变 → 无输出（不打扰）；变了 → 输出一行提示（会推送到微信）
exec python3 "$(dirname "$0")/cvdd_autocalibrate.py"
