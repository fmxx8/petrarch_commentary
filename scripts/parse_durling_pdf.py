#!/usr/bin/env python3
"""Extract poems and scholarly notes from Robert M. Durling's bilingual PDF edition
(Petrarch's Lyric Poems: The Rime sparse and Other Lyrics, Harvard UP, 1976).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.models import Commentary, Poem, Segment, save_canzoniere, save_commentary
from petrarch_search.paths import CANZONIERE_JSON, COMMENTARIES_DIR, RAW_COMMENTARIES_DIR

try:
    import fitz
except ImportError as exc:
    raise SystemExit("Install pymupdf: pip install pymupdf") from exc

DEFAULT_PDF = RAW_COMMENTARIES_DIR / "durling_rime_sparse_1976.pdf"
LAST_POEM = 366
# This PDF is a scan with an OCR text layer, so the poem numbers -- set in a
# large display face -- decode to junk ("DS", "Juli", "%") or, worse, to a
# plausible but wrong digit. Their *size* survives intact, which is what we key
# on: verse is 8-10pt, the poem number ~20pt.
NUMBER_SIZE = 14.0
# Line-count digits the edition prints beside the verse, in the right margin.
MARGIN_X_RATIO = 0.7
# Running header and page number live above this.
HEADER_Y = 30.0
# One printed verse occasionally arrives as two fragments on the same baseline.
BASELINE_TOLERANCE = 4.0
POEMS_HEADER = re.compile(r"^Poems?\s+(\d+)(?:\s*[-–]\s*(\d+))?$", re.I)
POEM_HEADER = re.compile(r"^Poem\s+(\d+)\s*$", re.I)
POEM_NUM_LINE = re.compile(r"^(\d{1,3})$")
RIME_HEADER = re.compile(r"Rimes?\s*sparse", re.I)
ITALIAN_MARKERS = re.compile(
    r"\b(ch'|ché|et |né |sì |perché|donna|Amor|l'|d'|s'|quand'|onde|già)\b",
    re.I,
)
ENGLISH_MARKERS = re.compile(r"\b(the |and |that |with |when |from |which )\b", re.I)
GLOSS_HEAD = re.compile(
    r"(?:^|\s)"
    r"((?:[a-z][a-z'’\- ]{1,45}|King|your enemy|blessed wise virgins|Tartarean gates))"
    r":\s*",
    re.I,
)
# A "." immediately followed by a digit (optionally after whitespace) is an
# abbreviation like "p. 609" (page number), not a sentence end -- without this
# exception the note was truncated mid-citation, e.g. "...(Appendix One, p."
# instead of "...(Appendix One, p. 609)."
_NOT_SENTENCE_END = r"(?:[^.]|\.(?=\s*\d))+\."
INTRO_NOTE = re.compile(
    rf"(The recipients? of this poem{_NOT_SENTENCE_END}|A reply, using the same rhymes{_NOT_SENTENCE_END})",
    re.I,
)
SCHOLARLY_MARKERS = re.compile(
    r"\b(Ovid|Virgil|Dante|Matthew|Genesis|Metamorphoses|B\.C\.|myth|goddess|according to|allusion|parable|Christ|Satan|Jupiter|Apollo)\b",
    re.I,
)
FALSE_GLOSS_HEADS = frozenset(
    {
        "hearing",
        "and",
        "no",
        "so",
        "how",
        "who",
        "but",
        "if",
        "then",
        "there",
        "thus",
        "come",
        "death",
        "words",
        "she",
        "what",
        "when",
        "where",
        "while",
        "because",
        "nor",
        "yet",
        "still",
        "also",
    }
)


def clean_line(line: str) -> str:
    line = line.strip()
    line = line.replace("\u2019", "'").replace("\u2018", "'").replace("'", "'")
    return re.sub(r"\s+", " ", line)


def normalize_note_text(text: str) -> str:
    text = clean_line(text)
    text = re.sub(r"(\w)\.\s+(?:\.\s+)+(\w)", r"\1...\2", text)
    text = re.sub(r"\s*\.\s*\.\s*", "..", text)
    return text


def is_valid_gloss(head: str, body: str) -> bool:
    head_key = head.strip().lower()
    first_word = head_key.split()[0] if head_key else ""
    if first_word in FALSE_GLOSS_HEADS or head_key in FALSE_GLOSS_HEADS:
        return False
    if body.lstrip().startswith(('"', "“", "'")):
        return False
    if SCHOLARLY_MARKERS.search(body):
        return True
    if len(head_key.split()) > 5:
        return False
    return len(body) <= 220


def is_italian_verse(line: str) -> bool:
    line = clean_line(line)
    if len(line) < 8:
        return False
    if line.isdigit() or re.match(r"^(Poem|Poems|Rime sparse|Introduction)", line, re.I):
        return False
    if ENGLISH_MARKERS.search(line) and not ITALIAN_MARKERS.search(line):
        return False
    if ITALIAN_MARKERS.search(line):
        return True
    if re.search(r"\b(il|lo|la|di|che|non|per|con|una|sono|donna|Amor)\b", line, re.I):
        return True
    return "'" in line and len(line.split()) >= 4


# is_italian_verse() above disambiguates Italian verse from English commentary
# on mixed-content pages, but it's too strict for the *confirmed* Italian-poem
# pages handled by parse_italian_poems(): a stray font/ligature quirk in this
# PDF occasionally drops the apostrophe out of elisions (e.g. "e 'l vago lume"
# extracts as "e 1 vago lume"), and short lines without any contraction at all
# (e.g. "uno spirto celeste, un vivo sole") match none of its keyword markers,
# so real verses were silently dropped. Once we already know we're inside a
# "Poems N-M" / "Poem N" block, trust every line except the junk we can name:
# headers, margin line-count digits, and their occasional garbled OCR-of-font
# renderings (always very short, e.g. "al"/"sui" standing in for "11").
# The same font quirk that drops the apostrophe out of "'l" (apocope of "il",
# extremely common in Petrarch's Italian) renders it in several broken ways:
# a bare digit "1" mid-line ("e 'l vago" -> "e 1 vago"), the two-char combo
# "'1" (apostrophe + digit one) or "'I" (apostrophe + capital I), or digit-1
# standing in for the letter before a genuine apostrophe ("l'altra" -> "1'altra").
# A literal digit never appears in running verse text (margin line-count
# digits are filtered out separately as standalone lines), so these are safe
# to normalize back to "'l" / "l'".
APOSTROPHE_THEN_L_ARTIFACT = re.compile(r"'[1I]\b")
# "]" and "|" never occur in the verse, so they need no word-boundary guard.
BRACKET_L_ARTIFACT = re.compile(r"['/|][\]|]+")
# The same elision also comes out doubled ("'11", "'1l") or with a bracket
# trailing an already correct "'l".
DOUBLED_L_ARTIFACT = re.compile(r"'[1lI][1lI\]|]+(?![A-Za-z])")
# A bracket glued to the end of a word swallowed the following "'l".
TRAILING_BRACKET_L_ARTIFACT = re.compile(r"(?<=\w)\](?=\s)")
L_THEN_APOSTROPHE_ARTIFACT = re.compile(r"(?<![\w])[1|]'")
BARE_DIGIT_L_ARTIFACT = re.compile(r"(?<![\w'])[1|](?!['\w])")
# Lone digits stand for letters: "6" for "ò" (Petrarch's "ho"), "0" for "o".
BARE_O_GRAVE_ARTIFACT = re.compile(r"(?<![\w'])6(?![\w])")
BARE_O_ARTIFACT = re.compile(r"(?<![\w'])0(?![\w])")
# Curly and straight apostrophes both land on the same elision ("l'’amorose").
DOUBLED_APOSTROPHE = re.compile(r"''+")


def fix_apostrophe_l(line: str) -> str:
    line = DOUBLED_L_ARTIFACT.sub("'l", line)
    line = BRACKET_L_ARTIFACT.sub("'l", line)
    line = TRAILING_BRACKET_L_ARTIFACT.sub(" 'l", line)
    line = APOSTROPHE_THEN_L_ARTIFACT.sub("'l", line)
    line = L_THEN_APOSTROPHE_ARTIFACT.sub("l'", line)
    line = BARE_DIGIT_L_ARTIFACT.sub("'l", line)
    line = BARE_O_GRAVE_ARTIFACT.sub("ò", line)
    line = BARE_O_ARTIFACT.sub("o", line)
    return DOUBLED_APOSTROPHE.sub("'", line)


def is_verse_line(line: str) -> bool:
    """Called only on the verse column of a confirmed Italian page, where the
    header, the margin counters and the oversized poem number have already been
    excluded by position. Anything left is verse -- including the short settenari
    of the canzoni, which a word-count threshold would throw away."""
    line = clean_line(line)
    if len(line) < 3 or line.isdigit():
        return False
    if re.match(r"^(Poem|Poems|Rime sparse|Introduction|Index)", line, re.I):
        return False
    return any(ch.isalpha() for ch in line)


def is_italian_poem_page(text: str) -> bool:
    if "Poems Excluded" in text:
        return False
    if POEMS_HEADER.search(text) or POEM_HEADER.search(text):
        return True
    italian = len(ITALIAN_MARKERS.findall(text))
    english = len(ENGLISH_MARKERS.findall(text))
    return italian >= 3 and italian > english


def has_rime_sparse_header(text: str) -> bool:
    return any(RIME_HEADER.fullmatch(clean_line(line)) for line in text.splitlines())


def is_english_notes_page(text: str) -> bool:
    if "Poems Excluded" in text:
        return False
    if is_italian_poem_page(text):
        return False
    return has_rime_sparse_header(text)


def explicit_poems_on_page(text: str) -> list[int]:
    nums: list[int] = []
    for line in text.splitlines():
        cleaned = clean_line(line)
        if POEMS_HEADER.match(cleaned):
            start = int(POEMS_HEADER.match(cleaned).group(1))
            end = int(POEMS_HEADER.match(cleaned).group(2) or start)
            nums.extend(range(start, end + 1))
            continue
        poem_match = POEM_HEADER.match(cleaned)
        if poem_match:
            nums.append(int(poem_match.group(1)))
    return sorted(set(nums))


def build_page_poem_map(doc: fitz.Document, start_page: int) -> dict[int, list[int]]:
    page_poems: dict[int, list[int]] = {}
    current: list[int] = []

    for page_index in range(start_page, doc.page_count):
        text = doc[page_index].get_text("text")
        if "Poems Excluded" in text:
            break

        explicit = explicit_poems_on_page(text)
        if explicit:
            current = explicit
        elif is_italian_poem_page(text):
            pass

        page_poems[page_index + 1] = list(current)

    return page_poems


def split_english_page(text: str) -> list[tuple[int | None, str]]:
    lines = [clean_line(x) for x in text.splitlines()]
    lines = [x for x in lines if x]

    start = 0
    while start < len(lines):
        if lines[start].isdigit() or RIME_HEADER.fullmatch(lines[start]):
            start += 1
            continue
        break

    sections: list[tuple[int | None, list[str]]] = []
    current_poem: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        if current_lines:
            sections.append((current_poem, current_lines))
            current_lines = []

    for line in lines[start:]:
        num_match = POEM_NUM_LINE.match(line)
        if num_match:
            num = int(num_match.group(1))
            if 1 <= num <= 366:
                if current_lines:
                    flush()
                current_poem = num
                continue
        current_lines.append(line)

    flush()
    return [(poem, normalize_note_text(" ".join(chunk))) for poem, chunk in sections]


def extract_glosses(text: str) -> list[str]:
    notes: list[str] = []
    for match in GLOSS_HEAD.finditer(text):
        head = match.group(1).strip()
        start = match.end()
        next_match = GLOSS_HEAD.search(text, start)
        end = next_match.start() if next_match else len(text)
        body = normalize_note_text(text[start:end])
        if len(body) < 20:
            continue
        if is_italian_verse(body):
            continue
        if not is_valid_gloss(head, body):
            continue
        notes.append(f"{head}: {body}")
    return notes


def extract_intro_notes(text: str) -> list[str]:
    return [normalize_note_text(match.group(1)) for match in INTRO_NOTE.finditer(text)]


def extract_notes_from_section(section_text: str) -> list[str]:
    if len(section_text) < 30:
        return []

    notes: list[str] = []
    notes.extend(extract_intro_notes(section_text))
    notes.extend(extract_glosses(section_text))

    seen: set[str] = set()
    unique: list[str] = []
    for note in notes:
        if note not in seen:
            seen.add(note)
            unique.append(note)
    return unique


Item = tuple[float, float, float, str]  # (top, left, font size, text)


def page_items(page: fitz.Page) -> list[Item]:
    items: list[Item] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = clean_line("".join(s["text"] for s in spans))
            if not text:
                continue
            items.append((
                min(s["bbox"][1] for s in spans),
                min(s["bbox"][0] for s in spans),
                max(s["size"] for s in spans),
                text,
            ))
    items.sort()
    return items


def merge_baselines(items: list[Item]) -> list[Item]:
    merged: list[Item] = []
    for top, left, size, text in sorted(items, key=lambda i: (i[0], i[1])):
        if merged and abs(top - merged[-1][0]) <= BASELINE_TOLERANCE:
            prev_top, prev_left, prev_size, prev_text = merged[-1]
            merged[-1] = (
                min(prev_top, top),
                min(prev_left, left),
                max(prev_size, size),
                f"{prev_text} {text}",
            )
        else:
            merged.append((top, left, size, text))
    return merged


def is_translation_page(items: list[Item], body: str) -> bool:
    header = "".join(t for top, _l, _s, t in items if top < HEADER_Y).lower().replace(" ", "")
    if "rimesparse" in header:
        return True
    # Some facing-translation pages lose their running header into the body,
    # so fall back to which language the page is actually written in.
    return len(ENGLISH_MARKERS.findall(body)) > len(ITALIAN_MARKERS.findall(body))


def header_range(items: list[Item]) -> tuple[int, int] | None:
    for top, _left, _size, text in items:
        if top >= HEADER_Y:
            break
        match = POEMS_HEADER.match(text)
        if match:
            return int(match.group(1)), int(match.group(2) or match.group(1))
    return None


def owner_of(starts: list[tuple[float, int]], top: float, current: int | None) -> int | None:
    """Which poem a line belongs to, given where the poem numbers sit."""
    owner = None
    for start_top, number in starts:
        if top > start_top:
            owner = number
    if owner is not None:
        return owner
    # Above the first number on the page: still the canzone carried over the
    # page break.
    return starts[0][1] - 1 if starts else current


def parse_italian_poems(doc: fitz.Document, start_page: int = 54) -> dict[int, list[str]]:
    """Poem numbers are taken from position in the book rather than from the
    unreliable OCR of the number itself, and resynchronised against the running
    header whenever it survives."""
    poems: dict[int, list[str]] = {}
    current: int | None = None

    for page_index in range(start_page, doc.page_count):
        page = doc[page_index]
        if "Poems Excluded" in page.get_text("text"):
            break

        items = page_items(page)
        if not items:
            continue

        margin_x = page.rect.width * MARGIN_X_RATIO
        body = " ".join(t for top, _l, size, t in items if top >= HEADER_Y and size <= NUMBER_SIZE)
        if is_translation_page(items, body):
            continue

        column = merge_baselines(
            [item for item in items if item[0] >= HEADER_Y and item[1] < margin_x]
        )
        markers = [(top, text) for top, _l, size, text in column if size > NUMBER_SIZE]
        # A verso page carries at most two poems; more means the page is a
        # scanning artefact whose entire text layer is noise.
        if len(markers) > 2:
            continue

        verses = [
            (top, text)
            for top, _l, size, text in column
            if size <= NUMBER_SIZE and is_verse_line(text)
        ]
        if not verses:
            continue

        expected = header_range(items)
        starts: list[tuple[float, int]] = []
        for position, (marker_top, _text) in enumerate(markers):
            number = (current or 0) + 1
            if number > LAST_POEM:
                break
            if expected and position == 0 and not (expected[0] <= number <= expected[1]):
                number = expected[0]
            current = number
            poems.setdefault(number, [])
            starts.append((marker_top, number))

        if current is None:
            continue

        for verse_top, text in verses:
            owner = owner_of(starts, verse_top, current)
            if owner is not None and 1 <= owner <= LAST_POEM:
                poems.setdefault(owner, []).append(fix_apostrophe_l(text))

    return poems


def parse_durling_commentary(doc: fitz.Document, italian_start: int = 54) -> tuple[dict[int, list[str]], Commentary]:
    poems = parse_italian_poems(doc, start_page=italian_start)
    page_poems = build_page_poem_map(doc, italian_start)

    segments: list[Segment] = []
    seen: set[tuple[int, str]] = set()

    for page_index in range(italian_start, doc.page_count):
        text = doc[page_index].get_text("text")
        if "Poems Excluded" in text:
            break
        if not is_english_notes_page(text):
            continue

        page_num = page_index + 1
        default_poems = page_poems.get(page_num, [])
        default_poem = default_poems[-1] if default_poems else None

        for section_poem, section_text in split_english_page(text):
            poem_num = section_poem or default_poem
            if poem_num is None:
                continue

            for note in extract_notes_from_section(section_text):
                key = (poem_num, note)
                if key in seen:
                    continue
                seen.add(key)
                segments.append(
                    Segment(
                        poem_nums=[poem_num],
                        text=note,
                        page=page_num,
                        confidence="auto",
                    )
                )

    commentary = Commentary(
        id="durling_rime_sparse_1976",
        author="Robert M. Durling",
        year="1976",
        language="english",
        source_pdf="data/raw/commentaries/durling_rime_sparse_1976.pdf",
        edition_note=(
            "Scholarly notes and glosses from Durling's bilingual edition "
            "(Petrarch's Lyric Poems: The Rime sparse and Other Lyrics, Harvard UP, 1976). "
            "Commentary is poem-level only (no line numbers)."
        ),
        segments=segments,
    )
    return poems, commentary


# The Canzoniere's metrical make-up is fixed and well established; deriving it
# from line counts instead would mislabel every ballata and madrigale.
SESTINE = frozenset({22, 30, 66, 80, 142, 214, 237, 239, 332})
MADRIGALS = frozenset({52, 54, 106, 121})
BALLATE = frozenset({11, 14, 55, 59, 63, 149, 324})
CANZONI = frozenset({
    23, 28, 29, 37, 50, 53, 70, 71, 72, 73, 105, 119, 125, 126, 127, 128, 129,
    135, 206, 207, 264, 268, 270, 323, 325, 331, 359, 360, 366,
})


def poem_form(num: int) -> str:
    if num in SESTINE:
        return "sestina"
    if num in MADRIGALS:
        return "madrigal"
    if num in BALLATE:
        return "ballata"
    if num in CANZONI:
        return "canzone"
    return "sonnet"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--canzoniere-out", type=Path, default=CANZONIERE_JSON)
    parser.add_argument(
        "--commentary-out",
        type=Path,
        default=COMMENTARIES_DIR / "durling_rime_sparse_1976.json",
    )
    parser.add_argument("--italian-start-page", type=int, default=55)
    args = parser.parse_args()

    if not args.pdf.exists():
        raise SystemExit(f"PDF not found: {args.pdf}")

    doc = fitz.open(args.pdf)
    poems_dict, commentary = parse_durling_commentary(doc, italian_start=args.italian_start_page - 1)

    poem_objs = [
        Poem(
            poem_num=num,
            incipit=lines[0],
            lines=lines,
            form=poem_form(num),
        )
        for num, lines in sorted(poems_dict.items())
        if lines
    ]

    save_canzoniere(poem_objs, args.canzoniere_out)
    save_commentary(commentary, args.commentary_out)

    poems_with_notes = len({n for seg in commentary.segments for n in seg.poem_nums})
    print(f"Poems extracted: {len(poem_objs)}")
    print(f"Note segments: {len(commentary.segments)}")
    print(f"Poems with at least one note: {poems_with_notes}")
    if poem_objs:
        print(f"Poem 1 incipit: {poem_objs[0].incipit[:60]}...")
    print(f"Wrote {args.canzoniere_out}")
    print(f"Wrote {args.commentary_out}")


if __name__ == "__main__":
    main()
