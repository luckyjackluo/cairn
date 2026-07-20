"""Cairn MCP server — exposes the file-organization toolset over MCP.

This is the flagship, UI-less face of the product: point Claude Code, Codex,
Cursor, or any MCP client at a folder and it gains file-organization tools.
The client supplies the agent loop, the approval prompts (driven by the tool
annotations below), and the chat — so there is nothing of ours to run remotely
and no UI required.

Run:
    cairn-mcp-server --workspace ~/Documents/notes
    # or set CAIRN_WORKSPACE and run `cairn-mcp-server`
"""

from __future__ import annotations

import argparse
import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from cairn_core import (
    FileError,
    FileService,
    Workspace,
    WorkspaceError,
    bills,
    digest,
    lifecycle,
    query,
    reco,
    retrieval,
    tags,
    tasks,
    templates,
)

# --- workspace bootstrap ---------------------------------------------------

_root = os.environ.get("CAIRN_WORKSPACE") or os.getcwd()
WS = Workspace(_root)
FS = FileService(WS)

mcp = FastMCP("cairn")


def _guard(fn, *args, **kwargs):
    """Run a core call, translating core errors into clean MCP tool errors."""
    try:
        return fn(*args, **kwargs)
    except (FileError, WorkspaceError) as exc:
        raise ToolError(str(exc)) from exc


# Annotation presets. readOnly tools run freely (and in parallel) in clients
# like Claude Code; destructive tools trigger a confirmation prompt.
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


# --- read-only tools -------------------------------------------------------

@mcp.tool(annotations=READ)
def list_dir(path: str = "") -> list[dict]:
    """List files and folders directly under a workspace-relative directory."""
    return _guard(FS.list_dir, path)


@mcp.tool(annotations=READ)
def get_file_tree(path: str = "", max_depth: int = 12) -> list[dict]:
    """Return the nested file/folder tree under a directory (default: whole workspace)."""
    return _guard(FS.get_tree, path, max_depth)


@mcp.tool(annotations=READ)
def read_detail(path: str) -> dict:
    """Read a file. For .uni docs returns HTML content, flattened text, and tags;
    for text/.md files returns raw text plus any YAML frontmatter tags & metadata."""
    return _guard(FS.read_detail, path)


@mcp.tool(annotations=READ)
def search_files(query: str, path: str = "", limit: int = 100) -> list[dict]:
    """Find files/folders whose name contains `query` (case-insensitive)."""
    return _guard(FS.search_files, query, path, limit)


@mcp.tool(annotations=READ)
def grep(pattern: str, path: str = "", limit: int = 200) -> list[dict]:
    """Search file contents for `pattern`; returns {path, line, text} matches."""
    return _guard(FS.grep, pattern, path, limit)


@mcp.tool(annotations=READ)
def get_file_tags(path: str) -> list[str]:
    """Return a document's tags — from a .uni's tags array or a text file's YAML frontmatter."""
    return _guard(tags.get_tags, WS, path)


@mcp.tool(annotations=READ)
def get_tag_tree() -> dict:
    """Return {tag: [file paths]} aggregated across the workspace."""
    return _guard(tags.get_tag_tree, WS)


@mcp.tool(annotations=READ)
def find_by_meta(filters: dict, path: str = "") -> list[dict]:
    """Find documents whose metadata matches all `filters` (exact, not fuzzy).

    Reads .uni JSON fields and .md YAML frontmatter alike. Scalar fields match
    case-insensitively; `tags` (or any list field) matches when every requested
    value is present. Example: {"status": "to-read", "project": "amazon"}.
    """
    return _guard(query.find_by_meta, WS, filters, path)


@mcp.tool(annotations=READ)
def list_tasks(
    status: str = "",
    project: str = "",
    context: str = "",
    due_before: str = "",
    path: str = "",
) -> list[dict]:
    """List tasks (documents with `category: task`), sorted by due date.

    A task is an ordinary frontmatter note with a lifecycle; this is the task
    view over it. `status` defaults to the open set (todo/doing/blocked) — pass
    "all" to include done, or a specific status / comma-separated list.
    `project` and `context` (e.g. "@errand") are exact case-insensitive filters;
    `due_before` (YYYY-MM-DD) keeps only tasks due before that date. Each result
    carries derived `overdue` and `today` flags relative to the current date.
    """
    return _guard(
        tasks.list_tasks,
        WS,
        status or None,
        project or None,
        context or None,
        due_before or None,
        path,
    )


@mcp.tool(annotations=READ)
def digest_workspace(path: str = "", group_by: str = "folder") -> dict:
    """Return a token-efficient map of the workspace: one entry (title, date,
    tags, summary) per document, grouped by `folder`, `tag`, or a metadata field
    like `status`/`project`. Read this before loading individual files."""
    return _guard(digest.build_digest, WS, path, group_by)


@mcp.tool(annotations=READ)
def list_templates() -> list[str]:
    """List available document templates (built-ins + workspace .cairn/templates)."""
    return _guard(templates.available, WS)


@mcp.tool(annotations=READ)
def semantic_retrieve(query: str, k: int = 5) -> list[dict]:
    """Return the documents most relevant to `query`.

    Uses embeddings when an embedding endpoint is configured (env), else
    lexical scoring.
    """
    return _guard(retrieval.semantic_retrieve, WS, query, k)


@mcp.tool(annotations=READ)
def list_paper_projects() -> list[dict]:
    """List projects configured for paper recommendations (.cairn/paper_reco.json)."""
    return _guard(reco.list_projects, WS)


@mcp.tool(annotations=READ)
def preview_paper_recommendations(project: str, count: int = 3) -> dict:
    """Preview the top unseen recommended papers for a project WITHOUT saving.

    Ranks the project's candidate pool by relevance × citation impact and skips
    anything already recommended or already filed. Use this to inspect the queue
    or check a project's topic queries before committing notes.
    """
    return _guard(reco.preview, WS, project, count)


# --- additive / modifying tools -------------------------------------------

@mcp.tool(annotations=WRITE)
def create_file(
    path: str, name: str, content: str = "", template: str = "", fields: dict | None = None
) -> dict:
    """Create a file under `path`. If `name` ends in .uni, wraps content as a .uni doc.

    Pass `template` (e.g. "paper", "note" — see list_templates) with `fields` to
    stamp out a document with frontmatter pre-filled; `content` is then ignored.
    """
    return _guard(FS.create_file, path, name, content, template or None, fields)


@mcp.tool(annotations=WRITE)
def create_folder(path: str, name: str) -> dict:
    """Create a new folder `name` under `path`."""
    return _guard(FS.create_folder, path, name)


@mcp.tool(annotations=WRITE)
def add_task(
    title: str,
    due: str = "",
    project: str = "",
    context: str = "",
    notes: str = "",
    status: str = "todo",
    dir: str = "tasks",
) -> dict:
    """Create a task (a `category: task` note) and return its record.

    Files into `dir` (default `tasks/`) with a slug from `title`. `due` is
    YYYY-MM-DD; `context` is a tag like "@errand"; `status` is
    todo/doing/blocked/done. Colliding names get a numeric suffix, never clobber.
    """
    return _guard(
        tasks.add_task, WS, title, due or None, project or None,
        context or None, notes, status, None, dir,
    )


@mcp.tool(annotations=WRITE)
def complete_task(path: str, when: str = "") -> dict:
    """Mark a task done, stamping a `completed` date (defaults to today).

    Surgical: only `status` and `completed` are rewritten; the rest of the file
    is preserved byte-for-byte.
    """
    return _guard(tasks.complete_task, WS, path, when or None)


@mcp.tool(annotations=WRITE)
def update_task(
    path: str,
    status: str = "",
    due: str = "",
    project: str = "",
    context: str = "",
) -> dict:
    """Update one or more fields of a task in place (surgical frontmatter edit).

    Pass only the fields to change. `status` must be todo/doing/blocked/done;
    `due` is YYYY-MM-DD; `context` is a tag like "@deep".
    """
    return _guard(
        tasks.update_task, WS, path, status or None, due or None,
        project or None, context or None,
    )


@mcp.tool(annotations=WRITE)
def harvest_checklists(path: str = "", dir: str = "tasks", link_back: bool = True) -> dict:
    """Promote `- [ ]` checkbox lines in notes into canonical task files.

    Scans text under `path` (a file or folder; default whole workspace), turning
    each unchecked checkbox into a task and parsing inline `due:YYYY-MM-DD`,
    `+project` and `@context` tokens. With `link_back` (default), each harvested
    line is annotated so re-running never double-harvests. Returns {created, count}.
    """
    return _guard(tasks.harvest_checklists, WS, path, dir, link_back)


# --- shared bills ----------------------------------------------------------

@mcp.tool(annotations=READ)
def who_owes(path: str = "") -> dict:
    """Summarize who still owes the user money, across all open shared bills.

    This is the reminder view: one row per person — {name, owes, bills,
    oldest_days} — sorted so whoever has been owing longest comes first, plus a
    grand `total` and `bill_count`. Returns an empty `people` list when
    everything is settled, which is the signal to stay quiet rather than send an
    empty reminder.
    """
    return _guard(bills.who_owes, WS, path)


@mcp.tool(annotations=READ)
def list_bills(status: str = "", person: str = "", path: str = "") -> list[dict]:
    """List shared bills (documents with `category: bill`), oldest first.

    `status` defaults to "open" (someone is still unpaid); pass "settled" or
    "all". `person` filters to bills naming that person. Each result carries the
    per-person breakdown plus a derived `outstanding` balance and `age_days`.
    Use this to find a specific bill; use `who_owes` for the reminder summary.
    """
    return _guard(bills.list_bills, WS, status or None, person or None, path)


@mcp.tool(annotations=READ)
def attention(kinds: list[str] | None = None, upcoming_days: int = 3, path: str = "") -> dict:
    """Everything across the workspace that is waiting on the user right now.

    The one reminder primitive spanning every lifecycle kind — tasks, bills,
    papers, and any future kind — not one query per kind. Sweeps all OPEN items
    and returns those that are `overdue`, `due_today`, `upcoming` (within
    `upcoming_days`), or `stale` (open past their kind's staleness threshold
    without a status change). `kinds` restricts to specific categories (e.g.
    ["task","bill"]); omit for all.

    Returns {as_of, count, buckets, items}: `buckets` groups items by urgency
    (most pressing first), each item carrying its `kind`, `due`, `age_days`, and
    the `reasons` it was flagged. A `count` of 0 means nothing needs the user —
    the signal to stay quiet rather than send an empty reminder.
    """
    return _guard(lifecycle.attention, WS, kinds, None, upcoming_days, path)


@mcp.tool(annotations=WRITE)
def reminder_digest(upcoming_days: int = 3, path: str = "", preview: bool = False) -> dict:
    """The daily reminder to send: only what is DUE to be re-surfaced today.

    Like `attention`, but cadence-filtered — an item reappears only when its own
    reminder interval has elapsed since it was last sent (a per-note `remind_every`
    frontmatter field, else the kind default of daily), with `overdue`/`due_today`
    items escalated to daily whatever their cadence. This is the tool a reminder
    cron calls; it records what it sent to a workspace ledger so the next run's
    cadence is right (a WRITE, hence the annotation). Pass preview=true to see the
    due set WITHOUT recording (leaves cadence untouched).

    Returns {as_of, count, buckets, items}. A `count` of 0 means send nothing —
    the signal to stay quiet rather than deliver an empty reminder.
    """
    return _guard(lifecycle.reminder_digest, WS, None, upcoming_days, path, not preview)


@mcp.tool(annotations=WRITE)
def set_status(path: str, status: str) -> dict:
    """Set a note's lifecycle status, stamping when it changed.

    The kind-agnostic way to move any lifecycle note between states — mark a
    paper `read`, a task `doing`, reopen a bill. Validates `status` against the
    note's kind (rejecting a value the kind doesn't define) and records
    `status_changed` so staleness is measured from this moment. For tasks and
    bills the kind-specific tools (complete_task, settle_bill) carry extra domain
    logic; prefer those when they fit, and this for everything else.

    Returns {path, kind, status, state, status_changed}.
    """
    return _guard(lifecycle.stamp_status, WS, path, status)


@mcp.tool(annotations=WRITE)
def add_bill(
    place: str,
    total: str,
    people: list[str],
    date: str = "",
    shares: dict | None = None,
    include_self: bool = True,
    currency: str = "USD",
    notes: str = "",
    dir: str = "personal/bills",
) -> dict:
    """Record a bill the user paid and is owed for, splitting it across `people`.

    `people` are the OTHERS on the bill — never the user. By default `total`
    splits evenly across them plus the user; pass include_self=false when the
    user was only fronting the money and owes no share. `shares` pins exact
    amounts for specific people ({"alex": "52.30"}) and the rest split what
    remains; rounding remainders fall on the user, never on a guest. `date` is
    YYYY-MM-DD (default today).
    """
    return _guard(
        bills.add_bill, WS, place, total, people, date or None, shares,
        include_self, currency, notes, dir,
    )


@mcp.tool(annotations=WRITE)
def settle_bill(person: str, path: str = "", state: str = "paid", when: str = "") -> dict:
    """Stop tracking what `person` owes — because they paid, or the user waived it.

    With `path` omitted this settles that person across EVERY open bill, which
    is what "Alex paid me back" usually means; pass `path` to settle just one.
    `state` is "paid" when the money actually arrived and "waived" when the user
    has decided to stop chasing it — both end the reminders, but only "paid"
    claims repayment, so do not substitute one for the other. A bill closes
    itself once nobody on it is unpaid.
    """
    return _guard(bills.settle, WS, person, path or None, state, when or None)


@mcp.tool(annotations=WRITE)
def add_person_to_bill(path: str, person: str, owes: str) -> dict:
    """Add someone to an existing bill for an explicit amount.

    Does not re-split the bill: the others have already been told what they owe.
    """
    return _guard(bills.add_person, WS, path, person, owes)


@mcp.tool(annotations=WRITE)
def update_file_tags(path: str, tags_list: list[str]) -> dict:
    """Replace a document's tags — in a .uni's tags array or a text file's YAML frontmatter."""
    return _guard(tags.set_tags, WS, path, tags_list)


@mcp.tool(annotations=WRITE)
def import_document(path: str, dest_dir: str = "") -> dict:
    """Convert a docx/pdf/pptx/csv/xlsx/md/text file into an editable .uni doc.

    The original file is kept. Returns the new .uni item.
    """
    return _guard(FS.import_file, path, dest_dir or None)


@mcp.tool(annotations=WRITE)
def import_folder(path: str = "") -> dict:
    """Import every convertible (non-.uni) file under a folder into .uni docs.

    Returns a summary with counts and per-file results (including skips).
    """
    return _guard(FS.import_tree, path)


@mcp.tool(annotations=WRITE)
def reindex() -> dict:
    """Build/refresh the embedding index (no-op if no embedder is configured)."""
    return _guard(retrieval.reindex, WS)


@mcp.tool(annotations=WRITE)
def recommend_papers(project: str, count: int = 1) -> dict:
    """Recommend + FILE the top unseen paper(s) for one project as to-read notes.

    Picks the highest relevance×impact paper the project hasn't seen, writes it
    into the papers folder as a `paper` note (status: to-read), and records it so
    it never repeats. Returns the picks (title, why, citations, path). Run daily
    for a steady walk through a field's canon, one paper per project per day.
    """
    return _guard(reco.recommend, WS, project, count)


@mcp.tool(annotations=WRITE)
def recommend_papers_all_projects(count: int = 1) -> dict:
    """Recommend + file the top unseen paper(s) for EVERY configured project.

    The daily-driver: one call yields one new to-read paper note per project.
    """
    return _guard(reco.recommend_all, WS, count)


# --- destructive tools (client will confirm) ------------------------------

@mcp.tool(annotations=DESTRUCTIVE)
def multi_edit(path: str, old_string: str, new_string: str) -> dict:
    """Replace a single, unique occurrence of `old_string` with `new_string` in a file."""
    return _guard(FS.multi_edit, path, old_string, new_string)


@mcp.tool(annotations=DESTRUCTIVE)
def rename_item(path: str, new_name: str) -> dict:
    """Rename a file or folder in place (bare name, not a path)."""
    return _guard(FS.rename_item, path, new_name)


@mcp.tool(annotations=DESTRUCTIVE)
def move_item(path: str, target_dir: str) -> dict:
    """Move a file or folder into `target_dir`."""
    return _guard(FS.move_item, path, target_dir)


@mcp.tool(annotations=DESTRUCTIVE)
def delete_item(path: str) -> dict:
    """Delete a file or folder (recursive for folders)."""
    return _guard(FS.delete_item, path)


# --- resources -------------------------------------------------------------

@mcp.resource("workspace://tree")
def tree_resource() -> str:
    """The current workspace file tree, as JSON."""
    return json.dumps(FS.get_tree(), ensure_ascii=False, indent=2)


@mcp.resource("workspace://tags")
def tags_resource() -> str:
    """The current tag tree ({tag: [paths]}), as JSON."""
    return json.dumps(tags.get_tag_tree(WS), ensure_ascii=False, indent=2)


# --- prompts (surface as slash-commands in clients) ------------------------

@mcp.prompt()
def organize(path: str = "") -> str:
    """Ask the agent to organize a folder using the workspace tools."""
    where = f"the '{path}' folder" if path else "the workspace"
    return (
        f"Organize {where}. First call get_file_tree to understand the current "
        "layout, then propose a grouping and use move_item / create_folder to "
        "carry it out. Confirm destructive moves before applying them."
    )


@mcp.prompt()
def tag_all(path: str = "") -> str:
    """Ask the agent to tag every document under a folder."""
    where = f"'{path}'" if path else "the workspace"
    return (
        f"Read each .uni document under {where} (use get_file_tree then "
        "read_detail) and assign concise, consistent tags with update_file_tags."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Cairn MCP server")
    parser.add_argument(
        "--workspace",
        help="Workspace directory to operate on (default: $CAIRN_WORKSPACE or CWD)",
    )
    args = parser.parse_args()
    if args.workspace:
        global WS, FS
        WS = Workspace(args.workspace)
        FS = FileService(WS)
    mcp.run()


if __name__ == "__main__":
    main()
