# Contributing to Cairn

Thanks for taking a look — Cairn is young and contributions of every size are
welcome, from a typo fix to a whole new tool.

## The shape of the project

Everything is a thin adapter over one standard-library core, so read `core/`
first — most real changes land there and the faces (MCP, CLI, web) follow.

```
core/          cairn-core — the engine (stdlib only): .uni format, file ops,
               tags, frontmatter, query, digest, templates, retrieval.
mcp-server/    cairn-mcp-server  — MCP server (the flagship face).
cli/           cairn-cli  — scriptable CLI + local HTTP API + mcp launcher.
web/           cairn-web  — optional zero-build local web UI.
```

**Core stays stdlib-only.** Heavy dependencies (document parsers, embeddings)
live behind optional extras (`convert`, `embeddings`) and must degrade
gracefully when absent. If you find yourself reaching for a third-party import
in `cairn_core`, that's a signal it belongs in an extra.

## Getting set up

```bash
git clone https://github.com/luckyjackluo/cairn
cd cairn
make setup      # uv venv (3.11) + editable install of all four packages + extras
make test       # run the full suite
```

To poke at it live:

```bash
make serve WORKSPACE=~/Documents/notes          # web UI at http://127.0.0.1:4177/
uv run cairn --workspace ~/Documents/notes tree # CLI
```

## Making a change

1. **Open an issue first** for anything non-trivial, so we can agree on the
   approach before you invest the work. Small fixes can go straight to a PR.
2. Branch off `main`.
3. Keep the change focused — one logical change per PR.
4. **Add a test.** The suite is fast (`make test`, well under a second); a bug
   fix should come with a test that fails without it.
5. Match the surrounding style: type hints, module docstrings that explain
   *why*, and the crisp voice you see in the existing code.
6. Run `make test` and make sure it's green.

## Adding a tool

A new capability is usually four small edits:

1. Add the logic to a module in `core/cairn_core/` (with a test in
   `core/tests/`).
2. Export it from `core/cairn_core/__init__.py` if it's a new module.
3. Surface it as an MCP tool in `mcp-server/cairn_mcp/server.py` — annotate it
   `READ` (safe, auto-run) or `WRITE` (destructive, client confirms). The
   annotation is what drives the client's approval prompt, so get it right.
4. Add the matching CLI subcommand in `cli/cairn_cli/cli.py`.

## Opening the PR

- Describe *what* changed and *why*; link the issue it closes.
- Note any new dependency and why it couldn't be avoided.
- Green CI / `make test` before you request review.

We try to respond to issues and PRs quickly — if something's gone quiet for a
few days, a nudge is welcome.

## License

By contributing, you agree your contributions are licensed under the project's
[MIT License](LICENSE).
