"""Embedding backends for semantic retrieval.

The default retriever is lexical and needs nothing. Embeddings are opt-in and
**bring-your-own-key**: point at any OpenAI-compatible ``/embeddings`` endpoint
(OpenAI, or a local server like Ollama / LM Studio / llama.cpp) via env vars:

    CAIRN_EMBED_API_KEY   (or OPENAI_API_KEY)
    CAIRN_EMBED_BASE_URL  (default https://api.openai.com/v1)
    CAIRN_EMBED_MODEL     (default text-embedding-3-small)

``httpx`` is imported lazily, so importing this module costs nothing until an
API-backed embedder is actually constructed.
"""

from __future__ import annotations

import os
from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...


class OpenAIEmbedder:
    """Calls an OpenAI-compatible /embeddings endpoint. BYO key + base_url."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        import httpx  # lazy: only needed when embeddings are actually used

        self._client = httpx.Client(timeout=60)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": list(texts)},
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


def get_embedder() -> Embedder | None:
    """Return an embedder configured from the environment, or None (→ lexical)."""
    key = os.getenv("CAIRN_EMBED_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    base = os.getenv("CAIRN_EMBED_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("CAIRN_EMBED_MODEL", "text-embedding-3-small")
    return OpenAIEmbedder(key, base, model)
