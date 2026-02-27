"""
Telegram Bot 通知模块 - LLM 总结版
"""
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests

from storage import NewsItem
from sources.llm import MiniMaxClient


class TelegramNotifier:
    def __init__(self, cfg: Dict) -> None:
        # 支持传入完整配置或仅 telegram 配置
        if "telegram" in cfg:
            telegram_cfg = cfg["telegram"]
            llm_cfg = cfg.get("llm")
        else:
            telegram_cfg = cfg
            llm_cfg = None

        self.bot_token = telegram_cfg["bot_token"]
        self.chat_id = str(telegram_cfg["chat_id"])
        self.timeout = int(telegram_cfg.get("timeout", 15))
        self.disable_web_page_preview = bool(telegram_cfg.get("disable_web_page_preview", True))

        self.llm: Optional[MiniMaxClient] = None
        if llm_cfg:
            try:
                self.llm = MiniMaxClient(llm_cfg)
                logging.info("LLM 客户端初始化成功")
            except Exception:
                logging.exception("LLM 客户端初始化失败")

        self.endpoint = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def _extract_vendor(self, item: NewsItem) -> str:
        """从标题提取实际厂商名"""
        title = item.title.strip()

        vendors = [
            "OpenAI", "Anthropic", "Google", "DeepSeek", "MiniMax", "xAI", "Meta", "Perplexity", "Mistral",
            "OpenClaw", "Claude", "Gemini", "Llama", "Grok", "Kimi", "通义千问", "智谱", "Qwen",
        ]

        if ":" in title:
            vendor_candidate = title.split(":")[0].strip()
            for v in vendors:
                if v.lower() in vendor_candidate.lower():
                    return v

        text = f"{title} {item.summary}".lower()
        for v in vendors:
            if v.lower() in text:
                return v

        return item.source

    def _detect_importance(self, item: NewsItem) -> str:
        """检测更新重要性"""
        text = f"{item.title} {item.summary}".strip().lower()

        acquisition_keywords = [
            "acqui", "acquisition", "acquire", "merge", "merger", "收购", "并购", "合并",
            "acquired", "acquires", "acquired by",
        ]
        for kw in acquisition_keywords:
            if kw in text:
                return "minor"

        major_keywords = [
            "new", "release", "launch", "debut", "introduce", "announce",
            "新", "发布", "推出", "上线", "首发", "重大",
            "gpt-5", "gpt4", "claude-4", "opus", "sonnet",
            "gemini", "llama", "deepseek", "qwen", "minimax",
            "model", "api", "version", "v2", "v3", "v4", "v5",
            "feature", "plugin", "mode", "extend", "support",
            "cowork", "codex", "app", "agent", "tool",
        ]

        normal_keywords = [
            "fix", "patch", "bug", "hotfix", "maintenance",
            "update", "修复", "补丁", "维护",
        ]

        for kw in major_keywords:
            if kw in text:
                return "major"

        for kw in normal_keywords:
            if kw in text:
                return "normal"

        return "normal"

    def _llm_summarize(self, item: NewsItem, importance: str) -> str:
        """使用 LLM 总结更新内容"""
        if not self.llm:
            return self._rule_summarize(item, importance)

        if importance == "major":
            system_prompt = """你是一个专业的 AI 产品分析师。用户会给你一条 AI 产品更新通知，你需要用中文简洁地总结这次更新的核心变化，并列出2-3个实际应用场景。

直接输出总结内容，不要加标题或前缀。"""

            user_prompt = f"""标题：{item.title}
摘要：{item.summary}
来源：{item.source}
链接：{item.url}"""
        else:
            system_prompt = """你是一个简洁的 AI 产品播报员。对于常规更新和收购/并购类新闻，用一两句话带过即可，保持简洁。

直接输出总结内容，不要加标题或前缀。"""

            user_prompt = f"""标题：{item.title}
摘要：{item.summary}
来源：{item.source}"""

        result = self.llm.chat(system_prompt, user_prompt)
        if result:
            return result.strip()
        return self._rule_summarize(item, importance)

    def _rule_summarize(self, item: NewsItem, importance: str) -> str:
        """规则引擎总结（LLM 不可用时备用）"""
        title = item.title.strip()

        if importance == "major":
            version_match = re.search(r"(v\d+(?:\.\d+)*|\d+\.\d+)", title, re.IGNORECASE)
            version_info = f" 版本 {version_match.group()}" if version_match else ""

            key_info = []
            if "gpt" in title.lower():
                key_info.append("GPT")
            if "claude" in title.lower():
                key_info.append("Claude")
            if "gemini" in title.lower():
                key_info.append("Gemini")
            if "llama" in title.lower():
                key_info.append("Llama")
            if "deepseek" in title.lower():
                key_info.append("DeepSeek")
            if "openclaw" in title.lower():
                key_info.append("OpenClaw")

            if key_info:
                return f"{', '.join(key_info)}{version_info} 发布更新"
            return f"产品更新{version_info}"

        return "常规更新"

    def _beijing_time(self, iso_date: str) -> str:
        """转换为北京时间"""
        try:
            if iso_date:
                dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
                beijing = dt.astimezone(timezone(timedelta(hours=8)))
                return beijing.strftime("%Y年%m月%d日 %H:%M")
            return datetime.now(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日 %H:%M")
        except Exception:
            return datetime.now(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日 %H:%M")

    def _format_item(self, item: NewsItem) -> str:
        importance = self._detect_importance(item)
        beijing_time = self._beijing_time(item.published_at)

        vendor = getattr(item, 'vendor', None) or self._extract_vendor(item)

        if hasattr(item, 'importance') and item.importance:
            importance = item.importance

        summary = self._llm_summarize(item, importance)
        title_prefix = "🚨 " if importance == "major" else ""
        title_line = f"{title_prefix}{vendor}: {item.title}"

        return (
            f"{title_line}\n"
            f"北京时间: {beijing_time}\n"
            f"{summary}\n"
            f"原文: {item.url}"
        )

    def _send_text(self, text: str) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": self.disable_web_page_preview,
        }

        try:
            resp = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok", False):
                logging.error("Telegram 返回失败: %s", data)
                return False
            return True
        except Exception:
            logging.exception("Telegram 推送失败")
            return False

    def send_batch(self, items: List[NewsItem]) -> bool:
        """白天批次推送：把本轮新闻汇总成 1~N 条消息。"""
        if not items:
            return True

        # major 放前面
        def score(it: NewsItem) -> int:
            imp = getattr(it, "importance", None) or self._detect_importance(it)
            return 0 if imp == "major" else 1

        ordered = sorted(items, key=score)

        now_bj = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        header = f"🧾 白天批次更新（{len(ordered)}条）\n北京时间: {now_bj}\n"

        lines: List[str] = []
        for idx, item in enumerate(ordered, 1):
            importance = getattr(item, "importance", None) or self._detect_importance(item)
            prefix = "🚨" if importance == "major" else "•"
            vendor = getattr(item, 'vendor', None) or self._extract_vendor(item)
            title = " ".join((item.title or "").split())
            if len(title) > 90:
                title = title[:90] + "..."
            lines.append(f"{idx}. {prefix} {vendor}: {title}\n{item.url}")

        # Telegram 单条限制约 4096，留余量
        chunks: List[str] = []
        current = header
        for line in lines:
            block = line + "\n\n"
            if len(current) + len(block) > 3600:
                chunks.append(current.rstrip())
                current = "🧾 批次续报\n" + block
            else:
                current += block
        if current.strip():
            chunks.append(current.rstrip())

        all_ok = True
        for msg in chunks:
            ok = self._send_text(msg)
            all_ok = all_ok and ok

        return all_ok

    def send(self, item: NewsItem) -> bool:
        text = self._format_item(item)
        return self._send_text(text)
