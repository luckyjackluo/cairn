"""cairn — a thin CLI over cairn_core.

Two jobs:
  1. Scriptable one-shot file operations (ls, tree, read, grep, tag, ...).
  2. `serve` — expose the core over a small local HTTP API for the web UI,
     and `mcp` — launch the MCP server.

Dependency-free (standard library only), like the core it wraps.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from cairn_core import (
    FileError,
    FileService,
    Workspace,
    WorkspaceError,
    digest,
    query,
    retrieval,
    tags,
    tasks,
    templates,
)


def _ws(args) -> Workspace:
    root = args.workspace or os.environ.get("CAIRN_WORKSPACE") or os.getcwd()
    return Workspace(root)


def _emit(data: Any, args) -> None:
    """Print JSON when --json, otherwise a human-friendly rendering."""
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(_humanize(data))


def _humanize(data: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(data, list):
        if not data:
            return f"{pad}(empty)"
        return "\n".join(_row(item, indent) for item in data)
    if isinstance(data, dict) and "children" in data:
        return _row(data, indent)
    if isinstance(data, dict):
        return "\n".join(f"{pad}{k}: {v}" for k, v in data.items())
    return f"{pad}{data}"


def _row(item: Any, indent: int) -> str:
    pad = "  " * indent
    if not isinstance(item, dict):
        return f"{pad}{item}"
    if item.get("type") == "folder":
        name = f"{item.get('name', item.get('path',''))}/"
    else:
        name = item.get("name", item.get("path", ""))
    extra = []
    if item.get("tags"):
        extra.append("#" + " #".join(item["tags"]))
    if "line" in item:  # grep row
        return f"{pad}{item['path']}:{item['line']}: {item.get('text','')}"
    if "score" in item:  # retrieval row
        return f"{pad}{item['score']:>6}  {item['path']}  {('#'+' #'.join(item.get('tags',[]))) if item.get('tags') else ''}\n{pad}        {item.get('snippet','')}"
    line = f"{pad}{name}" + (f"   {' '.join(extra)}" if extra else "")
    if item.get("children"):
        child_lines = "\n".join(_row(c, indent + 1) for c in item["children"])
        return f"{line}\n{child_lines}"
    return line


# --- command handlers ------------------------------------------------------

def cmd_ls(args):
    _emit(FileService(_ws(args)).list_dir(args.path), args)


def cmd_tree(args):
    _emit(FileService(_ws(args)).get_tree(args.path), args)


def cmd_read(args):
    detail = FileService(_ws(args)).read_detail(args.path)
    if args.json:
        _emit(detail, args)
    else:
        print(detail.get("text") or detail.get("content") or f"[{detail.get('type')}] {detail.get('size','')} bytes")


def cmd_create(args):
    content = sys.stdin.read() if args.content == "-" else (args.content or "")
    parent, _, name = args.path.rpartition("/")
    fields: dict[str, Any] = {}
    for pair in args.field or []:
        key, sep, val = pair.partition("=")
        if not sep:
            sys.exit(f"error: --field must be key=value, got {pair!r}")
        fields[key.strip()] = val.strip()
    _emit(
        FileService(_ws(args)).create_file(parent, name, content, args.template or None, fields),
        args,
    )


def cmd_templates(args):
    _emit(templates.available(_ws(args)), args)


def cmd_mkdir(args):
    parent, _, name = args.path.rpartition("/")
    _emit(FileService(_ws(args)).create_folder(parent, name), args)


def cmd_mv(args):
    _emit(FileService(_ws(args)).move_item(args.path, args.target_dir), args)


def cmd_rename(args):
    _emit(FileService(_ws(args)).rename_item(args.path, args.new_name), args)


def cmd_rm(args):
    _emit(FileService(_ws(args)).delete_item(args.path), args)


def cmd_edit(args):
    _emit(FileService(_ws(args)).multi_edit(args.path, args.old, args.new), args)


def cmd_import(args):
    fs = FileService(_ws(args))
    if args.all:
        _emit(fs.import_tree(args.path, keep_original=not args.replace), args)
    else:
        _emit(fs.import_file(args.path, keep_original=not args.replace), args)


def cmd_grep(args):
    _emit(FileService(_ws(args)).grep(args.pattern, args.path), args)


def cmd_find(args):
    _emit(FileService(_ws(args)).search_files(args.query, args.path), args)


def cmd_tag(args):
    ws = _ws(args)
    if args.tags:
        _emit(tags.set_tags(ws, args.path, args.tags), args)
    else:
        _emit(tags.get_tags(ws, args.path), args)


def cmd_tags(args):
    _emit(tags.get_tag_tree(_ws(args)), args)


def cmd_query(args):
    filters: dict[str, Any] = {}
    for pair in args.filters:
        key, sep, val = pair.partition("=")
        if not sep:
            sys.exit(f"error: filter must be key=value, got {pair!r}")
        filters[key.strip()] = val.strip()
    _emit(query.find_by_meta(_ws(args), filters, args.path), args)


def cmd_digest(args):
    groups = digest.build_digest(_ws(args), args.path, args.group_by)
    if args.json:
        print(json.dumps(groups, ensure_ascii=False, indent=2))
        return
    if not groups:
        print("(empty)")
        return
    blocks = []
    for group, entries in groups.items():
        lines = [f"## {group}"]
        for e in entries:
            date = f"[{e['date']}] " if e.get("date") else ""
            tagstr = ("  " + " ".join("#" + t for t in e["tags"])) if e.get("tags") else ""
            summary = f" — {e['summary']}" if e.get("summary") else ""
            lines.append(f"- {date}**{e['title']}**{summary}{tagstr}")
        blocks.append("\n".join(lines))
    print("\n\n".join(blocks))


def cmd_retrieve(args):
    _emit(retrieval.semantic_retrieve(_ws(args), args.query, args.k), args)


def cmd_reindex(args):
    _emit(retrieval.reindex(_ws(args)), args)


def cmd_tasks(args):
    out = tasks.list_tasks(
        _ws(args),
        status=args.status or None,
        project=args.project or None,
        context=args.context or None,
        due_before=args.due_before or None,
        path=args.path,
    )
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if not out:
        print("(no tasks)")
        return
    for t in out:
        flag = "  ⚠ overdue" if t["overdue"] else ("  ● today" if t["today"] else "")
        meta = " ".join(x for x in [f"+{t['project']}" if t["project"] else "", t["context"] or ""] if x)
        print(f"[{(t['status'] or '?'):6}] {(t['due'] or '—'):>10}  {t['title']}"
              f"{('  ' + meta) if meta else ''}{flag}   ({t['path']})")


def cmd_task_add(args):
    _emit(tasks.add_task(
        _ws(args), args.title, due=args.due or None, project=args.project or None,
        context=args.context or None, notes=args.notes, status=args.status, dir=args.dir,
    ), args)


def cmd_task_done(args):
    _emit(tasks.complete_task(_ws(args), args.path, when=args.when or None), args)


def cmd_task_update(args):
    _emit(tasks.update_task(
        _ws(args), args.path, status=args.status or None, due=args.due or None,
        project=args.project or None, context=args.context or None,
    ), args)


def cmd_harvest(args):
    res = tasks.harvest_checklists(
        _ws(args), path=args.path, dir=args.dir, link_back=not args.no_link_back,
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    print(f"harvested {res['count']} task(s)")
    for t in res["created"]:
        print(f"  + {t['path']}  {t['title']}")


def cmd_serve(args):
    from .serve import serve
    serve(_ws(args), host=args.host, port=args.port, web_dir=args.web_dir)


def cmd_mcp(args):
    try:
        from cairn_mcp.server import main as mcp_main
    except ImportError:
        sys.exit("The MCP server isn't installed. Install it with: uv pip install cairn-mcp-server")
    if args.workspace:
        os.environ["CAIRN_WORKSPACE"] = str(_ws(args).root)
    sys.argv = ["cairn-mcp-server"]
    mcp_main()


# --- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cairn", description="Local-first file-organization harness CLI")
    p.add_argument("--workspace", "-w", help="Workspace directory (default: $CAIRN_WORKSPACE or CWD)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)
        return sp

    sp = add("ls", cmd_ls, "List a directory"); sp.add_argument("path", nargs="?", default="")
    sp = add("tree", cmd_tree, "Show the file tree"); sp.add_argument("path", nargs="?", default="")
    sp = add("read", cmd_read, "Print a file's contents"); sp.add_argument("path")
    sp = add("create", cmd_create, "Create a file ('-' content reads stdin)")
    sp.add_argument("path"); sp.add_argument("content", nargs="?", default="")
    sp.add_argument("--template", "-t", help="Stamp from a template (see `cairn templates`)")
    sp.add_argument("--field", action="append", metavar="KEY=VALUE", help="Template field (repeatable)")
    add("templates", cmd_templates, "List available document templates")
    sp = add("mkdir", cmd_mkdir, "Create a folder"); sp.add_argument("path")
    sp = add("mv", cmd_mv, "Move a file/folder into a directory")
    sp.add_argument("path"); sp.add_argument("target_dir")
    sp = add("rename", cmd_rename, "Rename a file/folder in place")
    sp.add_argument("path"); sp.add_argument("new_name")
    sp = add("rm", cmd_rm, "Delete a file/folder"); sp.add_argument("path")
    sp = add("edit", cmd_edit, "Replace a unique occurrence of OLD with NEW")
    sp.add_argument("path"); sp.add_argument("old"); sp.add_argument("new")
    sp = add("import", cmd_import, "Convert docx/pdf/pptx/csv/xlsx/md into .uni")
    sp.add_argument("path", nargs="?", default="")
    sp.add_argument("--all", action="store_true", help="Import every convertible file under path")
    sp.add_argument("--replace", action="store_true", help="Delete the source after import (default: keep)")
    sp = add("grep", cmd_grep, "Search file contents"); sp.add_argument("pattern"); sp.add_argument("path", nargs="?", default="")
    sp = add("find", cmd_find, "Search file names"); sp.add_argument("query"); sp.add_argument("path", nargs="?", default="")
    sp = add("tag", cmd_tag, "Get or set tags on a .uni file")
    sp.add_argument("path"); sp.add_argument("tags", nargs="*")
    add("tags", cmd_tags, "Show the workspace tag tree")
    sp = add("query", cmd_query, "Find docs by metadata, e.g. query status=to-read project=amazon")
    sp.add_argument("filters", nargs="+", help="key=value filters (matched on .uni fields or .md frontmatter)")
    sp.add_argument("--path", default="", help="Limit the search to this subdirectory")
    sp = add("digest", cmd_digest, "Generate a one-line-per-doc map of the workspace")
    sp.add_argument("path", nargs="?", default="")
    sp.add_argument("--group-by", default="folder", help="folder | tag | a metadata field (status, project, ...)")
    sp = add("retrieve", cmd_retrieve, "Find documents relevant to a query")
    sp.add_argument("query"); sp.add_argument("-k", type=int, default=5)
    add("reindex", cmd_reindex, "Build/refresh the embedding index (if configured)")
    sp = add("tasks", cmd_tasks, "List tasks (category: task notes), sorted by due date")
    sp.add_argument("path", nargs="?", default="", help="Limit to this subdirectory")
    sp.add_argument("--status", default="", help="Filter: status / comma-list / 'all' (default: open)")
    sp.add_argument("--project", default="", help="Filter by project")
    sp.add_argument("--context", default="", help="Filter by context, e.g. @errand")
    sp.add_argument("--due-before", dest="due_before", default="", help="Only tasks due before YYYY-MM-DD")
    sp = add("task-add", cmd_task_add, "Create a task")
    sp.add_argument("title")
    sp.add_argument("--due", default="", help="Due date YYYY-MM-DD")
    sp.add_argument("--project", default="")
    sp.add_argument("--context", default="", help="e.g. @deep")
    sp.add_argument("--status", default="todo", help="todo | doing | blocked | done")
    sp.add_argument("--notes", default="", help="Body text")
    sp.add_argument("--dir", default="tasks", help="Folder to file into (default: tasks)")
    sp = add("task-done", cmd_task_done, "Mark a task done")
    sp.add_argument("path"); sp.add_argument("--when", default="", help="Completion date (default: today)")
    sp = add("task-update", cmd_task_update, "Update a task's fields in place")
    sp.add_argument("path")
    sp.add_argument("--status", default=""); sp.add_argument("--due", default="")
    sp.add_argument("--project", default=""); sp.add_argument("--context", default="")
    sp = add("harvest", cmd_harvest, "Promote '- [ ]' checkbox lines in notes into task files")
    sp.add_argument("path", nargs="?", default="", help="File or folder to scan (default: whole workspace)")
    sp.add_argument("--dir", default="tasks", help="Folder to file harvested tasks into")
    sp.add_argument("--no-link-back", action="store_true", help="Don't annotate source lines (allows re-harvest)")
    sp = add("serve", cmd_serve, "Run the local HTTP API + web UI")
    sp.add_argument("--host", default="127.0.0.1"); sp.add_argument("--port", type=int, default=4177)
    sp.add_argument("--web-dir", help="Serve a static web UI from this directory (default: installed cairn-web)")
    add("mcp", cmd_mcp, "Run the MCP server (stdio) for Claude Code / Codex")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (FileError, WorkspaceError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
