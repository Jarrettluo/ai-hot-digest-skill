---
name: "ai-hot-digest"
description: "每晚聚合 AI 热点（新闻/模型/MCP/Skills/套餐）并推送微信的 OpenClaw skill。Daily AI hot news aggregation with LLM summary and WeChat push."
version: "v1"
---

# AI 热点速递（ai-hot-digest）

每晚聚合多个渠道的 AI 领域热点信息（行业新闻、模型动态、MCP 生态、Skills 技能、Coding Plan 套餐），
用 LLM 筛选摘要后通过微信（OpenClaw cron announce）推送给用户。

## 何时使用

- 用户想要 AI 领域每日热点聚合推送（新闻 / 新模型 / MCP / Skills / 套餐价格）
- 用户说「每天的 AI 热点推给我」「聚合热门 AI 信息」「AI 日报」
- 需要排查/调整已有的每晚 AI 速递推送

## 快速开始

```bash
# 手动运行（生成 + 打印推送文本）
python3 {baseDir}/scripts/ai_hot_digest.py --print

# 仅输出推送消息（供 cron announce 使用）
bash {baseDir}/scripts/run_push.sh
```

## 数据源（全部实测可访问，2026-10 验证）

| 渠道 | 接口 | 说明 |
|------|------|------|
| Hacker News | hn.algolia.com API | 24h 内 >60 分且含 AI 关键词的帖子 |
| 量子位 | https://www.qbitai.com/feed | 中文 AI 新闻 |
| TechCrunch AI | RSS feed | 英文行业新闻 |
| The Verge AI | RSS feed | 英文科技新闻 |
| Ars Technica AI | RSS feed | 英文深度报道 |
| OpenAI 官方 | https://openai.com/news/rss.xml | 官方发布 |
| Simon Willison | atom feed | 技术博客 |
| Import AI | https://jack-clark.net/feed/ | 行业分析 |
| GitHub 新项目 | api.github.com search | 近 7 天创建、>80 星、AI 相关 |
| MCP 官方注册表 | registry.modelcontextprotocol.io | 近 72h 新收录/更新（支持 updated_since 参数） |
| ClawHub | clawhub.ai/api/v1/skills | 近 26h 新技能（免鉴权） |
| OpenRouter | openrouter.ai/api/v1/models | 近 3 天新上架模型及价格 |
| wmpeng/codingplan | jsdelivr 镜像 config.json | 国内套餐对比数据更新提醒 |

## 运行机制

1. 并行扫描上述所有渠道（ThreadPoolExecutor），各自收集近 26h（可调）的条目
2. 与 `state/seen.json` 去重（URL 规范化 + 30 天窗口清理）；MCP 用 `name|updatedAt` 键
3. 候选条目（≤60 条）提交 LLM（百炼 token-plan，Anthropic 协议）筛选并生成中文 JSON 摘要：
   - highlights（3-6 条最重要）+ sections（news/mcp/skills/models/plans，每板块≤4 条）
   - 输出渲染为微信推送文本（≤3400 字符）
4. JSON 解析失败重试 2 次；LLM 不可用时回退为原始扫描列表
5. 更新去重状态；无新内容时输出 `NO_REPLY`（cron 静默不打扰）

## 配置

- LLM: `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（默认百炼 token-plan `deepseek-v4.1-flash`）
- 别名: 直接编辑脚本顶部 `LLM_KEY` 默认值
- 窗口: `--hours 26`（默认）；`--no-llm`（跳过 LLM）

## 定时任务（已配置）

OpenClaw cron `ai-hot-digest`：每晚 22:00 (Asia/Shanghai)，命令型 payload 调用
`bash scripts/run_push.sh`，announce 到微信。
- 输出 `NO_REPLY` 时抑制投递
- 失败输出简短提示（避免静默黑洞）
- 日志: `/tmp/ai-hot-digest.log`

## 状态文件

- `state/seen.json` — 去重状态（seen / mcp_seen / codingplan_date）
- `state/latest_message.txt` — 最近一次生成的推送文本
- `output/ai-hot-YYYYMMDD.md` — 每日归档
- 故障时保留 `state/llm-raw.txt`（LLM 原始输出，便于排障）

## 调整指南

- 换渠道: 编辑脚本 `FEEDS` 列表或对应 `c_*` 函数
- 换时间: `openclaw cron edit <id> --cron "0 21 * * *"`
- 加板块: 在 LLM prompt 的 sections 说明中加入新 key，并在 `render()` 无需改动（按 key 动态渲染）
- 推送长度: 渲染时 >3400 字符自动截断（微信长消息不宜过长）

## 已知注意事项

- LLM 使用 deepseek 系模型会先输出 thinking 块：`max_tokens` 需要 ≥8000，否则 JSON 输出可能被截断
- raw.githubusercontent 在本机被墙：GitHub 数据用 api.github.com；jsdelivr 镜像可作 raw 替代
- 推送目标（如微信 ID）需根据所用推送渠道的要求配置
