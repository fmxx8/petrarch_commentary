#!/usr/bin/env python3
"""Parse the "Notes and Commentary" section of Mark Musa's complete Canzoniere
(Indiana UP edition) into line-level commentary segments.

Expects OCR page text already produced by ocr_pdf.py, e.g.:

    python scripts/ocr_pdf.py data/raw/commentaries/musa_manfredi_canzoniere_1996.pdf \
        --force-ocr --lang eng --start-page 555 --end-page 804

Page format (after OCR), one poem per block:

    68 Sonnet

    "Jousting" thoughts are the subject of this sonnet ...

    1. The sacred sight: Of ancient Rome ...
    2. the evil of my past: Cf. 53.77 ...
    9-11. who with His birth did... / ...exalt humility: God chose this place ...

    69 Sonnet
    ...
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.models import Commentary, Segment, save_commentary
from petrarch_search.paths import COMMENTARIES_DIR, PAGES_DIR

DEFAULT_DOC_ID = "musa_manfredi_canzoniere_1996"

# OCR garbles: "17 Sonnet" -> "\7 Sonnet", "22 Sestina" -> "22 SestTina".
HEADER = re.compile(
    r"^\s*(\d{1,3})\s+(Sonnet|Sonn?et|Canzone|Ballata|Madrigal|Madrigale|Sest\w*)\s*$",
    re.IGNORECASE,
)
GLOSS_START = re.compile(r"^\s*(\d{1,3}(?:[-–]\d{1,3})?)\.\s+(.*)$")
END_MARKERS = ("Works Cited", "Index of First Lines", "INDEX OF FIRST LINES")


def normalize_header_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^\\(\d)\b", r"1\1", line)
    return line


def load_pages(doc_id: str, pages_dir: Path = PAGES_DIR) -> list[tuple[int, str]]:
    page_dir = pages_dir / doc_id
    if not page_dir.exists():
        raise FileNotFoundError(f"No OCR output for {doc_id} in {page_dir}")
    pages: list[tuple[int, str]] = []
    for path in sorted(page_dir.glob("page_*.txt")):
        page_num = int(path.stem.split("_")[1])
        pages.append((page_num, path.read_text(encoding="utf-8")))
    return pages


# Tesseract occasionally renders an italicized capital letter (almost always
# "I" at the start of a first-person clause, e.g. "I find no other shield")
# as a stray "[". A genuine editorial insertion like "[his]" always closes
# with "]" nearby; an OCR artifact never does, so only fix the unmatched ones.
UNMATCHED_OPEN_BRACKET = re.compile(r"\[(?=[a-z])")


def fix_bracket_artifact(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        window = text[match.start() : match.start() + 15]
        return match.group(0) if "]" in window else "I "

    return UNMATCHED_OPEN_BRACKET.sub(repl, text)


def join_wrapped(lines: list[str]) -> str:
    text = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if text.endswith("-") and len(text) >= 2 and text[-2].isalpha() and line[:1].islower():
            text = text[:-1] + line
        elif text:
            text = text + " " + line
        else:
            text = line
    return re.sub(r"\s+", " ", text).strip()


def parse_line_range(token: str) -> tuple[int, int]:
    token = token.replace("–", "-")
    if "-" in token:
        start, end = token.split("-", 1)
        return int(start), int(end)
    return int(token), int(token)


def parse_notes(pages: list[tuple[int, str]]) -> list[Segment]:
    segments: list[Segment] = []

    current_poem: int | None = None
    seg_lines: list[str] = []
    seg_line_start: int | None = None
    seg_line_end: int | None = None
    seg_page: int | None = None
    stop = False

    def flush() -> None:
        nonlocal seg_lines, seg_line_start, seg_line_end, seg_page
        text = fix_bracket_artifact(join_wrapped(seg_lines))
        if text and current_poem is not None:
            segments.append(
                Segment(
                    poem_nums=[current_poem],
                    text=text,
                    line_start=seg_line_start,
                    line_end=seg_line_end,
                    page=seg_page,
                    confidence="auto",
                )
            )
        seg_lines = []
        seg_line_start = None
        seg_line_end = None
        seg_page = None

    for page_num, page_text in pages:
        if stop:
            break
        for raw_line in page_text.splitlines():
            if any(marker.lower() in raw_line.lower() for marker in END_MARKERS):
                stop = True
                break

            header_match = HEADER.match(normalize_header_line(raw_line))
            if header_match:
                flush()
                current_poem = int(header_match.group(1))
                seg_page = page_num
                continue

            gloss_match = GLOSS_START.match(raw_line)
            if gloss_match:
                flush()
                seg_line_start, seg_line_end = parse_line_range(gloss_match.group(1))
                seg_page = page_num
                # Keep the "N. " prefix in the stored text (matching how
                # Bettarini/Santagata segments keep their own "13. " heading)
                # instead of only the words after it -- consistent look across
                # sources even though the number is also shown in the panel title.
                seg_lines = [f"{gloss_match.group(1)}. {gloss_match.group(2)}"]
                continue

            if not raw_line.strip():
                continue

            if current_poem is None:
                continue

            if seg_page is None:
                seg_page = page_num
            seg_lines.append(raw_line)

    flush()
    return segments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-id", default=DEFAULT_DOC_ID)
    parser.add_argument(
        "--output",
        type=Path,
        default=COMMENTARIES_DIR / "musa_canzoniere_notes.json",
    )
    args = parser.parse_args()

    pages = load_pages(args.doc_id)
    segments = parse_notes(pages)

    commentary = Commentary(
        id="musa_canzoniere_notes",
        author="Mark Musa & Barbara Manfredi (Notes)",
        language="english",
        year="1996",
        source_pdf=f"data/raw/commentaries/{args.doc_id}.pdf",
        edition_note=(
            "Notes and Commentary from Musa & Manfredi's complete Canzoniere "
            "(Indiana University Press). Line-numbered glosses per poem."
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
