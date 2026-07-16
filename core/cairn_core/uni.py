"""The ``.uni`` document format.

A ``.uni`` file is a small JSON document::

    {
      "uuid": 12,
      "content": "<p>TipTap-compatible HTML</p>",
      "metadata": {"contentFormat": "html", "originalFormat": "text"},
      "tags": ["reports", "q3"]
    }

Tags live *inside the file* — there is no external tag database — which keeps
a workspace fully portable and local-first. Plain-text/code files can be
imported into ``.uni`` documents, and any ``.uni`` document can be flattened
back to plain text for reading.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any


def is_uni(path: str | Path) -> bool:
    return str(path).lower().endswith(".uni")


# --- read / write ----------------------------------------------------------

def read_uni(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Not a valid .uni document: {path}")
    obj.setdefault("content", "")
    obj.setdefault("metadata", {})
    obj.setdefault("tags", [])
    return obj


def write_uni(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def make_uni(
    content_html: str,
    uuid: int,
    original_format: str = "text",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "content": content_html,
        "metadata": {"contentFormat": "html", "originalFormat": original_format},
        "tags": list(tags or []),
    }


# --- conversions -----------------------------------------------------------

_BLOCK_RE = re.compile(r"</(p|div|h[1-6]|li|br|tr)>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def text_to_html(raw_text: str) -> str:
    """Wrap plain text as minimal TipTap-compatible HTML (one <p> per line)."""
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts = []
    for line in lines:
        if line.strip() == "":
            parts.append("<p></p>")
        else:
            parts.append(f"<p>{html.escape(line)}</p>")
    return "".join(parts) or "<p></p>"


def html_to_text(content_html: str) -> str:
    """Flatten HTML content to readable plain text (dependency-free)."""
    if not content_html:
        return ""
    text = _BLOCK_RE.sub("\n", content_html)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<[a-zA-Z][^>]*>", text or ""))


def to_uni_content(raw: str) -> str:
    """Return HTML content for a ``.uni`` document from raw text or HTML."""
    return raw if looks_like_html(raw) else text_to_html(raw)
