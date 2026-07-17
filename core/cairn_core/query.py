"""Structured queries over document metadata.

Where :mod:`cairn_core.retrieval` answers *fuzzy* questions ("what's relevant to
this query"), this answers *exact* ones: "every document where ``status`` is
``to-read``", "everything tagged ``tgn`` in project ``amazon``". It reads the
same unified metadata as the tag layer — ``.uni`` JSON fields and ``.md`` YAML
frontmatter alike — so a read-later queue is just ``{"status": "to-read"}``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import frontmatter, uni
from .files import _is_text
from .workspace import Workspace


def doc_meta(p: Path) -> dict[str, Any] | None:
    """Unified metadata for a file — fields plus a normalized ``tags`` list.

    Returns ``None`` for binary files or files with no metadata at all.
    """
    try:
        if uni.is_uni(p):
            obj = uni.read_uni(p)
            meta = dict(obj.get("metadata", {}))
            meta["tags"] = [str(t) for t in obj.get("tags", [])]
            return meta
        if _is_text(p):
            parsed, _ = frontmatter.parse(p.read_text(encoding="utf-8", errors="replace"))
            if not parsed:
                return None
            meta = dict(parsed)
            tv = meta.get("tags", [])
            meta["tags"] = [str(t) for t in tv] if isinstance(tv, list) else [str(tv)]
            return meta
    except (ValueError, OSError):
        return None
    return None


def _matches(meta: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, want in filters.items():
        have = meta.get(key)
        if key == "tags" or isinstance(have, list):
            haystack = {str(h).lower() for h in (have or [])}
            wants = want if isinstance(want, list) else [want]
            if not all(str(w).lower() in haystack for w in wants):
                return False
        else:
            if have is None or str(have).lower() != str(want).lower():
                return False
    return True


def find_by_meta(
    ws: Workspace, filters: dict[str, Any], path: str = ""
) -> list[dict[str, Any]]:
    """Return documents whose metadata matches *all* ``filters``.

    A scalar filter matches by case-insensitive equality; a filter against a
    list-valued field (or the ``tags`` key) matches when every requested value
    is present. Results are sorted by path.
    """
    root = ws.resolve(path)
    out: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        meta = doc_meta(p)
        if meta is None or not _matches(meta, filters):
            continue
        out.append(
            {
                "path": ws.relpath(p),
                "tags": meta.get("tags", []),
                "metadata": {k: v for k, v in meta.items() if k != "tags"},
            }
        )
    return out
