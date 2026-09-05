"""BYOK LLM providers + a dependency-free fallback synthesizer.

Providers are called with plain ``httpx`` — no heavy SDKs. Supported:
openai, openrouter, ollama (OpenAI-compatible), anthropic, gemini.
If no provider/key is configured, ``LLM.answer()`` transparently falls back
to an extractive synthesizer so ``/ai/search`` always works.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import httpx

from jiro.config import Settings
from jiro.errors import LLMError

SYSTEM_ROLE = "system"
USER_ROLE = "user"
ASSISTANT_ROLE = "assistant"

DEFAULT_SYSTEM_PROMPT = (
    "You are Jiro, a precise research assistant. Answer the user's question using "
    "only the provided web search excerpts. Prefer facts found in the sources, and "
    "explicitly say when sources do not contain enough information. Use short, "
    "well-structured paragraphs or bullets. Never fabricate citations."
)


class LLMProvider:
    """Single unified completion interface."""

    name = "base"

    def __init__(self, settings: Settings, *, model: Optional[str] = None,
                 api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self.settings = settings
        cfg = settings.llm
        self.model = model or cfg.get("model", "gpt-4o-mini")
        self.api_key = api_key or cfg.get("api_key", "") or ""
        self.base_url = base_url or cfg.get("base_url", "") or ""
        self.temperature = float(cfg.get("temperature", 0.2))
        self.max_tokens = int(cfg.get("max_tokens", 1024))

    async def complete(self, messages: List[Dict[str, str]], *,
                       system: Optional[str] = None) -> str:
        raise NotImplementedError


class OpenAICompatProvider(LLMProvider):
    """OpenAI / OpenRouter / Ollama / any OpenAI-compatible chat endpoint."""

    name = "openai-compatible"

    def __init__(self, settings: Settings, *, model: Optional[str] = None,
                 api_key: Optional[str] = None, base_url: Optional[str] = None,
                 name: str = "openai") -> None:
        super().__init__(settings, model=model, api_key=api_key, base_url=base_url)
        self.name = name
        if not self.base_url:
            self.base_url = "https://api.openai.com/v1"
        self.url = self.base_url.rstrip("/") + "/chat/completions"

    async def complete(self, messages: List[Dict[str, str]], *,
                       system: Optional[str] = None) -> str:
        if not self.api_key and self.name != "ollama":
            raise LLMError(f"no API key configured for provider '{self.name}'")
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": ([{"role": SYSTEM_ROLE, "content": system}] if system else [])
            + messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(self.url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response else "?"
            raise LLMError(f"{self.name} completion failed (HTTP {status})") from exc
        except Exception as exc:
            raise LLMError(f"{self.name} completion failed") from exc


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings, *, model: Optional[str] = None,
                 api_key: Optional[str] = None) -> None:
        super().__init__(settings, model=model, api_key=api_key)
        if self.model.startswith("gpt"):
            self.model = "claude-3-5-sonnet-20241022"

    async def complete(self, messages: List[Dict[str, str]], *,
                       system: Optional[str] = None) -> str:
        if not self.api_key:
            raise LLMError("no API key configured for provider 'anthropic'")
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m for m in messages if m["role"] != SYSTEM_ROLE],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if system:
            payload["system"] = system
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return "".join(
                    b.get("text", "") for b in data.get("content", [])
                    if b.get("type") == "text"
                ).strip()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response else "?"
            raise LLMError(f"anthropic completion failed (HTTP {status})") from exc
        except Exception as exc:
            raise LLMError("anthropic completion failed") from exc


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings, *, model: Optional[str] = None,
                 api_key: Optional[str] = None) -> None:
        super().__init__(settings, model=model, api_key=api_key)
        if self.model.startswith("gpt"):
            self.model = "gemini-1.5-flash"

    async def complete(self, messages: List[Dict[str, str]], *,
                       system: Optional[str] = None) -> str:
        if not self.api_key:
            raise LLMError("no API key configured for provider 'gemini'")
        contents = []
        for m in messages:
            if m["role"] == SYSTEM_ROLE:
                continue
            role = "user" if m["role"] == USER_ROLE else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
        payload: Dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        headers = {}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response else "?"
            raise LLMError(f"gemini completion failed (HTTP {status})") from exc
        except Exception as exc:
            raise LLMError("gemini completion failed") from exc


PROVIDERS: Dict[str, Any] = {
    "openai": OpenAICompatProvider,
    "openrouter": OpenAICompatProvider,
    "ollama": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}

BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}


def build_provider(settings: Settings, *, provider: Optional[str] = None,
                   model: Optional[str] = None) -> LLMProvider:
    name = (provider or settings.llm.get("provider") or "openai").lower()
    if name not in PROVIDERS:
        raise LLMError(f"unsupported LLM provider '{name}'",
                       details={"available": sorted(PROVIDERS)})
    cls = PROVIDERS[name]
    kwargs: Dict[str, Any] = {}
    if name in ("openai", "openrouter", "ollama"):
        kwargs["name"] = name
        if name in BASE_URLS and not settings.llm.get("base_url"):
            kwargs["base_url"] = BASE_URLS[name]
    return cls(settings, model=model, **kwargs)


class LLM:
    """Facade: try the configured LLM; fall back to extractive synthesis."""

    def __init__(self, settings: Settings, *, provider: Optional[str] = None,
                 model: Optional[str] = None) -> None:
        self.settings = settings
        self.provider_name = provider or settings.llm.get("provider") or "openai"
        self.model = model or settings.llm.get("model") or "gpt-4o-mini"
        self._llm: Optional[LLMProvider] = None

    @property
    def available(self) -> bool:
        cfg = self.settings.llm
        key = (cfg.get("api_key") or "").strip()
        return bool(key) or self.provider_name == "ollama"

    async def complete(self, messages: List[Dict[str, str]], *,
                       system: Optional[str] = None) -> str:
        if self._llm is None:
            self._llm = build_provider(
                self.settings, provider=self.provider_name, model=self.model
            )
        return await self._llm.complete(messages, system=system)

    # --------------------------------------------------- extractive fallback
    @staticmethod
    def synthesize_without_llm(question: str, sources: List[Dict[str, Any]],
                               max_sources: int = 5) -> str:
        """Produce a cited answer from snippets only (no LLM needed)."""
        used = sources[:max_sources]
        if not used:
            return "No sources were found for this query."
        lines = [f"Here is what the available sources say about “{question}”:\n"]
        for i, src in enumerate(used, start=1):
            snippet = re.sub(r"\s+", " ", src.get("snippet") or src.get("content") or "")
            lines.append(f"{i}. **{src.get('title', 'Source')}** — {snippet[:400]}")
        lines.append(
            "\n(Heuristic answer generated without an LLM. Configure `llm.api_key` "
            "for synthesized prose answers.)"
        )
        return "\n".join(lines)


def count_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)
