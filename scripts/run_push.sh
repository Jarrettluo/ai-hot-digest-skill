#!/usr/bin/env bash
# =============================================================
# AI 热点速递 · 推送包装（供 OpenClaw cron 命令任务调用）
#   - 正常: 输出速递文本（stdout -> cron announce 到微信）
#   - 无新内容: 输出 NO_REPLY（cron 抑制投递）
#   - 失败: 输出简短失败提示（避免静默黑洞）
# 日志: /tmp/ai-hot-digest.log
# =============================================================
set -u
cd "$(dirname "$0")/.." || { echo "⚠️ AI 热点速递：工作目录不存在"; exit 0; }

LOG=/tmp/ai-hot-digest.log
out="$(/usr/bin/python3 scripts/ai_hot_digest.py --stdout 2>>"$LOG")"
rc=$?

if [ "$rc" -ne 0 ]; then
    echo "⚠️ AI 热点速递今晚生成失败（exit=$rc），请稍后查看日志 /tmp/ai-hot-digest.log"
    exit 0
fi
if [ -z "$out" ]; then
    echo "NO_REPLY"
    exit 0
fi
printf '%s\n' "$out"