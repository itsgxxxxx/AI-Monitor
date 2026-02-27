# AI Monitor 2.0 改进计划 (v2)

## 更新说明

- 新增 **changedetection.io** 作为网页变更检测方案
- 新增 **去重机制** 避免 RSS 和 Web 重复推送

---

## 整体架构

```
                    ┌─────────────────────────────────────────────────────┐
                    │                   数据源层                           │
                    │                                                     │
                    │  ┌──────────────────────┐   ┌──────────────────┐  │
                    │  │   Releasebot RSS     │   │ changedetection │  │
                    │  │   (国外厂商/模型)    │   │ (国内厂商 SPA)  │  │
                    │  └──────────┬───────────┘   └────────┬─────────┘  │
                    │             │                        │             │
                    │             │    [去重检查]          │             │
                    │             └──────────┬───────────┘             │
                    └───────────────────────┼─────────────────────────┘
                                            │
                                            ▼
                    ┌─────────────────────────────────────────────────────┐
                    │              Python 轮询服务                        │
                    │                                                     │
                    │  - 定时拉取 Releasebot RSS                        │
                    │  - 与 changedetection.io 数据交叉去重             │
                    │  - SQLite 去重 (基于 URL/title hash)              │
                    │  - 发送 Telegram 通知                             │
                    │                                                     │
                    └─────────────────────────────────────────────────────┘
```

---

## 数据源清单

### Releasebot RSS (国外 + 部分国内)

| 厂商 | 类型 | 备注 |
|------|------|------|
| OpenAI | RSS | 主流模型发布 |
| Anthropic | RSS | Claude 系列 |
| Google | RSS | Gemini |
| DeepSeek | RSS | |
| MiniMax | RSS | |
| xAI | RSS | Grok |
| Meta | RSS | Llama |
| Perplexity | RSS | |
| Mistral | RSS | |

### changedetection.io (国内 SPA + 产品页)

| 厂商 | 监控 URL | 备注 |
|------|----------|------|
| 通义千问 | 待找 | 阿里云反爬，需测试 |
| Kimi (Moonshot) | 待找 | SPA，需 Playwright |
| MiniMax | 待找 | 需确认页面结构 |
| 智谱 AI | 待找 | |

---

## 去重机制设计

### 核心思路

1. **URL 去重** - 如果同一条 URL 已被 Web 源检测过，RSS 不再重复推送
2. **标题相似度去重** - 如果 title 高度相似（>80%），视为同一内容
3. **时间窗口去重** - 24 小时内相同 source 的内容不做重复推送

### 实现方式

```python
# storage.py 新增去重逻辑
class Deduplicator:
    def __init__(self, storage: Storage):
        self.storage = storage
    
    def is_duplicate(self, item: NewsItem) -> bool:
        # 1. URL 精确匹配
        if self.storage.url_exists(item.url):
            return True
        
        # 2. 标题相似度 (Levenshtein / fuzzy match)
        recent = self.storage.get_recent_by_source(item.source, hours=24)
        for old in recent:
            if fuzzy_match(item.title, old.title) > 0.8:
                return True
        
        return False
    
    def mark_as_seen(self, item: NewsItem, source_type: str):
        # source_type: "rss" | "web"
        self.storage.save_entry(item, source_type)
```

### 消息前缀区分

```
[RSS] OpenAI 发布 GPT-5
[WEB] 通义千问 Agent 更新
```

---

## 技术栈

| 组件 | 方案 |
|------|------|
| RSS 轮询 | Python + feedparser + APScheduler |
| 网页检测 | changedetection.io (Docker) |
| 去重 | SQLite + 相似度匹配 |
| 通知 | Telegram Bot (已有) |
| 部署 | Docker + systemd |

---

## 文件结构

```
ai-monitor/
├── app/
│   ├── __init__.py
│   ├── config.py          # Pydantic 配置
│   ├── scheduler.py       # APScheduler
│   └── deduplicator.py   # 去重逻辑 [新增]
├── sources/
│   ├── __init__.py
│   ├── rss.py            # Releasebot RSS
│   └── releasebot.py     # Releasebot 专用解析
├── notifiers/
│   ├── __init__.py
│   └── telegram.py       # Telegram
├── storage/
│   ├── __init__.py
│   └── sqlite.py
├── main.py
├── config.yaml
└── requirements.txt

# changedetection.io 独立部署
docker run -d --restart always \
  -p 5000:5000 \
  -v /opt/changedetection:/datastore \
  dgtlmoon/changedetection.io
```

---

## 实施步骤

### Phase 1: 部署 changedetection.io
```bash
docker run -d --restart always \
  -p 5000:5000 \
  -v /opt/changedetection:/datastore \
  dgtlmoon/changedetection.io
```

### Phase 2: 配置 changedetection.io
- Web UI 登录 (首次设置密码)
- 添加监控 URL
- 配置 Telegram 通知

### Phase 3: 精简 ai-monitor Python 代码
- 移除 web.py
- 新增 deduplicator.py
- 保留 RSS 轮询逻辑

### Phase 4: 测试去重
- 同时触发 RSS 和 Web 同一内容
- 验证只收到一条通知

---

## 待用户确认

1. ✅ Telegram Bot 配置完成
2. ⏳ Releasebot RSS URL (你注册 follow 后获取)
3. ⏳ changedetection.io 部署
4. ⏳ 监控 URL 清单 (通义/Kimi/MiniMax)

---

*最后更新: 2026-02-23*
