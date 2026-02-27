# TikHub Twitter 监控集成计划（ai-monitor）- v2

## 目标
把 TikHub 的 Twitter-Web API 接入当前 `ai-monitor`，用于持续监控 AI 公司官方账号动态（新推文/时间线），并通过现有去重与 Telegram 通知链路推送。

---

## 1) 如何使用 TikHub API for Twitter

### 1.1 认证与基础调用
- Base URL: `https://api.tikhub.io`
- 认证方式: `Authorization: Bearer <TIKHUB_API_KEY>`
- 主要接口（Twitter-Web）:
  - `GET /api/v1/twitter-web/user_posts`（获取用户推文/时间线核心接口）
  - `GET /api/v1/twitter-web/user_replies`
  - `GET /api/v1/twitter-web/user_media`
  - `GET /api/v1/twitter-web/user_comments`

### 1.2 Python 调用示例

```python
import requests

BASE = "https://api.tikhub.io"
API_KEY = "YOUR_TIKHUB_API_KEY"

resp = requests.get(
    f"{BASE}/api/v1/twitter-web/user_posts",
    params={"screen_name": "openai", "count": 10},
    headers={"Authorization": f"Bearer {API_KEY}"},
    timeout=20,
)
resp.raise_for_status()
data = resp.json()
```

---

## 2) 需要的数据

### 2.1 必需配置
- `tikhub.api_key`: TikHub API key
- `tikhub.base_url`: 默认 `https://api.tikhub.io`
- `sources[].accounts`: 监控账号列表
- `sources[].count`: 每次拉取条数（建议 10）

### 2.2 监控状态
- 每个账号维护 `last_tweet_id`
- 每轮只处理比 `last_tweet_id` 更新的推文
- 失败重试与指数退避

---

## 3) 账号分组与轮询策略

### 3.1 账号分组（合并为厂商）

| 厂商 | 监控账号 | 分组名称 | 备注 |
|------|---------|---------|------|
| OpenAI | openai, OpenAIDevs | OpenAI | 官方+开发者账号 |
| Claude | claudeai, bcherny | Claude | 官方+创始人账号 |
| Google | GeminiApp | Google/Gemini | 官方账号 |

### 3.2 轮询频率（自适应）

| 时段 (北京时间) | 频率 | 说明 |
|-----------------|------|------|
| 10:00 - 20:00 | 30 分钟 | 白天低频（北美深夜到晚上，公司不活跃） |
| 20:00 - 10:00 | 10 分钟 | 夜间高频（北美白天到早上，公司活跃发推）|

**原理**：
- 北京时间 10:00-20:00 = 北美深夜到晚上，公司基本不发推
- 北京时间 20:00-10:00 = 北美白天到早上，公司活跃发布

### 3.3 调用量估算

- 账号数：3 组（OpenAI 2个、Claude 2个、Google 1个 = 5个账号）
- 白天（10:00-20:00，10小时）：30分钟/次 = 20次/天
- 夜间（20:00-10:00，14小时）：10分钟/次 = 84次/天
- **总计：104 次/天**
- **每月（30天）：约 3,120 次**

---

## 4) 内容分级与推送策略

### 4.1 事件分级

| 级别 | 关键词/条件 | 显示 |
|------|-------------|------|
| 🚨 重大更新 (P1) | `release`, `launch`, `new model`, `new API`, `new mode`, `new feature`, `announce`, `introduce`, `version` (v2+, v3+) | 🚨 警报灯 |
| 📝 常规更新 (P2) | `update`, `fix`, `optimize`, `improve`, `bug`, `patch` | 📝 正常推送 |

### 4.2 厂商识别规则

| 账号 | 识别为 |
|------|--------|
| openai, OpenAIDevs | OpenAI |
| claudeai | Claude |
| bcherny | Claude (创始人) |
| GeminiApp | Google/Gemini |

- **官方账号**（如 claudeai）：直接识别为 "Claude"
- **创始人账号**（如 bcherny）：识别为 "Claude (创始人)"，但根据推文内容判断：
  - 如果是产品/模型更新 → 归类为 Claude 重大更新
  - 如果是个人分享/其他 → 归类为常规更新
- 链接放原始推文地址

### 4.3 内容过滤

- **保留**：原创推文、转推（除非明显水内容）
- **降权**：招聘、活动、podcast 等内容
- **过滤关键词**：`hiring`, `event`, `podcast`, `recap`, `welcoming`

---

## 5) 集成改动清单

### 5.1 新增文件
- `sources/tikhub_twitter.py` - TikHub Twitter 数据源

### 5.2 修改文件
- `main.py` - 新增 `type: tikhub_twitter` 分支
- `config.yaml` - 新增 `tikhub` 配置段

### 5.3 配置样例

```yaml
tikhub:
  api_key: "<YOUR_TIKHUB_API_KEY>"
  base_url: "https://api.tikhub.io"

sources:
  - name: AI Twitter Monitor
    type: tikhub_twitter
    enabled: true
    count: 10
    accounts:
      - screen_name: openai
        vendor: OpenAI
      - screen_name: OpenAIDevs
        vendor: OpenAI
      - screen_name: claudeai
        vendor: Claude
      - screen_name: bcherny
        vendor: Claude
      - screen_name: GeminiApp
        vendor: Google/Gemini
```

---

## 6) 输出模板

沿用现有 Telegram 模板格式：

**重大更新：**
```
🚨 AI 重大更新
厂商: OpenAI
更新: [推文标题/前80字]
北京时间: 2026年02月24日 14:30
总结: [LLM 生成的总结 + 应用场景]
原文: https://x.com/OpenAI/status/xxxxx
```

**常规更新（创始人）：**
```
📝 AI 更新速报
厂商: Claude (创始人)
更新: [推文标题/前80字]
北京时间: 2026年02月24日 11:15
总结: [简要说明]
原文: https://x.com/bcherny/status/xxxxx
```

---

## 7) 实施顺序

1. ✅ 方案确认（本方案）
2. 实现 `TikHubTwitterSource` + `config` 扩展
3. 跑通 2-3 个账号的真实抓取与 Telegram 推送
4. 加入自适应轮询频率
5. 扩展到完整 AI 公司列表

---

## 参考资料
- TikHub 官方文档: https://docs.tikhub.io/
- TikHub API 调试: https://api.tikhub.io
