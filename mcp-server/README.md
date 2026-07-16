# cairn-mcp

An MCP server that exposes Cairn's local-first file-organization tools to
**Claude Code, Codex, Cursor, and any MCP client**. Point it at a folder and
your agent gains file tools: list, read, search, grep, edit, move, tag, and
retrieve — over the `.uni` document format.

This is the flagship, **UI-less** face of the product. The MCP client supplies
the agent loop, the approval prompts, and the chat. Nothing of ours runs
remotely; the server is a local process over your own files.

## Install & run

```bash
uvx cairn-mcp --workspace ~/Documents/notes
```

To import real documents (docx/pdf/pptx/xlsx/md), install the converter extra:

```bash
uvx --with "cairn-core[convert]" cairn-mcp --workspace ~/Documents/notes
```

## Use it in Claude Code

```bash
claude mcp add cairn -- uvx cairn-mcp --workspace ~/Documents/notes
```

Or in any MCP client config:

```json
{
  "mcpServers": {
    "cairn": {
      "command": "uvx",
      "args": ["cairn-mcp", "--workspace", "~/Documents/notes"]
    }
  }
}
```

## Tools

| Tool | Kind | Purpose |
|------|------|---------|
| `list_dir`, `get_file_tree`, `read_detail` | read-only | inspect the workspace |
| `search_files`, `grep`, `semantic_retrieve` | read-only | find things |
| `get_file_tags`, `get_tag_tree` | read-only | inspect tags |
| `create_file`, `create_folder`, `update_file_tags` | write | add content |
| `import_document`, `import_folder` | write | convert docx/pdf/pptx/csv/xlsx/md → editable `.uni` |
| `multi_edit`, `rename_item`, `move_item`, `delete_item` | destructive | change/relocate (client confirms) |

Read-only tools carry `readOnlyHint`; destructive tools carry `destructiveHint`,
so clients like Claude Code run reads freely and prompt before destructive edits.

Also exposes `workspace://tree` and `workspace://tags` resources, and
`organize` / `tag_all` prompts (slash-commands in the client).
