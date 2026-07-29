"""Weekly review: closed-this-week window, week-ahead, stalled, and focus."""

from __future__ import annotations

import pytest

from cairn_core import review, tasks
from cairn_core.workspace import Workspace

TODAY = "2026-07-28"


def _note(ws, relpath, **fields):
    p = ws.root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: [{', '.join(map(str, v))}]" if isinstance(v, list) else f"{k}: {v}")
    lines += ["---", "", "body"]
    p.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture()
def ws(tmp_path):
    w = Workspace(tmp_path)
    # closed inside the window (2 days ago)
    _note(w, "tasks/won.md", category="task", status="done",
          project="amazon", completed="2026-07-26", status_changed="2026-07-26")
    # closed OUTSIDE the window (20 days ago) — must not appear
    _note(w, "tasks/old.md", category="task", status="done",
          project="amazon", completed="2026-07-08", status_changed="2026-07-08")
    # a paper read this week counts as an accomplishment too
    _note(w, "research/papers/2026-07-25-seen.md", category="paper", status="read",
          project="gnn-agop", status_changed="2026-07-25")
    # open task due in the week ahead
    _note(w, "tasks/soon.md", category="task", status="todo",
          project="amazon", due="2026-08-01", status_changed="2026-07-27")
    # open task due far out — not "next week"
    _note(w, "tasks/later.md", category="task", status="todo",
          project="amazon", due="2026-09-01", status_changed="2026-07-27")
    return w


def test_closed_this_week_respects_the_window(ws):
    r = review.weekly_review(ws, today=TODAY)
    slugs = {c["path"].split("/")[-1] for c in r["closed"]}
    assert "won.md" in slugs and "old.md" not in slugs      # 20-day-old excluded
    assert "2026-07-25-seen.md" in slugs                    # cross-kind: paper read
    assert r["closed_count"] == 2
    assert r["closed"][0]["closed_on"] >= r["closed"][-1]["closed_on"]  # newest first


def test_due_next_week_window(ws):
    r = review.weekly_review(ws, today=TODAY, horizon_days=7)
    titles = {it["title"] for it in r["due_next_week"]}
    due_paths = {it["path"] for it in r["due_next_week"]}
    assert "tasks/soon.md" in due_paths        # due 08-01, within 7d
    assert "tasks/later.md" not in due_paths   # due 09-01, beyond horizon


def test_focus_is_actionable_next_actions(ws):
    r = review.weekly_review(ws, today=TODAY, focus_limit=3)
    focus_paths = {t["path"] for t in r["focus"]}
    assert "tasks/soon.md" in focus_paths      # open + unblocked
    assert "tasks/won.md" not in focus_paths   # done tasks aren't focus
    assert len(r["focus"]) <= 3


def test_stalled_reflects_project_pulse(ws):
    # gnn-agop's only activity is the 07-25 paper (3d) -> not stale at threshold 10;
    # push it far back so it flags, and confirm it surfaces.
    _note(ws, "research/ideas/2026-06-01-stale-idea.md", category="research-idea",
          project="dormant-proj", date="2026-06-01")
    r = review.weekly_review(ws, today=TODAY)
    assert "dormant-proj" in r["stalled"]


def test_shape_is_stable_and_composable(ws):
    r = review.weekly_review(ws, today=TODAY)
    assert set(r) == {
        "as_of", "since", "horizon", "closed", "closed_count",
        "projects", "stalled", "due_next_week", "focus",
    }
    assert r["as_of"] == TODAY and r["since"] == "2026-07-21" and r["horizon"] == "2026-08-04"
