"""Document ingestion — convert source files into ``.uni`` HTML content.

Real workspaces are full of ``.docx``/``.pdf``/``.pptx``/``.csv``/``.xlsx``
files. This module turns each into TipTap-compatible HTML so it can live as an
editable ``.uni`` document. Plain-text and code become ``<p>`` blocks.

The heavy parsers (mammoth, python-docx, python-pptx, PyMuPDF, openpyxl,
markdown) are **optional**: they are imported lazily, and a missing one raises
:class:`ConversionError` with an install hint. Install them with the core's
``convert`` extra::

    pip install "cairn-core[convert]"
"""

from __future__ import annotations

import csv
import html
import io
from pathlib import Path

from .uni import text_to_html

# Extension -> the originalFormat label we record in .uni metadata.
_FORMAT = {
    ".docx": "docx", ".pdf": "pdf", ".pptx": "pptx", ".csv": "csv",
    ".xlsx": "xlsx", ".xls": "xlsx", ".md": "markdown", ".txt": "text",
}

# Code files convert as preformatted text.
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".go",
    ".rs", ".rb", ".php", ".sh", ".css", ".scss", ".html", ".htm", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".sql", ".json",
}


class ConversionError(Exception):
    """Raised when a file cannot be converted (unsupported or missing parser)."""


def can_convert(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in _FORMAT or ext in _CODE_EXTS


def original_format(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _FORMAT:
        return _FORMAT[ext]
    if ext in _CODE_EXTS:
        return "code"
    return "text"


def _require(module: str, extra_hint: str):
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise ConversionError(
            f"Converting this file needs '{extra_hint}'. "
            f"Install it with: pip install \"cairn-core[convert]\""
        ) from exc


def convert_to_html(path: Path) -> str:
    """Return TipTap-compatible HTML for a source file. Raises ConversionError."""
    ext = path.suffix.lower()
    if ext == ".docx":
        return _docx(path)
    if ext == ".pdf":
        return _pdf(path)
    if ext == ".pptx":
        return _pptx(path)
    if ext == ".csv":
        return _csv(path)
    if ext in (".xlsx", ".xls"):
        return _xlsx(path)
    if ext == ".md":
        return _markdown(path)
    if ext in _CODE_EXTS or ext == ".txt":
        return text_to_html(path.read_text(encoding="utf-8", errors="replace"))
    raise ConversionError(f"Unsupported source format: {ext}")


# --- individual converters -------------------------------------------------

def _docx(path: Path) -> str:
    try:
        mammoth = _require("mammoth", "mammoth")
        with path.open("rb") as f:
            return mammoth.convert_to_html(f).value or "<p></p>"
    except ConversionError:
        raise
    except Exception:
        # Fallback: python-docx paragraph extraction.
        docx = _require("docx", "python-docx")
        doc = docx.Document(str(path))
        paras = [f"<p>{html.escape(p.text)}</p>" for p in doc.paragraphs if p.text.strip()]
        return "".join(paras) or "<p>Unable to extract content from DOCX.</p>"


def _pdf(path: Path) -> str:
    fitz = _require("fitz", "pymupdf")
    doc = fitz.open(str(path))
    parts: list[str] = []
    try:
        for i in range(len(doc)):
            text = doc.load_page(i).get_text()
            if not text.strip():
                continue
            parts.append(f"<h2>Page {i + 1}</h2>")
            for line in (ln.strip() for ln in text.split("\n")):
                if line:
                    parts.append(f"<p>{html.escape(line)}</p>")
    finally:
        doc.close()
    return "".join(parts) or "<p>No text could be extracted from the PDF.</p>"


def _pptx(path: Path) -> str:
    pptx = _require("pptx", "python-pptx")
    prs = pptx.Presentation(str(path))
    parts = ["<h1>Presentation</h1>"]
    for idx, slide in enumerate(prs.slides, 1):
        parts.append(f"<h2>Slide {idx}</h2>")
        found = False
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text.strip():
                found = True
                for line in (ln.strip() for ln in shape.text.split("\n")):
                    if line:
                        parts.append(f"<p>{html.escape(line)}</p>")
        if not found:
            parts.append("<p><em>No text on this slide</em></p>")
    return "".join(parts)


def _rows_to_table(rows: list[list[str]]) -> str:
    if not rows:
        return "<p>(empty)</p>"
    out = ["<table>"]
    out.append("<thead><tr>" + "".join(f"<th>{html.escape(str(c).strip())}</th>" for c in rows[0]) + "</tr></thead>")
    if len(rows) > 1:
        out.append("<tbody>")
        for row in rows[1:]:
            out.append("<tr>" + "".join(f"<td>{html.escape(str(c).strip())}</td>" for c in row) + "</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out)


def _csv(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    buf = io.StringIO(text)
    try:
        dialect = csv.Sniffer().sniff(text[:1024])
        reader = csv.reader(buf, dialect)
    except csv.Error:
        buf.seek(0)
        reader = csv.reader(buf)
    return _rows_to_table(list(reader))


def _xlsx(path: Path) -> str:
    openpyxl = _require("openpyxl", "openpyxl")
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        parts.append(f"<h2>{html.escape(ws.title)}</h2>")
        rows = [[("" if c is None else c) for c in row] for row in ws.iter_rows(values_only=True)]
        parts.append(_rows_to_table(rows))
    wb.close()
    return "".join(parts) or "<p>(empty workbook)</p>"


def _markdown(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        md = __import__("markdown")
        return md.markdown(raw, extensions=["tables", "fenced_code"])
    except ImportError:
        return text_to_html(raw)  # graceful: plain paragraphs if markdown missing
