"""Project pulse: per-project aggregation, staleness, and dashboard refresh."""

from __future__ import annotations

import pytest

from cairn_core import pulse
from cairn_core.workspace import Workspace

TODAY = "2026-07-28"


def _note(ws: Workspace, relpath: str, **fields) -> None:
    """Write a frontmatter note at ``relpath`` with the given fields."""
    p = ws.root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines += ["---", "", f"body of {relpath}"]
    p.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture()
def ws(tmp_path):
    w = Workspace(tmp_path)
    # gnn-agop: 1 overdue task, 1 doing, activity 20 days ago -> stale
    _note(w, "tasks/a.md", category="task", status="todo", due="2026-07-20",
          project="gnn-agop", created="2026-07-08", status_changed="2026-07-08")
    _note(w, "tasks/b.md", category="task", status="doing", due="2026-08-05",
          project="gnn-agop", created="2026-07-08", status_changed="2026-07-08")
    _note(w, "research/papers/p1.md", category="paper", status="to-read",
          project="gnn-agop", date="2026-07-08")
    # amazon: 1 open task (future due), fresh activity yesterday -> not stale
    _note(w, "tasks/c.md", category="task", status="todo", due="2026-08-10",
          project="amazon", created="2026-07-27", status_changed="2026-07-27")
    # a done task must NOT count toward open workload
    _note(w, "tasks/d.md", category="task", status="done", due="2026-07-01",
          project="amazon", completed="2026-07-27", status_changed="2026-07-27")
    return w


def test_per_project_task_aggregation(ws):
    by = {p["project"]: p for p in pulse.project_pulse(ws, today=TODAY)["projects"]}
    g = by["gnn-agop"]
    assert (g["tasks_open"], g["tasks_overdue"], g["tasks_doing"]) == (2, 1, 1)
    assert g["in_progress"]  # the doing task's title
    assert g["next_due"] == "2026-07-20"       # earliest due among open tasks
    assert g["has_overdue"] is True

    a = by["amazon"]
    assert a["tasks_open"] == 1                 # the done task is excluded
    assert a["tasks_overdue"] == 0
    assert a["next_due"] == "2026-08-10"


def test_staleness_from_activity_dates(ws):
    by = {p["project"]: p for p in pulse.project_pulse(ws, today=TODAY, stale_after_days=10)["projects"]}
    assert by["gnn-agop"]["days_since_activity"] == 20 and by["gnn-agop"]["stale"] is True
    assert by["amazon"]["days_since_activity"] == 1 and by["amazon"]["stale"] is False


def test_sorted_longest_idle_first(ws):
    order = [p["project"] for p in pulse.project_pulse(ws, today=TODAY)["projects"]]
    assert order.index("gnn-agop") < order.index("amazon")


def test_notes_without_project_are_ignored(ws):
    _note(ws, "misc/loose.md", category="misc", status="draft")  # no project:
    projects = {p["project"] for p in pulse.project_pulse(ws, today=TODAY)["projects"]}
    assert projects == {"gnn-agop", "amazon"}


def test_filename_date_counts_as_activity(ws):
    # Auto-filed paper: date is ONLY in the filename, no frontmatter date field.
    _note(ws, "research/papers/2026-07-27-fresh-paper.md",
          category="paper", status="to-read", project="gnn-agop")
    g = {p["project"]: p for p in pulse.project_pulse(ws, today=TODAY)["projects"]}["gnn-agop"]
    assert g["days_since_activity"] == 1 and g["stale"] is False  # was 20d/stale before


def test_stale_projects_filter(ws):
    stale = pulse.stale_projects(ws, threshold_days=10, today=TODAY)
    assert [s["project"] for s in stale] == ["gnn-agop"]
    assert stale[0]["tasks_overdue"] == 1


def test_refresh_inserts_then_replaces_and_preserves_prose(ws):
    dash = ws.root / "projects" / "gnn-agop.md"
    dash.parent.mkdir(parents=True, exist_ok=True)
    dash.write_text(
        "# GNN-AGOP — Project Context\n\n> hand-written thesis\n\n## Open Questions\n- q1\n",
        encoding="utf-8",
    )

    r1 = pulse.refresh_dashboard(ws, "gnn-agop", today=TODAY)
    assert r1["updated"] and r1["inserted"]
    text = dash.read_text(encoding="utf-8")
    assert pulse.PULSE_START in text and pulse.PULSE_END in text
    assert "hand-written thesis" in text and "## Open Questions" in text
    assert text.startswith("# GNN-AGOP")          # H1 stays first
    assert "1 overdue" in text                     # computed workload rendered

    # Second run replaces in place (no second block), prose still intact.
    r2 = pulse.refresh_dashboard(ws, "gnn-agop", today=TODAY)
    assert r2["inserted"] is False
    text2 = dash.read_text(encoding="utf-8")
    assert text2.count(pulse.PULSE_START) == 1
    assert "hand-written thesis" in text2
    # Same day, same data -> identical block -> nothing rewritten.
    assert r2["updated"] is False


def test_refresh_all_skips_untracked_meta_dashboards(ws):
    pdir = ws.root / "projects"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "gnn-agop.md").write_text("# GNN-AGOP\n\nprose\n", encoding="utf-8")
    (pdir / "research-portfolio.md").write_text("# Portfolio map\n\nprose\n", encoding="utf-8")

    out = pulse.refresh_all_dashboards(ws, today=TODAY)
    by = {r["project"]: r for r in out["refreshed"]}
    assert by["gnn-agop"]["updated"] is True                    # real project → written
    assert by["research-portfolio"]["updated"] is False         # meta map → skipped
    assert "not a tracked project" in by["research-portfolio"]["reason"]
    assert pulse.PULSE_START not in (pdir / "research-portfolio.md").read_text(encoding="utf-8")


def test_refresh_missing_dashboard_is_reported_not_created(ws):
    r = pulse.refresh_dashboard(ws, "amazon", today=TODAY)
    assert r["updated"] is False and "no dashboard file" in r["reason"]
    assert not (ws.root / "projects" / "amazon.md").exists()  # never created
