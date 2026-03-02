"""
SQLite 存储层：去重 + 网页快照
"""
import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class NewsItem:
    source: str
    title: str
    url: str
    summary: str = ""
    published_at: str = ""  # 建议 ISO 8601 字符串


class Storage:
    """SQLite 存储层：去重 + 网页快照"""
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    published_at TEXT DEFAULT '',
                    content_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    source TEXT PRIMARY KEY,
                    page_hash TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entries_source_created ON entries(source, created_at)"
            )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_text(text: str) -> str:
        """标准化文本，尽量减少格式差异导致的重复漏判。"""
        normalized = (text or "").casefold()
        normalized = normalized.replace("\u200b", " ").replace("\ufeff", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    @staticmethod
    def build_item_hash(item: NewsItem) -> str:
        # 去重优先级：
        # 1) dedupe_text: 来源层提供的完整正文（最准确）
        # 2) title + summary: 兼容未提供 dedupe_text 的来源
        # 3) url: 无文本时兜底，避免空文本全部撞哈希
        dedupe_text = getattr(item, "dedupe_text", "")
        normalized_text = Storage._normalize_text(dedupe_text)
        if not normalized_text:
            normalized_text = Storage._normalize_text(f"{item.title}\n{item.summary}")

        if normalized_text:
            payload = f"text:{normalized_text}"
        else:
            payload = f"url:{Storage._normalize_text(item.url)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save_if_new(self, item: NewsItem) -> bool:
        item_hash = self.build_item_hash(item)
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO entries
                    (source, title, url, summary, published_at, content_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.source,
                        item.title,
                        item.url,
                        item.summary,
                        item.published_at,
                        item_hash,
                        self._now_iso(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception:
            logging.exception("写入 SQLite 失败: source=%s url=%s", item.source, item.url)
            return False

    def get_snapshot_hash(self, source: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT page_hash FROM snapshots WHERE source = ?",
            (source,),
        ).fetchone()
        return row["page_hash"] if row else None

    def upsert_snapshot_hash(self, source: str, page_hash: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO snapshots(source, page_hash, last_checked_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    page_hash = excluded.page_hash,
                    last_checked_at = excluded.last_checked_at
                """,
                (source, page_hash, self._now_iso()),
            )

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            logging.exception("关闭 SQLite 连接失败")
