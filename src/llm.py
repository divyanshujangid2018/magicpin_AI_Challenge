"""LLM client — Groq (primary) with Gemini fallback. Optional polish layer.

The deterministic composer is the source of truth. The LLM is used *only* to
make the grounded draft read more naturally. Any failure (timeout, quota, bad
JSON) silently falls back to the deterministic draft.

Config via environment (set in .env or shell):
    VERA_USE_LLM     — "1" to enable LLM polish (default "0")
    GROQ_API_KEY     — Groq key  (https://console.groq.com)
    GROQ_MODEL       — default "llama-3.3-70b-versatile"
    GEMINI_API_KEY   — fallback Gemini key (optional)
    GEMINI_MODEL     — default "gemini-2.0-flash"
    VERA_LLM_TIMEOUT — per-call seconds (default 8)
"""
from __future__ import annotations

import json
import os
from typing import Optional

try:
    import httpx
except ImportError:
    httpx = None


class LLMClient:
    """Unified LLM client: Groq first, Gemini fallback."""

    def __init__(self) -> None:
        self.timeout = float(os.getenv("VERA_LLM_TIMEOUT", "8"))
        self.enabled = os.getenv("VERA_USE_LLM", "0") == "1" and httpx is not None

        # Groq (OpenAI-compatible)
        self.groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

        # Gemini (fallback)
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()

    def available(self) -> bool:
        return self.enabled and bool(self.groq_key or self.gemini_key)

    def complete(self, prompt: str, system: str = "") -> Optional[str]:
        """Single-shot completion. Returns text or None on any failure."""
        if not self.available():
            return None
        if self.groq_key:
            result = self._groq(prompt, system)
            if result:
                return result
        if self.gemini_key:
            return self._gemini(prompt, system)
        return None

    def _groq(self, prompt: str, system: str) -> Optional[str]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": self.groq_model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 512,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.groq_key}",
                             "Content-Type": "application/json"},
                    json=body,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception:
            return None

    def _gemini(self, prompt: str, system: str) -> Optional[str]:
        full = f"{system}\n\n{prompt}" if system else prompt
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent?key={self.gemini_key}"
        )
        body = {
            "contents": [{"parts": [{"text": full}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 512},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            return None

    @staticmethod
    def extract_json(text: Optional[str]) -> Optional[dict]:
        if not text:
            return None
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return None


# module-level singleton — imported as `gemini` for backwards compat
gemini = LLMClient()
