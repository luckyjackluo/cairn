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

import calendar
import datetime
import re
from pathlib import Path
from typing import Any

from . import lifecycle, query
from .files import FileError, FileService, _is_text
from .workspace import Workspace

# Statuses that count as "not yet done" — the default working set.
OPEN_STATUSES = ("todo", "doing", "blocked")
VALID_STATUSES = ("todo", "doing", "blocked", "done")
# Priority vocabulary, most-urgent first. Absent/unknown sorts after all of them.
VALID_PRIORITIES = ("p1", "p2", "p3")
_PRIORITY_RANK = {"p1": 0, "p2": 1, "p3": 2}
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


def _task_slug(path: str) -> str:
    """A task's stable id: its filename stem — what `blocked_by` references."""
    return Path(path).stem


def _norm_priority(value: Any) -> str | None:
    """Normalize a priority to p1/p2/p3, or ``None`` if absent/invalid."""
    p = str(value or "").strip().lower()
    return p if p in VALID_PRIORITIES else None


def _blocked_by_list(value: Any) -> list[str]:
    """Normalize a ``blocked_by`` frontmatter value to a list of task slugs."""
    if not value:
        return []
    parts = value.replace(",", " ").split() if isinstance(value, str) else [str(v) for v in value]
    return [p.strip() for p in parts if p and str(p).strip()]


# --- recurrence ------------------------------------------------------------
#
# A recurring task carries a ``recur`` cadence; completing it does not archive it
# but rolls it forward to its next occurrence (status back to ``todo``, ``due``
# advanced), so a weekly sync stays a single living task instead of spawning 52
# done files. ``recur`` accepts a named cadence (daily/weekly/monthly/yearly,
# biweekly) or a compact ``<N><unit>`` where unit is d/w/m/y (e.g. ``3d``, ``2w``).

_NAMED_RECUR = {
    "daily": (1, "d"), "weekly": (1, "w"), "biweekly": (2, "w"),
    "monthly": (1, "m"), "yearly": (1, "y"), "annually": (1, "y"),
}
_RECUR_RE = re.compile(r"(\d+)\s*([dwmy])")


def _add_months(d: datetime.date, months: int) -> datetime.date:
    """Advance a date by whole months, clamping to the last valid day."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def _parse_recur(recur: Any) -> tuple[int, str] | None:
    """Parse a ``recur`` value into ``(count, unit)`` or ``None`` if unusable."""
    r = str(recur or "").strip().lower()
    if not r:
        return None
    if r in _NAMED_RECUR:
        return _NAMED_RECUR[r]
    m = _RECUR_RE.fullmatch(r)
    return (int(m[1]), m[2]) if m and int(m[1]) > 0 else None


def _advance(d: datetime.date, count: int, unit: str) -> datetime.date:
    if unit == "d":
        return d + datetime.timedelta(days=count)
    if unit == "w":
        return d + datetime.timedelta(weeks=count)
    if unit == "m":
        return _add_months(d, count)
    return _add_months(d, 12 * count)  # y


def _next_occurrence(
    due: datetime.date | None,
    done_on: datetime.date,
    recur: Any,
    recur_from: Any = None,
) -> datetime.date | None:
    """The next due date for a recurring task completed on ``done_on``.

    Anchored to the old ``due`` for a fixed schedule (rent on the 1st), or to the
    completion date when there is no due or ``recur_from`` is ``done`` (a floating
    "every 3 days after I actually do it"). A fixed schedule that has fallen into
    the past is advanced until it lands after completion, so it never returns a
    date already gone by.
    """
    step = _parse_recur(recur)
    if step is None:
        return None
    count, unit = step
    anchor_done = str(recur_from or "").strip().lower() == "done" or due is None
    base = done_on if anchor_done else due
    nxt = _advance(base, count, unit)
    guard = 0
    while nxt <= done_on and guard < 1000:
        nxt = _advance(nxt, count, unit)
        guard += 1
    return nxt


def _record(ws: Workspace, path: str, meta: dict[str, Any], ref: datetime.date) -> dict[str, Any]:
    """Build a task result record from unified metadata.

    The ``blocked`` / ``actionable`` flags are cross-task and cannot be known from
    one file, so they are left for :func:`_annotate_blockedness` to fill in over
    the whole set; a lone record carries them as ``None``.
    """
    status = str(meta.get("status", "")).lower()
    due = _parse_date(meta.get("due"))
    return {
        "path": path,
        "slug": _task_slug(path),
        "title": meta.get("title") or path,
        "status": status or None,
        "due": due.isoformat() if due else None,
        "project": meta.get("project") or None,
        "context": meta.get("context") or None,
        "priority": _norm_priority(meta.get("priority")),
        "blocked_by": _blocked_by_list(meta.get("blocked_by")),
        "recur": str(meta.get("recur") or "").strip().lower() or None,
        "tags": meta.get("tags", []),
        "overdue": bool(due and due < ref and status != "done"),
        "today": bool(due and due == ref),
        "blocked": None,       # filled by _annotate_blockedness
        "actionable": None,    # filled by _annotate_blockedness
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


def _all_records(ws: Workspace, ref: datetime.date, path: str = "") -> list[dict[str, Any]]:
    """Every task under ``path`` as a record, unfiltered — the annotate input."""
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
        out.append(_record(ws, ws.relpath(p), meta, ref))
    return out


def _annotate_blockedness(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill in ``blocked`` / ``actionable`` across the full task set.

    A dependency in ``blocked_by`` blocks only while it resolves to an *open*
    task; a dep that is done — or that names no known task (a typo, a deleted
    task) — does not block, so stale references can never hide real work forever.
    A task is ``actionable`` when it is open, not manually ``blocked``, and has no
    open dependency. Records that don't need annotating are left as passed.
    """
    status_by = {r["slug"]: r["status"] for r in records}
    for r in records:
        open_deps = [d for d in r["blocked_by"] if status_by.get(d) in OPEN_STATUSES]
        r["blocked_deps"] = open_deps
        st = r["status"]
        is_open = st in OPEN_STATUSES
        r["blocked"] = bool(open_deps) or st == "blocked"
        r["actionable"] = is_open and st != "blocked" and not open_deps
    return records


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

    Each result carries ``{path, slug, title, status, due, project, context,
    priority, blocked_by, tags, overdue, today, blocked, actionable}``. Blockedness
    is computed against every task (so a done dependency correctly unblocks) before
    the status/project filters narrow the returned set.
    """
    ref = _parse_date(today) or datetime.date.today()
    want_statuses = _norm_statuses(status)
    cutoff = _parse_date(due_before)
    proj = project.lower() if project else None
    ctx = context.lower() if context else None

    records = _annotate_blockedness(_all_records(ws, ref, path))
    out: list[dict[str, Any]] = []
    for rec in records:
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


def next_actions(
    ws: Workspace,
    project: str | None = None,
    path: str = "",
    today: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """The actionable tasks right now — open, unblocked — best-first.

    Filters the task set to what you can actually start (excludes done, manually
    ``blocked``, and anything still waiting on an open dependency) and ranks by
    urgency: overdue first, then priority (p1>p2>p3, unprioritized last), then
    soonest due (undated last). This is the "what should I do next?" primitive;
    pass ``project`` to scope it, ``limit`` to cap the list.
    """
    ref = _parse_date(today) or datetime.date.today()
    proj = project.lower() if project else None
    records = _annotate_blockedness(_all_records(ws, ref, path))

    out = [
        r for r in records
        if r["actionable"] and (proj is None or str(r["project"] or "").lower() == proj)
    ]
    out.sort(key=lambda r: (
        0 if r["overdue"] else 1,
        _PRIORITY_RANK.get(r["priority"], 3),
        r["due"] is None,
        r["due"] or "",
        r["slug"],
    ))
    return out[:limit] if limit else out


def newly_unblocked(
    ws: Workspace, slug: str, path: str = "", today: str | None = None,
) -> list[dict[str, Any]]:
    """Tasks that just became actionable because task ``slug`` is now done.

    A task is reported only if it lists ``slug`` in ``blocked_by``, is still open,
    isn't manually ``blocked``, and has no *other* open dependency left. Reads the
    workspace as it stands, so call it after the completing write has landed.
    """
    ref = _parse_date(today) or datetime.date.today()
    records = _annotate_blockedness(_all_records(ws, ref, path))
    return [
        {"path": r["path"], "slug": r["slug"], "title": r["title"], "due": r["due"]}
        for r in records
        if slug in r["blocked_by"] and r["actionable"]
    ]


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
    priority: str | None = None,
    blocked_by: list[str] | str | None = None,
    recur: str | None = None,
) -> dict[str, Any]:
    """Create a task file from the ``task`` template and return its record.

    Files into ``dir`` (default ``tasks/``) with a slug derived from ``title``;
    a colliding name gets a numeric suffix rather than clobbering. ``source`` is
    an optional workspace path recorded in the task's frontmatter — used by
    :func:`harvest_checklists` to link a task back to the note it came from.
    ``priority`` is p1/p2/p3; ``blocked_by`` lists the slugs of tasks that must
    finish first (a task's slug is its filename stem). ``recur`` makes it a
    recurring task (daily/weekly/monthly/yearly/biweekly or ``<N><d|w|m|y>``):
    completing it rolls the same task forward to its next due date.
    """
    title = " ".join((title or "").split())
    if not title:
        raise FileError("Task title is required.")
    status = status.lower()
    if status not in VALID_STATUSES:
        raise FileError(f"Invalid status {status!r}. Use one of: {', '.join(VALID_STATUSES)}")
    prio = ""
    if priority:
        prio = priority.lower()
        if prio not in VALID_PRIORITIES:
            raise FileError(f"Invalid priority {priority!r}. Use one of: {', '.join(VALID_PRIORITIES)}")
    if recur and _parse_recur(recur) is None:
        raise FileError(f"Invalid recur {recur!r}. Use daily/weekly/monthly/yearly or e.g. 3d, 2w.")
    deps = _blocked_by_list(blocked_by)

    fs = FileService(ws)
    slug = _slugify(title)
    name = f"{slug}.md"
    probe = f"{dir}/{name}" if dir else name
    if ws.resolve(probe).exists():
        name = f"{slug}-{ws.next_uuid()}.md"
    fields = {
        "title": title,
        "status": status,
        "priority": prio,
        "due": due or "",
        "project": project or "",
        "context": context or "",
        "blocked_by": ", ".join(deps),
        "recur": (recur or "").strip().lower(),
        "tags": tags or ["task"],
        "source": source or "",
        "notes": notes,
    }
    item = fs.create_file(dir, name, template="task", fields=fields)
    p = ws.resolve(item["path"])
    meta = query.doc_meta(p) or {}
    return _record(ws, item["path"], meta, datetime.date.today())


def _write_meta(
    ws: Workspace, path: str, updates: dict[str, Any], when: str | None = None,
) -> dict[str, Any]:
    """Apply metadata ``updates`` to a task and return its record.

    Delegates the surgical write to :func:`cairn_core.lifecycle.write_fields`, the
    shared writer — so a status change here stamps ``status_changed`` for staleness
    exactly as it would for any other kind. ``when`` dates that stamp; pass it when
    the caller is recording a transition on a specific day (e.g. a backdated
    completion) so ``status_changed`` and the domain date agree.
    """
    meta = lifecycle.write_fields(ws, path, updates, when=when)
    return _record(ws, ws.relpath(ws.resolve(path)), meta, datetime.date.today())


def complete_task(ws: Workspace, path: str, when: str | None = None) -> dict[str, Any]:
    """Complete a task, stamping ``completed`` (default today).

    A **recurring** task (one with a ``recur`` cadence) is not archived: it rolls
    forward in place — status back to ``todo``, ``due`` advanced to the next
    occurrence, ``last_completed`` stamped, ``occurrences`` bumped — and the record
    carries ``recurred: {next_due, occurrences}``. A one-off task is marked ``done``
    and its record gains ``unblocked``: the tasks it was blocking that are now
    actionable, so finishing tells you what it just freed up.
    """
    done_on = when or datetime.date.today().isoformat()
    done_date = _parse_date(done_on) or datetime.date.today()
    meta = query.doc_meta(ws.resolve(path)) or {}
    recur = meta.get("recur")

    if recur and _parse_recur(recur) is not None:
        nxt = _next_occurrence(_parse_date(meta.get("due")), done_date, recur, meta.get("recur_from"))
        if nxt is not None:
            occurrences = int(str(meta.get("occurrences") or "0").strip() or 0) + 1
            rec = _write_meta(ws, path, {
                "status": "todo",
                "due": nxt.isoformat(),
                "last_completed": done_on,
                "occurrences": occurrences,
            }, when=done_on)
            rec["recurred"] = {"next_due": nxt.isoformat(), "occurrences": occurrences}
            rec["unblocked"] = []  # still open — it frees up nothing yet
            return rec

    rec = _write_meta(ws, path, {"status": "done", "completed": done_on}, when=done_on)
    rec["unblocked"] = newly_unblocked(ws, rec["slug"], today=when)
    return rec


def update_task(
    ws: Workspace,
    path: str,
    status: str | None = None,
    due: str | None = None,
    project: str | None = None,
    context: str | None = None,
    priority: str | None = None,
    blocked_by: list[str] | str | None = None,
    recur: str | None = None,
) -> dict[str, Any]:
    """Update one or more task fields in place (surgical frontmatter rewrite).

    ``priority`` accepts p1/p2/p3 or an empty string to clear it; ``blocked_by``
    replaces the dependency list (pass an empty list/string to clear it); ``recur``
    sets or clears the recurrence cadence.
    """
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
    if priority is not None:
        prio = priority.strip().lower()
        if prio and prio not in VALID_PRIORITIES:
            raise FileError(f"Invalid priority {priority!r}. Use one of: {', '.join(VALID_PRIORITIES)}")
        updates["priority"] = prio
    if blocked_by is not None:
        updates["blocked_by"] = _blocked_by_list(blocked_by)
    if recur is not None:
        r = recur.strip().lower()
        if r and _parse_recur(r) is None:
            raise FileError(f"Invalid recur {recur!r}. Use daily/weekly/monthly/yearly or e.g. 3d, 2w.")
        updates["recur"] = r
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
