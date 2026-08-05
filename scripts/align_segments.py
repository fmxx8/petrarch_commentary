#!/usr/bin/env python3
"""Segment OCR pages and align commentary blocks to poem numbers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.align_heuristics import (
    GLOSS_LINE_PATTERN,
    GLOSS_RANGE_PATTERN,
    build_incipit_index,
    extract_page_poem,
    find_block_opening_poem,
    find_incipit_references,
    find_line_range,
    looks_like_commentary,
)
from petrarch_search.models import Commentary, Segment, load_canzoniere, save_commentary
from petrarch_search.paths import (
    CANZONIERE_JSON,
    COMMENTARIES_DIR,
    PAGES_DIR,
    REVIEW_DIR,
)

BLOCK_SPLIT = re.compile(r"\n{2,}")

# Back-of-book analytical/concordance indices (e.g. Bettarini's "Indici
# analitici", a cura di Alessandro Pancheri, pp. 790-902): word/name/place
# concordance lists with no commentary prose at all -- just "CCCXX\n4.12,
# 4.14, ..." -- long enough to slip past looks_like_commentary's length check
# and get attributed to whichever poem number happens to be the list's
# heading. Detected by the section's own title page and dropped wholesale.
BACK_MATTER_TITLE = re.compile(
    r"^(indic[ei]\s+(analitic[oi]|dei\s+nomi|dei\s+luoghi|metric[oa])|"
    r"indice\s+dei\s+capoversi)\s*$",
    re.IGNORECASE,
)


def load_pages(doc_id: str, pages_dir: Path = PAGES_DIR) -> list[tuple[int, str]]:
    page_dir = pages_dir / doc_id
    if not page_dir.exists():
        raise FileNotFoundError(f"No OCR output for {doc_id} in {page_dir}")
    pages: list[tuple[int, str]] = []
    for path in sorted(page_dir.glob("page_*.txt")):
        page_num = int(path.stem.split("_")[1])
        pages.append((page_num, path.read_text(encoding="utf-8")))
    return drop_back_matter(pages)


def drop_back_matter(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Truncate the page list at the first page whose own first line is a
    back-matter index title (table-of-contents mentions of the same title,
    e.g. "Indici analitici a cura di ..." buried mid-page, don't count)."""
    for i, (_, text) in enumerate(pages):
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if BACK_MATTER_TITLE.match(first_line):
            return pages[:i]
    return pages


def split_blocks(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    for page_num, text in pages:
        for block in BLOCK_SPLIT.split(text):
            cleaned = block.strip()
            if len(cleaned) < 40:
                continue
            # Always keep blocks with their own numbered gloss heading ("13."
            # or "9-11."); otherwise drop blocks that are just OCR'd poem verse
            # leaking through as their own paragraph (no real commentary
            # content -- no year/cross-reference/metrical-note keyword).
            if not (
                GLOSS_LINE_PATTERN.match(cleaned)
                or GLOSS_RANGE_PATTERN.match(cleaned)
                or looks_like_commentary(cleaned)
            ):
                continue
            blocks.append((page_num, cleaned))
    return blocks


def auto_segments(
    blocks: list[tuple[int, str]],
    incipit_index: dict[str, int],
    page_poems: dict[int, list[int]] | None = None,
) -> list[Segment]:
    segments: list[Segment] = []
    current_poems: list[int] = []
    current_page: int | None = None
    # A numbered gloss ("3. che tenne gli occhi mei: ...") routinely runs past
    # a page break or a spurious OCR blank line, so its tail becomes its own
    # blank-line-separated block with no heading of its own. Once we've seen
    # the first numbered gloss for the *current* poem, an unheaded block is
    # almost certainly that continuation -- not a fresh "general" note (real
    # general notes, like the headnote/metrical-scheme blocks, only appear
    # *before* the first numbered gloss starts).
    current_line_start: int | None = None
    current_line_end: int | None = None
    seen_gloss_for_poem = False

    for page_num, block in blocks:
        if page_num != current_page:
            current_page = page_num
            if page_poems and page_poems.get(page_num):
                new_poems = list(page_poems[page_num])
                if new_poems != current_poems:
                    current_poems = new_poems
                    current_line_start = current_line_end = None
                    seen_gloss_for_poem = False

        poem_nums = find_block_opening_poem(block)
        if not poem_nums:
            incipit_refs = find_incipit_references(block, incipit_index)
            if incipit_refs:
                poem_nums = incipit_refs[0].poem_nums

        if poem_nums and poem_nums != current_poems:
            current_poems = poem_nums
            current_line_start = current_line_end = None
            seen_gloss_for_poem = False

        if not current_poems:
            continue

        line_start, line_end = find_line_range(block)
        if line_start is not None:
            current_line_start, current_line_end = line_start, line_end
            seen_gloss_for_poem = True
        elif seen_gloss_for_poem:
            line_start, line_end = current_line_start, current_line_end

        segments.append(
            Segment(
                poem_nums=list(current_poems),
                text=block,
                line_start=line_start,
                line_end=line_end,
                page=page_num,
                confidence="auto",
            )
        )

    return segments


def make_template(doc_id: str, blocks: list[tuple[int, str]]) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    template_path = REVIEW_DIR / f"{doc_id}_template.json"
    payload = {
        "id": doc_id,
        "author": "UNKNOWN",
        "language": "latin",
        "year": None,
        "source_pdf": f"data/raw/commentaries/{doc_id}.pdf",
        "segments": [
            {
                "poem_nums": [],
                "line_start": None,
                "line_end": None,
                "page": page,
                "confidence": "manual",
                "text": text,
            }
            for page, text in blocks[:50]
        ],
    }
    template_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return template_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("doc_id", help="Document id (PDF stem)")
    parser.add_argument(
        "--mode",
        choices=("auto", "template"),
        default="auto",
        help="auto: heuristic alignment; template: write review JSON",
    )
    parser.add_argument("--author", default="UNKNOWN")
    parser.add_argument("--language", default="latin")
    parser.add_argument("--year", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=COMMENTARIES_DIR / "{doc_id}.json",
        help="Output commentary JSON",
    )
    args = parser.parse_args()

    pages = load_pages(args.doc_id)
    blocks = split_blocks(pages)
    page_poems = {page_num: extract_page_poem(text) for page_num, text in pages}
    page_poems = {k: v for k, v in page_poems.items() if v}

    if args.mode == "template":
        path = make_template(args.doc_id, blocks)
        print(f"Wrote review template with {min(len(blocks), 50)} blocks: {path}")
        return

    poems = load_canzoniere(CANZONIERE_JSON)
    incipit_index = build_incipit_index({p.poem_num: p.incipit for p in poems})
    segments = auto_segments(blocks, incipit_index, page_poems=page_poems)

    commentary = Commentary(
        id=args.doc_id,
        author=args.author,
        language=args.language,
        year=args.year,
        source_pdf=f"data/raw/commentaries/{args.doc_id}.pdf",
        segments=segments,
    )

    output = Path(str(args.output).format(doc_id=args.doc_id))
    save_commentary(commentary, output)
    print(f"Wrote {len(segments)} auto segments to {output}")


if __name__ == "__main__":
    main()
