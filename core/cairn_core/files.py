"""File operations — the harness's toolset, as plain Python functions.

Every method takes and returns JSON-serializable data so the MCP server, a
CLI, or a web backend can each be a thin adapter over this one class. No
FastAPI, no LangChain, no cloud — just a :class:`Workspace` (a directory) and
the ``.uni`` format.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import convert, frontmatter, templates, uni
from .workspace import Workspace

# Extensions we can read as text (subset of the original product's list).
TEXT_EXTS = {
    ".txt", ".md", ".uni", ".json", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
    ".sh", ".bash", ".zsh", ".css", ".scss", ".html", ".htm", ".xml", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".sql", ".csv", ".tex", ".vue", ".svelte",
}


class FileError(Exception):
    """A recoverable, user-facing file operation error."""


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTS


class FileService:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace

    # -- describe ----------------------------------------------------------

    def _item(self, p: Path) -> dict[str, Any]:
        rel = self.ws.relpath(p)
        if p.is_dir():
            return {"name": p.name, "path": rel, "type": "folder"}
        item: dict[str, Any] = {
            "name": p.name,
            "path": rel,
            "type": "file",
            "size": p.stat().st_size,
        }
        if uni.is_uni(p):
            try:
                obj = uni.read_uni(p)
                item["tags"] = obj.get("tags", [])
                item["originalFormat"] = obj.get("metadata", {}).get("originalFormat")
            except (ValueError, OSError):
                pass
        return item

    def list_dir(self, path: str = "") -> list[dict[str, Any]]:
        target = self.ws.resolve(path)
        if not target.is_dir():
            raise FileError(f"Not a directory: {path!r}")
        items = [
            self._item(child)
            for child in sorted(target.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
            if not child.name.startswith(".")
        ]
        return items

    def get_tree(self, path: str = "", max_depth: int = 12) -> list[dict[str, Any]]:
        root = self.ws.resolve(path)

        def build(d: Path, depth: int) -> list[dict[str, Any]]:
            out = []
            for child in sorted(d.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
                if child.name.startswith("."):
                    continue
                node = self._item(child)
                if child.is_dir() and depth < max_depth:
                    node["children"] = build(child, depth + 1)
                out.append(node)
            return out

        if not root.is_dir():
            raise FileError(f"Not a directory: {path!r}")
        return build(root, 0)

    def read_detail(self, path: str) -> dict[str, Any]:
        p = self.ws.resolve(path)
        if not p.is_file():
            raise FileError(f"File not found: {path!r}")
        detail: dict[str, Any] = {"path": self.ws.relpath(p), "name": p.name}
        if uni.is_uni(p):
            obj = uni.read_uni(p)
            detail.update(
                type="uni",
                content=obj.get("content", ""),
                text=uni.html_to_text(obj.get("content", "")),
                tags=obj.get("tags", []),
                metadata=obj.get("metadata", {}),
                uuid=obj.get("uuid"),
            )
        elif _is_text(p):
            raw = p.read_text(encoding="utf-8", errors="replace")
            detail.update(type="text", text=raw)
            meta, _body = frontmatter.parse(raw)
            if meta:
                tags_val = meta.get("tags", [])
                detail["tags"] = (
                    [str(t) for t in tags_val]
                    if isinstance(tags_val, list)
                    else [str(tags_val)]
                )
                detail["metadata"] = {k: v for k, v in meta.items() if k != "tags"}
        else:
            detail.update(type="binary", size=p.stat().st_size)
        return detail

    # -- create ------------------------------------------------------------

    def create_file(
        self,
        path: str,
        name: str,
        content: str = "",
        template: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if template:
            content = templates.render(template, fields, self.ws)
        parent = self.ws.resolve(path)
        parent.mkdir(parents=True, exist_ok=True)
        target = self.ws.resolve(f"{self.ws.relpath(parent)}/{name}".lstrip("/"))
        if target.exists():
            raise FileError(f"Already exists: {self.ws.relpath(target)}")
        if uni.is_uni(target):
            obj = uni.make_uni(uni.to_uni_content(content), self.ws.next_uuid())
            uni.write_uni(target, obj)
        else:
            target.write_text(content, encoding="utf-8")
        return self._item(target)

    def create_folder(self, path: str, name: str) -> dict[str, Any]:
        parent = self.ws.resolve(path)
        target = self.ws.resolve(f"{self.ws.relpath(parent)}/{name}".lstrip("/"))
        if target.exists():
            raise FileError(f"Already exists: {self.ws.relpath(target)}")
        target.mkdir(parents=True)
        return self._item(target)

    # -- mutate / relocate -------------------------------------------------

    def rename_item(self, path: str, new_name: str) -> dict[str, Any]:
        if "/" in new_name or "\\" in new_name:
            raise FileError("new_name must be a bare name, not a path")
        src = self.ws.resolve(path)
        if not src.exists():
            raise FileError(f"Not found: {path!r}")
        dst = src.with_name(new_name)
        if dst.exists():
            raise FileError(f"Already exists: {self.ws.relpath(dst)}")
        src.rename(dst)
        return self._item(dst)

    def move_item(self, path: str, target_dir: str) -> dict[str, Any]:
        src = self.ws.resolve(path)
        if not src.exists():
            raise FileError(f"Not found: {path!r}")
        dest_dir = self.ws.resolve(target_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dst = dest_dir / src.name
        if dst.exists():
            raise FileError(f"Already exists: {self.ws.relpath(dst)}")
        shutil.move(str(src), str(dst))
        return self._item(dst)

    def delete_item(self, path: str) -> dict[str, Any]:
        p = self.ws.resolve(path)
        if p == self.ws.root:
            raise FileError("Refusing to delete the workspace root")
        if not p.exists():
            raise FileError(f"Not found: {path!r}")
        rel = self.ws.relpath(p)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"path": rel, "deleted": True}

    def multi_edit(self, path: str, old_string: str, new_string: str) -> dict[str, Any]:
        """Replace a single, unique occurrence of ``old_string`` in a file.

        For ``.uni`` files the replacement happens inside the HTML ``content``
        field; for plain-text files, on the raw text. Errors if the anchor is
        missing or ambiguous — same contract the original agent tool used.
        """
        if not old_string:
            raise FileError("old_string cannot be empty")
        p = self.ws.resolve(path)
        if not p.is_file():
            raise FileError(f"File not found: {path!r}")

        if uni.is_uni(p):
            obj = uni.read_uni(p)
            before = obj.get("content", "")
            after = self._replace_once(before, old_string, new_string)
            obj["content"] = after
            uni.write_uni(p, obj)
        elif _is_text(p):
            before = p.read_text(encoding="utf-8")
            after = self._replace_once(before, old_string, new_string)
            p.write_text(after, encoding="utf-8")
        else:
            raise FileError("Cannot edit a binary file")
        return {"path": self.ws.relpath(p), "changed": True}

    def write_content(self, path: str, content: str) -> dict[str, Any]:
        """Replace a document's whole body (for human editors, not agents).

        For ``.uni`` files, sets the HTML ``content`` field; for plain-text
        files, overwrites the text. Deliberately not exposed as an MCP tool —
        agents use the targeted ``multi_edit`` instead.
        """
        p = self.ws.resolve(path)
        if not p.is_file():
            raise FileError(f"File not found: {path!r}")
        if uni.is_uni(p):
            obj = uni.read_uni(p)
            obj["content"] = content
            uni.write_uni(p, obj)
        elif _is_text(p):
            p.write_text(content, encoding="utf-8")
        else:
            raise FileError("Cannot edit a binary file")
        return {"path": self.ws.relpath(p), "saved": True}

    @staticmethod
    def _replace_once(text: str, old: str, new: str) -> str:
        count = text.count(old)
        if count == 0:
            raise FileError("old_string not found")
        if count > 1:
            raise FileError(f"old_string is ambiguous ({count} matches); add more context")
        return text.replace(old, new, 1)

    # -- ingest ------------------------------------------------------------

    def import_file(
        self, path: str, dest_dir: str | None = None, keep_original: bool = True
    ) -> dict[str, Any]:
        """Convert a source document into a ``.uni`` doc alongside it.

        Turns a ``.docx``/``.pdf``/``.pptx``/``.csv``/``.xlsx``/``.md``/text/code
        file into an editable ``.uni`` document. The original is kept by default
        (local-first: we don't delete the user's file). Returns the new item.
        """
        src = self.ws.resolve(path)
        if not src.is_file():
            raise FileError(f"File not found: {path!r}")
        if uni.is_uni(src):
            raise FileError("File is already a .uni document")
        if not convert.can_convert(src.name):
            raise FileError(f"Cannot convert {src.suffix or 'this'} file to .uni")

        try:
            html_content = convert.convert_to_html(src)
        except convert.ConversionError as exc:
            raise FileError(str(exc)) from exc

        out_dir = self.ws.resolve(dest_dir) if dest_dir is not None else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (src.stem + ".uni")
        if target.exists():
            target = out_dir / (src.stem + f"-{self.ws.next_uuid()}.uni")

        obj = uni.make_uni(html_content, self.ws.next_uuid(), convert.original_format(src.name))
        uni.write_uni(target, obj)
        if not keep_original:
            src.unlink()
        result = self._item(target)
        result["importedFrom"] = self.ws.relpath(src) if keep_original else None
        return result

    def import_tree(
        self, path: str = "", keep_original: bool = True
    ) -> dict[str, Any]:
        """Import every convertible (non-.uni) file under ``path``.

        Returns a summary: counts and per-file results (including skips/errors),
        so a caller never silently drops files.
        """
        root = self.ws.resolve(path)
        imported: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or uni.is_uni(p):
                continue
            if any(part.startswith(".") for part in p.relative_to(self.ws.root).parts):
                continue
            rel = self.ws.relpath(p)
            if not convert.can_convert(p.name):
                skipped.append({"path": rel, "reason": "unsupported format"})
                continue
            try:
                imported.append(self.import_file(rel, keep_original=keep_original))
            except FileError as exc:
                skipped.append({"path": rel, "reason": str(exc)})
        return {"imported": len(imported), "skipped": len(skipped),
                "files": imported, "skips": skipped}

    # -- search ------------------------------------------------------------

    def search_files(self, query: str, path: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Find files/folders whose name contains ``query`` (case-insensitive)."""
        root = self.ws.resolve(path)
        q = query.lower()
        results = []
        for p in root.rglob("*"):
            if any(part.startswith(".") for part in p.relative_to(self.ws.root).parts):
                continue
            if q in p.name.lower():
                results.append(self._item(p))
                if len(results) >= limit:
                    break
        return results

    def grep(
        self, pattern: str, path: str = "", limit: int = 200, context: int = 0
    ) -> list[dict[str, Any]]:
        """Search text/``.uni`` file *contents* for ``pattern`` (substring).

        Returns match rows: ``{path, line, text}``. ``.uni`` files are searched
        against their flattened text so anchors match what a human reads.
        """
        root = self.ws.resolve(path)
        needle = pattern.lower()
        results: list[dict[str, Any]] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part.startswith(".") for part in p.relative_to(self.ws.root).parts):
                continue
            if not (_is_text(p) or uni.is_uni(p)):
                continue
            try:
                if uni.is_uni(p):
                    body = uni.html_to_text(uni.read_uni(p).get("content", ""))
                else:
                    body = p.read_text(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                continue
            for i, line in enumerate(body.splitlines(), start=1):
                if needle in line.lower():
                    results.append({"path": self.ws.relpath(p), "line": i, "text": line.strip()[:400]})
                    if len(results) >= limit:
                        return results
        return results
