from __future__ import annotations

import re
from dataclasses import dataclass

ROMAN_VALUES = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}

# Short tokens that are valid Roman glyphs but usually Italian words in commentary prose.
ROMAN_STOPWORDS = frozenset(
    {
        "DI",
        "IL",
        "MI",
        "VI",
        "ID",
        "IN",
        "IT",
        "IS",
        "US",
        "AD",
        "AC",
        "DC",
        "ET",
        "EX",
        "IV",
    }
)

MAX_POEM_NUM = 366

# Shared "is this block real prose commentary, or just OCR'd poem verse that
# leaked through as its own paragraph?" heuristic. Both Bettarini and Santagata
# print the poem text and the apparatus on the same pages with generous line
# spacing, so a verse (or a pair of merged verses) often becomes its own
# blank-line-separated "block" with no commentary content at all. Real prose
# notes are either long, or cite a year / cross-reference / metrical term.
MIN_BLOCK_LEN_LONG = 120
YEAR_TOKEN = re.compile(r"\b(1[4-9]\d{2}|20[0-2]\d)\b")
COMMENTARY_MARKERS = re.compile(
    r"\bBIBL\b|\bcfr?\.|\bnota\b|\bSchema\b|\brima[e]?\b|\bSonetto\b|\bCanzone\b|"
    r"\bBallata\b|\bMadrigale\b|\bSestina\b|\bstrofe\b|\bsenhal\b",
    re.IGNORECASE,
)

# A word broken across a line by a hyphen ("filtra-\nti", "secon-\ndo") is a
# print/OCR justification artifact unique to prose set to the page's full
# width; poetry is always typeset one complete verse per line, so a genuine
# verse block never has one. Strong, format-based signal that survives even
# when a block is long enough to otherwise pass on length alone (canzoni have
# multi-line stanzas, so a whole stanza can exceed MIN_BLOCK_LEN_LONG while
# still being pure poem text with zero commentary content).
PROSE_LINE_WRAP = re.compile(r"\w-\s*\n\s*\w")
MAX_VERSE_LINE_LEN = 70


def looks_like_commentary(text: str) -> bool:
    """Drop OCR'd poem verses/stanzas printed as their own blank-line-separated
    block: real commentary prose almost always cites a year, 'cf./cfr.', a
    'nota a...' cross-reference, a bibliography entry, or a metrical-note
    keyword -- or, being typeset to the page's full width, has at least one
    line break that splits a word with a hyphen. Lacking any of those, a block
    made up entirely of short lines (no single line near a full text-width
    line) is treated as leaked verse regardless of its total length."""
    if YEAR_TOKEN.search(text) or COMMENTARY_MARKERS.search(text):
        return True
    if PROSE_LINE_WRAP.search(text):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(len(ln) <= MAX_VERSE_LINE_LEN for ln in lines):
        return False
    return len(text) >= MIN_BLOCK_LEN_LONG


def roman_to_int(value: str) -> int | None:
    value = value.upper().strip()
    if not value or not re.fullmatch(r"[IVXLCDM]+", value):
        return None
    total = 0
    prev = 0
    for ch in reversed(value):
        current = ROMAN_VALUES.get(ch)
        if current is None:
            return None
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total if total > 0 else None


@dataclass
class PoemReference:
    poem_nums: list[int]
    line_start: int | None = None
    line_end: int | None = None
    marker: str = ""
    confidence: str = "auto"


POEM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "rvf_num",
        re.compile(
            r"\b(?:R\.?\s*V\.?\s*F\.?|Rvf|Rerum vulgarium fragmenta)\s*"
            r"(?:n(?:um(?:ero)?)?\.?\s*)?(\d{1,3})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sonetto_roman",
        re.compile(
            r"(?i)\b(?:sonetto|sonet(?:to)?|sonn?\.?)\s+([IVXLCDM]{2,})\b",
        ),
    ),
    (
        "sonetto_arabic",
        re.compile(
            r"\b(?:sonetto|sonet(?:to)?|sonn?\.?)\s+(\d{1,3})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "canzone_roman",
        re.compile(
            r"(?i)\b(?:canzone|canz\.?)\s+(?!di\b|del\b|della\b|dei\b|degli\b|delle\b|da\b)"
            r"([IVXLCDM]{2,})\b",
        ),
    ),
    (
        "canzone_arabic",
        re.compile(
            r"\b(?:canzone|canz\.?)\s+(\d{1,3})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "poem_hash",
        re.compile(r"\b(?:poem|poesia|carmen)\s*#?\s*(\d{1,3})\b", re.IGNORECASE),
    ),
]

LINE_PATTERN = re.compile(
    r"\b(?:vv?\.?|vers[oi]|line[ae]?)\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\b",
    re.IGNORECASE,
)

# LINE_PATTERN is a weak fallback: a real single-line gloss conventionally opens
# with its own "v. N" / "vv. N-M" reference, but long discursive notes (general
# intro to a whole poem, cross-poem comparisons, etc.) often cite an unrelated
# line deep in their prose -- e.g. a poem-level intro mentioning "(vv. 13 e 10)"
# in passing was getting the *entire* note mistagged as being about line 13.
# Only trust this fallback if the reference appears near the very start of the
# block, the way a genuine gloss heading would.
LINE_PATTERN_MAX_OFFSET = 50

# Bettarini/Santagata line glosses: "13. la dolce vista:" (up to 3 digits for long canzoni, e.g. 366).
GLOSS_LINE_PATTERN = re.compile(r"^(\d{1,3})\.\s+\S", re.MULTILINE)

# Same editions also gloss a *range* of lines under one heading: "9-11. SPERANDO ...
# TANTE: riprende...". Without this, find_line_range() falls through to the much
# looser LINE_PATTERN below, which then grabs the first unrelated "cf. v. N" /
# "v. N" cross-reference it finds anywhere in the gloss's own prose -- e.g. a
# 9-11 gloss that happens to mention "(cf. v. 13)" was getting mistagged as
# line 13 instead of lines 9-11.
GLOSS_RANGE_PATTERN = re.compile(r"^(\d{1,3})[-–](\d{1,3})\.\s+\S", re.MULTILINE)

# Santagata (Mondadori) running header: "Parte prima 142" / "Parte seconda 310".
SANTAGATA_HEADER_PATTERN = re.compile(
    r"^\s*Parte\s+(?:prima|seconda)\s+(\d{1,3})\b",
    re.IGNORECASE | re.MULTILINE,
)

# This edition alternates recto/verso running headers: recto (right) pages
# carry "Parte seconda 320 1233" (poem number + page number), but verso
# (left) pages only print "1232 Canzoniere" -- page number and book title,
# no poem number at all. Since that's frequently the very page where the
# *next* poem's text and head-note (the real general/whole-poem comment)
# actually start, roughly two-thirds of all pages give no poem number via
# the header alone, silently inheriting whatever poem the last header-bearing
# page belonged to -- e.g. poem 320's intro note printed on its verso
# poem-text page was getting tagged as poem 319 (the previous, stale poem).
# Fall back to the bare poem-number line that always precedes the printed
# verses themselves, e.g. a page that opens with header / blank / "320" /
# blank / "Sento l'aura ...".
SANTAGATA_POEM_OPEN_PATTERN = re.compile(r"^\d{1,3}$")


def extract_page_poem_santagata(text: str) -> list[int]:
    """Poem number from Santagata (Mondadori) running headers, e.g. 'Parte
    prima 142', falling back to the bare poem-number line that precedes the
    poem's own text on pages whose running header omits it (see above)."""
    head = text[:200]
    match = SANTAGATA_HEADER_PATTERN.search(head)
    if match:
        num = parse_poem_token(match.group(1))
        if num:
            return [num]

    non_empty = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(non_empty[:8]):
        if not SANTAGATA_POEM_OPEN_PATTERN.match(line):
            continue
        num = parse_poem_token(line)
        if not num:
            continue
        # Book page number immediately before a standalone "Canzoniere" line
        # (e.g. verso "254" / "Canzoniere", not poem 254).
        if i + 1 < len(non_empty) and non_empty[i + 1].lower() == "canzoniere":
            continue
        # Poem number on the line after "NNN Canzoniere" (e.g. "264 Canzoniere" / "51").
        if i > 0 and "canzoniere" in non_empty[i - 1].lower():
            return [num]
    return []

HEADER_PATTERN = re.compile(
    r"^(?:SONETTO|CANZONE|BALLATA|MADRIGALE|SESTINA)\s+([IVXLCDM]{2,}|\d{1,3})\b",
    re.IGNORECASE | re.MULTILINE,
)

# Bettarini/Einaudi edition: running headers and poem openings use uppercase Roman numerals.
BETTARINI_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "bettarini_running_header",
        re.compile(r"^Prima Parte\s+([IVXLCDM]{2,})\s+\d+", re.MULTILINE),
    ),
    (
        "bettarini_second_part_header",
        re.compile(r"^Seconda Parte\s+([IVXLCDM]{2,})\s+\d+", re.MULTILINE),
    ),
    (
        "bettarini_block_open",
        re.compile(r"^([IVXLCDM]{2,})\s*(?:\n|$)"),
    ),
]


def parse_poem_token(token: str) -> int | None:
    token = token.strip()
    if token.isdigit():
        num = int(token)
        return num if 1 <= num <= MAX_POEM_NUM else None
    upper = token.upper()
    if upper in ROMAN_STOPWORDS or len(upper) < 2:
        return None
    num = roman_to_int(token)
    if num is None or not (1 <= num <= MAX_POEM_NUM):
        return None
    return num


def find_poem_references(text: str) -> list[PoemReference]:
    refs: list[PoemReference] = []
    seen: set[tuple[int, ...]] = set()

    for name, pattern in POEM_PATTERNS:
        for match in pattern.finditer(text):
            poem_num = parse_poem_token(match.group(1))
            if poem_num is None:
                continue
            key = (poem_num,)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                PoemReference(
                    poem_nums=[poem_num],
                    marker=f"{name}:{match.group(0)}",
                )
            )

    for match in HEADER_PATTERN.finditer(text):
        poem_num = parse_poem_token(match.group(1))
        if poem_num is None:
            continue
        key = (poem_num,)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            PoemReference(
                poem_nums=[poem_num],
                marker=f"header:{match.group(0)}",
            )
        )

    for name, pattern in BETTARINI_PATTERNS:
        for match in pattern.finditer(text):
            poem_num = parse_poem_token(match.group(1))
            if poem_num is None:
                continue
            key = (poem_num,)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                PoemReference(
                    poem_nums=[poem_num],
                    marker=f"{name}:{match.group(0)}",
                )
            )

    refs.sort(key=lambda r: text.find(r.marker.split(":", 1)[-1]) if r.marker else 0)
    return refs


def extract_page_poem(text: str) -> list[int]:
    """Poem number from Bettarini page headers (running header or opening line)."""
    head = text[:300]
    for ref in find_poem_references(head):
        if ref.marker.startswith(("bettarini_", "header:")):
            return ref.poem_nums
    return []


def find_block_opening_poem(block: str) -> list[int]:
    """Poem number that genuinely opens/continues a commentary block: a running
    header, a bare Roman-numeral heading, or an explicit "SONETTO/CANZONE N"
    line. Deliberately excludes the looser POEM_PATTERNS (rvf_num, sonetto_roman,
    sonetto_arabic, canzone_roman, canzone_arabic, poem_hash) -- those match
    in-prose cross-references like "si configura nel sonetto XC 1-8" (a citation
    to a *different* poem, used to compare rhymes/imagery), which previously got
    mistaken for a new "current poem" and silently re-tagged notes belonging to
    one poem onto whatever poem its footnotes happened to cite first.
    """
    for ref in find_poem_references(block):
        if ref.marker.startswith(("bettarini_", "header:")):
            return ref.poem_nums
    return []


def find_incipit_references(text: str, incipit_index: dict[str, int]) -> list[PoemReference]:
    refs: list[PoemReference] = []
    lowered = text.lower()
    for prefix, poem_num in incipit_index.items():
        if len(prefix) < 12:
            continue
        pos = lowered.find(prefix.lower())
        if pos == -1:
            continue
        snippet = text[pos : pos + min(len(prefix) + 20, 80)]
        refs.append(
            PoemReference(
                poem_nums=[poem_num],
                marker=f"incipit:{snippet[:40]}",
                confidence="auto",
            )
        )
    refs.sort(key=lambda r: lowered.find(r.marker.split(":", 1)[-1][:20].lower()))
    return refs


def find_line_range(text: str) -> tuple[int | None, int | None]:
    range_gloss = GLOSS_RANGE_PATTERN.search(text)
    gloss = GLOSS_LINE_PATTERN.search(text)

    # Both patterns can technically match the same "N." prefix (a range like
    # "9-11." also satisfies single-number patterns' look of digits+period
    # only if mis-anchored), so when both fire, trust whichever one's match
    # starts earlier in the text -- that's the block's own heading, not some
    # later coincidental digit-period sequence.
    if range_gloss and (not gloss or range_gloss.start() <= gloss.start()):
        return int(range_gloss.group(1)), int(range_gloss.group(2))
    if gloss:
        line = int(gloss.group(1))
        return line, line

    match = LINE_PATTERN.search(text)
    if not match or match.start() > LINE_PATTERN_MAX_OFFSET:
        return None, None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    return start, end


def build_incipit_index(incipits: dict[int, str], prefix_len: int = 24) -> dict[str, int]:
    index: dict[str, int] = {}
    for poem_num, incipit in incipits.items():
        prefix = incipit.strip()[:prefix_len]
        if prefix:
            index[prefix] = poem_num
    return index
