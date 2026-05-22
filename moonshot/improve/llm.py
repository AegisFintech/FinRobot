"""Thin OpenAI-compatible client with retry / structured JSON parse.

Supports any OpenAI-protocol endpoint via OPENAI_BASE_URL.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    content: str
    usage: Dict[str, Any]
    model: str
    raw: Dict[str, Any]


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 90.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com").rstrip("/")
        self.model = model or os.getenv("IMPROVER_MODEL", "gpt-5.5")
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        max_completion_tokens: int = 4096,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        retries: int = 3,
        backoff: float = 4.0,
    ) -> LLMResult:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_completion_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if temperature is not None:
            payload["temperature"] = temperature
        url = f"{self.base_url}/v1/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                choice = (raw.get("choices") or [{}])[0]
                content = ((choice.get("message") or {}).get("content")) or ""
                return LLMResult(
                    content=content,
                    usage=raw.get("usage", {}),
                    model=raw.get("model", self.model),
                    raw=raw,
                )
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
                logger.warning("LLM HTTP %s: %s", e.code, err_body[:500])
                last_err = e
                # Don't retry 4xx other than 408/429
                if 400 <= e.code < 500 and e.code not in (408, 429):
                    break
            except Exception as e:
                logger.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
                last_err = e
            time.sleep(backoff * (2 ** attempt))
        raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")

    def chat_json(self, messages: List[Dict[str, str]], **kwargs: Any) -> Dict[str, Any]:
        result = self.chat(
            messages,
            response_format={"type": "json_object"},
            **kwargs,
        )
        text = result.content.strip()
        if not text:
            raise RuntimeError("Empty LLM response")
        # Tolerate code-fenced JSON if the endpoint added a wrapper.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().lower().startswith("json"):
                text = text.split("\n", 1)[1] if "\n" in text else text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Last-ditch: find the first {...} block
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise


__all__ = ["LLMClient", "LLMResult"]
