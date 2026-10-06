"""Ollama chat with tool calling (POST /api/chat)."""

import requests


class LLMError(RuntimeError):
    pass


class Ollama:
    def __init__(self, cfg):
        self.url = cfg["OllamaUrl"].rstrip("/")
        self.model = cfg["Model"]
        self.think = cfg["Think"]
        self.ctx = cfg["ContextTokens"]

    def chat(self, messages, tools=None, timeout=600):
        """Returns the assistant message: {"role", "content", "thinking"?, "tool_calls"?}."""
        body = {"model": self.model, "messages": messages, "stream": False,
                "options": {"num_ctx": self.ctx, "temperature": 0.3}}
        if tools:
            body["tools"] = tools
        if self.think:
            body["think"] = True
        try:
            r = requests.post(f"{self.url}/api/chat", json=body, timeout=timeout)
        except requests.RequestException as e:
            raise LLMError(f"Ollama isn't answering at {self.url}: {e}")
        if r.status_code != 200:
            raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
        return r.json().get("message", {})

    def available(self):
        try:
            tags = requests.get(f"{self.url}/api/tags", timeout=3).json().get("models", [])
            return any(m.get("name") == self.model or m.get("model") == self.model for m in tags)
        except (requests.RequestException, ValueError):
            return False
