# Cairn

**A local-first, open-source harness for organizing your files with an AI agent.**

Point Claude Code, Codex, Cursor — any MCP client — at a folder, and your agent
gains a full file-organization toolset: list, read, search, grep, edit, move,
tag, import documents, and semantically retrieve. No account, no cloud, no UI
required. Your files never leave your machine.

```bash
claude mcp add cairn -- uvx cairn-mcp --workspace ~/Documents/notes
```

Then ask your agent to *"organize this folder and tag everything"* — it will,
using tools that run locally over your own files.

> A cairn is a stack of stones that marks a trail. This one keeps your files in
> order and shows the way through them.

---

## Why a harness

The agent-harness ecosystem converged on a clear pattern: the model and the
agent loop are commodities supplied by the client (Claude Code, Codex, OpenCode).
The durable value is **the tools and the local data**. So Cairn ships *tools, not
an agent* — the client provides the loop, the approval prompts (driven by our
tool annotations), and the chat. Cairn is just the local file-organization
toolset that snaps into whatever harness you already use.

## Architecture

Everything is a thin adapter over one standard-library core, so the faces can
never drift.

```
core/          cairn-core — the engine (stdlib only): the .uni document format,
               file operations, tags, retrieval; optional document ingestion
               (docx/pdf/pptx/xlsx/md) and embeddings-based search.
mcp-server/    cairn-mcp  — MCP server: 21 tools with read-only/destructive
               annotations, resources, and prompts.        ← the flagship
cli/           cairn-cli  — scriptable CLI, a local HTTP API (`serve`),
               and an `mcp` launcher.
web/           cairn-web  — optional zero-build local web UI
               (browse / edit / view / tag / search).
```

## Use it

**In Claude Code / Codex / Cursor (primary):**

```bash
claude mcp add cairn -- uvx cairn-mcp --workspace ~/Documents/notes
# with real-document import: uvx --with "cairn-core[convert]" cairn-mcp ...
```

The server exposes read-only tools (list, read, search, grep, retrieve) that the
client runs freely, and destructive tools (move, delete, edit) that it confirms
first — driven by MCP tool annotations, so you get approval prompts with no UI
of ours.

**As a CLI:**

```bash
uvx cairn-cli --workspace ~/Documents/notes tree
uvx cairn-cli --workspace ~/Documents/notes retrieve "budget planning"
```

**With the optional web UI:**

```bash
pip install cairn-cli cairn-web
cairn --workspace ~/Documents/notes serve   # → http://127.0.0.1:4177/
```

## The `.uni` format

A `.uni` file is a small JSON document — `{ uuid, content: "<html>", metadata,
tags }` — with tags stored *in the file*. A workspace is just a directory: fully
portable, no external database. Source documents (docx/pdf/pptx/…) import into
editable `.uni` docs; the originals are kept by default.

## Local-first & bring-your-own-key

- No account, no login, no cloud sync. The filesystem is the source of truth.
- The default retriever is lexical and needs nothing. Embeddings are opt-in and
  bring-your-own-key (any OpenAI-compatible endpoint, including local servers
  like Ollama or LM Studio); vectors live in a local SQLite index under
  `<workspace>/.cairn/` and never leave your machine.

## Roadmap

Cairn is young. Rough direction, and where help is most welcome:

- **More document faces** — richer frontmatter support (nested metadata, dates),
  and round-tripping edits back into imported source formats.
- **Smarter digests** — summaries that use an embedding model when one is
  configured, and saved views (`.cairn/views/*.md`).
- **More retrieval backends** — pluggable rerankers; first-class local-model
  presets (Ollama, LM Studio) beyond the generic OpenAI-compatible path.
- **Editor/agent integrations** — thin recipes for wiring Cairn into common
  MCP clients and note-taking setups.
- **Packaging** — signed releases and a Homebrew formula.

Have a use case that doesn't fit? [Open an issue](https://github.com/luckyjackluo/cairn/issues)
or start a [discussion](https://github.com/luckyjackluo/cairn/discussions).

## Contributing

Contributions of every size are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)
for the project layout, dev setup, and how to add a tool. Good first issues are
labeled [`good first issue`](https://github.com/luckyjackluo/cairn/labels/good%20first%20issue).

## Develop

```bash
make setup      # uv venv (3.11) + editable install of all packages + extras
make test       # run the suite
make serve WORKSPACE=~/Documents/notes   # web UI
```

## License

MIT — see [LICENSE](LICENSE).
