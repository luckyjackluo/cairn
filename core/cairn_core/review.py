"""Weekly review — the synthesis layer for a manager's weekly ritual.

The daily digest (:func:`cairn_core.lifecycle.reminder_digest`) answers "what needs
me today?". The weekly review answers the bigger questions you only ask once a
week: what did I finish, which projects are moving or stalled, what's due in the
week ahead, and what should I focus on. It is pure composition over primitives that
already exist —

* **closed this week** — a windowed sweep of every *closed* lifecycle item (tasks
  done, papers read, bills settled) by its ``completed`` / ``status_changed`` date;
* **projects** and **stalled** — :func:`cairn_core.pulse.project_pulse`;
* **due next week** — :func:`cairn_core.lifecycle.attention` over the coming days;
* **focus** — the top of :func:`cairn_core.tasks.next_actions`.

so the engine assembles the numbers deterministically and the agent only narrates
them into a Telegram brief.
"""

from __future__ import annotations

import datetime
from typing import Any

from . import lifecycle, pulse, query, tasks
from .workspace import Workspace

DEFAULT_WINDOW_DAYS = 7      # lookback for "closed this week"
DEFAULT_HORIZON_DAYS = 7     # lookahead for "due next week"
DEFAULT_FOCUS_LIMIT = 5      # how many next-actions to spotlight


def _iter_meta(ws: Workspace, path: str = ""):
    """Yield ``(relpath, meta)`` for every metadata-bearing file (skips dotfiles)."""
    root = ws.resolve(path)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        meta = query.doc_meta(p)
        if meta is not None:
            yield ws.relpath(p), meta


def _closed_since(
    ws: Workspace, since: datetime.date, ref: datetime.date, path: str = "",
) -> list[dict[str, Any]]:
    """Every lifecycle item closed in ``(since, ref]``, newest first.

    Cross-kind: a task marked done, a paper marked read, a bill settled all count.
    The close date is the item's ``completed`` stamp, else the ``status_changed``
    stamp that :func:`cairn_core.lifecycle.write_fields` writes on any transition.
    """
    out: list[dict[str, Any]] = []
    for rel, meta in _iter_meta(ws, path):
        spec = lifecycle.kind_of(meta)
        if spec is None or spec.state(meta.get("status")) != "closed":
            continue
        closed_on = tasks._parse_date(meta.get("completed")) or tasks._parse_date(meta.get("status_changed"))
        if closed_on is None or not (since < closed_on <= ref):
            continue
        out.append({
            "path": rel,
            "kind": spec.category,
            "title": meta.get("title") or rel,
            "project": meta.get("project") or None,
            "closed_on": closed_on.isoformat(),
        })
    out.sort(key=lambda r: r["closed_on"], reverse=True)
    return out


def weekly_review(
    ws: Workspace,
    today: str | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    focus_limit: int = DEFAULT_FOCUS_LIMIT,
    path: str = "",
) -> dict[str, Any]:
    """Assemble the weekly review — accomplishments, health, week ahead, focus.

    Returns ``{as_of, since, horizon, closed, closed_count, projects, stalled,
    due_next_week, focus}``:

    * ``closed`` / ``closed_count`` — lifecycle items closed in the last
      ``window_days`` (newest first), the week's accomplishments across all kinds;
    * ``projects`` — a compact per-project pulse (open/overdue tasks, idle days,
      stale flag); ``stalled`` — the names of the projects flagged stale;
    * ``due_next_week`` — dated items needing attention within ``horizon_days``
      (overdue carry-over + due-today + upcoming), most urgent first;
    * ``focus`` — the top ``focus_limit`` next-actions to lead the week with.
    """
    ref = tasks._parse_date(today) or datetime.date.today()
    ref_iso = ref.isoformat()
    since = ref - datetime.timedelta(days=max(1, window_days))
    horizon_days = max(1, horizon_days)
    horizon = ref + datetime.timedelta(days=horizon_days)

    closed = _closed_since(ws, since, ref, path)

    pulse_res = pulse.project_pulse(ws, today=ref_iso, path=path)
    projects = [
        {
            "project": p["project"],
            "tasks_open": p["tasks_open"],
            "tasks_overdue": p["tasks_overdue"],
            "days_since_activity": p["days_since_activity"],
            "stale": p["stale"],
            "has_overdue": p["has_overdue"],
        }
        for p in pulse_res["projects"]
    ]
    stalled = [p["project"] for p in pulse_res["projects"] if p["stale"]]

    att = lifecycle.attention(ws, None, ref_iso, horizon_days, path)
    due_next_week = (
        att["buckets"]["overdue"] + att["buckets"]["due_today"] + att["buckets"]["upcoming"]
    )

    focus = tasks.next_actions(ws, path=path, today=ref_iso, limit=focus_limit)

    return {
        "as_of": ref_iso,
        "since": since.isoformat(),
        "horizon": horizon.isoformat(),
        "closed": closed,
        "closed_count": len(closed),
        "projects": projects,
        "stalled": stalled,
        "due_next_week": due_next_week,
        "focus": focus,
    }
