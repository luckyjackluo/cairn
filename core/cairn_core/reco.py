"""Paper recommendation — surface impactful, on-topic papers into the workspace.

Where the rest of the engine *organizes* what you already have, this *brings new
things in*: for each project you care about, it pulls the field's high-impact
literature from the Semantic Scholar Academic Graph, ranks it by relevance ×
impact, and drops the single most important paper you haven't seen yet into your
read-later queue as a normal ``paper`` note. Run it daily and you work through a
field's canon one paper at a time, per project.

Design choices worth knowing:

* **Impact, not recency.** We deliberately do *not* use Semantic Scholar's
  Recommendations API (it only returns papers from the last 60 days). We query
  the Academic Graph with tight boolean topic queries and rank by citation
  weight, so day one surfaces the seminal paper, not yesterday's preprint.
* **Relevance is the query.** Bulk-search results come back in arbitrary corpus
  order, so position carries no signal. Instead, relevance comes from (a) tight
  quoted-phrase queries whose matched set is inherently on-topic and (b)
  co-occurrence: a paper matching several of a project's facets is core to it.
* **No repeats.** A per-workspace ledger plus a scan of existing paper notes'
  ``arxiv`` ids means a paper is recommended once, ever.
* **Generic core, personal config.** No project is hard-coded here. Projects,
  queries and weights live in ``<workspace>/.cairn/paper_reco.json`` so this
  module stays reusable and every workspace curates its own reading list.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import frontmatter, tags
from .files import FileError, FileService, _is_text
from .workspace import Workspace

CONFIG_PATH = ".cairn/paper_reco.json"
STATE_PATH = ".cairn/paper_reco_state.json"

_S2_BULK = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
_S2_FIELDS = (
    "title,abstract,year,citationCount,influentialCitationCount,"
    "authors,externalIds,openAccessPdf,venue"
)

# Sensible defaults; every one is overridable per config / per project.
_DEFAULTS: dict[str, Any] = {
    "papers_dir": "research/papers",
    "fields_of_study": ["Computer Science"],
    "min_citations": 20,
    "per_query": 30,      # top-by-citation members pulled from each tight query
    "impact_weight": 0.5,  # how much citations shift the relevance-primary score
    "max_authors": 6,      # authors recorded in a note's frontmatter
    "import_fulltext": True,               # download + convert each pick's PDF
    "fulltext_dir": "research/papers/fulltext",
    "fulltext_max_mb": 40,                 # skip absurdly large PDFs
}


# --------------------------------------------------------------------------- #
# config + state
# --------------------------------------------------------------------------- #

def load_config(ws: Workspace) -> dict[str, Any]:
    """Read the workspace's paper-reco config, or raise a helpful error."""
    p = ws.root / CONFIG_PATH
    if not p.is_file():
        raise FileError(
            f"No paper-reco config at {CONFIG_PATH}. Create it with a "
            '"projects" map (each with "queries") — see the module docstring.'
        )
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise FileError(f"Could not read {CONFIG_PATH}: {exc}") from exc
    if not isinstance(cfg, dict) or not isinstance(cfg.get("projects"), dict):
        raise FileError(f'{CONFIG_PATH} must have a "projects" object.')
    return cfg


def _defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    merged = dict(_DEFAULTS)
    merged.update(cfg.get("defaults", {}) or {})
    return merged


def _project_config(cfg: dict[str, Any], project: str) -> dict[str, Any]:
    projects = cfg["projects"]
    if project not in projects:
        known = ", ".join(sorted(projects)) or "(none)"
        raise FileError(f"Unknown project {project!r}. Configured: {known}.")
    pcfg = dict(projects[project])
    if not pcfg.get("queries"):
        raise FileError(f"Project {project!r} has no 'queries' in {CONFIG_PATH}.")
    return pcfg


def list_projects(ws: Workspace) -> list[dict[str, Any]]:
    """Names + a one-line topic summary for every configured project."""
    cfg = load_config(ws)
    out = []
    for name, pcfg in sorted(cfg["projects"].items()):
        out.append(
            {
                "project": name,
                "title": pcfg.get("title", name),
                "tag": pcfg.get("project_tag", name),
                "queries": len(pcfg.get("queries", [])),
            }
        )
    return out


def _load_state(ws: Workspace) -> dict[str, Any]:
    p = ws.root / STATE_PATH
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_state(ws: Workspace, state: dict[str, Any]) -> None:
    p = ws.root / STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Semantic Scholar access
# --------------------------------------------------------------------------- #

def _http_get_json(
    url: str, params: dict[str, str], api_key: str | None, retries: int = 5
) -> dict[str, Any]:
    """GET JSON with exponential backoff on 429 (the shared pool is throttled)."""
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}")
    req.add_header("User-Agent", "cairn-paper-reco/0.1")
    if api_key:
        req.add_header("x-api-key", api_key)
    delay = 3.0
    last_err: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code == 429:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            raise FileError(f"Semantic Scholar error {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_err = exc
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise FileError(
        "Semantic Scholar is rate-limiting or unreachable after retries "
        f"({last_err}). A free API key (S2_API_KEY) raises the limit."
    )


def _search(
    query: str,
    fields_of_study: list[str],
    min_citations: int,
    per_query: int,
    api_key: str | None,
) -> list[dict[str, Any]]:
    """Highest-cited members of a tight boolean topic query."""
    params = {
        "query": query,
        "sort": "citationCount:desc",
        "fields": _S2_FIELDS,
        "minCitationCount": str(int(min_citations)),
    }
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)
    data = _http_get_json(_S2_BULK, params, api_key)
    rows = data.get("data") or []
    return rows[: max(1, per_query)]


# --------------------------------------------------------------------------- #
# ranking
# --------------------------------------------------------------------------- #

def _impact(paper: dict[str, Any]) -> float:
    cites = paper.get("citationCount") or 0
    infl = paper.get("influentialCitationCount") or 0
    return math.log10(1 + cites) + 0.3 * math.log10(1 + infl)


def _rank_candidates(
    pcfg: dict[str, Any], defaults: dict[str, Any], api_key: str | None
) -> list[dict[str, Any]]:
    """Aggregate a project's queries into one relevance×impact-ranked list.

    A paper's relevance is how many of the project's facet-queries it matched
    (co-occurrence); its score multiplies that by a citation-weighted impact
    term, so a tangential-but-famous paper that matches only one loose facet
    can't outrank a core paper matching several.
    """
    fos = pcfg.get("fields_of_study", defaults["fields_of_study"])
    min_c = int(pcfg.get("min_citations", defaults["min_citations"]))
    per_q = int(pcfg.get("per_query", defaults["per_query"]))
    imp_w = float(defaults["impact_weight"])

    by_id: dict[str, dict[str, Any]] = {}
    for query in pcfg["queries"]:
        for paper in _search(query, fos, min_c, per_q, api_key):
            pid = paper.get("paperId")
            if not pid:
                continue
            entry = by_id.get(pid)
            if entry is None:
                by_id[pid] = {"paper": paper, "matched": [query]}
            else:
                entry["matched"].append(query)

    ranked = []
    for pid, entry in by_id.items():
        paper = entry["paper"]
        rel = float(len(entry["matched"]))  # co-occurrence across facets
        score = rel * (1.0 + imp_w * _impact(paper))
        ranked.append(
            {
                "paperId": pid,
                "paper": paper,
                "matched_queries": entry["matched"],
                "score": round(score, 3),
            }
        )
    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked


# --------------------------------------------------------------------------- #
# "seen" bookkeeping — ledger + existing notes
# --------------------------------------------------------------------------- #

def _existing_note_keys(ws: Workspace, papers_dir: str) -> set[str]:
    """arXiv ids and normalized titles already filed as notes — never re-recommend."""
    keys: set[str] = set()
    root = ws.resolve(papers_dir)
    if not root.is_dir():
        return keys
    for p in root.rglob("*.md"):
        if any(part.startswith(".") for part in p.relative_to(ws.root).parts):
            continue
        try:
            meta, _ = frontmatter.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            continue
        if not meta:
            continue
        arx = meta.get("arxiv")
        if arx:
            keys.add(f"arxiv:{_norm_arxiv(str(arx))}")
        title = meta.get("title")
        if title:
            keys.add(f"title:{_norm_title(str(title))}")
    return keys


def _seen_keys(ws: Workspace, project: str, papers_dir: str) -> set[str]:
    keys = _existing_note_keys(ws, papers_dir)
    proj = _load_state(ws).get(project, {})
    for pid, rec in (proj.get("seen") or {}).items():
        keys.add(f"pid:{pid}")
        if rec.get("arxiv"):
            keys.add(f"arxiv:{_norm_arxiv(str(rec['arxiv']))}")
        if rec.get("title"):
            keys.add(f"title:{_norm_title(str(rec['title']))}")
    return keys


def _candidate_keys(paper: dict[str, Any]) -> set[str]:
    keys = {f"pid:{paper.get('paperId')}"}
    arx = (paper.get("externalIds") or {}).get("ArXiv")
    if arx:
        keys.add(f"arxiv:{_norm_arxiv(str(arx))}")
    if paper.get("title"):
        keys.add(f"title:{_norm_title(str(paper['title']))}")
    return keys


# --------------------------------------------------------------------------- #
# note rendering
# --------------------------------------------------------------------------- #

def _norm_arxiv(s: str) -> str:
    m = re.search(r"(\d{4}\.\d{4,5})", s)
    return m.group(1) if m else s.strip().lower()


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


_STOP = {"a", "an", "the", "of", "for", "and", "to", "in", "on", "with", "via", "is",
         "from", "by", "using", "towards", "toward", "as", "at"}


def _slugify(title: str, limit: int = 8) -> str:
    words = [w for w in re.sub(r"[^a-z0-9\s]", "", title.lower()).split() if w and w not in _STOP]
    return "-".join(words[:limit]) or "paper"


def _paper_url(paper: dict[str, Any]) -> str:
    ext = paper.get("externalIds") or {}
    if ext.get("ArXiv"):
        return f"https://arxiv.org/abs/{_norm_arxiv(str(ext['ArXiv']))}"
    if paper.get("paperId"):
        return f"https://www.semanticscholar.org/paper/{paper['paperId']}"
    if ext.get("DOI"):
        return f"https://doi.org/{ext['DOI']}"
    return ""


def _why(cand: dict[str, Any], project: str, today: str) -> str:
    paper = cand["paper"]
    cites = paper.get("citationCount") or 0
    infl = paper.get("influentialCitationCount") or 0
    facets = len(cand["matched_queries"])
    facet_note = (
        f"matches {facets} of your **{project}** topics"
        if facets > 1
        else f"matches your **{project}** topics"
    )
    bits = [
        f"Auto-recommended by Cairn paper-reco on {today}.",
        f"High-impact pick ({cites:,} citations, {infl:,} influential); {facet_note}.",
    ]
    s2 = f"https://www.semanticscholar.org/paper/{paper.get('paperId')}"
    links = [f"[Semantic Scholar]({s2})"]
    pdf = (paper.get("openAccessPdf") or {}).get("url")
    if pdf:
        links.append(f"[open-access PDF]({pdf})")
    bits.append(" · ".join(links))
    return " ".join(bits)


def _to_pick(cand: dict[str, Any], project: str, pcfg: dict[str, Any], today: str) -> dict[str, Any]:
    """Flatten a ranked candidate into a serializable recommendation record."""
    paper = cand["paper"]
    ext = paper.get("externalIds") or {}
    authors = [a.get("name", "") for a in (paper.get("authors") or [])]
    return {
        "project": project,
        "paperId": paper.get("paperId"),
        "title": paper.get("title") or "",
        "authors": authors,
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "arxiv": _norm_arxiv(str(ext["ArXiv"])) if ext.get("ArXiv") else "",
        "url": _paper_url(paper),
        "citationCount": paper.get("citationCount") or 0,
        "influentialCitationCount": paper.get("influentialCitationCount") or 0,
        "abstract": paper.get("abstract") or "",
        "pdf_url": (paper.get("openAccessPdf") or {}).get("url") or "",
        "score": cand["score"],
        "matched_queries": cand["matched_queries"],
        "why": _why(cand, project, today),
        "tags": list(pcfg.get("tags", [pcfg.get("project_tag", project)])),
    }


def _pdf_candidates(pick: dict[str, Any]) -> list[str]:
    """Ordered PDF URLs to try — arXiv is the most reliable, then any OA PDF."""
    urls = []
    if pick.get("arxiv"):
        urls.append(f"https://arxiv.org/pdf/{pick['arxiv']}")
    if pick.get("pdf_url"):
        urls.append(pick["pdf_url"])
    return urls


def _download_pdf(url: str, dest: Path, max_bytes: int) -> bool:
    """Fetch a PDF to ``dest``; returns False (never raises) if it isn't a PDF."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cairn-paper-reco/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False
    if len(data) > max_bytes or not data.startswith(b"%PDF"):
        return False  # too big, or an HTML error/paywall page masquerading as a PDF
    try:
        dest.write_bytes(data)
    except OSError:
        return False
    return True


def _import_fulltext(
    ws: Workspace, fs: FileService, pick: dict[str, Any],
    defaults: dict[str, Any], today: str,
) -> str | None:
    """Best-effort: download the pick's PDF and convert it to a searchable .uni.

    Returns the workspace-relative .uni path, or ``None`` if no PDF was
    reachable/convertible. Never raises — full text is a bonus, and a failure
    here must not stop the paper note from being filed.
    """
    urls = _pdf_candidates(pick)
    if not urls:
        return None
    fulltext_dir = defaults["fulltext_dir"]
    max_bytes = int(defaults["fulltext_max_mb"]) * 1024 * 1024
    out_dir = ws.resolve(fulltext_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today}-{_slugify(pick['title'])}"
    pdf_path = out_dir / f"{stem}.pdf"
    if pdf_path.exists():
        pdf_path = out_dir / f"{stem}-{ws.next_uuid()}.pdf"
    if not any(_download_pdf(u, pdf_path, max_bytes) for u in urls):
        return None
    try:
        # Convert the PDF into a .uni full-text doc; drop the heavy binary after.
        item = fs.import_file(ws.relpath(pdf_path), keep_original=False)
        uni_path = item["path"]
        tags.set_tags(ws, uni_path, list(pick.get("tags", [])) + ["fulltext"])
        return uni_path
    except FileError:
        # Conversion unsupported/failed — leave nothing half-imported behind.
        if pdf_path.exists():
            try:
                pdf_path.unlink()
            except OSError:
                pass
        return None


def _save_note(
    ws: Workspace, fs: FileService, pick: dict[str, Any], pcfg: dict[str, Any],
    defaults: dict[str, Any], today: str,
) -> str:
    papers_dir = pcfg.get("papers_dir", defaults["papers_dir"])
    slug = _slugify(pick["title"])
    name = f"{today}-{slug}.md"
    # Avoid clobbering an existing file for the same day/slug.
    target = ws.resolve(f"{papers_dir}/{name}")
    if target.exists():
        name = f"{today}-{slug}-{ws.next_uuid()}.md"
    max_a = int(defaults["max_authors"])
    fields = {
        "title": pick["title"],
        "arxiv": pick["arxiv"],
        "url": pick["url"],
        "date": today,
        "project": pick["project"],
        "tags": pick["tags"],
        "authors": pick["authors"][:max_a],
        "abstract": pick["abstract"] or "(no abstract available)",
        "why": pick["why"],
        "notes": "",
    }
    fs.create_file(papers_dir, name, template="paper", fields=fields)
    return f"{papers_dir}/{name}"


def _record_seen(ws: Workspace, project: str, pick: dict[str, Any], path: str, today: str) -> None:
    state = _load_state(ws)
    proj = state.setdefault(project, {})
    seen = proj.setdefault("seen", {})
    seen[pick["paperId"]] = {
        "date": today,
        "title": pick["title"],
        "arxiv": pick["arxiv"],
        "path": path,
        "fulltext_path": pick.get("fulltext_path"),
        "score": pick["score"],
    }
    _save_state(ws, state)


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #

def recommend(
    ws: Workspace,
    project: str,
    count: int = 1,
    save: bool = True,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Recommend the top ``count`` unseen papers for ``project``.

    Ranks the project's candidate pool by relevance × impact, skips anything
    already recommended or already filed, and (when ``save``) writes each pick
    into the workspace as a ``to-read`` paper note, recording it so it never
    repeats. Returns ``{project, picks: [...], saved, exhausted}``.
    """
    api_key = api_key or os.environ.get("S2_API_KEY")
    cfg = load_config(ws)
    defaults = _defaults(cfg)
    pcfg = _project_config(cfg, project)
    today = datetime.date.today().isoformat()
    papers_dir = pcfg.get("papers_dir", defaults["papers_dir"])

    ranked = _rank_candidates(pcfg, defaults, api_key)
    seen = _seen_keys(ws, project, papers_dir)

    fs = FileService(ws)
    picks: list[dict[str, Any]] = []
    for cand in ranked:
        if len(picks) >= max(1, count):
            break
        if _candidate_keys(cand["paper"]) & seen:
            continue
        pick = _to_pick(cand, project, pcfg, today)
        if save:
            if defaults.get("import_fulltext"):
                fulltext = _import_fulltext(ws, fs, pick, defaults, today)
                if fulltext:
                    pick["fulltext_path"] = fulltext
                    pick["why"] += f" Full text imported to Cairn: `{fulltext}`."
            path = _save_note(ws, fs, pick, pcfg, defaults, today)
            _record_seen(ws, project, pick, path, today)
            pick["path"] = path
        picks.append(pick)
        # Guard against two same-run picks colliding on identity.
        seen |= _candidate_keys(cand["paper"])

    return {
        "project": project,
        "picks": picks,
        "saved": save,
        "candidates": len(ranked),
        "exhausted": len(picks) < max(1, count),
    }


def recommend_all(
    ws: Workspace, count: int = 1, save: bool = True, api_key: str | None = None
) -> dict[str, Any]:
    """Run :func:`recommend` for every configured project (the daily driver)."""
    cfg = load_config(ws)
    results = []
    for project in sorted(cfg["projects"]):
        try:
            results.append(recommend(ws, project, count=count, save=save, api_key=api_key))
        except FileError as exc:
            results.append({"project": project, "error": str(exc), "picks": []})
    return {"date": datetime.date.today().isoformat(), "projects": results}


def preview(
    ws: Workspace, project: str, count: int = 3, api_key: str | None = None
) -> dict[str, Any]:
    """Like :func:`recommend` but never writes — for inspecting the queue."""
    return recommend(ws, project, count=count, save=False, api_key=api_key)
