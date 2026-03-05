---
name: findsimilarpost
description: 给定 X 推文链接，输出中文表达与中文区同项目高流量帖子，并给出是否建议发送
metadata: {"openclaw":{"emoji":"🔎","requires":{"bins":["python3"],"env":["TIKHUB_API_KEY"]},"primaryEnv":"TIKHUB_API_KEY"}}
---

# Find Similar Post Skill

用于“英文区发现工具/项目帖子后，判断是否值得在中文群发送”的标准流程。

## Agent 配置（任意 Agent 通用）

1. 确保工作目录是仓库根目录（包含 `skills/findsimilarpost/`）。
2. 配置环境变量：
```bash
export TIKHUB_API_KEY="your_tikhub_key"
```
3. 运行固定命令（推荐 Agent 模式）：
```bash
python skills/findsimilarpost/findsimilarpost.py "<tweet_url>" --agent
```

## 触发条件

- 用户提供 X/Twitter 链接
- 目标是做“是否发送到中文群”的判断
- 需要输出中文表达 + 中文区同项目高流量参考帖

## 执行流程（必须按顺序）

1. 拉取原推文正文（Jina Reader）。
2. 自动提取项目关键词（支持 `called X`、GitHub repo 名等）。
3. 生成中文表达（LLM 可用时生成高质量改写，否则自动回退为要点摘录）。
4. 在中文区检索高流量同项目帖子（TikHub）。
5. 生成统一 JSON，包含 `decision.should_send`。

默认筛选阈值（OR）：
- 点赞 >= 100
- 浏览 >= 50,000
- 粉丝 > 5,000

## 输入约定

- 必填：`tweet_url`
- 可选：`--keyword`（手动指定项目名）
- 可选：`--min-likes --min-views --min-followers --max-items`

## 输出约定（Agent 模式）

命令：
```bash
python skills/findsimilarpost/findsimilarpost.py "<tweet_url>" --agent
```

输出为结构化 JSON（无日志干扰），核心字段：
- `status`: `ok | not_found | rate_limited | search_error`
- `keyword`: 识别出的项目关键词
- `source_post_cn`: 英文原文中文表达
- `results[]`: 中文区高流量帖子列表
- `decision.should_send`: `true | false | null`
- `decision.reason`: 判断理由

决策含义：
- `true`: 建议发送（未发现中文区高流量同项目内容）
- `false`: 不建议直接发送（已有高流量同项目内容）
- `null`: 暂无法判断（限流或检索错误）

## 失败回退

- 若 Jina 抓取失败但提供了 `--keyword`，脚本会继续检索。
- 若 TikHub 限流（`DAILY_LIMIT_EXCEEDED`），返回 `status=rate_limited`。
- 若字段不稳定，可加 `--debug` 查看解析结果。

## 给 Agent 的调用模板

“请使用 `$findsimilarpost`。严格按 `skills/findsimilarpost/SKILL.md` 执行，运行 `python skills/findsimilarpost/findsimilarpost.py "<tweet_url>" --agent`，返回 JSON 里的 `decision` 和 `results`。”
