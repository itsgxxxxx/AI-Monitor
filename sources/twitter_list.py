"""
Twitter List 数据源 - 基于 twikit 的列表监控
监控指定 Twitter List 中所有成员的推文更新
"""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from twikit import Client

from storage import NewsItem


class TwitterListSource:
    def __init__(self, cfg: Dict, cookies_path: str, llm_config: Optional[Dict] = None, timeout: int = 20):
        self.name = cfg.get("name", "Twitter List Monitor")
        self.list_id = cfg.get("list_id", "")
        self.cookies_path = cookies_path
        self.timeout = timeout
        self.llm_config = llm_config or {}

        # 账号分类配置
        self.account_categories = cfg.get("account_categories", {})

        self._bj_tz = timezone(timedelta(hours=8))
        self._init_time = self._now_beijing()
        self._last_check_time = self._init_time

        # 初始化 twikit 客户端
        self.client = Client(language="en-US")
        self._authenticated = False
        self._loop = asyncio.new_event_loop()

        # 列表成员缓存（screen_name -> user_id）
        self.list_members: Set[str] = set()
        self._members_loaded = False

    def _authenticate(self) -> bool:
        """使用 cookies 认证"""
        if self._authenticated:
            return True

        try:
            cookies_file = Path(self.cookies_path)
            if not cookies_file.exists():
                logging.error("[TwitterList] cookies 文件不存在: %s", self.cookies_path)
                return False

            with open(cookies_file, "r", encoding="utf-8") as f:
                cookies_list = json.load(f)

            cookies_dict = {}
            for cookie in cookies_list:
                cookies_dict[cookie["name"]] = cookie["value"]

            self.client.set_cookies(cookies_dict)
            self._authenticated = True
            logging.info("[TwitterList] 认证成功")
            return True
        except Exception:
            logging.exception("[TwitterList] 认证失败")
            return False

    def _now_beijing(self) -> datetime:
        return datetime.now(self._bj_tz)

    async def _load_list_members_async(self) -> None:
        """加载列表成员"""
        if not self._authenticate():
            return

        try:
            list_obj = await self.client.get_list(self.list_id)
            members = await list_obj.get_members(count=200)

            for member in members:
                screen_name = member.screen_name.lower()
                self.list_members.add(screen_name)

            self._members_loaded = True
            logging.info("[TwitterList] 加载列表成员完成，共 %d 个账号", len(self.list_members))
        except Exception:
            logging.exception("[TwitterList] 加载列表成员失败")

    def _load_list_members(self) -> None:
        """同步包装器"""
        if self._loop.is_closed():
            self._loop = asyncio.new_event_loop()

        try:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._load_list_members_async())
        except RuntimeError as exc:
            if "Event loop is closed" in str(exc):
                logging.warning("[TwitterList] 事件循环已关闭，重建后重试")
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self._load_list_members_async())
            raise

    async def _fetch_list_tweets_async(self, count: int = 50) -> List[Any]:
        """拉取列表推文"""
        if not self._authenticate():
            return []

        try:
            list_obj = await self.client.get_list(self.list_id)
            tweets = await list_obj.get_tweets(count=count)
            return list(tweets)
        except Exception:
            logging.exception("[TwitterList] 拉取列表推文失败")
            return []

    async def _fetch_tweet_detail_async(self, tweet_id: str) -> Optional[Any]:
        """获取推文详情（包含完整 Thread）"""
        if not self._authenticate():
            return None

        try:
            tweet = await self.client.get_tweet_by_id(tweet_id)
            return tweet
        except Exception:
            logging.exception("[TwitterList] 获取推文详情失败: %s", tweet_id)
            return None

    def _fetch_tweet_detail(self, tweet_id: str) -> Optional[Any]:
        """同步包装器"""
        if self._loop.is_closed():
            self._loop = asyncio.new_event_loop()

        try:
            asyncio.set_event_loop(self._loop)
            return self._loop.run_until_complete(self._fetch_tweet_detail_async(tweet_id))
        except RuntimeError as exc:
            if "Event loop is closed" in str(exc):
                logging.warning("[TwitterList] 事件循环已关闭，重建后重试")
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                return self._loop.run_until_complete(self._fetch_tweet_detail_async(tweet_id))
            raise

    def _fetch_list_tweets(self, count: int = 50) -> List[Any]:
        """同步包装器"""
        if self._loop.is_closed():
            self._loop = asyncio.new_event_loop()

        try:
            asyncio.set_event_loop(self._loop)
            return self._loop.run_until_complete(self._fetch_list_tweets_async(count))
        except RuntimeError as exc:
            if "Event loop is closed" in str(exc):
                logging.warning("[TwitterList] 事件循环已关闭，重建后重试")
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                return self._loop.run_until_complete(self._fetch_list_tweets_async(count))
            raise

    def _get_account_category(self, screen_name: str) -> str:
        """获取账号分类"""
        lower = screen_name.lower()
        for category, accounts in self.account_categories.items():
            if lower in [acc.lower() for acc in accounts]:
                return category
        return "其他"

    def _is_retweet_or_quote_from_list(self, tweet: Any) -> Tuple[bool, str]:
        """
        检测是否为列表内账号的转发/引用
        返回 (是否过滤, 原因)
        """
        # 检测 retweet
        if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet:
            original_author = tweet.retweeted_tweet.user.screen_name.lower()
            if original_author in self.list_members:
                return True, f"retweet_from_list_member:{original_author}"

        # 检测 quote tweet
        if hasattr(tweet, 'quoted_tweet') and tweet.quoted_tweet:
            original_author = tweet.quoted_tweet.user.screen_name.lower()
            if original_author in self.list_members:
                return True, f"quote_from_list_member:{original_author}"

        return False, ""

    def _detect_importance_with_rule(self, text: str, screen_name: str, tweet: Any) -> Tuple[str, str]:
        """
        检测推文重要性
        返回 (重要性级别, 命中规则)
        - critical: 🚨级别（模型更新、功能发布、优质工具分享）
        - normal: 普通级别（有价值内容）
        - filter: 过滤（meme、短评论、无价值）
        """
        text_lower = text.lower()

        # 1. 过滤纯 meme 图（只有图片没有实质文本）
        if len(text.strip()) < 20 and hasattr(tweet, 'media') and tweet.media:
            return "filter", "MEME_IMAGE_ONLY"

        # 2. 过滤短评论（少于30字符且无链接）
        if len(text.strip()) < 30 and "http" not in text_lower:
            return "filter", "SHORT_COMMENT"

        # 3. 过滤纯评论性推文（常见评论词）
        comment_only_patterns = [
            "lol", "lmao", "haha", "😂", "🤣",
            "this is", "this was", "so true", "exactly",
            "agree", "disagree", "thoughts?", "what do you think"
        ]
        if len(text.strip()) < 50 and any(p in text_lower for p in comment_only_patterns):
            return "filter", "COMMENT_ONLY"

        # 4. 🚨级别：模型/应用/功能更新
        model_keywords = [
            "gpt", "claude", "gemini", "o1", "o3", "sora", "veo",
            "chatgpt", "claude code", "deepseek", "llama"
        ]
        update_keywords = [
            "release", "released", "launch", "launched", "announce", "announced",
            "introducing", "available now", "now available", "new feature",
            "new model", "new version", "update", "rollout"
        ]

        has_model = any(kw in text_lower for kw in model_keywords)
        has_update = any(kw in text_lower for kw in update_keywords)

        if has_model and has_update:
            return "critical", "MODEL_UPDATE"

        # 5. 🚨级别：AI工具/应用分享
        tool_share_keywords = [
            "tool", "app", "application", "github", "open source",
            "check out", "try this", "built with", "using", "demo"
        ]
        has_tool_share = any(kw in text_lower for kw in tool_share_keywords)
        has_link = "http" in text_lower or "github.com" in text_lower

        category = self._get_account_category(screen_name)
        if category == "AI实用技巧分享" and has_tool_share and has_link:
            return "critical", "TOOL_SHARE"

        # 6. 🚨级别：长文教程/指南（超过500字符的深度内容）
        guide_keywords = [
            "guide", "tutorial", "how to", "step by step", "masterclass",
            "beginner", "advanced", "complete", "ultimate", "comprehensive"
        ]
        has_guide = any(kw in text_lower for kw in guide_keywords)

        if category == "AI实用技巧分享" and has_guide and len(text) > 500:
            return "critical", "GUIDE_TUTORIAL"

        # 7. 🚨级别：官方账号的重要公告
        if category == "AI更新监测" and (has_update or has_model):
            return "critical", "OFFICIAL_ANNOUNCEMENT"

        # 8. 普通级别：有价值的技术分享
        tech_keywords = [
            "prompt", "agent", "api", "code", "build", "tutorial",
            "guide", "tip", "trick", "workflow", "automation"
        ]
        has_tech = any(kw in text_lower for kw in tech_keywords)

        if has_tech and len(text.strip()) > 50:
            return "normal", "TECH_SHARE"

        # 9. 普通级别：AI大佬的深度思考
        if category == "AI大佬" and len(text.strip()) > 100:
            return "normal", "EXPERT_INSIGHT"

        # 默认：普通级别
        return "normal", "DEFAULT"

    def _generate_summary(self, text: str, importance: str) -> str:
        """
        生成推文摘要
        TODO: 可选接入 LLM API
        """
        # 简单版本：截取前200字符
        if len(text) <= 200:
            return text

        # 如果是 critical 级别，可以考虑调用 LLM 生成摘要
        # if importance == "critical" and self.llm_config.get("enabled"):
        #     return self._llm_summarize(text)

        return text[:200] + "..."

    def _parse_tweet_datetime(self, created_at: str) -> Optional[datetime]:
        """解析推文时间"""
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            return dt.astimezone(self._bj_tz)
        except Exception:
            return None

    def _is_in_window(self, tweet_time: Optional[datetime], last_check: datetime, now: datetime) -> bool:
        """检查推文是否在时间窗口内"""
        if tweet_time is None:
            return False
        return last_check < tweet_time <= now

    def fetch(self, poll_id: str = "", run_id: str = "", decision_logger: Any = None) -> List[NewsItem]:
        """获取列表推文"""
        all_items: List[NewsItem] = []
        poll_start_time = self._now_beijing()

        # 首次加载列表成员
        if not self._members_loaded:
            self._load_list_members()

        stats = {
            "raw": 0,
            "time_window": 0,
            "retweet_filter": 0,
            "importance_filter": 0,
            "final": 0,
        }

        # 拉取列表推文
        raw_tweets = self._fetch_list_tweets(count=100)
        stats["raw"] = len(raw_tweets)

        for tweet in raw_tweets:
            try:
                tweet_id = str(tweet.id)
                screen_name = tweet.user.screen_name
                text = tweet.text or ""

                if not text:
                    continue

                # 时间窗口过滤
                created_at = tweet.created_at
                tweet_time = self._parse_tweet_datetime(created_at)

                if not self._is_in_window(tweet_time, self._last_check_time, poll_start_time):
                    if decision_logger:
                        decision_logger.log(
                            poll_id=poll_id,
                            run_id=run_id or poll_id,
                            account=screen_name,
                            tweet_id=tweet_id,
                            tier="LIST",
                            stage="time_window",
                            decision="drop",
                            reason_code="TIME_WINDOW_OLD",
                            matched_rule=created_at,
                        )
                    continue

                stats["time_window"] += 1

                # 检测是否为列表内账号的转发/引用
                is_duplicate, duplicate_reason = self._is_retweet_or_quote_from_list(tweet)
                if is_duplicate:
                    stats["retweet_filter"] += 1
                    if decision_logger:
                        decision_logger.log(
                            poll_id=poll_id,
                            run_id=run_id or poll_id,
                            account=screen_name,
                            tweet_id=tweet_id,
                            tier="LIST",
                            stage="retweet_filter",
                            decision="drop",
                            reason_code="RETWEET_QUOTE_FROM_LIST",
                            matched_rule=duplicate_reason,
                        )
                    logging.info("[%s][TwitterList过滤] %s/%s: %s", poll_id, screen_name, tweet_id, duplicate_reason)
                    continue

                # 检测 Thread（需要获取完整推文详情）
                is_thread = False
                thread_count = 0
                full_text = text

                # 尝试获取完整推文详情以检测 Thread
                try:
                    detailed_tweet = self._fetch_tweet_detail(tweet_id)
                    if detailed_tweet and hasattr(detailed_tweet, 'replies') and detailed_tweet.replies:
                        author_screen_name = screen_name.lower()
                        self_replies = [
                            r for r in detailed_tweet.replies
                            if hasattr(r, 'user') and r.user.screen_name.lower() == author_screen_name
                        ]

                        if self_replies:
                            is_thread = True
                            thread_count = len(self_replies)
                            thread_texts = [text]
                            for reply in self_replies:
                                if hasattr(reply, 'text') and reply.text:
                                    thread_texts.append(reply.text)
                            full_text = "\n\n".join(thread_texts)
                            logging.info(
                                "[%s][TwitterList Thread] %s/%s: 检测到 Thread，包含 %d 条回复",
                                poll_id, screen_name, tweet_id, thread_count
                            )
                except Exception:
                    logging.debug("[%s][TwitterList] 获取 Thread 详情失败: %s/%s", poll_id, screen_name, tweet_id)

                # 重要性检测
                importance, importance_rule = self._detect_importance_with_rule(full_text, screen_name, tweet)

                if importance == "filter":
                    stats["importance_filter"] += 1
                    if decision_logger:
                        decision_logger.log(
                            poll_id=poll_id,
                            run_id=run_id or poll_id,
                            account=screen_name,
                            tweet_id=tweet_id,
                            tier="LIST",
                            stage="importance",
                            decision="drop",
                            reason_code="IMPORTANCE_FILTER",
                            matched_rule=importance_rule,
                        )
                    logging.info("[%s][TwitterList过滤] %s/%s: %s", poll_id, screen_name, tweet_id, importance_rule)
                    continue

                if decision_logger:
                    decision_logger.log(
                        poll_id=poll_id,
                        run_id=run_id or poll_id,
                        account=screen_name,
                        tweet_id=tweet_id,
                        tier="LIST",
                        stage="importance",
                        decision="pass",
                        reason_code=f"IMPORTANCE_{importance.upper()}",
                        matched_rule=importance_rule,
                    )

                # 生成标题和摘要
                category = self._get_account_category(screen_name)
                alert_prefix = "🚨 " if importance == "critical" else ""

                # 标题格式：🚨 [分类] 内容描述
                title = f"{alert_prefix}[{category}] {importance_rule.replace('_', ' ').title()}"

                # 摘要
                summary = self._generate_summary(full_text, importance)

                # 推文链接
                url = f"https://x.com/{screen_name}/status/{tweet_id}"

                # 发布时间
                published_at = tweet_time.isoformat() if tweet_time else ""

                item = NewsItem(
                    source=f"TwitterList:{screen_name}",
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=published_at,
                )
                item.dedupe_text = full_text
                item.account = screen_name
                item.tweet_id = tweet_id
                item.tier = "LIST"
                item.importance = importance
                item.selected_reason = f"importance:{importance_rule}"
                item.category = category
                item.is_thread = is_thread
                item.thread_count = thread_count if is_thread else 0

                all_items.append(item)
                stats["final"] += 1
                logging.info(
                    "[%s][TwitterList选中] %s/%s category=%s importance=%s reason=%s",
                    poll_id, screen_name, tweet_id, category, importance, importance_rule
                )

            except Exception:
                logging.exception("[%s][TwitterList] 解析推文失败", poll_id)
                continue

        # 更新最后检查时间
        self._last_check_time = poll_start_time

        logging.info(
            "[%s][TwitterList埋点] 原始拉取=%s, 时间窗口过滤后=%s, 转发过滤后=%s, 重要性过滤后=%s, 最终输出=%s",
            poll_id,
            stats["raw"],
            stats["time_window"],
            stats["time_window"] - stats["retweet_filter"],
            stats["time_window"] - stats["retweet_filter"] - stats["importance_filter"],
            stats["final"],
        )

        return all_items

    def close(self) -> None:
        """释放资源"""
        try:
            if not self._loop.is_closed():
                try:
                    if hasattr(self.client, "http"):
                        self._loop.run_until_complete(self.client.http.aclose())
                except Exception:
                    logging.debug("[TwitterList] 关闭 HTTP 客户端时忽略异常", exc_info=True)
                self._loop.close()
        except Exception:
            logging.exception("[TwitterList] 关闭资源失败")
