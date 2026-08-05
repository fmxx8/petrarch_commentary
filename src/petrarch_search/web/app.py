from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from petrarch_search.index import (
    DEPRIORITIZED_AUTHORS,
    SearchSyntaxError,
    all_poems,
    get_poem,
    gloss_counts,
    list_authors,
    poem_numbers,
    search_segments,
    segments_for_poem,
    stats,
)
from petrarch_search.paths import DB_PATH

WEB_DIR = Path(__file__).resolve().parent
MAX_POEM = 366

app = FastAPI(title="Petrarch Commentary Search", docs_url=None, redoc_url=None)
# The reading view ships the whole Canzoniere in one response.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
# The reading view renders ~4500 lines, so template indentation is real weight.
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True


def poem_url(
    poem_num: int,
    *,
    line: int | None = None,
    author: str = "any",
    show_all: bool = False,
) -> str:
    """Link to a poem, carrying the active filters along."""
    params: dict[str, object] = {}
    if line:
        params["line"] = line
    if author and author.lower() != "any":
        params["author"] = author
    if show_all:
        params["all"] = "1"
    query = urlencode(params)
    return f"/poem/{poem_num}?{query}" if query else f"/poem/{poem_num}"


def plural(count: int, one: str, few: str, many: str) -> str:
    """Russian number agreement: 1 стихотворение, 2 стихотворения, 5 стихотворений."""
    tail = abs(int(count)) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


templates.env.globals["poem_url"] = poem_url
templates.env.globals["plural"] = plural


_MARKER_SPLIT = re.compile(r"(\[\[|\]\])")


def _highlight(text: str) -> Markup:
    """Turn FTS5 snippet()'s '[[match]]' markers into <mark>, escaping the rest.

    Everything between markers is OCR'd text that routinely contains stray
    brackets and angle brackets, so it is escaped piece by piece rather than
    marking the whole string safe.
    """
    out: list[str] = []
    highlighting = False
    for part in _MARKER_SPLIT.split(text):
        if part == "[[" and not highlighting:
            out.append("<mark>")
            highlighting = True
        elif part == "]]" and highlighting:
            out.append("</mark>")
            highlighting = False
        elif part:
            out.append(str(escape(part)))
    if highlighting:
        out.append("</mark>")
    return Markup("".join(out))


def _poem_nums(raw: str) -> list[int]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _line_label(row: sqlite3.Row) -> str:
    start, end = row["line_start"], row["line_end"]
    if start is None:
        return ""
    if end is None or start == end:
        return str(start)
    return f"{start}-{end}"


def _segment(row: sqlite3.Row, *, excerpt: bool = False) -> dict:
    data = {
        "author": row["author"],
        "year": row["year"],
        "language": row["language"],
        "page": row["page"],
        "line_start": row["line_start"],
        "line_label": _line_label(row),
        "poem_nums": _poem_nums(row["poem_nums"]),
        "text": row["text"],
    }
    if excerpt:
        data["excerpt"] = _highlight(row["excerpt"])
    return data


_counts_cache: dict[tuple, tuple[dict, dict]] = {}


def _cached_gloss_counts(author: str) -> tuple[dict, dict]:
    """Corpus-wide badge counts (~200 ms), memoised until the index is rebuilt."""
    try:
        stamp = DB_PATH.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = (author, stamp)
    if key not in _counts_cache:
        _counts_cache.clear()
        _counts_cache[key] = gloss_counts(author=author, language="any")
    return _counts_cache[key]


def _ctx(**extra) -> dict:
    """Context shared by every page: the header form needs sources and stats."""
    base = {
        "authors": [dict(row) for row in list_authors()],
        "db_stats": stats(),
        "max_poem": MAX_POEM,
        "nav": None,
    }
    base.update(extra)
    return base


def _error(request: Request, message: str, status: int = 404) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "error.html", _ctx(message=message), status_code=status
    )


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    author: str = "any",
    poem: int | None = None,
    line: int | None = None,
    limit: int = Query(20, ge=1, le=500),
) -> HTMLResponse:
    """Search form; renders results when a query is present."""
    query = q.strip()
    results: list[dict] = []
    error: str | None = None

    if line is not None and poem is None:
        error = "Фильтр по строке работает только вместе с номером стихотворения."
    elif query:
        try:
            rows = search_segments(
                query,
                author=author,
                poem_num=poem,
                line=line,
                limit=limit,
            )
        except SearchSyntaxError as exc:
            error = f"Некорректный поисковый запрос: {exc}"
        else:
            results = [_segment(row, excerpt=True) for row in rows]

    return templates.TemplateResponse(
        request,
        "search.html",
        _ctx(
            q=query,
            author=author,
            poem=poem,
            line=line,
            limit=limit,
            results=results,
            error=error,
            searched=bool(query) and error is None,
        ),
    )


@app.get("/poem/{poem_num}", response_class=HTMLResponse)
def poem_page(
    request: Request,
    poem_num: int,
    author: str = "any",
    line: int | None = None,
    show_all: bool = Query(False, alias="all"),
) -> HTMLResponse:
    """Anchor poem with clickable lines plus the commentary bound to it."""
    if not 1 <= poem_num <= MAX_POEM:
        return _error(request, f"Стихотворение {poem_num} вне диапазона 1–{MAX_POEM}.")
    if line is not None and line < 1:
        line = None

    poem_row = get_poem(poem_num)
    poem_lines = poem_row["full_text"].splitlines() if poem_row else []

    # One extra pass over every segment of this poem so each line can carry a
    # badge with the number of glosses waiting behind it.
    counts: dict[int, int] = defaultdict(int)
    for row in segments_for_poem(poem_num, author=author):
        start = row["line_start"]
        if start is None:
            continue
        for num in range(start, (row["line_end"] or start) + 1):
            counts[num] += 1

    if line is not None:
        rows = segments_for_poem(poem_num, author=author, line=line)
    else:
        rows = segments_for_poem(
            poem_num, author=author, general_only=not show_all
        )

    line_text = None
    line_beyond_anchor = False
    if line is not None:
        if line <= len(poem_lines):
            line_text = poem_lines[line - 1]
        elif poem_lines:
            line_beyond_anchor = True

    return templates.TemplateResponse(
        request,
        "poem.html",
        _ctx(
            poem_num=poem_num,
            poem=dict(poem_row) if poem_row else None,
            poem_lines=list(enumerate(poem_lines, start=1)),
            line_counts=counts,
            line=line,
            line_text=line_text,
            line_beyond_anchor=line_beyond_anchor,
            author=author,
            show_all=show_all,
            segments=[_segment(row) for row in rows],
            deprioritized=DEPRIORITIZED_AUTHORS,
            hiding_deprioritized=(not author or author.lower() == "any"),
            prev_poem=poem_num - 1 if poem_num > 1 else None,
            next_poem=poem_num + 1 if poem_num < MAX_POEM else None,
            in_anchor=poem_row is not None,
        ),
    )


@app.get("/read", response_class=HTMLResponse)
def read(
    request: Request,
    author: str = "any",
    focus: int | None = None,
) -> HTMLResponse:
    """The whole anchor text in one scrollable page, every line clickable."""
    per_line, per_poem = _cached_gloss_counts(author)
    anchored = {row["poem_num"]: row for row in all_poems()}

    entries = []
    for num in range(1, MAX_POEM + 1):
        row = anchored.get(num)
        if row is None:
            # No anchor text for this one, but commentary may still exist:
            # keep the slot so the numbering a reader scrolls past is honest.
            entries.append(
                {
                    "num": num,
                    "in_anchor": False,
                    "general_count": per_poem.get(num, 0),
                }
            )
            continue
        entries.append(
            {
                "num": num,
                "in_anchor": True,
                "form": row["form"],
                "incipit": row["incipit"],
                "general_count": per_poem.get(num, 0),
                "lines": [
                    (i, text, per_line.get((num, i), 0))
                    for i, text in enumerate(row["full_text"].splitlines(), start=1)
                ],
            }
        )

    return templates.TemplateResponse(
        request,
        "read.html",
        _ctx(
            nav="read",
            entries=entries,
            author=author,
            focus=focus,
            anchor_count=len(anchored),
        ),
    )


@app.get("/fragment/commentary", response_class=HTMLResponse)
def commentary_fragment(
    request: Request,
    poem: int = Query(..., ge=1, le=MAX_POEM),
    line: int | None = Query(None, ge=1),
    author: str = "any",
) -> HTMLResponse:
    """Commentary for one poem or line, as a chunk of HTML for the reader panel."""
    if line is not None:
        rows = segments_for_poem(poem, author=author, line=line)
    else:
        rows = segments_for_poem(poem, author=author, general_only=True)

    poem_row = get_poem(poem)
    line_text = None
    if poem_row is not None and line is not None:
        poem_lines = poem_row["full_text"].splitlines()
        if line <= len(poem_lines):
            line_text = poem_lines[line - 1]

    return templates.TemplateResponse(
        request,
        "_commentary.html",
        {
            "poem_num": poem,
            "line": line,
            "line_text": line_text,
            "author": author,
            "segments": [_segment(row) for row in rows],
        },
    )


@app.get("/goto")
def goto(poem: int = Query(..., ge=1, le=MAX_POEM)) -> RedirectResponse:
    """Target of the 'jump to poem' box, which cannot build a path itself."""
    return RedirectResponse(url=f"/poem/{poem}", status_code=303)


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request) -> HTMLResponse:
    """What is actually in the index: editions, languages, coverage."""
    return templates.TemplateResponse(
        request, "sources.html", _ctx(nav="sources", anchor_poems=poem_numbers())
    )
