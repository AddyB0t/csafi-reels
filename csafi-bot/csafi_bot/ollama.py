"""Minimal Ollama REST client (/api/chat, /api/tags, unload via keep_alive=0)."""
import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class OllamaError(RuntimeError):
    pass


@dataclass
class ChatResult:
    content: str
    seconds: float
    prompt_tokens: int | None
    output_tokens: int | None


class Ollama:
    def __init__(self, base_url: str, timeout_s: int = 600):
        self.base = base_url.rstrip("/")
        self.timeout = timeout_s

    def available(self) -> bool:
        try:
            return httpx.get(f"{self.base}/api/tags", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def models(self) -> list[str]:
        r = httpx.get(f"{self.base}/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def has_model(self, name: str) -> bool:
        names = self.models()
        want = name if ":" in name else f"{name}:latest"
        return want in names or name in names

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        fmt: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | int = "5m",
        think: bool | None = False,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
            "options": options or {},
        }
        if fmt is not None:
            body["format"] = fmt
        if think is not None:
            body["think"] = think
        start = time.monotonic()
        try:
            r = httpx.post(f"{self.base}/api/chat", json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise OllamaError(f"cannot reach Ollama at {self.base}: {e}") from e
        if r.status_code == 400 and think is not None and "think" in r.text.lower():
            body.pop("think", None)
            r = httpx.post(f"{self.base}/api/chat", json=body, timeout=self.timeout)
        if r.status_code != 200:
            raise OllamaError(f"Ollama {r.status_code}: {r.text[:300]}")
        data = r.json()
        return ChatResult(
            content=(data.get("message") or {}).get("content", ""),
            seconds=time.monotonic() - start,
            prompt_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )

    def unload(self, model: str) -> None:
        try:
            httpx.post(f"{self.base}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
        except httpx.HTTPError:
            pass


def image_b64(path: Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()
