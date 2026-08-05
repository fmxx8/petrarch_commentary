from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from petrarch_search.models import Commentary, Poem, load_canzoniere, load_commentary
from petrarch_search.paths import CANZONIERE_JSON, COMMENTARIES_DIR, DB_PATH


class SearchSyntaxError(ValueError):
    """The user's query is not valid FTS5 syntax (unbalanced quote, dangling AND...)."""


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS poems (
    poem_num INTEGER PRIMARY KEY,
    incipit TEXT NOT NULL,
    form TEXT,
    section TEXT,
    full_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commentaries (
    id TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    language TEXT NOT NULL,
    year TEXT,
    source_pdf TEXT,
    edition_note TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commentary_id TEXT NOT NULL,
    poem_nums TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    page INTEGER,
    confidence TEXT NOT NULL,
    text TEXT NOT NULL,
    FOREIGN KEY (commentary_id) REFERENCES commentaries(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text,
    author,
    language,
    poem_nums,
    commentary_id,
    segment_id UNINDEXED
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def clear_search_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM segments_fts")
    conn.execute("DELETE FROM segments")
    conn.execute("DELETE FROM commentaries")
    conn.commit()


def insert_poems(conn: sqlite3.Connection, poems: list[Poem]) -> int:
    conn.execute("DELETE FROM poems")
    rows = [
        (
            p.poem_num,
            p.incipit,
            p.form,
            p.section,
            "\n".join(p.lines),
        )
        for p in poems
    ]
    conn.executemany(
        """
        INSERT INTO poems (poem_num, incipit, form, section, full_text)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def insert_commentary(conn: sqlite3.Connection, commentary: Commentary) -> int:
    conn.execute(
        """
        INSERT OR REPLACE INTO commentaries
        (id, author, language, year, source_pdf, edition_note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            commentary.id,
            commentary.author,
            commentary.language,
            commentary.year,
            commentary.source_pdf,
            commentary.edition_note,
        ),
    )
    count = 0
    for segment in commentary.segments:
        poem_nums_json = json.dumps(segment.poem_nums)
        cursor = conn.execute(
            """
            INSERT INTO segments
            (commentary_id, poem_nums, line_start, line_end, page, confidence, text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commentary.id,
                poem_nums_json,
                segment.line_start,
                segment.line_end,
                segment.page,
                segment.confidence,
                segment.text,
            ),
        )
        segment_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO segments_fts
            (text, author, language, poem_nums, commentary_id, segment_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                segment.text,
                commentary.author,
                commentary.language,
                poem_nums_json,
                commentary.id,
                segment_id,
            ),
        )
        count += 1
    conn.commit()
    return count


def _author_filter(author: str | None, column: str = "author") -> tuple[str, list[object]]:
    if not author or author.lower() == "any":
        return "", []
    return f"{column} LIKE ?", [f"%{author}%"]


def _line_filter(line: int, column: str = "s") -> tuple[str, list[object]]:
    """Match commentary tied to a specific poem line."""
    text_col = f"{column}.text"
    return (
        f"("
        f"({column}.line_start IS NOT NULL AND {column}.line_end IS NOT NULL "
        f"AND {column}.line_start <= ? AND {column}.line_end >= ?) "
        f"OR ({column}.line_start IS NOT NULL AND {column}.line_end IS NULL "
        f"AND {column}.line_start = ?) "
        f"OR ({text_col} GLOB ?)"
        f")",
        [line, line, line, f"{line}.*"],
    )


def _poem_nums_filter(poem_num: int, column: str = "poem_nums") -> tuple[str, list[object]]:
    patterns = [
        f"[{poem_num}]",
        f"[{poem_num},%",
        f"%, {poem_num}]",
        f"%, {poem_num},%",
    ]
    clause = "(" + " OR ".join(f"{column} LIKE ?" for _ in patterns) + ")"
    return clause, patterns


def _poem_line_count(conn: sqlite3.Connection, poem_num: int) -> int | None:
    row = conn.execute(
        "SELECT full_text FROM poems WHERE poem_num = ?",
        (poem_num,),
    ).fetchone()
    if not row or not row["full_text"]:
        return None
    return len(row["full_text"].splitlines())


def _line_segment_in_bounds(line_start: int | None, line_end: int | None, poem_len: int | None) -> bool:
    """Drop glosses tagged beyond the anchor's line count (stale verso inheritance)."""
    if line_start is None or poem_len is None:
        return True
    end = line_end or line_start
    return 1 <= line_start <= poem_len and end <= poem_len


def build_index(
    canzoniere_path: Path = CANZONIERE_JSON,
    commentaries_dir: Path = COMMENTARIES_DIR,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    poems = load_canzoniere(canzoniere_path)
    commentary_files = sorted(commentaries_dir.glob("*.json"))
    commentaries = [load_commentary(path) for path in commentary_files]

    conn = connect(db_path)
    init_db(conn)
    clear_search_tables(conn)
    poem_count = insert_poems(conn, poems)
    segment_count = 0
    for commentary in commentaries:
        segment_count += insert_commentary(conn, commentary)
    conn.close()

    return {
        "poems": poem_count,
        "commentaries": len(commentaries),
        "segments": segment_count,
    }


def search_segments(
    query: str,
    *,
    author: str | None = None,
    language: str | None = None,
    poem_num: int | None = None,
    line: int | None = None,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[sqlite3.Row]:
    conn = connect(db_path)
    clauses = ["segments_fts MATCH ?"]
    params: list[object] = [query]

    author_clause, author_params = _author_filter(author, "f.author")
    if author_clause:
        clauses.append(author_clause)
        params.extend(author_params)

    if language and language.lower() != "any":
        clauses.append("f.language = ?")
        params.append(language.lower())

    if poem_num is not None:
        poem_clause, poem_params = _poem_nums_filter(poem_num, "f.poem_nums")
        clauses.append(poem_clause)
        params.extend(poem_params)

    if line is not None:
        line_clause, line_params = _line_filter(line, "s")
        clauses.append(line_clause)
        params.extend(line_params)

    sql = f"""
        SELECT
            s.id,
            s.commentary_id,
            s.poem_nums,
            s.line_start,
            s.line_end,
            s.page,
            s.confidence,
            snippet(segments_fts, 0, '[[', ']]', '...', 24) AS excerpt,
            s.text,
            c.author,
            c.language,
            c.year
        FROM segments_fts f
        JOIN segments s ON s.id = f.segment_id
        JOIN commentaries c ON c.id = s.commentary_id
        WHERE {' AND '.join(clauses)}
        ORDER BY rank
        LIMIT ?
    """
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise SearchSyntaxError(str(exc)) from exc
        raise
    finally:
        conn.close()
    return rows


# Sparse, lemma-only poem-level source (notes on barely 100/251 poems, often
# a single word's gloss) that otherwise crowds out the more substantial
# general notes from Bettarini/Santagata in the default `show --poem N` view.
# Still fully reachable via explicit --author, and still fully searchable via
# `search` (a one-word gloss is exactly what you want when searching for that
# word, e.g. "Bactria").
DEPRIORITIZED_AUTHORS = ("Robert M. Durling",)


def segments_for_poem(
    poem_num: int,
    *,
    author: str | None = None,
    language: str | None = None,
    line: int | None = None,
    general_only: bool = False,
    db_path: Path = DB_PATH,
) -> list[sqlite3.Row]:
    conn = connect(db_path)
    poem_clause, poem_params = _poem_nums_filter(poem_num, "s.poem_nums")
    clauses = [poem_clause]
    params: list[object] = list(poem_params)

    author_clause, author_params = _author_filter(author, "c.author")
    if author_clause:
        clauses.append(author_clause)
        params.extend(author_params)
    else:
        for deprioritized in DEPRIORITIZED_AUTHORS:
            clauses.append("c.author != ?")
            params.append(deprioritized)

    if language and language.lower() != "any":
        clauses.append("c.language = ?")
        params.append(language.lower())

    if line is not None:
        line_clause, line_params = _line_filter(line, "s")
        clauses.append(line_clause)
        params.extend(line_params)
    elif general_only:
        # No specific line requested: default to whole-poem notes only, so
        # `show --poem N` isn't a wall of every individual line-by-line gloss.
        clauses.append("s.line_start IS NULL")

    sql = f"""
        SELECT
            s.id,
            s.commentary_id,
            s.poem_nums,
            s.line_start,
            s.line_end,
            s.page,
            s.confidence,
            s.text,
            c.author,
            c.language,
            c.year
        FROM segments s
        JOIN commentaries c ON c.id = s.commentary_id
        WHERE {' AND '.join(clauses)}
        ORDER BY
            CASE WHEN s.line_start IS NULL THEN 1 ELSE 0 END,
            s.line_start,
            c.author,
            s.page,
            s.id
    """
    rows = conn.execute(sql, params).fetchall()
    poem_len = _poem_line_count(conn, poem_num)
    if poem_len is not None:
        rows = [
            r for r in rows
            if _line_segment_in_bounds(r["line_start"], r["line_end"], poem_len)
        ]
    conn.close()
    return rows


def all_poems(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    """Every anchor poem, in reading order."""
    conn = connect(db_path)
    rows = conn.execute("SELECT * FROM poems ORDER BY poem_num").fetchall()
    conn.close()
    return rows


def gloss_counts(
    *,
    author: str | None = None,
    language: str | None = None,
    db_path: Path = DB_PATH,
) -> tuple[dict[tuple[int, int], int], dict[int, int]]:
    """Commentary counts for the whole corpus at once.

    Returns (per-line counts keyed by (poem, line), whole-poem note counts
    keyed by poem). The reading view needs a badge on every line of every
    poem, which would otherwise be one query per poem.
    """
    conn = connect(db_path)
    clauses: list[str] = []
    params: list[object] = []

    author_clause, author_params = _author_filter(author, "c.author")
    if author_clause:
        clauses.append(author_clause)
        params.extend(author_params)
    else:
        for deprioritized in DEPRIORITIZED_AUTHORS:
            clauses.append("c.author != ?")
            params.append(deprioritized)

    if language and language.lower() != "any":
        clauses.append("c.language = ?")
        params.append(language.lower())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT s.poem_nums, s.line_start, s.line_end
        FROM segments s
        JOIN commentaries c ON c.id = s.commentary_id
        {where}
        """,
        params,
    ).fetchall()
    conn.close()

    lens: dict[int, int] = {}
    if rows:
        conn2 = connect(db_path)
        for prow in conn2.execute("SELECT poem_num, full_text FROM poems").fetchall():
            if prow["full_text"]:
                lens[prow["poem_num"]] = len(prow["full_text"].splitlines())
        conn2.close()

    per_line: dict[tuple[int, int], int] = {}
    per_poem: dict[int, int] = {}
    for row in rows:
        try:
            poem_nums = json.loads(row["poem_nums"])
        except json.JSONDecodeError:
            continue
        start = row["line_start"]
        for poem_num in poem_nums:
            if start is None:
                per_poem[poem_num] = per_poem.get(poem_num, 0) + 1
                continue
            pl = lens.get(poem_num)
            end = row["line_end"] or start
            if pl is not None and not _line_segment_in_bounds(start, end, pl):
                continue
            for line in range(start, end + 1):
                if pl is not None and line > pl:
                    continue
                key = (poem_num, line)
                per_line[key] = per_line.get(key, 0) + 1
    return per_line, per_poem


def get_poem(poem_num: int, db_path: Path = DB_PATH) -> sqlite3.Row | None:
    conn = connect(db_path)
    row = conn.execute(
        "SELECT * FROM poems WHERE poem_num = ?",
        (poem_num,),
    ).fetchone()
    conn.close()
    return row


def list_authors(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    """Commentary sources with their segment counts, for filter menus."""
    conn = connect(db_path)
    rows = conn.execute(
        """
        SELECT c.id, c.author, c.language, c.year, COUNT(s.id) AS segment_count
        FROM commentaries c
        LEFT JOIN segments s ON s.commentary_id = c.id
        GROUP BY c.id
        ORDER BY segment_count DESC
        """
    ).fetchall()
    conn.close()
    return rows


def poem_numbers(db_path: Path = DB_PATH) -> list[int]:
    """Poem numbers present in the anchor text, ascending."""
    conn = connect(db_path)
    rows = conn.execute("SELECT poem_num FROM poems ORDER BY poem_num").fetchall()
    conn.close()
    return [row["poem_num"] for row in rows]


def stats(db_path: Path = DB_PATH) -> dict[str, int]:
    conn = connect(db_path)
    poem_count = conn.execute("SELECT COUNT(*) FROM poems").fetchone()[0]
    commentary_count = conn.execute("SELECT COUNT(*) FROM commentaries").fetchone()[0]
    segment_count = conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    conn.close()
    return {
        "poems": poem_count,
        "commentaries": commentary_count,
        "segments": segment_count,
    }
