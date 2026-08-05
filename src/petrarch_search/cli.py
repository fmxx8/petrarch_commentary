from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from petrarch_search.index import (
    SearchSyntaxError,
    build_index,
    get_poem,
    search_segments,
    segments_for_poem,
    stats,
)
from petrarch_search.paths import CANZONIERE_JSON, DB_PATH

app = typer.Typer(
    help="Petrarch Commentary Search — local search and reading for Canzoniere commentaries.",
    no_args_is_help=True,
)
console = Console()


def _panel(body: str, title: str) -> Panel:
    """Panel safe for OCR text containing square brackets."""
    return Panel(Text(body), title=title, expand=False)


_EXCERPT_SPLIT = re.compile(r"(\[\[|\]\])")


def _render_excerpt(text: str) -> Text:
    """Turn FTS5 snippet()'s '[[match]]' markers into real Rich highlighting.

    Built as a Text (not a raw markup string) so any stray literal brackets
    in OCR'd commentary text (e.g. an editorial "[his]") are also displayed
    as plain text instead of being parsed as (and silently eaten by) Rich
    markup tags.
    """
    result = Text()
    highlighting = False
    for part in _EXCERPT_SPLIT.split(text):
        if part == "[[":
            highlighting = True
        elif part == "]]":
            highlighting = False
        elif part:
            result.append(part, style="bold yellow" if highlighting else None)
    return result


def _format_poem_nums(raw: str) -> str:
    try:
        nums = json.loads(raw)
        return ", ".join(str(n) for n in nums)
    except json.JSONDecodeError:
        return raw


def _format_line_range(row: sqlite3.Row) -> str:
    start = row["line_start"]
    end = row["line_end"]
    if start is None and end is None:
        return ""
    if end is None or start == end:
        return str(start)
    return f"{start}-{end}"


def _require_poem_for_line(poem: int | None, line: int | None) -> None:
    if line is not None and poem is None:
        console.print("[red]--line requires --poem.[/red]")
        raise typer.Exit(1)


def _segment_title(row: sqlite3.Row) -> str:
    meta = row["author"]
    if row["year"]:
        meta += f" ({row['year']})"
    line_label = _format_line_range(row)
    if line_label:
        meta += f" — v. {line_label}"
    if row["page"]:
        meta += f" — p. {row['page']}"
    return meta


@app.command()
def search(
    query: str = typer.Argument(..., help="Full-text query (FTS5 syntax supported)."),
    author: str = typer.Option("any", "--author", "-a", help="Filter by commentary author."),
    lang: str = typer.Option("any", "--lang", "-l", help="Filter by language."),
    poem: int | None = typer.Option(None, "--poem", "-p", help="Restrict to a poem number."),
    line: int | None = typer.Option(None, "--line", "-n", help="Restrict to a poem line (requires --poem)."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results."),
) -> None:
    """Search commentary segments by keyword."""
    _require_poem_for_line(poem, line)
    try:
        rows = search_segments(
            query,
            author=author,
            language=lang,
            poem_num=poem,
            line=line,
            limit=limit,
        )
    except SearchSyntaxError as exc:
        console.print(f"[red]Invalid search query:[/red] {exc}")
        raise typer.Exit(1)
    if not rows:
        console.print("[yellow]No results.[/yellow]")
        raise typer.Exit(0)

    title = f"Search results for: {query}"
    if poem is not None and line is not None:
        title += f" (poem {poem}, line {line})"
    elif poem is not None:
        title += f" (poem {poem})"

    table = Table(title=title)
    table.add_column("#", style="dim")
    table.add_column("Author")
    table.add_column("Poem(s)")
    table.add_column("Line")
    table.add_column("Excerpt")

    for idx, row in enumerate(rows, start=1):
        table.add_row(
            str(idx),
            row["author"],
            _format_poem_nums(row["poem_nums"]),
            _format_line_range(row),
            _render_excerpt(row["excerpt"]),
        )
    console.print(table)


def _show_poem_line(
    poem: int,
    line: int | None,
    *,
    author: str,
    lang: str,
    show_all: bool = False,
) -> None:
    poem_row = get_poem(poem)
    if poem_row is not None:
        lines = poem_row["full_text"].splitlines()
        if line is not None:
            if line < 1:
                console.print(f"[red]Line {line} is out of range for poem {poem}.[/red]")
                raise typer.Exit(1)
            if line > len(lines):
                console.print(
                    f"[yellow]Line {line} is beyond the anchor text we have for poem {poem} "
                    f"(only {len(lines)} lines extracted); showing commentary only.[/yellow]"
                )
            else:
                console.print(_panel(lines[line - 1], title=f"Poem {poem}, line {line}"))
        else:
            header = f"Poem {poem_row['poem_num']}"
            if poem_row["form"]:
                header += f" ({poem_row['form']})"
            console.print(_panel(poem_row["full_text"], title=header))
            console.print(f"[dim]{poem_row['incipit']}[/dim]")
    else:
        console.print(
            f"[yellow]Poem {poem} text not in anchor index; showing commentary only.[/yellow]"
        )

    general_only = line is None and not show_all
    rows = segments_for_poem(
        poem, author=author, language=lang, line=line, general_only=general_only
    )
    if not rows:
        if line is not None:
            console.print("[yellow]No commentary segments for this line.[/yellow]")
        elif general_only:
            console.print(
                "[yellow]No whole-poem commentary for this poem (only line-by-line "
                "notes exist). Use --all to see them, or --line N for a specific line.[/yellow]"
            )
        else:
            console.print("[yellow]No commentary segments linked to this poem yet.[/yellow]")
        raise typer.Exit(0)

    for row in rows:
        console.print(_panel(row["text"], title=_segment_title(row)))


@app.command()
def show(
    poem: int = typer.Option(..., "--poem", "-p", help="Poem number (1-366)."),
    line: int | None = typer.Option(None, "--line", "-n", help="Show commentary for one line only."),
    author: str = typer.Option("any", "--author", "-a"),
    lang: str = typer.Option("any", "--lang", "-l"),
    show_all: bool = typer.Option(
        False,
        "--all",
        help=(
            "Without --line, only whole-poem notes are shown by default "
            "(line-by-line glosses would be a wall of text). Pass --all to "
            "also include every individual line-level commentary segment."
        ),
    ),
) -> None:
    """Show the anchor poem and linked commentary segments."""
    _show_poem_line(poem, line, author=author, lang=lang, show_all=show_all)


@app.command(name="at")
def at_line(
    poem: int = typer.Option(..., "--poem", "-p"),
    line: int = typer.Option(..., "--line", "-n"),
    author: str = typer.Option("any", "--author", "-a"),
    lang: str = typer.Option("any", "--lang", "-l"),
) -> None:
    """Show commentary segments tied to a specific poem line."""
    _show_poem_line(poem, line, author=author, lang=lang)


@app.command("build-index")
def build_index_cmd(
    canzoniere: Path = typer.Option(CANZONIERE_JSON, "--canzoniere", help="Path to canzoniere.json"),
    db: Path = typer.Option(DB_PATH, "--db", help="SQLite database path"),
) -> None:
    """Build or rebuild the SQLite FTS5 index."""
    result = build_index(canzoniere_path=canzoniere, db_path=db)
    console.print(
        f"[green]Index built:[/green] {result['poems']} poems, "
        f"{result['commentaries']} commentaries, {result['segments']} segments"
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (localhost by default)."),
    port: int = typer.Option(8000, "--port", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Restart on code changes."),
) -> None:
    """Run the local web UI."""
    try:
        import uvicorn
    except ImportError:
        console.print('[red]Web UI needs extra packages:[/red] pip install -e ".[web]"')
        raise typer.Exit(1)

    console.print(f"[green]Petrarch Search:[/green] http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run("petrarch_search.web:app", host=host, port=port, reload=reload)


@app.command()
def index_stats(
    db: Path = typer.Option(DB_PATH, "--db"),
) -> None:
    """Show index statistics."""
    data = stats(db)
    console.print(
        f"Poems: {data['poems']} | Commentaries: {data['commentaries']} | Segments: {data['segments']}"
    )


if __name__ == "__main__":
    app()
