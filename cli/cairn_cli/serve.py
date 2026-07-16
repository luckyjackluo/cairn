"""A tiny local HTTP API over cairn_core.

This is the localhost contract the optional web UI (packages/web) will consume.
Standard-library only (http.server), so it adds no dependencies and starts
instantly. It is bound to localhost by default and operates on a single local
workspace — there is nothing multi-tenant or cloud here.

Routes (all JSON):
  GET  /api/health
  GET  /api/tree?path=            GET  /api/list?path=
  GET  /api/file?path=            POST /api/file      {path,name,content}
  POST /api/edit  {path,old_string,new_string}
  POST /api/move  {path,target_dir}
  POST /api/rename{path,new_name} POST /api/delete    {path}
  GET  /api/tags                  POST /api/tags      {path,tags}
  GET  /api/search?q=&path=       GET  /api/grep?q=&path=
  GET  /api/retrieve?q=&k=
"""

from __future__ import annotations

import json
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cairn_core import FileError, FileService, Workspace, WorkspaceError, retrieval, tags


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer without the slow startup reverse-DNS lookup.

    stdlib's HTTPServer.server_bind calls socket.getfqdn(host), which can block
    for tens of seconds on a flaky/offline network. We don't need the FQDN, so
    bind directly and record the host verbatim.
    """

    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".webp": "image/webp", ".pdf": "application/pdf",
}


def _make_handler(ws: Workspace, web_dir=None):
    fs = FileService(ws)

    class Handler(BaseHTTPRequestHandler):
        server_version = "cairn/0.1"

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_static(self, route: str) -> None:
            if web_dir is None:
                return self._send(404, {"error": "web UI not installed (pip install cairn-web)"})
            rel = "index.html" if route in ("/", "") else route.lstrip("/")
            target = (web_dir / rel).resolve()
            if not target.is_file() or not str(target).startswith(str(web_dir.resolve())):
                return self._send(404, {"error": "not found"})
            ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self._send_bytes(200, target.read_bytes(), ctype)

        def _serve_raw(self) -> None:
            """Serve a workspace file's raw bytes (for image/PDF viewing)."""
            try:
                p = ws.resolve(self._q().get("path", ""))
                if not p.is_file():
                    return self._send(404, {"error": "not found"})
                ctype = _CONTENT_TYPES.get(p.suffix.lower(), "application/octet-stream")
                self._send_bytes(200, p.read_bytes(), ctype)
            except (FileError, WorkspaceError) as e:
                self._send(400, {"error": str(e)})

        def _send(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _q(self):
            return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")

        def log_message(self, *args):  # quiet by default
            pass

        def do_OPTIONS(self):
            self._send(204, {})

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/raw":
                return self._serve_raw()
            if not route.startswith("/api/"):
                return self._serve_static(route)
            q = self._q()
            try:
                if route == "/api/health":
                    return self._send(200, {"ok": True, "workspace": str(ws.root)})
                if route == "/api/tree":
                    return self._send(200, fs.get_tree(q.get("path", "")))
                if route == "/api/list":
                    return self._send(200, fs.list_dir(q.get("path", "")))
                if route == "/api/file":
                    return self._send(200, fs.read_detail(q["path"]))
                if route == "/api/tags":
                    return self._send(200, tags.get_tag_tree(ws))
                if route == "/api/search":
                    return self._send(200, fs.search_files(q.get("q", ""), q.get("path", "")))
                if route == "/api/grep":
                    return self._send(200, fs.grep(q.get("q", ""), q.get("path", "")))
                if route == "/api/retrieve":
                    return self._send(200, retrieval.semantic_retrieve(ws, q.get("q", ""), int(q.get("k", 5))))
                return self._send(404, {"error": "not found"})
            except (FileError, WorkspaceError) as e:
                return self._send(400, {"error": str(e)})
            except KeyError as e:
                return self._send(400, {"error": f"missing parameter: {e}"})

        def do_POST(self):
            route = urlparse(self.path).path
            try:
                b = self._body()
                if route == "/api/file":
                    return self._send(200, fs.create_file(b.get("path", ""), b["name"], b.get("content", "")))
                if route == "/api/folder":
                    return self._send(200, fs.create_folder(b.get("path", ""), b["name"]))
                if route == "/api/edit":
                    return self._send(200, fs.multi_edit(b["path"], b["old_string"], b["new_string"]))
                if route == "/api/move":
                    return self._send(200, fs.move_item(b["path"], b["target_dir"]))
                if route == "/api/rename":
                    return self._send(200, fs.rename_item(b["path"], b["new_name"]))
                if route == "/api/delete":
                    return self._send(200, fs.delete_item(b["path"]))
                if route == "/api/tags":
                    return self._send(200, tags.set_tags(ws, b["path"], b.get("tags", [])))
                if route == "/api/import":
                    return self._send(200, fs.import_file(b["path"], b.get("dest_dir") or None))
                if route == "/api/import-folder":
                    return self._send(200, fs.import_tree(b.get("path", "")))
                if route == "/api/reindex":
                    return self._send(200, retrieval.reindex(ws))
                if route == "/api/content":
                    return self._send(200, fs.write_content(b["path"], b.get("content", "")))
                return self._send(404, {"error": "not found"})
            except (FileError, WorkspaceError) as e:
                return self._send(400, {"error": str(e)})
            except KeyError as e:
                return self._send(400, {"error": f"missing field: {e}"})

    return Handler


def _resolve_web_dir(explicit=None):
    """Locate the static web UI: an explicit path, else the installed package."""
    from pathlib import Path

    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_dir() else None
    try:
        import cairn_web

        return cairn_web.static_dir()
    except Exception:
        return None


def serve(ws: Workspace, host: str = "127.0.0.1", port: int = 4177, web_dir=None) -> None:
    resolved = _resolve_web_dir(web_dir)
    httpd = _Server((host, port), _make_handler(ws, resolved))
    print(f"cairn serving {ws.root}")
    if resolved:
        print(f"  web UI:  http://{host}:{port}/")
    else:
        print("  (web UI not installed — API only. `pip install cairn-web` to enable)")
    print(f"  api:     http://{host}:{port}/api/health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        httpd.server_close()
