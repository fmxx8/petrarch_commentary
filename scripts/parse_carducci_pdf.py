#!/usr/bin/env python3
"""Parse Carducci & Ferrari's 1908 critical edition (Sansoni) into commentary
segments.

Expects page text from ocr_pdf.py on the Archive.org scan:

    python scripts/ocr_pdf.py data/raw/commentaries/carducci_ferrari_1908.pdf \\
        --start-page 55 --end-page 574

This edition prints poem text and numbered footnotes on the same pages. Poems
are marked with Roman numerals (II, CCLXVII); glosses look like "1. Voi." or
"5-6. Le parole...".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.align_heuristics import (
    find_line_range,
    looks_like_commentary,
    parse_poem_token,
)
from petrarch_search.models import Commentary, Segment, save_commentary
from petrarch_search.paths import COMMENTARIES_DIR, PAGES_DIR

DEFAULT_DOC_ID = "carducci_ferrari_1908"

ROMAN_POEM_LINE = re.compile(r"^([IVXLCDM]{2,})(?:\s*\[[^\]]+\])?\s*$")
GLOSS_MARKER = re.compile(r"(?m)^(\d{1,3})(?:[-–](\d{1,3}))?\.\s+")
HEADNOTE_START = re.compile(
    r"^(Proemio|Elogio|Tardi conosce|Dice essere|Notevole|S['']?\s*io avesse|"
    r"Questo e il|Quest['']?appendice|Il ravvisarsi|Menami a morte|"
    r"Amor m['']?\s*assale|Tardi conosce)",
    re.I,
)
CARDUCCI_BACK_MATTER = re.compile(
    r"^(INDICE\s+(COMPILATO|DEI|ALFABETICO)|DEI VOCABOLI E DEI MODI)\b",
    re.I,
)
SKIP_LINE = re.compile(
    r"^(SONETTI\s+E\s+CANZON|IN VITA|IN MORTE|Petrarca\s*—|DI MADONNA LAURA)\b",
    re.I,
)
# Printed book page numbers in the margin (usually 3 digits near section breaks).
BOOK_PAGE_NUM = re.compile(r"^\d{3}$")


def is_back_matter_page(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if CARDUCCI_BACK_MATTER.search(s):
            return True
        if s.lower().startswith("indice alfabetico delle rime"):
            return True
        return False
    return False


def drop_back_matter(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    for i, (_, text) in enumerate(pages):
        if is_back_matter_page(text):
            return pages[:i]
    return pages


def load_pages(doc_id: str, pages_dir: Path = PAGES_DIR) -> list[tuple[int, str]]:
    page_dir = pages_dir / doc_id
    if not page_dir.exists():
        raise FileNotFoundError(f"No page text for {doc_id} in {page_dir}")
    pages: list[tuple[int, str]] = []
    for path in sorted(page_dir.glob("page_*.txt")):
        page_num = int(path.stem.split("_")[1])
        pages.append((page_num, path.read_text(encoding="utf-8")))
    return drop_back_matter(pages)


def poem_markers_in_text(text: str, _current: int | None = None) -> list[tuple[int, int]]:
    """(char_offset, poem_num) for standalone Roman headings on a page."""
    markers: list[tuple[int, int]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if SKIP_LINE.search(s) or BOOK_PAGE_NUM.match(s):
            pos += len(line)
            continue
        match = ROMAN_POEM_LINE.match(s)
        if match:
            num = parse_poem_token(match.group(1))
            if num:
                markers.append((pos, num))
        pos += len(line)
    return markers


def build_global_markers(pages: list[tuple[int, str]]) -> list[tuple[int, int, int]]:
    """Sorted (page, offset, poem_num) timeline with synthetic start at poem 1."""
    by_page: dict[int, list[int]] = {}
    for page_num, text in pages:
        nums = [num for _, num in poem_markers_in_text(text)]
        if nums:
            by_page[page_num] = nums

    timeline: list[tuple[int, int, int]] = []
    start_page = pages[0][0] if pages else 55
    timeline.append((start_page, 0, 1))
    prev_poem = 1

    for page_num in sorted(by_page):
        nums = sorted(set(by_page[page_num]))
        for num in nums:
            if prev_poem < num <= prev_poem + 8:
                timeline.append((page_num, 0, num))
                prev_poem = num

    return timeline


def assign_poem(
    page_num: int,
    offset: int,
    timeline: list[tuple[int, int, int]],
) -> int:
    prev = timeline[0]
    nxt: tuple[int, int, int] | None = None
    for marker in timeline:
        if (marker[0], marker[1]) <= (page_num, offset):
            prev = marker
        else:
            nxt = marker
            break

    if nxt is None:
        return prev[2]

    if page_num <= prev[0]:
        return prev[2]
    if page_num >= nxt[0]:
        return nxt[2]

    page_span = nxt[0] - prev[0]
    poem_span = nxt[2] - prev[2]
    if page_span <= 0 or poem_span <= 0:
        return prev[2]

    progress = (page_num - prev[0]) / page_span
    return prev[2] + max(0, min(poem_span, int(progress * poem_span + 0.5)))


def _normalize_block(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def split_gloss_blocks(text: str) -> list[str]:
    matches = list(GLOSS_MARKER.finditer(text))
    if not matches:
        return []
    blocks: list[str] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = _normalize_block(text[start:end])
        if len(block) >= 12:
            blocks.append(block)
    return blocks


def extract_headnote(text: str) -> str | None:
    """General note before the first numbered gloss on a page."""
    match = GLOSS_MARKER.search(text)
    prefix = text[: match.start()] if match else text
    chunks: list[str] = []
    for line in prefix.splitlines():
        s = line.strip()
        if not s or SKIP_LINE.search(s) or BOOK_PAGE_NUM.match(s):
            continue
        if ROMAN_POEM_LINE.match(s):
            continue
        if re.match(r"^\d{1,2}$", s):
            continue
        if re.match(r"^[A-Za-zÀ-ÿ].*[;,!?:]$", s) and len(s) < 72:
            continue
        if HEADNOTE_START.match(s) or looks_like_commentary(s):
            chunks.append(s)
    if not chunks:
        return None
    block = _normalize_block(" ".join(chunks))
    if len(block) < 40 or not looks_like_commentary(block):
        return None
    return block


def auto_segments(pages: list[tuple[int, str]]) -> list[Segment]:
    segments: list[Segment] = []
    timeline = build_global_markers(pages)
    line_by_poem: dict[int, tuple[int | None, int | None]] = {}
    seen_gloss: dict[int, bool] = {}

    for page_num, text in pages:
        headnote = extract_headnote(text)
        if headnote:
            poem = assign_poem(page_num, 0, timeline)
            segments.append(
                Segment(
                    poem_nums=[poem],
                    text=headnote,
                    line_start=None,
                    line_end=None,
                    page=page_num,
                    confidence="auto",
                )
            )

        for match in GLOSS_MARKER.finditer(text):
            start = match.start()
            poem = assign_poem(page_num, start, timeline)
            next_m = GLOSS_MARKER.search(text, match.end())
            block_end = next_m.start() if next_m else len(text)
            block = _normalize_block(text[start:block_end])
            if len(block) < 12:
                continue

            line_start, line_end = find_line_range(block)
            prev_start, prev_end = line_by_poem.get(poem, (None, None))
            if line_start is not None:
                line_by_poem[poem] = (line_start, line_end)
                seen_gloss[poem] = True
            elif seen_gloss.get(poem) and prev_start is not None:
                line_start, line_end = prev_start, prev_end

            segments.append(
                Segment(
                    poem_nums=[poem],
                    text=block,
                    line_start=line_start,
                    line_end=line_end,
                    page=page_num,
                    confidence="auto",
                )
            )

    return segments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-id", default=DEFAULT_DOC_ID)
    parser.add_argument(
        "--output",
        type=Path,
        default=COMMENTARIES_DIR / "carducci_ferrari_1908.json",
    )
    args = parser.parse_args()

    pages = load_pages(args.doc_id)
    segments = auto_segments(pages)

    commentary = Commentary(
        id="carducci_ferrari_1908",
        author="Giosuè Carducci & Severino Ferrari",
        language="italian",
        year="1908",
        source_pdf="data/raw/commentaries/carducci_ferrari_1908.pdf",
        edition_note=(
            "Le rime di Francesco Petrarca di su gli originali (Firenze: G.C. Sansoni, 1908). "
            "Archive.org scan; text layer + auto-aligned glosses."
        ),
        segments=segments,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_commentary(commentary, args.output)

    poems_covered = len({n for seg in segments for n in seg.poem_nums})
    with_lines = sum(1 for seg in segments if seg.line_start is not None)
    print(f"Segments: {len(segments)} (with line numbers: {with_lines})")
    print(f"Poems covered: {poems_covered}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
