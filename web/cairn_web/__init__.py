"""cairn_web — the optional local web UI (static assets only).

A zero-build, dependency-free single-page app served by `cairn serve`. This
package ships only static files; :func:`static_dir` returns their location so
the CLI's HTTP server can serve them.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"


def static_dir() -> Path:
    """Absolute path to the bundled static web app."""
    return Path(__file__).parent / "static"
