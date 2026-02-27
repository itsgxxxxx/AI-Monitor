"""
网页变更检测模块（用于无 RSS 的源）
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from storage import NewsItem, Storage


class WebSource:
    def __init__(self, cfg: Dict, user_agent: str, timeout: int) -> None:
        self.name = cfg["name"]
        self.url = cfg["url"]
        self.max_items = int(cfg.get("max_items", 10))
        self.article_selector = cfg.get("article_selector", "article")
        self.title_selector = cfg.get("title_selector", "h1, h2, h3, a")
        self.link_selector = cfg.get("link_selector", "a[href]")
        self.summary_selector = cfg.get("summary_selector", "p")
        self.date_selector = cfg.get("date_selector", "time")
        self.fallback_change_notice = bool(cfg.get("fallback_change_notice", True))
        self.user_agent = user_agent
        self.timeout = timeout

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _fetch_html(self) -> str:
        resp = requests.get(
            self.url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.text

    def _parse_structured_items(self, html: str) -> List[NewsItem]:
        soup = BeautifulSoup(html, "html.parser")
        containers = soup.select(self.article_selector)
        items: List[NewsItem] = []
        seen_urls: Set[str] = set()

        # 若未命中容器，使用启发式：全站链接中筛选新闻/博客相关
        if not containers:
            candidate_links = soup.select("a[href]")
            for a in candidate_links:
                href = (a.get("href") or "").strip()
                text = a.get_text(" ", strip=True)
                if not href or len(text) < 4:
                    continue
                full_url = urljoin(self.url, href)
                key = full_url.lower()
                if key in seen_urls:
                    continue
                if any(k in key for k in ["/blog", "/news", "/post", "/article"]):
                    seen_urls.add(key)
                    items.append(
                        NewsItem(
                            source=self.name,
                            title=text[:160],
                            url=full_url,
                            summary="",
                            published_at="",
                        )
                    )
                if len(items) >= self.max_items:
                    break
            return items

        for container in containers:
            title_el = container.select_one(self.title_selector)
            if title_el is None:
                continue

            title = title_el.get_text(" ", strip=True)[:160]
            if not title:
                continue

            link_el = container.select_one(self.link_selector)
            href = ""
            if link_el is not None:
                href = (link_el.get("href") or "").strip()
            if not href and title_el.name == "a":
                href = (title_el.get("href") or "").strip()

            if not href:
                continue

            full_url = urljoin(self.url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            summary_el = container.select_one(self.summary_selector)
            summary = summary_el.get_text(" ", strip=True)[:400] if summary_el else ""

            date_el = container.select_one(self.date_selector)
            published_at = date_el.get_text(" ", strip=True) if date_el else ""

            items.append(
                NewsItem(
                    source=self.name,
                    title=title,
                    url=full_url,
                    summary=summary,
                    published_at=published_at,
                )
            )

            if len(items) >= self.max_items:
                break

        return items

    def fetch(self, storage: Storage) -> List[NewsItem]:
        try:
            html = self._fetch_html()
        except Exception:
            logging.exception("[%s] 网页抓取失败: %s", self.name, self.url)
            return []

        # 先尝试结构化解析
        items = self._parse_structured_items(html)

        if items:
            fingerprint = "||".join(f"{it.title}|{it.url}" for it in items)
            page_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            storage.upsert_snapshot_hash(self.name, page_hash)
            return items

        # 结构化解析失败时，做整页变更检测
        current_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
        old_hash = storage.get_snapshot_hash(self.name)
        storage.upsert_snapshot_hash(self.name, current_hash)

        if self.fallback_change_notice and old_hash and old_hash != current_hash:
            return [
                NewsItem(
                    source=self.name,
                    title="官网页面检测到更新",
                    url=self.url,
                    summary="未解析到结构化文章，但页面内容发生变化。",
                    published_at=self._now_iso(),
                )
            ]

        return []
