"""
MiniMax LLM 客户端
"""
import json
import logging
import requests
from typing import Dict, Optional


class MiniMaxClient:
    def __init__(self, cfg: Dict):
        self.api_key = cfg["api_key"]
        self.base_url = cfg.get("base_url", "https://api.minimaxi.com/v1")
        self.model = cfg.get("model", "MiniMax-M2.5")
        self.timeout = int(cfg.get("timeout", 30))

    def chat(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """调用 LLM 返回文本"""
        url = f"{self.base_url}/text/chatcompletion_v2"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            
            if "choices" in data and len(data["choices"]) > 0:
                return data["choices"][0]["message"]["content"]
            else:
                logging.error("LLM 返回格式异常: %s", data)
                return None
        except Exception:
            logging.exception("LLM 调用失败")
            return None
