# AI Monitor

AI 新闻监测与推送服务 - 基于 Twitter List 的智能监控系统

## 核心功能

- **Twitter List 监控**：监控指定 Twitter List 中所有成员的推文更新
- **智能去重**：过滤列表内账号互相转发/引用，避免重复推送
- **Thread 完整提取**：自动识别并合并长文章推文
- **重要性分级**：🚨标识重要更新（模型发布、工具分享、长文教程）
- **LLM 智能总结**：使用 AI 自动生成推文摘要（可选）
- **内容过滤**：自动过滤 meme 图、短评论、无价值内容
- **详细日志**：完整记录每条推文的处理流程，方便排查

## 推送格式

### 🚨 Critical 级别
```
🚨 [AI更新监测] Model Update
监控源：@OpenAI
总结：GPT-4.2 预计即将发布，新增多模态能力和更长上下文窗口
推文原文：https://x.com/OpenAI/status/xxx
```

### Normal 级别
```
[AI大佬] Expert Insight
监控源：@karpathy
总结：关于 Transformer 架构优化的深度思考
推文原文：https://x.com/karpathy/status/xxx
```

## 快速开始

### 1. 克隆仓库
```bash
git clone https://github.com/itsgxxxxx/AI-Monitor.git
cd AI-Monitor
```

### 2. 安装依赖
```bash
# 需要 Python 3.10+
python3 --version

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置
```bash
cp config.example.yaml config.yaml
nano config.yaml  # 编辑配置文件
```

填写以下信息：
- **Telegram Bot Token** 和 **Chat ID**
- **Twitter List ID**（从 URL 中提取：`https://x.com/i/lists/YOUR_LIST_ID/members`）
- **Twitter Cookies**（保存到 `cookies.json`）
- **LLM API Key**（可选，推荐使用 aiberm 的 gpt-5-mini，月成本约 4.6 元）

### 4. 准备 Twitter Cookies

从浏览器导出 Twitter cookies 到 `cookies.json`：
```json
[
  {
    "name": "auth_token",
    "value": "你的auth_token",
    "domain": ".x.com"
  },
  {
    "name": "ct0",
    "value": "你的ct0",
    "domain": ".x.com"
  }
]
```

### 5. 启动服务

**前台运行**（测试用）：
```bash
python main.py
```

**后台运行**（生产环境）：
```bash
nohup python main.py > logs/runtime.log 2>&1 &
echo $! > logs/ai_monitor.pid
```

**停止服务**：
```bash
kill $(cat logs/ai_monitor.pid)
```

## 配置说明

### 账号分类

在 `config.yaml` 中配置账号分类：

```yaml
account_categories:
  AI更新监测:
    - claudeai
    - geminiapp
    - openai
  AI实用技巧分享:
    - aiedge_
    - heynavtoor
  AI大佬:
    - karpathy
    - rileybrown
```

### LLM 配置（可选）

推荐使用 aiberm 的 gpt-5-mini：

```yaml
llm:
  api_key: "你的_AIBERM_API_KEY"
  base_url: "https://api.aiberm.com/v1"
  model: "gpt-5-mini"  # 月成本约 4.6 元
  enabled: true
```

**模型选择**：
- `gpt-5-nano`：月成本约 0.9 元（基础能力）
- `gpt-5-mini`：月成本约 4.6 元（推荐，性价比高）
- `claude-sonnet-4-6`：月成本约 29 元（最佳质量）

如果不启用 LLM，设置 `enabled: false`，将使用规则引擎总结。

## 日志检查

### 查看运行状态
```bash
./check_logs.sh
```

会显示：
- 最近一次轮询信息
- 推送成功数量
- 过滤原因统计
- 重要性分级
- 最近推送的推文

### 查询特定推文
```bash
grep "推文ID" logs/decision.jsonl | jq .
```

### 实时监控
```bash
tail -f logs/runtime.log
```

## 重要性规则

### 🚨 Critical（带标识，推送）
- 模型/应用/功能更新（GPT、Claude、Gemini 发布）
- AI 工具/应用分享（GitHub 项目、实用工具）
- 长文教程/指南（超过 500 字符的深度内容）
- 官方账号重要公告

### Normal（无标识，推送）
- 有价值的技术分享（Prompt 技巧、开发经验）
- AI 大佬的深度思考和分析
- 其他有内容价值的推文

### Filter（不推送）
- 纯 meme 图（只有图片无文本）
- 短评论（少于 30 字符且无链接）
- 纯评论性推文（"lol"、"agree"等）

## 故障排查

### 推文没有推送？

查看日志找原因：
```bash
grep "推文ID" logs/decision.jsonl | jq .
```

常见过滤原因：
- `TIME_WINDOW_OLD`：推文时间在窗口外
- `RETWEET_QUOTE_FROM_LIST`：转发/引用列表内账号
- `SHORT_COMMENT`：短评论
- `MEME_IMAGE_ONLY`：纯图片无文本
- `COMMENT_ONLY`：纯评论性推文

### Cookies 过期？

重新登录 Twitter，导出新的 cookies.json，然后重启服务。

### Python 版本问题？

需要 Python 3.10 或更高版本。检查版本：
```bash
python3 --version
```

## 目录结构

```
AI-Monitor/
├── main.py                 # 主程序入口
├── config.yaml             # 配置文件（需自行创建）
├── cookies.json            # Twitter cookies（需自行创建）
├── check_logs.sh           # 日志检查脚本
├── sources/
│   ├── twitter_list.py     # Twitter List 数据源
│   ├── llm.py              # LLM 客户端
│   └── ...
├── notifiers/
│   └── telegram.py         # Telegram 推送
├── logs/
│   ├── decision.jsonl      # 决策日志
│   └── runtime.log         # 运行日志
└── storage.py              # 存储与去重
```

## 安全建议

- ❌ 不要提交 `config.yaml` 和 `cookies.json` 到 Git
- ✅ 定期轮换 API Key 和 Bot Token
- ✅ 使用最小权限的 Telegram Bot Token
- ✅ 定期更新 Twitter Cookies

## 技术栈

- **Python 3.10+**
- **twikit**：Twitter API 客户端
- **Telegram Bot API**：消息推送
- **SQLite**：去重存储
- **aiberm API**：LLM 总结（可选）

## License

MIT
