"""
RSS 数据源抓取模块
"""
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List

import feedparser
from bs4 import BeautifulSoup

from storage import NewsItem


def _clean_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def _normalize_date(date_text: str) -> str:
    if not date_text:
        return ""
    try:
        dt = parsedate_to_datetime(date_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return date_text.strip()


class RSSSource:
    def __init__(self, cfg: Dict, user_agent: str, timeout: int) -> None:
        self.name = cfg["name"]
        self.url = cfg["url"]
        self.max_items = int(cfg.get("max_items", 10))
        self.user_agent = user_agent
        self.timeout = timeout

    def fetch(self) -> List[NewsItem]:
        items: List[NewsItem] = []
        try:
            feed = feedparser.parse(
                self.url,
                request_headers={"User-Agent": self.user_agent},
            )

            if getattr(feed, "bozo", 0):
                logging.warning("[%s] RSS 格式可能异常: %s", self.name, getattr(feed, "bozo_exception", ""))

            for entry in feed.entries[: self.max_items]:
                title = (getattr(entry, "title", "") or "").strip() or "(无标题)"
                link = (getattr(entry, "link", "") or "").strip()
                if not link:
                    continue

                summary_raw = (
                    getattr(entry, "summary", "")
                    or getattr(entry, "description", "")
                    or ""
                )
                summary = _clean_html(summary_raw)[:400]

                published_raw = (
                    getattr(entry, "published", "")
                    or getattr(entry, "updated", "")
                    or ""
                )
                published_at = _normalize_date(published_raw)

                items.append(
                    NewsItem(
                        source=self.name,
                        title=title,
                        url=link,
                        summary=summary,
                        published_at=published_at,
                    )
                )
        except Exception:
            logging.exception("[%s] 拉取 RSS 失败: %s", self.name, self.url)

        return items
