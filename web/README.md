# cairn-web

The **optional** local web UI for the Cairn harness — a zero-build,
dependency-free single-page app (vanilla JS, no npm, no node_modules). It is
served by `cairn serve` and talks to the local HTTP API.

Remember: the harness's primary face is the MCP server (Claude Code / Codex).
This UI is the "see-and-touch" companion for people who want to browse, read,
edit `.uni` documents, view images/PDFs, manage tags, and search — without a
terminal.

## Use

```bash
pip install cairn-cli cairn-web
cairn --workspace ~/Documents/notes serve
# open http://127.0.0.1:4177/
```

`serve` auto-detects the installed `cairn-web` package. To point at a custom
build instead: `cairn serve --web-dir ./my-ui`.

## What it does

- File tree with lazy-expanding folders and tag badges
- Edit `.uni` documents (rich HTML) and plain-text files; **Save** writes back
- View images and PDFs inline (`/raw`)
- Add/edit tags per document
- Semantic search box (uses `/api/retrieve`) and a **Reindex** button
- New file / new folder / rename / delete

Everything is a call to the `cairn serve` API over `localhost` — nothing
leaves your machine, and there's no build step to run.
