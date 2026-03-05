#!/usr/bin/env python3
"""
findsimilarpost:
1) 从英文推文提取项目名
2) 生成中文清晰表达
3) 在中文区搜索同项目高流量推文
"""
import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml


class DailyLimitExceededError(RuntimeError):
    pass


class SimilarPostFinder:
    def __init__(self, tikhub_api_key: str, base_url: str = "https://api.tikhub.io"):
        self.api_key = tikhub_api_key
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {tikhub_api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def clean_jina_content(text: str) -> str:
        lines = text.splitlines()
        keep: List[str] = []
        skip_prefixes = (
            "Title:",
            "URL Source:",
            "Published Time:",
            "Markdown Content:",
            "Warning:",
        )
        for line in lines:
            if line.startswith(skip_prefixes):
                continue
            keep.append(line)
        return "\n".join(keep).strip()

    @staticmethod
    def parse_int(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return 0
        match = re.match(r"^(\d+(?:\.\d+)?)([kKmM]?)$", text)
        if not match:
            return 0
        number = float(match.group(1))
        suffix = match.group(2).lower()
        if suffix == "k":
            number *= 1000
        elif suffix == "m":
            number *= 1000000
        return int(number)

    @staticmethod
    def dig(obj: Any, *path: str) -> Any:
        cur = obj
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
            if cur is None:
                return None
        return cur

    def first_int(self, tweet: Dict[str, Any], paths: List[List[str]]) -> int:
        for path in paths:
            value = self.dig(tweet, *path)
            if value is None:
                continue
            parsed = self.parse_int(value)
            if parsed > 0:
                return parsed
        return 0

    def first_text(self, tweet: Dict[str, Any], paths: List[List[str]]) -> str:
        for path in paths:
            value = self.dig(tweet, *path)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def extract_tool_name(self, tweet_text: str) -> Optional[str]:
        """
        优先级：
        1) It's called X / called X
        2) GitHub repo 名
        3) named X
        4) 合理的大写词
        """
        text = self.clean_jina_content(tweet_text)

        called_match = re.search(
            r"(?:it'?s\s+called|called)\s+([A-Za-z][A-Za-z0-9_-]{1,49})",
            text,
            re.IGNORECASE,
        )
        if called_match:
            return called_match.group(1)

        gh_match = re.search(r"github\.com/[\w.-]+/([\w.-]+)", text, re.IGNORECASE)
        if gh_match:
            return gh_match.group(1).strip(" .,:;!?")

        named_match = re.search(
            r"(?:named|name is)\s+([A-Za-z][A-Za-z0-9_-]{1,49})",
            text,
            re.IGNORECASE,
        )
        if named_match:
            return named_match.group(1)

        exclude_words = {
            "Title",
            "Someone",
            "Today",
            "Thread",
            "This",
            "That",
            "There",
            "These",
            "Those",
            "Github",
        }
        candidates = re.findall(r"\b[A-Z][a-zA-Z0-9_-]{2,}\b", text)
        for token in candidates:
            if token in exclude_words:
                continue
            if re.match(r"^\d+$", token):
                continue
            return token

        return None

    def fetch_tweet_content(self, tweet_url: str) -> str:
        jina_url = f"https://r.jina.ai/{tweet_url}"
        response = requests.get(jina_url, timeout=20)
        response.raise_for_status()
        return response.text

    def search_chinese_tweets(
        self,
        keyword: str,
        min_likes: int = 100,
        min_views: int = 50000,
        min_followers: int = 5000,
        max_items: int = 5,
        debug: bool = False,
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/v1/twitter/web/fetch_search_timeline"
        params = {
            "keyword": f"{keyword} lang:zh",
            "search_type": "Top",
            "cursor": None,
        }

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        if response.status_code == 429 and "DAILY_LIMIT_EXCEEDED" in response.text:
            raise DailyLimitExceededError("TikHub DAILY_LIMIT_EXCEEDED")
        response.raise_for_status()
        data = response.json()

        timeline = data.get("data", {}).get("timeline", [])
        results: List[Dict[str, Any]] = []
        debug_printed = False
        for tweet in timeline:
            if tweet.get("type") != "tweet":
                continue

            likes = self.first_int(
                tweet,
                [
                    ["favorites"],
                    ["favorite_count"],
                    ["legacy", "favorite_count"],
                    ["legacy", "favorites"],
                ],
            )
            views = self.first_int(
                tweet,
                [
                    ["views"],
                    ["view_count"],
                    ["tweet_views", "count"],
                    ["legacy", "views", "count"],
                ],
            )
            followers = self.first_int(
                tweet,
                [
                    ["user_followers"],
                    ["followers"],
                    ["followers_count"],
                    ["author_followers"],
                    ["user", "followers_count"],
                    ["user", "followers"],
                    ["author", "followers_count"],
                    ["author", "followers"],
                    ["user_info", "followers_count"],
                    ["legacy", "followers_count"],
                    ["core", "user_results", "result", "legacy", "followers_count"],
                    ["user_result", "legacy", "followers_count"],
                ],
            )

            if likes < min_likes and views < min_views and followers <= min_followers:
                continue

            screen_name = self.first_text(
                tweet,
                [
                    ["screen_name"],
                    ["user", "screen_name"],
                    ["author", "screen_name"],
                    ["legacy", "screen_name"],
                    ["core", "user_results", "result", "legacy", "screen_name"],
                ],
            )
            tweet_id = self.first_text(
                tweet,
                [
                    ["tweet_id"],
                    ["id_str"],
                    ["rest_id"],
                    ["legacy", "id_str"],
                ],
            )
            if not screen_name or not tweet_id:
                continue

            author_name = self.first_text(
                tweet,
                [
                    ["name"],
                    ["user", "name"],
                    ["author", "name"],
                    ["legacy", "name"],
                    ["core", "user_results", "result", "legacy", "name"],
                ],
            )

            if debug and not debug_printed:
                debug_payload = {
                    "top_level_keys": sorted(list(tweet.keys())),
                    "resolved": {
                        "screen_name": screen_name,
                        "tweet_id": tweet_id,
                        "author_name": author_name,
                        "followers": followers,
                        "views": views,
                        "likes": likes,
                    },
                }
                print("🔧 DEBUG 首条推文结构:")
                print(json.dumps(debug_payload, ensure_ascii=False, indent=2))
                debug_printed = True

            results.append(
                {
                    "author": author_name or screen_name,
                    "screen_name": screen_name,
                    "followers": followers,
                    "views": views,
                    "likes": likes,
                    "url": f"https://x.com/{screen_name}/status/{tweet_id}",
                }
            )

        results.sort(key=lambda x: (x["views"], x["likes"], x["followers"]), reverse=True)
        return results[:max_items]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config() -> Dict[str, Any]:
    config_path = os.getenv("AI_MONITOR_CONFIG", str(repo_root() / "config.yaml"))
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def get_tikhub_api_key(cfg: Dict[str, Any]) -> str:
    env_key = os.getenv("TIKHUB_API_KEY", "").strip()
    if env_key:
        return env_key
    return str(cfg.get("tikhub", {}).get("api_key", "")).strip()


def rewrite_to_chinese(source_text: str, cfg: Dict[str, Any]) -> str:
    cleaned = SimilarPostFinder.clean_jina_content(source_text)
    snippet = cleaned[:1200]
    llm_cfg = cfg.get("llm", {})
    api_key = str(llm_cfg.get("api_key", "")).strip()
    enabled = bool(llm_cfg.get("enabled", False))

    if enabled and api_key:
        try:
            root = str(repo_root())
            if root not in sys.path:
                sys.path.insert(0, root)
            from sources.llm import MiniMaxClient  # pylint: disable=import-outside-toplevel

            client = MiniMaxClient(llm_cfg)
            system_prompt = (
                "你是 AI 工具情报编辑。请把英文原文整理成中文，要求准确、简洁、可用于转发决策。"
            )
            user_prompt = (
                "请输出两段：\n"
                "1) 一句话结论（20-40字）\n"
                "2) 关键点（最多3条）\n\n"
                f"原文：\n{snippet}"
            )
            root_logger = logging.getLogger()
            prev_level = root_logger.level
            root_logger.setLevel(logging.CRITICAL)
            try:
                translated = client.chat(system_prompt, user_prompt)
            finally:
                root_logger.setLevel(prev_level)
            if translated:
                return translated.strip()
        except Exception:
            pass

    lines = [line.strip() for line in snippet.splitlines() if line.strip()]
    fallback = " ".join(lines[:3])[:220]
    return f"LLM 不可用，原文要点（自动提取）：{fallback}"


def format_text_output(keyword: str, cn_rewrite: str, matches: List[Dict[str, Any]]) -> str:
    chunks = []
    chunks.append(f"项目关键词: {keyword}")
    chunks.append("")
    chunks.append("英文原文中文表达:")
    chunks.append(cn_rewrite)
    chunks.append("")

    if not matches:
        chunks.append("未找到中文区高流量同项目推荐。")
        return "\n".join(chunks)

    chunks.append(f"找到 {len(matches)} 条中文区高流量同项目推荐:")
    chunks.append("")
    for idx, item in enumerate(matches, 1):
        chunks.append(f"{idx}. 作者: @{item['screen_name']} ({item['author']})")
        chunks.append(f"   粉丝量: {item['followers']:,}")
        chunks.append(f"   浏览量: {item['views']:,}")
        chunks.append(f"   点赞数: {item['likes']:,}")
        chunks.append(f"   链接: {item['url']}")
        if idx < len(matches):
            chunks.append("")
    return "\n".join(chunks)


def build_decision(status: str, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    if status == "not_found":
        return {
            "should_send": True,
            "reason": "未发现中文区高流量同项目内容，适合优先发送。",
        }
    if status == "ok":
        top = matches[0] if matches else {}
        return {
            "should_send": False,
            "reason": "已存在中文区高流量同项目内容，建议先比对角度再发送。",
            "top_post": {
                "author": f"@{top.get('screen_name', '')}",
                "views": top.get("views", 0),
                "likes": top.get("likes", 0),
                "url": top.get("url", ""),
            },
        }
    if status == "rate_limited":
        return {
            "should_send": None,
            "reason": "TikHub 当日额度已用尽，当前无法判断。",
        }
    return {
        "should_send": None,
        "reason": "检索失败，当前无法判断。",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="查找中文区同项目高流量推文")
    parser.add_argument("tweet_url", help="原始英文推文链接")
    parser.add_argument(
        "--source-text-file",
        default="",
        help="离线调试：直接读取本地文本作为推文内容，跳过抓取",
    )
    parser.add_argument("-k", "--keyword", default="", help="手动指定关键词，跳过自动提取")
    parser.add_argument("--min-likes", type=int, default=100, help="最低点赞数")
    parser.add_argument("--min-views", type=int, default=50000, help="最低浏览量")
    parser.add_argument("--min-followers", type=int, default=5000, help="最低粉丝数")
    parser.add_argument("--max-items", type=int, default=5, help="最多返回条数")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--agent", action="store_true", help="输出 Agent 标准 JSON（仅结构化结果）")
    parser.add_argument("--debug", action="store_true", help="打印首条数据的字段解析信息")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    quiet = args.json or args.agent
    cfg = load_config()
    api_key = get_tikhub_api_key(cfg)
    if not api_key:
        print("❌ 缺少 TikHub API Key。请设置 TIKHUB_API_KEY 或 config.yaml 中 tikhub.api_key。")
        sys.exit(1)

    finder = SimilarPostFinder(api_key)

    source_text = ""
    source_fetch_error = ""
    if args.source_text_file:
        try:
            source_text = Path(args.source_text_file).read_text(encoding="utf-8")
            if not quiet:
                print(f"📄 已从本地文件读取推文内容: {args.source_text_file}")
        except Exception as exc:
            print(f"❌ 读取 source_text_file 失败: {exc}")
            sys.exit(1)
    else:
        try:
            if not quiet:
                print("📥 正在获取推文内容...")
            source_text = finder.fetch_tweet_content(args.tweet_url)
        except Exception as exc:
            source_fetch_error = str(exc)
            if not args.keyword:
                print(f"❌ 获取推文内容失败: {exc}")
                print("提示：可用 --keyword 手动指定项目名继续检索。")
                sys.exit(1)
            if not quiet:
                print(f"⚠️ 获取原文失败，已进入关键词模式: {exc}")

    keyword = args.keyword.strip()
    if not keyword:
        if not quiet:
            print("🔍 正在提取项目关键词...")
        keyword = finder.extract_tool_name(source_text) or ""
    if not keyword:
        print("❌ 无法提取关键词，请使用 --keyword 手动指定。")
        sys.exit(1)

    if not quiet:
        print(f"✅ 关键词: {keyword}")
    if source_text:
        cn_rewrite = rewrite_to_chinese(source_text, cfg)
    else:
        cn_rewrite = "未获取到原推文正文（Jina 不可达或被限制），未生成中文表达。"

    search_error = ""
    try:
        if not quiet:
            print("🔎 正在搜索中文区高流量内容...")
        matches = finder.search_chinese_tweets(
            keyword=keyword,
            min_likes=args.min_likes,
            min_views=args.min_views,
            min_followers=args.min_followers,
            max_items=args.max_items,
            debug=(args.debug and not quiet),
        )
        status = "ok" if matches else "not_found"
    except DailyLimitExceededError:
        matches = []
        status = "rate_limited"
    except Exception as exc:
        matches = []
        status = "search_error"
        search_error = str(exc)

    if args.json or args.agent:
        decision = build_decision(status, matches)
        payload = {
            "skill": "findsimilarpost",
            "status": status,
            "tweet_url": args.tweet_url,
            "keyword": keyword,
            "source_post_cn": cn_rewrite,
            "results": matches,
            "decision": decision,
        }
        if status == "rate_limited":
            payload["message"] = "TikHub 今日额度已用尽，无法完成中文区搜索。"
        if status == "search_error":
            payload["message"] = f"搜索失败: {search_error}"
        if source_fetch_error:
            payload["source_fetch_warning"] = f"原文抓取失败: {source_fetch_error}"
        if args.agent:
            payload["agent_next_action"] = (
                "should_send=true 时可直接发送；false 时先查看 top_post 链接再决定；null 时稍后重试。"
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if status == "rate_limited":
        print("⚠️ TikHub 今日额度已用尽，无法完成中文区搜索。")
        print("你可以明天重试，或设置新的 TIKHUB_API_KEY。")
    elif status == "search_error":
        print(f"⚠️ 搜索失败: {search_error}")

    print()
    print(format_text_output(keyword, cn_rewrite, matches))


if __name__ == "__main__":
    main()
