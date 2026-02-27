"""
AI Model Updates Monitor - 主入口
定时轮询各大厂商更新，推送到 Telegram
"""
import logging
import secrets
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

import yaml

from decision_logger import DecisionLogger
from notifiers.telegram import TelegramNotifier
from sources.rss import RSSSource
from sources.tikhub_twitter import TikHubTwitterSource
from storage import NewsItem, Storage


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_sources(cfg: Dict[str, Any]) -> List[Any]:
    global_http = cfg.get("http", {})
    timeout = int(global_http.get("timeout", 20))
    user_agent = global_http.get("user_agent", "ai-monitor/1.0")

    source_clients: List[Any] = []

    # 处理 TikHub Twitter 配置
    tikhub_cfg = cfg.get("tikhub", {})
    api_key = tikhub_cfg.get("api_key", "")
    base_url = tikhub_cfg.get("base_url", "https://api.tikhub.io")

    for scfg in cfg.get("sources", []):
        if not scfg.get("enabled", True):
            continue

        stype = scfg.get("type", "").lower().strip()

        if stype == "rss":
            source_clients.append(RSSSource(scfg, user_agent=user_agent, timeout=timeout))
        elif stype == "tikhub_twitter":
            # TikHub Twitter 源需要特殊处理
            if api_key:
                scfg["accounts"] = scfg.get("accounts", [])
                source_clients.append(
                    TikHubTwitterSource(
                        scfg,
                        api_key=api_key,
                        base_url=base_url,
                        user_agent=user_agent,
                        timeout=timeout,
                    )
                )
            else:
                logging.warning("[TikHub] 缺少 API Key，跳过")
        else:
            logging.warning("未知数据源类型，已跳过: %s", scfg)
    return source_clients


def _is_night_window_beijing() -> bool:
    now = datetime.now(timezone(timedelta(hours=8)))
    h = now.hour
    return h >= 21 or h < 3


def _new_poll_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def run_once(storage: Storage, notifier: TelegramNotifier, sources: List[Any], decision_logger: DecisionLogger) -> None:
    poll_id = _new_poll_id()
    run_id = poll_id
    logging.info("[%s] 本轮开始", poll_id)

    staged_items: List[NewsItem] = []

    for source in sources:
        try:
            try:
                items = source.fetch(poll_id=poll_id, run_id=run_id, decision_logger=decision_logger)
            except TypeError:
                items = source.fetch()

            for item in items:
                if not isinstance(item, NewsItem):
                    continue

                # 内容过滤：推送 major 和 minor 重要性的
                importance = getattr(item, "importance", None) or notifier._detect_importance(item)
                if importance not in ("major", "minor"):
                    continue

                account = getattr(item, "account", item.source.replace("Twitter:", ""))
                tweet_id = str(getattr(item, "tweet_id", ""))
                tier = str(getattr(item, "tier", "A"))

                saved = storage.save_if_new(item)
                if saved:
                    staged_items.append(item)
                    decision_logger.log(
                        poll_id=poll_id,
                        run_id=run_id,
                        account=account,
                        tweet_id=tweet_id,
                        tier=tier,
                        stage="dedupe",
                        decision="pass",
                        reason_code="DEDUPE_NEW",
                        matched_rule=getattr(item, "selected_reason", ""),
                    )
                else:
                    decision_logger.log(
                        poll_id=poll_id,
                        run_id=run_id,
                        account=account,
                        tweet_id=tweet_id,
                        tier=tier,
                        stage="dedupe",
                        decision="drop",
                        reason_code="DEDUPE_HASH",
                        matched_rule="content_hash_exists",
                    )
                    logging.info("[%s][Twitter去重] %s/%s: 命中重复哈希", poll_id, account, tweet_id)
        except Exception:
            logging.exception("[%s] 执行数据源失败: %s", poll_id, getattr(source, "name", source))

    if not staged_items:
        logging.info("[%s] 本轮结束，无新推送", poll_id)
        return

    pushed = 0

    # 夜间：逐条即时推送；白天：批次汇总推送
    if _is_night_window_beijing():
        for item in staged_items:
            account = getattr(item, "account", item.source.replace("Twitter:", ""))
            tweet_id = str(getattr(item, "tweet_id", ""))
            tier = str(getattr(item, "tier", "A"))
            ok = notifier.send(item)
            if ok:
                pushed += 1
            decision_logger.log(
                poll_id=poll_id,
                run_id=run_id,
                account=account,
                tweet_id=tweet_id,
                tier=tier,
                stage="push",
                decision="pass" if ok else "drop",
                reason_code="PUSH_OK" if ok else "PUSH_FAIL",
                matched_rule="night_single",
            )
        logging.info("[%s] 本轮结束（夜间逐条），新推送数量: %d", poll_id, pushed)
    else:
        batch_tweet_ids = [str(getattr(item, "tweet_id", "")) for item in staged_items]
        ok = notifier.send_batch(staged_items)
        decision_logger.log(
            poll_id=poll_id,
            run_id=run_id,
            account="__batch__",
            tweet_id=",".join(batch_tweet_ids),
            tier="-",
            stage="push",
            decision="pass" if ok else "drop",
            reason_code="PUSH_OK" if ok else "PUSH_FAIL",
            matched_rule="day_batch",
            extra={"batch_tweet_ids": batch_tweet_ids},
        )
        logging.info("[%s][Twitter推送] 白天批次 tweet_ids=%s result=%s", poll_id, batch_tweet_ids, "ok" if ok else "fail")

        if ok:
            pushed = len(staged_items)
            for item in staged_items:
                decision_logger.log(
                    poll_id=poll_id,
                    run_id=run_id,
                    account=getattr(item, "account", item.source.replace("Twitter:", "")),
                    tweet_id=str(getattr(item, "tweet_id", "")),
                    tier=str(getattr(item, "tier", "A")),
                    stage="push",
                    decision="pass",
                    reason_code="PUSH_OK",
                    matched_rule="day_batch",
                    extra={"batch_tweet_ids": batch_tweet_ids},
                )
            logging.info("[%s] 本轮结束（白天批次），新推送数量: %d", poll_id, pushed)
        else:
            # 批次失败时回退逐条，避免消息丢失
            for item in staged_items:
                account = getattr(item, "account", item.source.replace("Twitter:", ""))
                tweet_id = str(getattr(item, "tweet_id", ""))
                tier = str(getattr(item, "tier", "A"))
                one_ok = notifier.send(item)
                if one_ok:
                    pushed += 1
                decision_logger.log(
                    poll_id=poll_id,
                    run_id=run_id,
                    account=account,
                    tweet_id=tweet_id,
                    tier=tier,
                    stage="push",
                    decision="pass" if one_ok else "drop",
                    reason_code="PUSH_OK" if one_ok else "PUSH_FAIL",
                    matched_rule="day_batch_fallback_single",
                    extra={"batch_tweet_ids": batch_tweet_ids},
                )
            logging.info("[%s] 本轮结束（批次失败回退逐条），新推送数量: %d", poll_id, pushed)


def get_poll_interval(sources: List[Any], default_minutes: int = 10) -> int:
    """根据数据源动态计算轮询间隔"""
    for source in sources:
        if isinstance(source, TikHubTwitterSource):
            return source._get_poll_interval()
    return default_minutes * 60


def main() -> int:
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        print("未找到 config.yaml，请先配置。")
        return 1

    cfg = load_config(str(config_path))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db_path = cfg.get("database", {}).get("path", "./ai_monitor.db")
    poll_minutes = int(cfg.get("poll_interval_minutes", 10))

    storage = Storage(db_path)
    notifier = TelegramNotifier(cfg)
    decision_logger = DecisionLogger(str(Path(__file__).parent / "logs" / "decision.jsonl"))
    sources = build_sources(cfg)

    if not sources:
        logging.error("没有可用数据源，程序退出。")
        return 1

    logging.info("AI Monitor 启动成功")

    try:
        while True:
            poll_seconds = get_poll_interval(sources, poll_minutes)
            current_interval = poll_seconds // 60
            logging.info("当前轮询间隔: %d 分钟", current_interval)

            run_once(storage, notifier, sources, decision_logger)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        logging.info("收到退出信号，程序结束。")
    finally:
        storage.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
