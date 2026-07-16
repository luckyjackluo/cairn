"""Smoke tests for the CLI and the local HTTP API."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from cairn_cli.cli import main
from cairn_cli.serve import _Server, _make_handler
from cairn_core import Workspace


def run(capsys, argv):
    main(argv)
    return capsys.readouterr().out


def test_cli_create_read_tag(tmp_path, capsys):
    w = ["-w", str(tmp_path)]
    run(capsys, [*w, "create", "note.uni", "hello harness"])
    out = run(capsys, [*w, "--json", "read", "note.uni"])
    assert json.loads(out)["text"] == "hello harness"
    run(capsys, [*w, "tag", "note.uni", "a", "b"])
    out = run(capsys, [*w, "--json", "tags"])
    assert set(json.loads(out)) == {"a", "b"}


def test_cli_tree_and_move(tmp_path, capsys):
    w = ["-w", str(tmp_path)]
    run(capsys, [*w, "mkdir", "inbox"])
    run(capsys, [*w, "mkdir", "done"])
    run(capsys, [*w, "create", "inbox/x.uni", "content"])
    run(capsys, [*w, "mv", "inbox/x.uni", "done"])
    out = run(capsys, [*w, "--json", "ls", "done"])
    assert json.loads(out)[0]["name"] == "x.uni"


def test_cli_error_exits(tmp_path):
    with pytest.raises(SystemExit):
        main(["-w", str(tmp_path), "read", "missing.uni"])


def test_http_api(tmp_path):
    ws = Workspace(tmp_path)
    httpd = _Server(("127.0.0.1", 0), _make_handler(ws))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        req = urllib.request.Request(
            f"{base}/api/file",
            data=json.dumps({"path": "", "name": "a.uni", "content": "via http"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        created = json.loads(urllib.request.urlopen(req).read())
        assert created["name"] == "a.uni"
        tree = json.loads(urllib.request.urlopen(f"{base}/api/tree").read())
        assert any(n["name"] == "a.uni" for n in tree)
    finally:
        httpd.shutdown()
        httpd.server_close()
