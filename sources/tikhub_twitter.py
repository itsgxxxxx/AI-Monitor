"""
TikHub Twitter 数据源 - 分层轮询 + 增量窗口版
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from storage import NewsItem


class TikHubTwitterSource:
    def __init__(self, cfg: Dict, api_key: str, base_url: str, user_agent: str, timeout: int):
        self.name = cfg.get("name", "Twitter Monitor")
        self.accounts = cfg.get("accounts", [])
        self.count = cfg.get("count", 10)
        self.api_key = api_key
        self.base_url = base_url
        self.user_agent = user_agent
        self.timeout = timeout

        self._bj_tz = timezone(timedelta(hours=8))
        self._init_time = self._now_beijing()

        # 厂商映射
        self.vendor_map: Dict[str, Dict[str, Any]] = {}

        # 账号状态（用于分层调度 + 各账号独立增量窗口）
        # key=screen_name.lower()
        # value={tier,last_check_time,last_polled_at,no_news_streak,next_due_at}
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

    def _now_beijing(self) -> datetime:
        return datetime.now(self._bj_tz)

    def _is_night_window(self, dt: Optional[datetime] = None) -> bool:
        """夜间窗口：21:00-03:00（北京时间）"""
        now = dt or self._now_beijing()
        hour = now.hour
        return hour >= 21 or hour < 3

    def _day_base_minutes(self, tier: str) -> int:
        # 用户要求：B 级白天 60m 起步；其余 30m 起步
        return 60 if tier == "B" else 30

    def _next_interval_minutes(self, tier: str, no_news_streak: int, now: datetime) -> int:
        """
        分层间隔策略：
        - 夜间（21:00-03:00）：固定 15m
        - 白天（03:00-21:00）：
          - S/A: 30 -> 60 -> 90 (max)
          - B:   60 -> 90 (max)
        """
        if self._is_night_window(now):
            return 15

        base = self._day_base_minutes(tier)
        if base == 60:
            return 60 if no_news_streak <= 0 else 90

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
        账号是否真正拉取由账号自身 due 状态决定
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

        # AI快讯账号 (testingcatalog) 特殊过滤：只发大公司相关
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

        major_keywords = [
            "release", "launch", "new model", "new api", "new mode", "new feature",
            "announce", "introduce", "introducing", "debut",
            "gpt-", "claude-", "gemini", "codex", "operator",
            "version 2", "version 3", "version 4", "version 5",
            "veo", "sora", "gemini 2", "claude 4", "gpt-5",
            "meet ", "meet+",
        ]

        minor_keywords = [
            "update", "fix", "bug", "optimize", "improve", "patch",
            "now support", "add ", "enhance",
            "demo", "showcase", "example", "walkthrough", "how to",
            "new capability", "improvement", "new feature",
            "introducing", "announcing", "available",
        ]

        for kw in major_keywords:
            if kw in text_lower:
                return "major", kw

        for kw in minor_keywords:
            if kw in text_lower:
                return "minor", kw

        return "normal", ""

    def _detect_importance(self, text: str, vendor: str = "") -> str:
        importance, _ = self._detect_importance_with_rule(text, vendor)
        return importance

    def _get_vendor(self, screen_name: str) -> str:
        info = self.vendor_map.get(screen_name.lower(), {})
        vendor = info.get("vendor", screen_name)
        is_founder = info.get("is_founder", False)

        if is_founder:
            return f"{vendor} (创始人)"
        return vendor

    def _parse_tweet_datetime(self, created_at: str) -> Optional[datetime]:
        try:
            dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            return dt.astimezone(self._bj_tz)
        except Exception:
            return None

    def _is_in_window(self, tweet_time: Optional[datetime], last_check_time: datetime, now: datetime) -> bool:
        if tweet_time is None:
            return False
        return last_check_time < tweet_time <= now

    def _fetch_user_tweets(self, screen_name: str) -> Dict[str, Any]:
        """拉取用户推文"""
        url = f"{self.base_url}/api/v1/twitter/web/fetch_user_post_tweet"
        params = {
            "screen_name": screen_name,
            "count": self.count,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": self.user_agent,
        }

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if "data" in data:
                return data.get("data", {})
            return {}
        except Exception:
            logging.exception("[Twitter] 拉取失败: %s", screen_name)
            return {}

    def _extract_tweets(
        self,
        raw_data: Dict[str, Any],
        screen_name: str,
        now: datetime,
        poll_id: str,
        run_id: str,
        tier: str,
        decision_logger: Any,
    ) -> List[Dict[str, Any]]:
        """从原始数据提取推文列表（账号级增量窗口过滤）"""
        tweets: List[Dict[str, Any]] = []

        pinned = raw_data.get("pinned", {})
        if pinned:
            tweets.append(pinned)

        timeline = raw_data.get("timeline", [])
        if isinstance(timeline, list):
            for item in timeline:
                if isinstance(item, dict):
                    tweets.append(item)

        state = self._get_state(screen_name)
        last_check_time = state.get("last_check_time", self._init_time)

        filtered: List[Dict[str, Any]] = []
        dropped_window = 0
        sampled: List[str] = []

        for t in tweets:
            tweet_id = str(t.get("tweet_id", "unknown"))
            created_at = t.get("created_at", "")
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
                filtered.append(t)
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

            raw_data = self._fetch_user_tweets(screen_name)

            if raw_data.get("pinned"):
                stats["raw"] += 1
            stats["raw"] += len(raw_data.get("timeline", []))

            tweets = self._extract_tweets(
                raw_data,
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
                    tweet_id = str(tweet.get("tweet_id", ""))
                    text = tweet.get("text", "")
                    if not text:
                        continue

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

                    url = f"https://x.com/{screen_name}/status/{tweet_id}"
                    created_at = tweet.get("created_at", "")
                    tweet_time = self._parse_tweet_datetime(created_at)
                    published_at = tweet_time.isoformat() if tweet_time else ""

                    summary = text[:400]
                    title = text[:80] + "..." if len(text) > 80 else text

                    item = NewsItem(
                        source=f"Twitter:{screen_name}",
                        title=title,
                        url=url,
                        summary=summary,
                        published_at=published_at,
                    )
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
