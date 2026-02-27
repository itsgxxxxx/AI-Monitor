"""
结构化决策日志（JSONL）
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class DecisionLogger:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).isoformat()

    def log(
        self,
        *,
        poll_id: str,
        run_id: Optional[str],
        account: str,
        tweet_id: str,
        tier: str,
        stage: str,
        decision: str,
        reason_code: str,
        matched_rule: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "timestamp": self._ts(),
            "poll_id": poll_id,
            "run_id": run_id or poll_id,
            "account": account,
            "tweet_id": tweet_id,
            "tier": tier,
            "stage": stage,
            "decision": decision,
            "reason_code": reason_code,
            "matched_rule": matched_rule,
        }
        if extra:
            payload.update(extra)

        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            logging.exception("写入决策日志失败: %s", self.path)
