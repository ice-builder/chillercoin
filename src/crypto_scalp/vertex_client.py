from __future__ import annotations

import json
from typing import List, Optional

from .ollama_local import OllamaClient, OllamaConfig


class HybridAIClient:
    """
    Фасадный клиент, который маршрутизирует запросы:
    - Модели, начинающиеся с 'gemini-', отправляются в Google Cloud Vertex AI
    - Остальные отправляются в локальный Ollama
    """
    def __init__(self, config: Optional[OllamaConfig] = None) -> None:
        self.config = config or OllamaConfig()
        self.ollama = OllamaClient(self.config)
        self.project_id = "coding-maximum"
        self.location = "global"
        try:
            from google import genai
            self.vertex_client = genai.Client(vertexai=True, project=self.project_id, location=self.location)
        except ImportError:
            self.vertex_client = None

    def chat(self, prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
        actual_model = model or self.config.reasoning_model
        if actual_model.startswith("gemini-"):
            return self._vertex_chat(prompt, system, actual_model)
        return self.ollama.chat(prompt, system, actual_model)

    def embed(self, text: str, model: Optional[str] = None) -> List[float]:
        actual_model = model or self.config.embedding_model
        # Пока поддерживаем только локальные эмбеддинги, но при желании можно добавить Vertex
        return self.ollama.embed(text, actual_model)

    def _vertex_chat(self, prompt: str, system: Optional[str], model: str) -> str:
        if not self.vertex_client:
            raise RuntimeError("The google-genai library is not installed. Please run: pip install google-genai")
            
        from google.genai import types
        
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)]
            )
        ]
        
        config = types.GenerateContentConfig()
        if system:
            config.system_instruction = system
            
        try:
            response = self.vertex_client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            return response.text
        except Exception as e:
            raise RuntimeError(f"Vertex AI API Error: {e}")
