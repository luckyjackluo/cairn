"""Task layer over the workspace.

A task is an ordinary frontmatter document with ``category: task`` and a small
lifecycle vocabulary — nothing here needs its own store. The read side
(:func:`list_tasks`) reuses the same unified metadata reader as
:mod:`cairn_core.query` (``.md`` frontmatter and ``.uni`` JSON alike) and layers
on the two things a raw metadata query can't express: an *ordering* by due date
and the *derived* flags (``overdue`` / ``today``) that only exist relative to a
runtime clock.

    ---
    title: File conference travel reimbursement
    category: task
    status: todo          # todo | doing | blocked | done
    due: 2026-07-22
    project: neurips
    context: "@errand"
    ---

The write side stays deliberately thin. :func:`add_task` stamps the ``task``
template through the same :class:`~cairn_core.files.FileService` path everything
else is filed with; :func:`complete_task` / :func:`update_task` are single-field
frontmatter rewrites (:func:`cairn_core.frontmatter.set_field`), so the rest of
the file — notes, links, hand-authored fields — is preserved byte-for-byte.
:func:`harvest_checklists` promotes ``- [ ]`` lines written inline in notes into
canonical task files, linking each line back to the task it became so a note
never harvests twice.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from . import frontmatter, query, uni
from .files import FileError, FileService, _is_text
from .workspace import Workspace

# Statuses that count as "not yet done" — the default working set.
OPEN_STATUSES = ("todo", "doing", "blocked")
VALID_STATUSES = ("todo", "doing", "blocked", "done")
# The tag/category markers that identify a document as a task.
TASK_MARKERS = ("task",)
# Default folder new tasks are filed into.
DEFAULT_DIR = "tasks"

_STOP = {"a", "an", "the", "of", "for", "and", "to", "in", "on", "with", "via",
         "is", "from", "by", "using", "as", "at", "my", "i"}


# --- shared helpers --------------------------------------------------------

def _is_task(meta: dict[str, Any]) -> bool:
    if str(meta.get("category", "")).lower() == "task":
        return True
    tags = {str(t).lower() for t in meta.get("tags", [])}
    return any(m in tags for m in TASK_MARKERS)


def _parse_date(value: Any) -> datetime.date | None:
    """Best-effort ISO date parse; ``None`` for empty or malformed values."""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _slugify(title: str, limit: int = 8) -> str:
    words = [w for w in re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
             if w and w not in _STOP]
    return "-".join(words[:limit]) or "task"


def _record(ws: Workspace, path: str, meta: dict[str, Any], ref: datetime.date) -> dict[str, Any]:
    """Build a task result record from unified metadata."""
    status = str(meta.get("status", "")).lower()
    due = _parse_date(meta.get("due"))
    return {
        "path": path,
        "title": meta.get("title") or path,
        "status": status or None,
        "due": due.isoformat() if due else None,
        "project": meta.get("project") or None,
        "context": meta.get("context") or None,
        "tags": meta.get("tags", []),
        "overdue": bool(due and due < ref and status != "done"),
        "today": bool(due and due == ref),
    }


# --- read ------------------------------------------------------------------

def _norm_statuses(status: str | list[str] | None) -> set[str] | None:
    """Normalize the ``status`` filter to a lowercase set, or ``None`` for all.

    ``"all"`` (or an explicit empty value) disables the status filter; anything
    else — a single status, a comma-separated string, or a list — becomes the
    allowed set. Defaults to the open statuses when ``status`` is omitted.
    """
    if status is None:
        return set(OPEN_STATUSES)
    if isinstance(status, str):
        if status.strip().lower() in ("", "all"):
            return None
        parts = status.replace(",", " ").split()
    else:
        parts = [str(s) for s in status]
    return {s.lower() for s in parts} or None


def list_tasks(
    ws: Workspace,
    status: str | list[str] | None = None,
    project: str | None = None,
    context: str | None = None,
    due_before: str | None = None,
    path: str = "",
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Return tasks in the workspace, sorted by due date (undated last).

    ``status`` defaults to the open set (todo/doing/blocked); pass ``"all"`` to
    include done tasks, or a specific status / list of statuses. ``project`` and
    ``context`` are exact case-insensitive filters. ``due_before`` keeps only
    tasks due strictly before that ISO date. ``today`` overrides the reference
    date used for the ``overdue`` / ``today`` flags (defaults to the real today).

    Each result carries ``{path, title, status, due, project, context, tags,
    overdue, today}``.
    """
    ref = _parse_date(today) or datetime.date.today()
    want_statuses = _norm_statuses(status)
    cutoff = _parse_date(due_before)
    proj = project.lower() if project else None
    ctx = context.lower() if context else None

    root = ws.resolve(path)
    out: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        meta = query.doc_meta(p)
        if meta is None or not _is_task(meta):
            continue

        rec = _record(ws, ws.relpath(p), meta, ref)
        if want_statuses is not None and (rec["status"] or "") not in want_statuses:
            continue
        if proj is not None and str(rec["project"] or "").lower() != proj:
            continue
        if ctx is not None and str(rec["context"] or "").lower() != ctx:
            continue
        if cutoff is not None:
            due = _parse_date(rec["due"])
            if due is None or not due < cutoff:
                continue
        out.append(rec)

    # Due date ascending, undated last; stable tiebreak on path.
    out.sort(key=lambda t: (t["due"] is None, t["due"] or "", t["path"]))
    return out


# --- write -----------------------------------------------------------------

def add_task(
    ws: Workspace,
    title: str,
    due: str | None = None,
    project: str | None = None,
    context: str | None = None,
    notes: str = "",
    status: str = "todo",
    tags: list[str] | None = None,
    dir: str = DEFAULT_DIR,
    source: str = "",
) -> dict[str, Any]:
    """Create a task file from the ``task`` template and return its record.

    Files into ``dir`` (default ``tasks/``) with a slug derived from ``title``;
    a colliding name gets a numeric suffix rather than clobbering. ``source`` is
    an optional workspace path recorded in the task's frontmatter — used by
    :func:`harvest_checklists` to link a task back to the note it came from.
    """
    title = " ".join((title or "").split())
    if not title:
        raise FileError("Task title is required.")
    status = status.lower()
    if status not in VALID_STATUSES:
        raise FileError(f"Invalid status {status!r}. Use one of: {', '.join(VALID_STATUSES)}")

    fs = FileService(ws)
    slug = _slugify(title)
    name = f"{slug}.md"
    probe = f"{dir}/{name}" if dir else name
    if ws.resolve(probe).exists():
        name = f"{slug}-{ws.next_uuid()}.md"
    fields = {
        "title": title,
        "status": status,
        "due": due or "",
        "project": project or "",
        "context": context or "",
        "tags": tags or ["task"],
        "source": source or "",
        "notes": notes,
    }
    item = fs.create_file(dir, name, template="task", fields=fields)
    p = ws.resolve(item["path"])
    meta = query.doc_meta(p) or {}
    return _record(ws, item["path"], meta, datetime.date.today())


def _write_meta(ws: Workspace, path: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Apply frontmatter/metadata ``updates`` to a task and return its record."""
    p = ws.resolve(path)
    if not p.is_file():
        raise FileError(f"Not a file: {path!r}")
    if uni.is_uni(p):
        obj = uni.read_uni(p)
        obj.setdefault("metadata", {}).update(updates)
        uni.write_uni(p, obj)
    elif _is_text(p):
        text = p.read_text(encoding="utf-8", errors="replace")
        for key, value in updates.items():
            text = frontmatter.set_field(text, key, value)
        p.write_text(text, encoding="utf-8")
    else:
        raise FileError(f"Cannot update a binary file: {path!r}")
    meta = query.doc_meta(p) or {}
    return _record(ws, ws.relpath(p), meta, datetime.date.today())


def complete_task(ws: Workspace, path: str, when: str | None = None) -> dict[str, Any]:
    """Mark a task done, stamping a ``completed`` date (defaults to today)."""
    done_on = when or datetime.date.today().isoformat()
    return _write_meta(ws, path, {"status": "done", "completed": done_on})


def update_task(
    ws: Workspace,
    path: str,
    status: str | None = None,
    due: str | None = None,
    project: str | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    """Update one or more task fields in place (surgical frontmatter rewrite)."""
    updates: dict[str, Any] = {}
    if status is not None:
        if status.lower() not in VALID_STATUSES:
            raise FileError(f"Invalid status {status!r}. Use one of: {', '.join(VALID_STATUSES)}")
        updates["status"] = status.lower()
    if due is not None:
        updates["due"] = due
    if project is not None:
        updates["project"] = project
    if context is not None:
        updates["context"] = context
    if not updates:
        raise FileError("Nothing to update: pass at least one field.")
    return _write_meta(ws, path, updates)


# --- harvest ---------------------------------------------------------------

# An unchecked GitHub-style checkbox line: ``- [ ] text`` (dash or star bullet).
_CHECKBOX_RE = re.compile(r"^(\s*[-*]\s+)\[ \]\s+(.+?)\s*$")
_DUE_RE = re.compile(r"\bdue:(\d{4}-\d{2}-\d{2})\b")
_PROJ_RE = re.compile(r"(?:^|\s)\+([^\s]+)")
_CTX_RE = re.compile(r"(?:^|\s)@([^\s]+)")
# Marker appended to a harvested line so it is never harvested a second time.
_MARKER = "<!--cairn:"


def _parse_checkbox(body: str) -> tuple[str, str | None, str | None, str | None]:
    """Pull inline ``due:``/``+project``/``@context`` out of a checkbox line.

    Returns ``(clean_title, due, project, context)`` with the metadata tokens
    stripped from the title.
    """
    due = project = context = None
    m = _DUE_RE.search(body)
    if m:
        due = m.group(1)
        body = body[: m.start()] + " " + body[m.end():]
    m = _PROJ_RE.search(body)
    if m:
        project = m.group(1)
        body = body[: m.start()] + " " + body[m.end():]
    m = _CTX_RE.search(body)
    if m:
        context = "@" + m.group(1)
        body = body[: m.start()] + " " + body[m.end():]
    return " ".join(body.split()), due, project, context


def harvest_checklists(
    ws: Workspace,
    path: str = "",
    dir: str = DEFAULT_DIR,
    link_back: bool = True,
) -> dict[str, Any]:
    """Promote ``- [ ]`` checkbox lines in notes into canonical task files.

    Scans text files under ``path`` (a single file or a folder; default the
    whole workspace), skipping the task folder itself and any dotfolders. Each
    unchecked checkbox becomes a task via :func:`add_task`, parsing inline
    ``due:YYYY-MM-DD``, ``+project`` and ``@context`` tokens. When ``link_back``
    is set (default), the source line is annotated with a hidden marker pointing
    at the new task, so re-running never double-harvests. Already-checked
    (``- [x]``) boxes are left alone. Returns ``{created, count}``.
    """
    fs_root = ws.resolve(path)
    targets = [fs_root] if fs_root.is_file() else [
        p for p in sorted(fs_root.rglob("*")) if p.is_file()
    ]
    created: list[dict[str, Any]] = []
    for p in targets:
        rel = ws.relpath(p)
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        if not _is_text(p):
            continue
        # Don't harvest tasks out of the task folder itself.
        if dir and (rel == dir or rel.startswith(f"{dir}/")):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        changed = False
        for i, line in enumerate(lines):
            if _MARKER in line:
                continue
            m = _CHECKBOX_RE.match(line)
            if not m:
                continue
            title, due, project, context = _parse_checkbox(m.group(2))
            if not title:
                continue
            item = add_task(
                ws, title, due=due, project=project, context=context,
                dir=dir, source=rel,
            )
            created.append(item)
            if link_back:
                lines[i] = f"{line} {_MARKER}{item['path']}-->"
                changed = True
        if changed:
            p.write_text("\n".join(lines), encoding="utf-8")
    return {"created": created, "count": len(created)}
