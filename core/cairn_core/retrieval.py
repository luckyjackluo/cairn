"""Retrieval — find the most relevant documents for a query.

Two backends, one entry point:

* **lexical** (default, zero-dep): token-overlap scoring with a tag boost.
* **embeddings** (opt-in): if an :class:`Embedder` is configured (env vars) or
  passed in, documents are embedded into a local SQLite index and ranked by
  cosine similarity. Falls back to lexical if anything goes wrong.

``semantic_retrieve`` is the stable signature every caller uses, so switching
backends never touches the MCP server, CLI, or web API.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import frontmatter, uni
from .embeddings import Embedder, get_embedder
from .files import _is_text
from .workspace import Workspace

_WORD = re.compile(r"[a-zA-Z0-9_]+")
_AUTO = object()  # sentinel: resolve embedder from environment


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def semantic_retrieve(
    ws: Workspace, query: str, k: int = 5, embedder: Any = _AUTO
) -> list[dict[str, Any]]:
    """Return up to ``k`` documents most relevant to ``query``.

    Uses embeddings when available (``embedder`` passed, or configured via env),
    otherwise lexical scoring. Set ``embedder=None`` to force lexical.
    """
    if embedder is _AUTO:
        embedder = get_embedder()
    if embedder is not None:
        try:
            return _embedding_retrieve(ws, query, k, embedder)
        except Exception:
            pass  # any failure (network, numpy missing, ...) → lexical fallback
    return _lexical_retrieve(ws, query, k)


def reindex(ws: Workspace, embedder: Any = _AUTO) -> dict[str, Any]:
    """Force-build the embedding index. Returns change counts, or a note if
    no embedder is configured."""
    if embedder is _AUTO:
        embedder = get_embedder()
    if embedder is None:
        return {"indexed": False, "reason": "no embedder configured (lexical retrieval in use)"}
    from .index import VectorIndex

    idx = VectorIndex(ws)
    try:
        stats = idx.refresh(embedder)
    finally:
        idx.close()
    return {"indexed": True, **stats}


def _embedding_retrieve(ws: Workspace, query: str, k: int, embedder: Embedder) -> list[dict[str, Any]]:
    from .index import VectorIndex

    idx = VectorIndex(ws)
    try:
        idx.refresh(embedder)
        qv = embedder.embed([query])[0]
        return idx.search(qv, k)
    finally:
        idx.close()


def _lexical_retrieve(ws: Workspace, query: str, k: int) -> list[dict[str, Any]]:
    q = Counter(_tokens(query))
    if not q:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for p in ws.root.rglob("*"):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        tags: list[str] = []
        try:
            if uni.is_uni(p):
                obj = uni.read_uni(p)
                body = uni.html_to_text(obj.get("content", ""))
                tags = [str(t) for t in obj.get("tags", [])]
            elif _is_text(p):
                body = p.read_text(encoding="utf-8", errors="replace")
                tags = frontmatter.get_tags(body)
            else:
                continue
        except (ValueError, OSError):
            continue

        doc = Counter(_tokens(body))
        tag_tokens = set(_tokens(" ".join(tags)))
        score = 0.0
        for term, qf in q.items():
            if term in doc:
                score += qf * doc[term]
            if term in tag_tokens:
                score += qf * 3
        if score > 0:
            snippet = body.strip().replace("\n", " ")[:240]
            scored.append((score, {"path": ws.relpath(p), "score": round(score, 2),
                                   "tags": tags, "snippet": snippet}))

    scored.sort(key=lambda s: s[0], reverse=True)
    return [item for _, item in scored[:k]]
