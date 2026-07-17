"""A local SQLite vector index for embeddings-based retrieval.

Vectors live in ``<workspace>/.cairn/index.db`` (a hidden dir the file
scanners already skip). The index is **incremental**: only new or modified
files (by mtime) are re-embedded on ``refresh``, and vanished files are pruned.
Similarity is cosine, computed with numpy.

Requires the ``embeddings`` extra (numpy). Import is lazy so the default
lexical retriever keeps the core dependency-free.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

from . import frontmatter, uni
from .embeddings import Embedder
from .files import _is_text
from .workspace import Workspace

INDEX_DIRNAME = ".cairn"
_MAX_CHARS = 8000
_BATCH = 64


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class VectorIndex:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        d = ws.root / INDEX_DIRNAME
        d.mkdir(exist_ok=True)
        self.db = sqlite3.connect(d / "index.db")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            "path TEXT PRIMARY KEY, mtime REAL, model TEXT, dim INTEGER, "
            "vec BLOB, snippet TEXT, tags TEXT)"
        )
        self.db.commit()

    # -- helpers -----------------------------------------------------------

    def _doc_text(self, p: Path) -> tuple[str, list[str]]:
        if uni.is_uni(p):
            obj = uni.read_uni(p)
            return uni.html_to_text(obj.get("content", "")), [str(t) for t in obj.get("tags", [])]
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, frontmatter.get_tags(text)

    def _current_files(self) -> list[Path]:
        out = []
        for p in self.ws.root.rglob("*"):
            if not p.is_file():
                continue
            if any(part.startswith(".") for part in p.relative_to(self.ws.root).parts):
                continue
            if uni.is_uni(p) or _is_text(p):
                out.append(p)
        return out

    # -- build / query -----------------------------------------------------

    def refresh(self, embedder: Embedder) -> dict[str, int]:
        """Bring the index up to date with the workspace. Returns change counts."""
        model = embedder.model
        existing = {
            row[0]: (row[1], row[2])
            for row in self.db.execute("SELECT path, mtime, model FROM vectors")
        }
        files = self._current_files()
        live = {self.ws.relpath(p) for p in files}

        pending: list[tuple[str, str, list[str]]] = []  # (rel, text, tags)
        for p in files:
            rel = self.ws.relpath(p)
            mtime = p.stat().st_mtime
            prev = existing.get(rel)
            if prev and abs(prev[0] - mtime) < 1e-6 and prev[1] == model:
                continue
            text, tags = self._doc_text(p)
            if text.strip():
                pending.append((rel, text[:_MAX_CHARS], tags))

        added = 0
        for i in range(0, len(pending), _BATCH):
            batch = pending[i : i + _BATCH]
            vecs = embedder.embed([t for _, t, _ in batch])
            for (rel, text, tags), vec in zip(batch, vecs):
                mtime = (self.ws.resolve(rel)).stat().st_mtime
                snippet = text.strip().replace("\n", " ")[:240]
                self.db.execute(
                    "REPLACE INTO vectors (path, mtime, model, dim, vec, snippet, tags) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (rel, mtime, model, len(vec), _pack(vec), snippet, json.dumps(tags)),
                )
                added += 1

        stale = [pth for pth in existing if pth not in live]
        for pth in stale:
            self.db.execute("DELETE FROM vectors WHERE path = ?", (pth,))
        self.db.commit()
        return {"updated": added, "removed": len(stale), "total": len(live)}

    def search(self, query_vec: list[float], k: int = 5) -> list[dict[str, Any]]:
        import numpy as np

        rows = self.db.execute("SELECT path, dim, vec, snippet, tags FROM vectors").fetchall()
        if not rows:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        qn = np.linalg.norm(q) or 1.0
        scored = []
        for path, dim, blob, snippet, tags in rows:
            v = np.frombuffer(blob, dtype=np.float32)
            if v.shape[0] != q.shape[0]:
                continue  # dimension mismatch (model changed); skip until re-embedded
            sim = float(v @ q / ((np.linalg.norm(v) or 1.0) * qn))
            scored.append((sim, path, snippet, tags))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [
            {"path": p, "score": round(sim, 4), "tags": json.loads(t or "[]"), "snippet": sn}
            for sim, p, sn, t in scored[:k]
        ]

    def close(self) -> None:
        self.db.close()
