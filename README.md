# AI Monitor

AI 新闻监测与推送服务（当前以 Telegram 推送为主）。

## 功能
- 多源监测（当前包含 TikHub Twitter、RSS）
- 轮询抓取与去重入库（SQLite）
- 重要性分级与推送（major/minor）
- 夜间逐条推送 / 白天批次汇总推送

## 快速开始
```bash
git clone <your-repo-url>
cd AI-Monitor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
# 编辑 config.yaml 填写你的密钥和目标 chat
python main.py
```

## 配置
请基于 `config.example.yaml` 创建本地 `config.yaml`，并填写：
- Telegram Bot Token / Chat ID
- LLM API Key
- TikHub API Key

> `config.yaml` 已被 `.gitignore` 忽略，避免密钥进入仓库。

## 安全建议
- 不要提交真实密钥
- 定期轮换 API Key/Bot Token
- 生产环境使用最小权限 Token

## 目录
- `main.py`：主程序入口
- `sources/`：数据源实现
- `notifiers/`：通知实现
- `storage.py`：存储与去重逻辑
