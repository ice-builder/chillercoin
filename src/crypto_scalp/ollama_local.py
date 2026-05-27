from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import List, Optional

import requests


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


@dataclass
class OllamaConfig:
    host: str = DEFAULT_OLLAMA_HOST
    reasoning_model: str = "qwen3:4b"
    embedding_model: str = "embeddinggemma"


class OllamaClient:
    def __init__(self, config: Optional[OllamaConfig] = None) -> None:
        self.config = config or OllamaConfig()

    def tags(self) -> dict:
        response = requests.get(f"{self.config.host}/api/tags", timeout=30)
        response.raise_for_status()
        return response.json()

    def pull(self, model: str) -> dict:
        response = requests.post(
            f"{self.config.host}/api/pull",
            json={"model": model, "stream": False},
            timeout=3600,
        )
        response.raise_for_status()
        return response.json()

    def chat(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
        messages: List[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = requests.post(
            f"{self.config.host}/api/chat",
            json={
                "model": model or self.config.reasoning_model,
                "messages": messages,
                "stream": False,
            },
            timeout=3600,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["message"]["content"]

    def embed(self, text: str, model: Optional[str] = None) -> List[float]:
        response = requests.post(
            f"{self.config.host}/api/embed",
            json={"model": model or self.config.embedding_model, "input": text},
            timeout=3600,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["embeddings"][0]


def load_ollama_config(path: Path, fallback: Optional[OllamaConfig] = None) -> OllamaConfig:
    config = fallback or OllamaConfig()
    if not path.exists():
        return config

    payload = json.loads(path.read_text(encoding="utf-8"))
    return OllamaConfig(
        host=payload.get("ollama_host", config.host),
        reasoning_model=payload.get("reasoning_model", config.reasoning_model),
        embedding_model=payload.get("embedding_model", config.embedding_model),
    )
