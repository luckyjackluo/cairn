"""Task view: templating, filtering, due-date ordering, and derived flags."""

from __future__ import annotations

import pytest

from cairn_core import FileService, Workspace, tasks, templates


@pytest.fixture()
def fs(tmp_path):
    return FileService(Workspace(tmp_path))


def _task(fs, name, *, status="todo", due="", project="", context=""):
    text = templates.render(
        "task",
        {"title": name, "status": status, "due": due, "project": project, "context": context},
    )
    fs.create_file("", name, text)


def test_task_template_is_frontmatter_native(fs):
    _task(fs, "a.md", due="2026-07-22", project="neurips")
    # The task drops straight into the generic metadata query with no task code.
    from cairn_core import query

    hits = query.find_by_meta(fs.ws, {"category": "task", "project": "neurips"})
    assert [h["path"] for h in hits] == ["a.md"]


def test_default_status_filters_to_open_and_sorts_by_due(fs):
    _task(fs, "later.md", due="2026-08-01")
    _task(fs, "soon.md", due="2026-07-20")
    _task(fs, "undated.md")
    _task(fs, "done.md", status="done", due="2026-07-01")

    out = tasks.list_tasks(fs.ws, today="2026-07-19")
    # done is excluded by default; due ascending, undated last.
    assert [t["path"] for t in out] == ["soon.md", "later.md", "undated.md"]


def test_overdue_and_today_flags(fs):
    _task(fs, "past.md", due="2026-07-10")
    _task(fs, "now.md", due="2026-07-19")
    _task(fs, "future.md", due="2026-07-25")

    by_path = {t["path"]: t for t in tasks.list_tasks(fs.ws, today="2026-07-19")}
    assert by_path["past.md"]["overdue"] and not by_path["past.md"]["today"]
    assert by_path["now.md"]["today"] and not by_path["now.md"]["overdue"]
    assert not by_path["future.md"]["overdue"] and not by_path["future.md"]["today"]


def test_status_all_includes_done_and_never_overdue(fs):
    _task(fs, "done.md", status="done", due="2026-07-01")
    out = {t["path"]: t for t in tasks.list_tasks(fs.ws, status="all", today="2026-07-19")}
    assert "done.md" in out
    assert out["done.md"]["overdue"] is False  # done is never overdue


def test_project_context_and_due_before_filters(fs):
    _task(fs, "p1.md", due="2026-07-20", project="cairn", context="@deep")
    _task(fs, "p2.md", due="2026-07-30", project="cairn", context="@errand")
    _task(fs, "other.md", due="2026-07-20", project="openclaw")

    assert {t["path"] for t in tasks.list_tasks(fs.ws, project="cairn")} == {"p1.md", "p2.md"}
    assert [t["path"] for t in tasks.list_tasks(fs.ws, context="@errand")] == ["p2.md"]
    # due_before keeps both 07-20 tasks (p1, other) but drops p2 (07-30).
    assert {t["path"] for t in tasks.list_tasks(fs.ws, due_before="2026-07-25")} == {"p1.md", "other.md"}


def test_tag_marked_task_without_category(fs):
    fs.create_file("", "t.md", "---\ntitle: X\nstatus: todo\ntags: [task]\n---\nbody")
    assert [t["path"] for t in tasks.list_tasks(fs.ws)] == ["t.md"]


# --- write -----------------------------------------------------------------

def test_add_task_files_into_tasks_dir_and_is_listable(fs):
    rec = tasks.add_task(fs.ws, "Email advisor about defense", due="2026-07-25", project="thesis")
    assert rec["path"] == "tasks/email-advisor-about-defense.md"
    assert rec["status"] == "todo" and rec["project"] == "thesis"
    listed = tasks.list_tasks(fs.ws)
    assert [t["path"] for t in listed] == [rec["path"]]


def test_add_task_dedupes_slug_collisions(fs):
    a = tasks.add_task(fs.ws, "Review PR")
    b = tasks.add_task(fs.ws, "Review PR")
    assert a["path"] != b["path"]
    assert a["path"] == "tasks/review-pr.md"


def test_add_task_rejects_empty_title_and_bad_status(fs):
    with pytest.raises(Exception):
        tasks.add_task(fs.ws, "   ")
    with pytest.raises(Exception):
        tasks.add_task(fs.ws, "x", status="bogus")


def test_complete_task_is_surgical_and_drops_from_open_list(fs):
    rec = tasks.add_task(fs.ws, "Ship it", project="cairn", notes="body text here")
    done = tasks.complete_task(fs.ws, rec["path"], when="2026-07-19")
    assert done["status"] == "done"
    # Open-list default excludes it; body is preserved.
    assert tasks.list_tasks(fs.ws) == []
    content = (fs.ws.root / rec["path"]).read_text()
    assert "body text here" in content
    assert "completed: 2026-07-19" in content
    assert "project: cairn" in content  # untouched field survives


def test_update_task_changes_only_named_fields(fs):
    rec = tasks.add_task(fs.ws, "Draft section", project="thesis")
    tasks.update_task(fs.ws, rec["path"], status="doing", due="2026-08-01")
    listed = tasks.list_tasks(fs.ws)[0]
    assert listed["status"] == "doing" and listed["due"] == "2026-08-01"
    assert listed["project"] == "thesis"  # unchanged


def test_update_task_requires_a_field(fs):
    rec = tasks.add_task(fs.ws, "x")
    with pytest.raises(Exception):
        tasks.update_task(fs.ws, rec["path"])


# --- harvest ---------------------------------------------------------------

_NOTE_WITH_BOXES = """---
title: Standup notes
---
Discussion points here.

- [ ] Send the reimbursement form due:2026-07-22 +neurips @errand
- [x] Already did this one
- [ ] Refactor the parser +cairn
Not a checkbox line.
"""


def test_harvest_creates_tasks_and_parses_inline_meta(fs):
    fs.create_file("", "standup.md", _NOTE_WITH_BOXES)
    result = tasks.harvest_checklists(fs.ws)
    assert result["count"] == 2  # the [x] box is skipped

    by_title = {t["title"]: t for t in tasks.list_tasks(fs.ws)}
    reimb = by_title["Send the reimbursement form"]
    assert reimb["due"] == "2026-07-22"
    assert reimb["project"] == "neurips"
    assert reimb["context"] == "@errand"
    assert by_title["Refactor the parser"]["project"] == "cairn"


def test_harvest_is_idempotent(fs):
    fs.create_file("", "standup.md", _NOTE_WITH_BOXES)
    first = tasks.harvest_checklists(fs.ws)
    second = tasks.harvest_checklists(fs.ws)
    assert first["count"] == 2 and second["count"] == 0  # marker prevents re-harvest


def test_harvest_records_source_backlink(fs):
    fs.create_file("", "standup.md", _NOTE_WITH_BOXES)
    tasks.harvest_checklists(fs.ws)
    task_path = tasks.list_tasks(fs.ws)[0]["path"]
    assert "source: standup.md" in (fs.ws.root / task_path).read_text()
    # And the source note's line points back at the task.
    assert "<!--cairn:tasks/" in (fs.ws.root / "standup.md").read_text()
