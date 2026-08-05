#!/usr/bin/env python3
"""Parse Marco Santagata's Canzoniere commentary (Mondadori, 1996/2004) into
line-level commentary segments.

Combines two OCR'd volumes (vol.1 covers roughly poems 1-?, vol.2 continues
to 366 plus Notes). Pages alternate between poem text (skipped) and prose
commentary; running headers like "Parte prima 142" / "Parte seconda 310"
mark which poem the surrounding pages discuss.

Format inside the commentary, one block per blank-line-separated paragraph:

    Co. E particolarmente importante riuscire a datare questo testo ...
    Sonetto su 5 rime a schema ABBA ABBA CDE CDE: ...
    BIBL.: Noferi 1974; ...

    1. VOI CH'ASCOLTATE: l'apostrofe ai lettori ... Q ALTRA SUA GLOSSA: ...
    2. SOSPIRI: ...
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.align_heuristics import (
    GLOSS_LINE_PATTERN,
    GLOSS_RANGE_PATTERN,
    extract_page_poem_santagata,
    find_line_range,
    looks_like_commentary,
)
from petrarch_search.models import Commentary, Segment, save_commentary
from petrarch_search.paths import COMMENTARIES_DIR, PAGES_DIR

BLOCK_SPLIT = re.compile(r"\n{2,}")
MIN_BLOCK_LEN = 65

# Vol. 2 ends with concordance / index pages (like Bettarini's back matter).
# OCR blocks there look like "278\n30: 272: (1) 315..." and were mis-tagged
# as poem-level commentary.
SANTAGATA_BACK_MATTER = re.compile(
    r"^(Indic[ei]|Indice dei|Tavola metrica|INDICE ALFABETICO|TAVOLA METRICA)\b",
    re.I,
)


def is_back_matter_page(text: str) -> bool:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first:
        return False
    if SANTAGATA_BACK_MATTER.search(first):
        return True
    return "indici e tavole" in first.lower()


def drop_back_matter(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    for i, (_, text) in enumerate(pages):
        if is_back_matter_page(text):
            return pages[:i]
    return pages


def load_pages(doc_id: str, pages_dir: Path = PAGES_DIR) -> list[tuple[int, str]]:
    page_dir = pages_dir / doc_id
    if not page_dir.exists():
        raise FileNotFoundError(f"No OCR output for {doc_id} in {page_dir}")
    pages: list[tuple[int, str]] = []
    for path in sorted(page_dir.glob("page_*.txt")):
        page_num = int(path.stem.split("_")[1])
        pages.append((page_num, path.read_text(encoding="utf-8")))
    return drop_back_matter(pages)


def split_blocks(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Split into paragraph blocks, dropping short fragments (mostly OCR'd
    poem verses, which this edition prints with generous line spacing so
    each verse becomes its own blank-line-separated "block")."""
    blocks: list[tuple[int, str]] = []
    for page_num, text in pages:
        for block in BLOCK_SPLIT.split(text):
            cleaned = " ".join(block.strip().splitlines()).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            if not cleaned:
                continue
            if GLOSS_LINE_PATTERN.match(cleaned) or GLOSS_RANGE_PATTERN.match(cleaned):
                blocks.append((page_num, cleaned))
                continue
            if len(cleaned) < MIN_BLOCK_LEN:
                continue
            if not looks_like_commentary(cleaned):
                continue
            blocks.append((page_num, cleaned))
    return blocks


def auto_segments(
    blocks: list[tuple[int, str]],
    page_poems: dict[int, list[int]],
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
            if page_poems.get(page_num):
                new_poems = list(page_poems[page_num])
                if new_poems != current_poems:
                    current_poems = new_poems
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


def process_volume(doc_id: str) -> list[Segment]:
    pages = load_pages(doc_id)
    blocks = split_blocks(pages)
    page_poems = {page_num: extract_page_poem_santagata(text) for page_num, text in pages}
    page_poems = {k: v for k, v in page_poems.items() if v}
    return auto_segments(blocks, page_poems)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--volumes",
        nargs="+",
        default=["santagata_canzoniere_vol1", "santagata_canzoniere_vol2"],
        help="Doc ids (PDF stems) to process, in order",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=COMMENTARIES_DIR / "santagata_canzoniere_1996.json",
    )
    args = parser.parse_args()

    all_segments: list[Segment] = []
    for doc_id in args.volumes:
        segs = process_volume(doc_id)
        print(f"{doc_id}: {len(segs)} segments")
        all_segments.extend(segs)

    commentary = Commentary(
        id="santagata_canzoniere_1996",
        author="Marco Santagata",
        language="italian",
        year="1996/2004",
        source_pdf=f"data/raw/commentaries/{args.volumes[0]}.pdf",
        edition_note=(
            "Canzoniere, a cura di Marco Santagata (Mondadori, 1996; ed. "
            "aggiornata 2004). Vol. 1-2 OCR'd and auto-aligned by poem/line."
        ),
        segments=all_segments,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_commentary(commentary, args.output)

    poems_covered = len({n for seg in all_segments for n in seg.poem_nums})
    with_lines = sum(1 for seg in all_segments if seg.line_start is not None)
    print(f"\nTotal segments: {len(all_segments)} (with line numbers: {with_lines})")
    print(f"Poems covered: {poems_covered}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
