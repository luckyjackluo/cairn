"""Project pulse — per-project momentum and health, computed from frontmatter.

Where :func:`cairn_core.lifecycle.attention` surfaces individual *items* that need
the user, ``pulse`` steps up one level to the **project**. For every distinct
``project:`` value in the workspace it derives the signals a project manager
actually uses — how many tasks are open, how many are overdue, what's in progress,
the next deadline, and how long since anything in that project was last touched —
so you can tell at a glance which projects are moving and which have stalled.

Everything here is derived deterministically from frontmatter you already write
(``project``, ``status``, ``due``, ``status_changed``, ``completed``, ``created``,
``date``). The engine computes the numbers; the agent only narrates them. Nothing
here reaches the network or an LLM.

:func:`refresh_dashboard` writes the computed numbers back into a project's
``projects/<name>.md`` dashboard, between HTML-comment markers, leaving every
hand-written section untouched — so the "Last updated" line stops lying.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

from . import query, tasks
from .workspace import Workspace

# A project is "stale" once nothing in it has been touched for this many days.
# Overridable per call. 10 days ≈ "you haven't looked at this in over a week".
STALE_AFTER_DAYS = 10

# Frontmatter fields that count as "the project was worked on", newest wins.
_ACTIVITY_FIELDS = ("status_changed", "completed", "date", "created")

# The workspace filename convention is `YYYY-MM-DD-slug.md` (see _GUIDE.md), and
# auto-filed paper notes carry their date ONLY there, not in frontmatter — so the
# leading date is a first-class activity signal, not a fallback afterthought.
_FILENAME_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Dashboard block markers. Everything between them is regenerated on refresh;
# everything outside them is yours to edit freely.
PULSE_START = "<!-- cairn:pulse:start -->"
PULSE_END = "<!-- cairn:pulse:end -->"


# --------------------------------------------------------------------------- #
# sweep + aggregation
# --------------------------------------------------------------------------- #

def _iter_meta(ws: Workspace, path: str):
    """Yield ``(relpath, meta)`` for every metadata-bearing file under ``path``.

    Mirrors the task/bill/lifecycle readers' document set: dotfiles and dotfolders
    (``.cairn`` state, git) are skipped, and files with no frontmatter — the
    ``projects/*.md`` dashboards themselves — return no meta and drop out, so a
    dashboard refresh can never feed back into a project's own activity date.
    """
    root = ws.resolve(path)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        meta = query.doc_meta(p)
        if meta is not None:
            yield ws.relpath(p), meta


def _filename_date(relpath: str) -> datetime.date | None:
    """The leading ``YYYY-MM-DD`` in a filename, if present (the note convention)."""
    m = _FILENAME_DATE.match(Path(relpath).name)
    if not m:
        return None
    try:
        return datetime.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _activity_date(meta: dict[str, Any], relpath: str = "") -> datetime.date | None:
    """Most recent date at which this note was worked on, or ``None``.

    Takes the newest of the frontmatter activity fields and the filename date —
    a paper note dates itself only in its filename, a task only in frontmatter,
    and an edited paper could legitimately have both.
    """
    best = _filename_date(relpath) if relpath else None
    for f in _ACTIVITY_FIELDS:
        d = tasks._parse_date(meta.get(f))
        if d is not None and (best is None or d > best):
            best = d
    return best


def project_pulse(
    ws: Workspace,
    today: str | None = None,
    stale_after_days: int = STALE_AFTER_DAYS,
    path: str = "",
) -> dict[str, Any]:
    """Per-project health across the workspace, most-neglected first.

    One sweep groups every ``project:``-bearing note by project and derives:
    ``notes_total``; open / overdue / in-progress / blocked task counts; the next
    deadline (``next_due`` + ``next_due_title``); the first in-progress task
    (``in_progress``); ``last_activity`` and ``days_since_activity``; and the
    ``stale`` / ``has_overdue`` flags. Returns
    ``{as_of, stale_after_days, projects: [...]}`` sorted by longest-idle first
    (never-dated projects last), so the top of the list is what a manager should
    poke at.
    """
    ref = tasks._parse_date(today) or datetime.date.today()
    acc: dict[str, dict[str, Any]] = {}

    for _rel, meta in _iter_meta(ws, path):
        project = str(meta.get("project") or "").strip()
        if not project:
            continue
        p = acc.setdefault(project, {
            "project": project,
            "notes_total": 0,
            "tasks_open": 0,
            "tasks_overdue": 0,
            "tasks_doing": 0,
            "tasks_blocked": 0,
            "_next_due": None,          # (date, title) — internal, resolved below
            "in_progress": None,
            "_last": None,              # date — internal
        })
        p["notes_total"] += 1

        act = _activity_date(meta, _rel)
        if act is not None and (p["_last"] is None or act > p["_last"]):
            p["_last"] = act

        if not tasks._is_task(meta):
            continue
        status = str(meta.get("status") or "").lower()
        if status not in tasks.OPEN_STATUSES:
            continue  # done tasks don't count toward open workload
        p["tasks_open"] += 1
        due = tasks._parse_date(meta.get("due"))
        title = str(meta.get("title") or "").strip() or _rel
        if due is not None and due < ref:
            p["tasks_overdue"] += 1
        if status == "doing":
            p["tasks_doing"] += 1
            if p["in_progress"] is None:
                p["in_progress"] = title
        elif status == "blocked":
            p["tasks_blocked"] += 1
        if due is not None and (p["_next_due"] is None or due < p["_next_due"][0]):
            p["_next_due"] = (due, title)

    projects: list[dict[str, Any]] = []
    for p in acc.values():
        last = p.pop("_last")
        nd = p.pop("_next_due")
        days_idle = (ref - last).days if last is not None else None
        projects.append({
            **p,
            "last_activity": last.isoformat() if last else None,
            "days_since_activity": days_idle,
            "next_due": nd[0].isoformat() if nd else None,
            "next_due_title": nd[1] if nd else None,
            "has_overdue": p["tasks_overdue"] > 0,
            "stale": days_idle is not None and days_idle > stale_after_days,
        })

    # Longest-idle first; projects with no datable activity sort to the end.
    projects.sort(key=lambda r: (
        r["days_since_activity"] is None,
        -(r["days_since_activity"] or 0),
        r["project"],
    ))
    return {
        "as_of": ref.isoformat(),
        "stale_after_days": stale_after_days,
        "projects": projects,
    }


def stale_projects(
    ws: Workspace,
    threshold_days: int = STALE_AFTER_DAYS,
    today: str | None = None,
    path: str = "",
) -> list[dict[str, Any]]:
    """Just the stalled projects — a lean call for a daily digest line.

    Returns ``[{project, days_since_activity, tasks_open, tasks_overdue}]`` for
    every project idle longer than ``threshold_days``, longest-idle first.
    """
    pulse = project_pulse(ws, today=today, stale_after_days=threshold_days, path=path)
    return [
        {
            "project": p["project"],
            "days_since_activity": p["days_since_activity"],
            "tasks_open": p["tasks_open"],
            "tasks_overdue": p["tasks_overdue"],
        }
        for p in pulse["projects"] if p["stale"]
    ]


# --------------------------------------------------------------------------- #
# dashboard refresh
# --------------------------------------------------------------------------- #

def _momentum_label(entry: dict[str, Any], stale_after_days: int) -> str:
    """A one-word read on the project, from its idle time and overdue load."""
    days = entry["days_since_activity"]
    if days is None:
        return "no dated activity"
    if entry["has_overdue"]:
        return "needs attention (overdue tasks)"
    if days > 2 * stale_after_days:
        return "stalled"
    if days > stale_after_days:
        return "slowing"
    return "active"


def render_pulse_block(
    entry: dict[str, Any],
    open_tasks: list[dict[str, Any]],
    as_of: str,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> str:
    """Render the marker-delimited computed block for one project's dashboard."""
    lines: list[str] = [PULSE_START, f"## 📊 Pulse — auto-updated {as_of}", ""]

    momentum = _momentum_label(entry, stale_after_days)
    last = entry["last_activity"]
    idle = entry["days_since_activity"]
    if last:
        lines.append(f"- **Momentum:** {momentum} — last activity {last} ({idle}d ago)")
    else:
        lines.append(f"- **Momentum:** {momentum}")

    ov = entry["tasks_overdue"]
    doing = entry["tasks_doing"]
    workload = f"- **Open tasks:** {entry['tasks_open']}"
    extra = []
    if ov:
        extra.append(f"{ov} overdue")
    if doing:
        extra.append(f"{doing} in progress")
    if entry["tasks_blocked"]:
        extra.append(f"{entry['tasks_blocked']} blocked")
    if extra:
        workload += f" ({', '.join(extra)})"
    lines.append(workload)

    if entry["next_due"]:
        lines.append(f"- **Next deadline:** {entry['next_due']} — {entry['next_due_title']}")
    if entry["in_progress"]:
        lines.append(f"- **In progress:** {entry['in_progress']}")

    if open_tasks:
        lines += ["", "**Open tasks**"]
        for t in open_tasks:
            if t.get("overdue"):
                mark = "⏰"
            elif str(t.get("status") or "").lower() == "doing":
                mark = "▶"
            elif str(t.get("status") or "").lower() == "blocked":
                mark = "⛔"
            else:
                mark = "☐"
            due = f" (due {t['due']})" if t.get("due") else ""
            lines.append(f"- {mark} {t.get('title') or t['path']}{due}")

    lines += [
        "",
        "_Auto-generated by `cairn refresh-dashboard`; edit above or below the "
        "markers freely — this block is regenerated._",
        PULSE_END,
    ]
    return "\n".join(lines)


def refresh_dashboard(
    ws: Workspace,
    project: str,
    projects_dir: str = "projects",
    today: str | None = None,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """Write the computed pulse block into ``<projects_dir>/<project>.md``.

    On first run the block is inserted just after the dashboard's ``# H1`` title
    (or prepended if there is none); thereafter it is replaced in place between its
    markers. Hand-written content is never touched. Does **not** create a
    dashboard: if the file is missing, returns ``{updated: False, reason: ...}`` so
    a caller can skip or create it deliberately.
    """
    rel = f"{projects_dir.rstrip('/')}/{project}.md"
    path = ws.resolve(rel)
    if not path.is_file():
        return {"project": project, "path": rel, "updated": False,
                "reason": f"no dashboard file at {rel}"}

    pulse = project_pulse(ws, today=today, stale_after_days=stale_after_days)
    entry = next(
        (p for p in pulse["projects"] if p["project"].lower() == project.lower()),
        {  # a project with a dashboard but no notes yet
            "project": project, "notes_total": 0, "tasks_open": 0,
            "tasks_overdue": 0, "tasks_doing": 0, "tasks_blocked": 0,
            "next_due": None, "next_due_title": None, "in_progress": None,
            "last_activity": None, "days_since_activity": None,
            "has_overdue": False, "stale": False,
        },
    )
    open_tasks = tasks.list_tasks(ws, project=project, today=today)
    block = render_pulse_block(entry, open_tasks, pulse["as_of"], stale_after_days)

    text = path.read_text(encoding="utf-8")
    if PULSE_START in text and PULSE_END in text:
        pattern = re.compile(
            re.escape(PULSE_START) + r".*?" + re.escape(PULSE_END), re.DOTALL
        )
        new_text = pattern.sub(lambda _m: block, text, count=1)
        inserted = False
    else:
        new_text = _insert_after_h1(text, block)
        inserted = True

    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return {
        "project": project,
        "path": rel,
        "updated": new_text != text,
        "inserted": inserted,
        "block": block,
    }


def refresh_all_dashboards(
    ws: Workspace,
    projects_dir: str = "projects",
    today: str | None = None,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """Refresh every ``<projects_dir>/*.md`` that maps to a real, tracked project.

    A dashboard is refreshed only when the workspace actually has notes carrying
    its ``project:`` value — so meta files like ``research-portfolio.md`` (a map
    with no notes of its own) are skipped rather than stamped with an empty pulse.
    """
    root = ws.resolve(projects_dir)
    if not root.is_dir():
        return {"projects_dir": projects_dir, "refreshed": []}

    tracked = {
        p["project"].lower()
        for p in project_pulse(ws, today=today, stale_after_days=stale_after_days)["projects"]
    }
    results = []
    for p in sorted(root.glob("*.md")):
        if p.stem.lower() not in tracked:
            results.append({"project": p.stem, "path": f"{projects_dir}/{p.name}",
                            "updated": False, "reason": "not a tracked project (no notes)"})
            continue
        results.append(refresh_dashboard(
            ws, p.stem, projects_dir=projects_dir,
            today=today, stale_after_days=stale_after_days,
        ))
    return {"projects_dir": projects_dir, "refreshed": results}


def _insert_after_h1(text: str, block: str) -> str:
    """Insert ``block`` (with padding) right after the first ``# H1`` line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            head, tail = lines[: i + 1], lines[i + 1:]
            return "\n".join(head + ["", block, ""] + tail).rstrip() + "\n"
    # No H1 — prepend.
    return block + "\n\n" + text
