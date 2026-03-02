"""
Jina Reader Twitter 数据源 - 分层轮询 + 增量窗口版
使用 Jina Reader API 获取推文内容（适用于 B 级账号）
"""
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from storage import NewsItem


class JinaTwitterSource:
    def __init__(self, cfg: Dict, user_agent: str, timeout: int = 20):
        self.name = cfg.get("name", "Twitter Monitor (Jina)")
        self.accounts = cfg.get("accounts", [])
        self.user_agent = user_agent
        self.timeout = timeout

        self._bj_tz = timezone(timedelta(hours=8))
        self._init_time = self._now_beijing()

        # 厂商映射
        self.vendor_map: Dict[str, Dict[str, Any]] = {}

        # 账号状态（用于分层调度 + 各账号独立增量窗口）
        self.account_states: Dict[str, Dict[str, Any]] = {}

        for acc in self.accounts:
            screen_name = (acc.get("screen_name", "") or "").strip()
            if not screen_name:
                continue

            tier = str(acc.get("tier", "B") or "B").upper()
            if tier not in {"S", "A", "B"}:
                tier = "B"

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
          - B:   60 -> 90 (max)
        """
        if self._is_night_window(now):
            return 15

        base = self._day_base_minutes(tier)
        return 60 if no_news_streak <= 0 else 90

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
                "tier": "B",
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
        tier = state.get("tier", "B")

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

    def _fetch_profile_with_jina(self, screen_name: str) -> str:
        """使用 Jina Reader 获取用户主页内容"""
        url = f"https://r.jina.ai/https://x.com/{screen_name}"
        headers = {"User-Agent": self.user_agent}

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.text
        except Exception:
            logging.exception("[Jina] 拉取失败: %s", screen_name)
            return ""

    def _parse_jina_markdown(self, markdown: str, screen_name: str) -> List[Dict[str, Any]]:
        """从 Jina Reader 返回的 Markdown 中解析推文"""
        tweets: List[Dict[str, Any]] = []

        # Jina Reader 返回的格式通常是：
        # Title: @username
        # URL Source: https://x.com/username
        # Markdown Content:
        #
        # Tweet text here...
        #
        # [timestamp]
        # [engagement stats]

        # 简单的正则匹配提取推文内容
        # 这里需要根据实际 Jina Reader 返回格式调整
        lines = markdown.split("\n")
        current_tweet = None
        tweet_text = []

        for line in lines:
            # 检测推文 URL 模式
            tweet_url_match = re.search(r"https://x\.com/[^/]+/status/(\d+)", line)
            if tweet_url_match:
                # 保存上一条推文
                if current_tweet and tweet_text:
                    current_tweet["text"] = "\n".join(tweet_text).strip()
                    if current_tweet["text"]:
                        tweets.append(current_tweet)

                # 开始新推文
                tweet_id = tweet_url_match.group(1)
                current_tweet = {
                    "tweet_id": tweet_id,
                    "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                }
                tweet_text = []
                continue

            # 收集推文文本
            if current_tweet:
                # 跳过时间戳和统计信息行
                if re.match(r"^\d+:\d+\s+(AM|PM)", line) or re.match(r"^\d+\s+(Retweets?|Quotes?|Likes?|Bookmarks?)", line):
                    continue
                if line.strip():
                    tweet_text.append(line.strip())

        # 保存最后一条推文
        if current_tweet and tweet_text:
            current_tweet["text"] = "\n".join(tweet_text).strip()
            if current_tweet["text"]:
                tweets.append(current_tweet)

        return tweets

    def _is_in_window(self, last_check_time: datetime, now: datetime) -> bool:
        """
        Jina Reader 无法获取精确时间戳，使用简化的窗口策略：
        - 假设所有推文都在窗口内（因为我们按固定间隔轮询）
        - 依赖去重机制过滤重复推文
        """
        return True

    def fetch(self, poll_id: str = "", run_id: str = "", decision_logger: Any = None) -> List[NewsItem]:
        """获取所有账号的新推文"""
        all_items: List[NewsItem] = []
        poll_start_time = self._now_beijing()

        for acc in self.accounts:
            screen_name = (acc.get("screen_name", "") or "").strip()
            if not screen_name:
                continue

            state = self._get_state(screen_name)
            tier = state.get("tier", "B")

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

            markdown = self._fetch_profile_with_jina(screen_name)
            if not markdown:
                continue

            tweets = self._parse_jina_markdown(markdown, screen_name)
            stats["raw"] = len(tweets)
            stats["time_window"] = len(tweets)  # Jina 无精确时间，全部通过

            for tweet in tweets:
                try:
                    tweet_id = tweet.get("tweet_id", "")
                    text = tweet.get("text", "")
                    if not text or not tweet_id:
                        continue

                    if decision_logger:
                        decision_logger.log(
                            poll_id=poll_id,
                            run_id=run_id or poll_id,
                            account=screen_name,
                            tweet_id=tweet_id,
                            tier=tier,
                            stage="raw",
                            decision="pass",
                            reason_code="RAW_FETCHED",
                        )

                    vendor = self._get_vendor(screen_name)
                    importance, importance_rule = self._detect_importance_with_rule(text, vendor)

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

                    text_lower = text.lower()
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

                    url = tweet.get("url", f"https://x.com/{screen_name}/status/{tweet_id}")
                    published_at = poll_start_time.isoformat()  # Jina 无精确时间，使用轮询时间

                    summary = text[:400]
                    title = text[:80] + "..." if len(text) > 80 else text

                    item = NewsItem(
                        source=f"Twitter:{screen_name}",
                        title=title,
                        url=url,
                        summary=summary,
                        published_at=published_at,
                    )
                    item.dedupe_text = text
                    item.vendor = vendor
                    item.importance = importance if importance not in ["filter", "normal"] else "minor"
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
