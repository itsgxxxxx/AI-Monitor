"""
Twikit Twitter 数据源 - 分层轮询 + 增量窗口版
使用 twikit 库通过 cookies 认证获取推文
"""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from twikit import Client

from storage import NewsItem


class TwikitTwitterSource:
    def __init__(self, cfg: Dict, cookies_path: str, timeout: int = 20):
        self.name = cfg.get("name", "Twitter Monitor")
        self.accounts = cfg.get("accounts", [])
        self.count = cfg.get("count", 10)
        self.cookies_path = cookies_path
        self.timeout = timeout

        self._bj_tz = timezone(timedelta(hours=8))
        self._init_time = self._now_beijing()

        # 初始化 twikit 客户端
        self.client = Client(language="en-US")
        self._authenticated = False
        # 复用同一个事件循环，避免每次 asyncio.run() 造成 loop 反复关闭
        self._loop = asyncio.new_event_loop()

        # 厂商映射
        self.vendor_map: Dict[str, Dict[str, Any]] = {}

        # 账号状态（用于分层调度 + 各账号独立增量窗口）
        self.account_states: Dict[str, Dict[str, Any]] = {}

        for acc in self.accounts:
            screen_name = (acc.get("screen_name", "") or "").strip()
            if not screen_name:
                continue

            tier = str(acc.get("tier", "A") or "A").upper()
            if tier not in {"S", "A", "B"}:
                tier = "A"

            lower = screen_name.lower()
            vendor = acc.get("vendor", "")
            is_founder = acc.get("is_founder", False)
            self.vendor_map[lower] = {"vendor": vendor, "is_founder": is_founder}

            self.account_states[lower] = {
                "tier": tier,
                "last_check_time": self._init_time,
                "last_polled_at": None,
                "no_news_streak": 0,
                "next_due_at": self._init_time,
            }

    def _authenticate(self) -> bool:
        """使用 cookies 认证"""
        if self._authenticated:
            return True

        try:
            cookies_file = Path(self.cookies_path)
            if not cookies_file.exists():
                logging.error("[Twikit] cookies 文件不存在: %s", self.cookies_path)
                return False

            with open(cookies_file, "r", encoding="utf-8") as f:
                cookies_list = json.load(f)

            # 转换 cookies 格式：从浏览器导出格式转为 dict
            cookies_dict = {}
            for cookie in cookies_list:
                cookies_dict[cookie["name"]] = cookie["value"]

            # 设置 cookies
            self.client.set_cookies(cookies_dict)
            self._authenticated = True
            logging.info("[Twikit] 认证成功")
            return True
        except Exception:
            logging.exception("[Twikit] 认证失败")
            return False

    def _now_beijing(self) -> datetime:
        return datetime.now(self._bj_tz)

    def _is_night_window(self, dt: Optional[datetime] = None) -> bool:
        """夜间窗口：21:00-03:00（北京时间）"""
        now = dt or self._now_beijing()
        hour = now.hour
        return hour >= 21 or hour < 3

    def _day_base_minutes(self, tier: str) -> int:
        return 60 if tier == "B" else 30

    def _next_interval_minutes(self, tier: str, no_news_streak: int, now: datetime) -> int:
        """
        分层间隔策略：
        - 夜间（21:00-03:00）：固定 15m
        - 白天（03:00-21:00）：
          - S:   30 -> 60 (max)
          - A:   30 -> 60 -> 90 (max)
          - B:   60 -> 90 (max)
        """
        if self._is_night_window(now):
            return 15

        base = self._day_base_minutes(tier)
        if base == 60:
            return 60 if no_news_streak <= 0 else 90

        # S 级白天最长 60 分钟
        if tier == "S":
            return 30 if no_news_streak <= 0 else 60

        # base=30
        if no_news_streak <= 0:
            return 30
        if no_news_streak == 1:
            return 60
        return 90

    def _get_poll_interval(self) -> int:
        """
        主循环节拍：
        - 夜间 15m
        - 白天 30m
        """
        now = self._now_beijing()
        return (15 if self._is_night_window(now) else 30) * 60

    def _get_state(self, screen_name: str) -> Dict[str, Any]:
        lower = screen_name.lower()
        if lower not in self.account_states:
            self.account_states[lower] = {
                "tier": "A",
                "last_check_time": self._init_time,
                "last_polled_at": None,
                "no_news_streak": 0,
                "next_due_at": self._init_time,
            }
        return self.account_states[lower]

    def _should_poll_account(self, screen_name: str, now: datetime) -> bool:
        state = self._get_state(screen_name)

        # 夜间强制按 15 分钟节拍
        if self._is_night_window(now):
            last_polled = state.get("last_polled_at")
            if not last_polled:
                return True
            return (now - last_polled).total_seconds() >= 15 * 60

        # 白天按账号 next_due_at
        next_due = state.get("next_due_at")
        if not next_due:
            return True
        return now >= next_due

    def _advance_account_schedule(self, screen_name: str, has_news: bool, now: datetime) -> None:
        state = self._get_state(screen_name)
        tier = state.get("tier", "A")

        if has_news:
            state["no_news_streak"] = 0
        else:
            state["no_news_streak"] = min(int(state.get("no_news_streak", 0)) + 1, 6)

        state["last_polled_at"] = now
        interval_minutes = self._next_interval_minutes(tier, int(state["no_news_streak"]), now)
        state["next_due_at"] = now + timedelta(minutes=interval_minutes)

        logging.info(
            "[Twitter调度] %s tier=%s has_news=%s streak=%s next=%s (%sm)",
            screen_name,
            tier,
            has_news,
            state["no_news_streak"],
            state["next_due_at"].strftime("%H:%M:%S"),
            interval_minutes,
        )

    def _detect_importance_with_rule(self, text: str, vendor: str = "") -> Tuple[str, str]:
        """检测内容重要性并返回命中规则"""
        text_lower = text.lower()

        # AI快讯账号特殊过滤
        if vendor == "AI快讯":
            filter_keywords = [
                "perplexity", "kane ai", "keep", "raycast", "alfred",
                "linear", "notion", "obsidian", "cursor",
            ]
            for kw in filter_keywords:
                if kw in text_lower:
                    return "filter", kw

            important_keywords = [
                "openai", "anthropic", "google", "meta", "microsoft", "apple", "amazon",
                "deepseek", "mistral", "llama", "claude", "gpt", "gemini",
                "chatgpt", "copilot", "cursor", "windsurf",
            ]
            has_important = any(kw in text_lower for kw in important_keywords)
            if not has_important:
                return "filter", "AI快讯_非大厂"

        core_product_keywords = [
            "gpt", "chatgpt", "o1", "o3", "o4",
            "claude", "claude code",
            "gemini", "veo", "sora",
            "codex", "operator",
            "llama", "deepseek", "qwen", "mistral",
        ]
        release_keywords = [
            "release", "released", "launch", "launched",
            "announce", "announced", "introduce", "introduced", "introducing", "debut",
            "rolling out", "rollout", "available now", "now available",
            "general availability", "ga", "public beta",
        ]
        new_artifact_keywords = [
            "new model", "new api", "new mode", "new feature",
            "version ", "v2", "v3", "v4", "v5",
            "gpt-5", "claude 4", "gemini 2",
        ]
        capability_action_keywords = [
            "support", "supports", "supported",
            "integrate", "integration", "connect", "connected",
            "enable", "enabled", "available", "rollout", "rolling out",
        ]
        high_impact_capability_keywords = [
            "reason", "reasoning", "chain of thought",
            "image", "vision", "video", "pdf",
            "context window", "long context",
            "memory", "agent", "tool use", "search",
            "maps", "navigation", "route",
        ]
        scenario_keywords = [
            "planning", "plan", "workflow", "trip", "travel",
            "coding", "customer support", "analysis", "automation",
        ]
        partner_platform_keywords = [
            "google maps", "gmail", "google docs", "drive",
            "slack", "notion", "github", "calendar", "youtube",
        ]

        minor_update_keywords = [
            "add ", "added ", "adds ",
            "new skill", "new skills", "added to new skills",
            "now support", "support for",
            "enhance", "enhanced", "enhancement",
            "new capability", "improvement",
        ]
        maintenance_only_keywords = [
            "bug", "bugfix", "bug fix", "hotfix",
            "patch", "maintenance", "stability", "typo",
        ]

        has_core_product = any(kw in text_lower for kw in core_product_keywords)
        has_release = any(kw in text_lower for kw in release_keywords)
        has_new_artifact = any(kw in text_lower for kw in new_artifact_keywords)
        has_capability_action = any(kw in text_lower for kw in capability_action_keywords)
        has_high_impact_capability = any(kw in text_lower for kw in high_impact_capability_keywords)
        has_partner_platform = any(kw in text_lower for kw in partner_platform_keywords)
        has_scenario = any(kw in text_lower for kw in scenario_keywords)

        # Major-1: 新模型/新 API 正式发布
        if has_core_product and has_release and has_new_artifact:
            return "major", "MAJOR_MODEL_OR_API_RELEASE"
        # Major-2: 核心产品重大能力升级
        if has_core_product and has_capability_action and has_high_impact_capability:
            return "major", "MAJOR_CAPABILITY_RELEASE"
        # Major-3: 明确的平台联动+应用场景（如 Gemini + Maps 路线规划）
        if has_core_product and has_capability_action and has_partner_platform and has_scenario:
            return "major", "MAJOR_PLATFORM_INTEGRATION_SCENARIO"
        # Major-4: 面向全量用户/开发者可用的能力上线
        if has_core_product and has_capability_action and has_high_impact_capability and any(
            kw in text_lower for kw in ["all users", "all developers", "everyone", "globally"]
        ):
            return "major", "MAJOR_GLOBAL_AVAILABILITY"

        # Minor: 增量能力更新（如 add two new skills）
        for kw in minor_update_keywords:
            if kw in text_lower:
                return "minor", kw

        # 普通维护类更新不推送
        if any(kw in text_lower for kw in maintenance_only_keywords):
            return "normal", "MAINTENANCE_ONLY"

        return "normal", ""

    def _get_vendor(self, screen_name: str) -> str:
        info = self.vendor_map.get(screen_name.lower(), {})
        vendor = info.get("vendor", screen_name)
        is_founder = info.get("is_founder", False)

        if is_founder:
            return f"{vendor} (创始人)"
        return vendor

    def _parse_tweet_datetime(self, created_at: str) -> Optional[datetime]:
        """解析推文时间"""
        try:
            # twikit 返回的时间格式可能是 ISO 8601
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            return dt.astimezone(self._bj_tz)
        except Exception:
            return None

    def _is_in_window(self, tweet_time: Optional[datetime], last_check_time: datetime, now: datetime) -> bool:
        if tweet_time is None:
            return False
        return last_check_time < tweet_time <= now

    def _fetch_user_tweets(self, screen_name: str) -> List[Any]:
        """使用 twikit 拉取用户推文（同步包装器）"""
        if self._loop.is_closed():
            self._loop = asyncio.new_event_loop()

        try:
            asyncio.set_event_loop(self._loop)
            return self._loop.run_until_complete(self._fetch_user_tweets_async(screen_name))
        except RuntimeError as exc:
            # 极端情况下 loop 状态异常，重建后重试一次
            if "Event loop is closed" in str(exc):
                logging.warning("[Twikit] 事件循环已关闭，重建后重试: %s", screen_name)
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                return self._loop.run_until_complete(self._fetch_user_tweets_async(screen_name))
            raise

    async def _fetch_user_tweets_async(self, screen_name: str) -> List[Any]:
        """使用 twikit 拉取用户推文（异步实现）"""
        if not self._authenticate():
            return []

        try:
            user = await self.client.get_user_by_screen_name(screen_name)
            tweets = await user.get_tweets("Tweets", count=self.count)
            return list(tweets)
        except Exception:
            logging.exception("[Twikit] 拉取失败: %s", screen_name)
            return []

    def close(self) -> None:
        """释放 Twikit 相关资源"""
        try:
            if not self._loop.is_closed():
                # 尽量优雅关闭底层 HTTP 连接池
                try:
                    if hasattr(self.client, "http"):
                        self._loop.run_until_complete(self.client.http.aclose())
                except Exception:
                    logging.debug("[Twikit] 关闭 HTTP 客户端时忽略异常", exc_info=True)
                self._loop.close()
        except Exception:
            logging.exception("[Twikit] 关闭资源失败")

    def _generate_title(self, text: str, vendor: str) -> str:
        """生成简短的总结性标题"""
        text_lower = text.lower()

        # 提取关键产品/模型名称
        product_patterns = [
            r'(gpt-\d+[a-z]*)',
            r'(claude[- ]\d+\.?\d*)',
            r'(gemini[- ]?\d*\.?\d*[a-z]*)',
            r'(nano banana \d+)',
            r'(sora)',
            r'(veo \d*)',
            r'(codex)',
            r'(operator)',
            r'(o\d+[a-z]*)',
        ]

        import re
        product_name = None
        for pattern in product_patterns:
            match = re.search(pattern, text_lower)
            if match:
                product_name = match.group(1)
                break

        # 识别动作类型（按优先级排序）
        if any(kw in text_lower for kw in ['release', 'launch', 'debut', 'announce', 'announcing', 'introducing', 'introduce', 'meet ', 'is here', 'available now', 'now available']):
            action = '发布了'
        elif any(kw in text_lower for kw in ['update', 'new version', 'version']):
            action = '更新了'
        elif any(kw in text_lower for kw in ['new feature', 'add', 'now support']):
            action = '新增功能'
        elif any(kw in text_lower for kw in ['fix', 'bug', 'patch']):
            action = '修复更新'
        else:
            action = '更新'

        # 生成标题
        if product_name:
            # 标准化产品名称
            product_name = product_name.replace('-', ' ').title()
            title = f"{product_name} {action}"
        else:
            # 如果没有识别到产品名，使用前30个字符
            title = text[:30] + "..." if len(text) > 30 else text

        return title

    def _extract_tweets(
        self,
        raw_tweets: List[Any],
        screen_name: str,
        now: datetime,
        poll_id: str,
        run_id: str,
        tier: str,
        decision_logger: Any,
    ) -> List[Any]:
        """从原始数据提取推文列表（账号级增量窗口过滤）"""
        state = self._get_state(screen_name)
        last_check_time = state.get("last_check_time", self._init_time)

        filtered: List[Any] = []
        dropped_window = 0
        sampled: List[str] = []

        for tweet in raw_tweets:
            tweet_id = str(tweet.id)
            created_at = tweet.created_at
            tweet_time = self._parse_tweet_datetime(created_at)

            if decision_logger:
                decision_logger.log(
                    poll_id=poll_id,
                    run_id=run_id,
                    account=screen_name,
                    tweet_id=tweet_id,
                    tier=tier,
                    stage="raw",
                    decision="pass",
                    reason_code="RAW_FETCHED",
                )

            if self._is_in_window(tweet_time, last_check_time, now):
                filtered.append(tweet)
                if decision_logger:
                    decision_logger.log(
                        poll_id=poll_id,
                        run_id=run_id,
                        account=screen_name,
                        tweet_id=tweet_id,
                        tier=tier,
                        stage="window",
                        decision="pass",
                        reason_code="WINDOW_IN_RANGE",
                    )
            else:
                dropped_window += 1
                if len(sampled) < 3:
                    sampled.append(f"{tweet_id}@{created_at}")
                if decision_logger:
                    decision_logger.log(
                        poll_id=poll_id,
                        run_id=run_id,
                        account=screen_name,
                        tweet_id=tweet_id,
                        tier=tier,
                        stage="window",
                        decision="drop",
                        reason_code="WINDOW_OLD",
                        matched_rule=created_at,
                    )

        if dropped_window > 0:
            logging.info(
                "[%s][Twitter过滤] %s time_window drop=%s sample=%s last=%s",
                poll_id,
                screen_name,
                dropped_window,
                sampled,
                last_check_time.isoformat(),
            )

        return filtered

    def fetch(self, poll_id: str = "", run_id: str = "", decision_logger: Any = None) -> List[NewsItem]:
        """获取所有账号的新推文"""
        all_items: List[NewsItem] = []
        poll_start_time = self._now_beijing()

        for acc in self.accounts:
            screen_name = (acc.get("screen_name", "") or "").strip()
            if not screen_name:
                continue

            state = self._get_state(screen_name)
            tier = state.get("tier", "A")

            if not self._should_poll_account(screen_name, poll_start_time):
                logging.info("[%s][Twitter调度] %s tier=%s skip (not due)", poll_id, screen_name, tier)
                continue

            stats = {
                "raw": 0,
                "time_window": 0,
                "importance": 0,
                "noise": 0,
                "final": 0,
            }

            raw_tweets = self._fetch_user_tweets(screen_name)
            stats["raw"] = len(raw_tweets)

            tweets = self._extract_tweets(
                raw_tweets,
                screen_name,
                poll_start_time,
                poll_id=poll_id,
                run_id=run_id or poll_id,
                tier=tier,
                decision_logger=decision_logger,
            )
            stats["time_window"] = len(tweets)

            for tweet in tweets:
                try:
                    tweet_id = str(tweet.id)
                    text = tweet.text or ""
                    if not text:
                        continue

                    # 检测并获取 Thread 内容
                    is_thread = False
                    thread_count = 0
                    full_text = text

                    if hasattr(tweet, 'replies') and tweet.replies:
                        # 过滤出作者自己的回复
                        author_screen_name = tweet.user.screen_name.lower()
                        self_replies = [
                            r for r in tweet.replies
                            if hasattr(r, 'user') and r.user.screen_name.lower() == author_screen_name
                        ]

                        if self_replies:
                            is_thread = True
                            thread_count = len(self_replies)
                            # 组合完整 Thread 文本
                            thread_texts = [text]
                            for reply in self_replies:
                                if hasattr(reply, 'text') and reply.text:
                                    thread_texts.append(reply.text)
                            full_text = "\n\n".join(thread_texts)
                            logging.info(
                                "[%s][Twitter Thread] %s/%s: 检测到 Thread，包含 %d 条回复",
                                poll_id, screen_name, tweet_id, thread_count
                            )

                    vendor = self._get_vendor(screen_name)
                    # 如果是 Thread，在 vendor 后添加标签
                    if is_thread:
                        vendor = f"{vendor} [Thread {thread_count+1}条]"

                    # 使用完整 Thread 文本进行重要性检测
                    importance, importance_rule = self._detect_importance_with_rule(full_text, vendor)

                    if importance == "filter":
                        if decision_logger:
                            decision_logger.log(
                                poll_id=poll_id,
                                run_id=run_id or poll_id,
                                account=screen_name,
                                tweet_id=tweet_id,
                                tier=tier,
                                stage="importance",
                                decision="drop",
                                reason_code="IMPORTANCE_FILTER",
                                matched_rule=importance_rule,
                            )
                        logging.info("[%s][Twitter过滤] %s/%s: filter", poll_id, screen_name, tweet_id)
                        continue

                    if importance in ("normal", "filter"):
                        if decision_logger:
                            decision_logger.log(
                                poll_id=poll_id,
                                run_id=run_id or poll_id,
                                account=screen_name,
                                tweet_id=tweet_id,
                                tier=tier,
                                stage="importance",
                                decision="drop",
                                reason_code="IMPORTANCE_NORMAL",
                            )
                        logging.info("[%s][Twitter过滤] %s/%s: importance=%s", poll_id, screen_name, tweet_id, importance)
                        continue

                    stats["importance"] += 1
                    if decision_logger:
                        decision_logger.log(
                            poll_id=poll_id,
                            run_id=run_id or poll_id,
                            account=screen_name,
                            tweet_id=tweet_id,
                            tier=tier,
                            stage="importance",
                            decision="pass",
                            reason_code="IMPORTANCE_PASS",
                            matched_rule=importance_rule,
                        )

                    # 降噪过滤（使用完整 Thread 文本）
                    text_lower = full_text.lower()
                    noise_keywords = ["hiring", "job ", "event", "meetup", "podcast", "welcoming"]
                    hit_noise = next((kw for kw in noise_keywords if kw in text_lower), "")
                    if hit_noise:
                        logging.info("[%s][Twitter过滤] %s/%s: noise", poll_id, screen_name, tweet_id)
                        stats["noise"] += 1
                        if decision_logger:
                            decision_logger.log(
                                poll_id=poll_id,
                                run_id=run_id or poll_id,
                                account=screen_name,
                                tweet_id=tweet_id,
                                tier=tier,
                                stage="noise",
                                decision="drop",
                                reason_code="NOISE_KEYWORD",
                                matched_rule=hit_noise,
                            )
                        continue

                    if decision_logger:
                        decision_logger.log(
                            poll_id=poll_id,
                            run_id=run_id or poll_id,
                            account=screen_name,
                            tweet_id=tweet_id,
                            tier=tier,
                            stage="noise",
                            decision="pass",
                            reason_code="NOISE_PASS",
                        )

                    url = f"https://x.com/{screen_name}/status/{tweet_id}"
                    created_at = tweet.created_at
                    tweet_time = self._parse_tweet_datetime(created_at)
                    published_at = tweet_time.isoformat() if tweet_time else ""

                    # 使用完整 Thread 文本生成摘要
                    summary = full_text[:400]

                    # 生成简短的总结性标题
                    title = self._generate_title(full_text, vendor)

                    item = NewsItem(
                        source=f"Twitter:{screen_name}",
                        title=title,
                        url=url,
                        summary=summary,
                        published_at=published_at,
                    )
                    item.dedupe_text = full_text
                    item.vendor = vendor
                    item.importance = importance if importance not in ["filter", "normal"] else "minor"
                    item.account = screen_name
                    item.tweet_id = tweet_id
                    item.tier = tier
                    item.selected_reason = f"importance:{importance_rule or 'n/a'}"
                    item.is_thread = is_thread
                    item.thread_count = thread_count if is_thread else 0
                    item.account = screen_name
                    item.tweet_id = tweet_id
                    item.tier = tier
                    item.selected_reason = f"importance:{importance_rule or 'n/a'}"

                    all_items.append(item)
                    stats["final"] += 1
                    logging.info("[%s][Twitter选中] %s/%s tier=%s reason=%s", poll_id, screen_name, tweet_id, tier, item.selected_reason)

                except Exception:
                    logging.exception("[%s][Twitter] 解析推文失败: %s", poll_id, screen_name)
                    continue

            state["last_check_time"] = poll_start_time
            self._advance_account_schedule(screen_name, has_news=stats["final"] > 0, now=poll_start_time)

            logging.info(
                "[%s][Twitter埋点] %s(tier=%s): 原始拉取=%s, 时间窗口过滤后=%s, importance过滤后=%s, 降噪后=%s, 最终输出=%s",
                poll_id,
                screen_name,
                tier,
                stats["raw"],
                stats["time_window"],
                stats["importance"],
                stats["noise"],
                stats["final"],
            )

        logging.info("[%s][Twitter] 本轮获取 %s 条推文", poll_id, len(all_items))
        return all_items
