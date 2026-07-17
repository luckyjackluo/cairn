# cairn-cli

A thin command-line interface over `cairn-core`. Standard-library only.

```bash
uvx cairn --workspace ~/Documents/notes ls
```

## Commands

```
cairn [--workspace DIR] [--json] <command>

  ls [path]                  list a directory
  tree [path]                show the file tree
  read <path>                print a file (flattened .uni text)
  create <path> [content]    create a file ('-' reads stdin)
  mkdir <path>               create a folder
  mv <path> <target_dir>     move a file/folder
  rename <path> <new_name>   rename in place
  rm <path>                  delete
  edit <path> <old> <new>    replace a unique occurrence
  import <path> [--all]      convert docx/pdf/pptx/csv/xlsx/md into .uni
  grep <pattern> [path]      search file contents
  find <query> [path]        search file names
  tag <path> [tags...]       get (no args) or set tags
  tags                       show the workspace tag tree
  retrieve <query> [-k N]    find relevant documents

  serve [--host H --port N]  run the local HTTP API (contract for the web UI)
  mcp                        run the MCP server (stdio) for Claude Code / Codex
```

`--json` makes any command emit machine-readable JSON.

## Examples

```bash
export CAIRN_WORKSPACE=~/Documents/notes
cairn tree
echo "meeting notes" | cairn create inbox/2026-07-16.uni -
cairn tag inbox/2026-07-16.uni meetings q3
cairn retrieve "budget planning" -k 5
cairn serve            # http://127.0.0.1:4177/api/health
```

## Three faces, one core

`cairn` (this CLI), `cairn-mcp-server` (the MCP server), and the planned web UI
are all thin adapters over `cairn-core`, so they can never drift. Use the
CLI for scripting, `mcp` inside Claude Code / Codex, and `serve` to back the
web UI.
