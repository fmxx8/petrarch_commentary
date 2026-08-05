#!/usr/bin/env python3
"""Audit commentary alignment: coverage, line bounds, poem assignment, OCR cross-check.

Usage:
    python scripts/validate_alignment.py
    python scripts/validate_alignment.py --json data/review/alignment_audit.json
    python scripts/validate_alignment.py --poem 278 --source santagata
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.align_heuristics import (
    GLOSS_LINE_PATTERN,
    GLOSS_RANGE_PATTERN,
    extract_page_poem,
    extract_page_poem_santagata,
    looks_like_commentary,
)
from petrarch_search.models import Commentary, load_canzoniere, load_commentary
from petrarch_search.paths import CANZONIERE_JSON, COMMENTARIES_DIR, PAGES_DIR

MAX_POEM = 366
GLOSS_HEAD = re.compile(r"^(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\.\s", re.I)
PAGE_NUM_AS_LINE = re.compile(
    r"^(\d{3,4})\.\s+(Canzoniere|Parte|BIBL|Sonetto|Canzone|Schema)",
    re.I,
)

SOURCES = {
    "santagata": {
        "file": "santagata_canzoniere_1996.json",
        "page_extractor": "santagata",
        "ocr_volumes": ["santagata_canzoniere_vol1", "santagata_canzoniere_vol2"],
    },
    "musa": {
        "file": "musa_canzoniere_notes.json",
        "page_extractor": None,
        "ocr_volumes": ["musa_manfredi_canzoniere_1996"],
    },
    "bettarini": {
        "file": "bettarini_canzoniere_2005.json",
        "page_extractor": "bettarini",
        "ocr_volumes": ["bettarini_canzoniere_2005"],
    },
    "durling": {
        "file": "durling_rime_sparse_1976.json",
        "page_extractor": None,
        "ocr_volumes": [],
    },
}


@dataclass
class Issue:
    kind: str
    source: str
    poem_num: int
    line_start: int | None = None
    line_end: int | None = None
    page: int | None = None
    detail: str = ""
    text_preview: str = ""


@dataclass
class SourceReport:
    author: str
    segments: int = 0
    poems_covered: int = 0
    missing_poems: list[int] = field(default_factory=list)
    line_segments: int = 0
    poem_level_segments: int = 0
    line_in_bounds: int = 0
    line_out_of_bounds: int = 0
    header_mismatch: int = 0
    page_as_line: int = 0
    runaway_sonnets: list[dict] = field(default_factory=list)
    clean_sonnets: int = 0
    suspect_sonnets: int = 0
    page_header_mismatch: int = 0
    verse_leak: int = 0
    issues: list[Issue] = field(default_factory=list)


def load_pages(doc_id: str) -> dict[int, str]:
    page_dir = PAGES_DIR / doc_id
    if not page_dir.exists():
        return {}
    out: dict[int, str] = {}
    for path in sorted(page_dir.glob("page_*.txt")):
        out[int(path.stem.split("_")[1])] = path.read_text(encoding="utf-8")
    return out


def page_poem_map(source_key: str, volumes: list[str]) -> dict[int, list[int]]:
    extractor = SOURCES[source_key]["page_extractor"]
    if not extractor or not volumes:
        return {}
    combined: dict[int, list[int]] = {}
    for vol in volumes:
        for page_num, text in load_pages(vol).items():
            if extractor == "santagata":
                nums = extract_page_poem_santagata(text)
            else:
                nums = extract_page_poem(text)
            if nums:
                combined[page_num] = nums
    return combined


def poem_lengths() -> dict[int, int]:
    return {p.poem_num: len(p.lines) for p in load_canzoniere(CANZONIERE_JSON)}


def poem_forms() -> dict[int, str]:
    return {p.poem_num: p.form or "unknown" for p in load_canzoniere(CANZONIERE_JSON)}


def anchor_lines() -> dict[int, list[str]]:
    return {p.poem_num: p.lines for p in load_canzoniere(CANZONIERE_JSON)}


def audit_source(
    source_key: str,
    poem_lens: dict[int, int],
    poem_forms: dict[int, str],
    anchor: dict[int, list[str]],
    *,
    poem_filter: int | None = None,
    max_issues: int = 200,
) -> SourceReport:
    meta = SOURCES[source_key]
    commentary: Commentary = load_commentary(COMMENTARIES_DIR / meta["file"])
    report = SourceReport(author=commentary.author)
    page_map = page_poem_map(source_key, meta["ocr_volumes"])

    by_poem: dict[int, list] = defaultdict(list)
    for seg in commentary.segments:
        if poem_filter is not None and poem_filter not in seg.poem_nums:
            continue
        for n in seg.poem_nums:
            by_poem[n].append(seg)

    report.segments = len(commentary.segments) if poem_filter is None else sum(len(v) for v in by_poem.values())
    report.poems_covered = len(by_poem)
    report.missing_poems = [n for n in range(1, MAX_POEM + 1) if n not in by_poem]
    report.line_segments = sum(1 for s in commentary.segments if s.line_start and (poem_filter is None or poem_filter in s.poem_nums))
    report.poem_level_segments = report.segments - report.line_segments

    for seg in commentary.segments:
        if poem_filter is not None and poem_filter not in seg.poem_nums:
            continue
        n = seg.poem_nums[0]
        pl = poem_lens.get(n, 0)

        # Line bounds
        if seg.line_start is not None:
            le = seg.line_end or seg.line_start
            if pl and (seg.line_start < 1 or le > pl):
                report.line_out_of_bounds += 1
                if len(report.issues) < max_issues:
                    report.issues.append(
                        Issue(
                            kind="line_out_of_bounds",
                            source=source_key,
                            poem_num=n,
                            line_start=seg.line_start,
                            line_end=le,
                            page=seg.page,
                            detail=f"gloss {seg.line_start}-{le}, poem has {pl} lines ({poem_forms.get(n)})",
                            text_preview=seg.text[:100],
                        )
                    )
            else:
                report.line_in_bounds += 1

            # Gloss header vs metadata
            m = GLOSS_HEAD.match(seg.text.strip())
            if m:
                h1, h2 = int(m.group(1)), int(m.group(2) or m.group(1))
                if h1 != seg.line_start or h2 != (seg.line_end or seg.line_start):
                    report.header_mismatch += 1
                    if len(report.issues) < max_issues:
                        report.issues.append(
                            Issue(
                                kind="header_mismatch",
                                source=source_key,
                                poem_num=n,
                                line_start=seg.line_start,
                                line_end=seg.line_end,
                                page=seg.page,
                                detail=f"text says {h1}-{h2}, metadata {seg.line_start}-{seg.line_end}",
                                text_preview=seg.text[:80],
                            )
                        )

            if PAGE_NUM_AS_LINE.match(seg.text.strip()):
                report.page_as_line += 1
                if len(report.issues) < max_issues:
                    report.issues.append(
                        Issue(
                            kind="page_as_line",
                            source=source_key,
                            poem_num=n,
                            line_start=seg.line_start,
                            page=seg.page,
                            detail="line number looks like a book page number",
                            text_preview=seg.text[:80],
                        )
                    )

        # Page header cross-check (Santagata/Bettarini only)
        if page_map and seg.page and seg.line_start is not None:
            header_poems = page_map.get(seg.page, [])
            if header_poems and n not in header_poems:
                report.page_header_mismatch += 1
                if len(report.issues) < max_issues:
                    report.issues.append(
                        Issue(
                            kind="page_header_mismatch",
                            source=source_key,
                            poem_num=n,
                            line_start=seg.line_start,
                            page=seg.page,
                            detail=f"segment poem {n}, OCR page header says {header_poems}",
                            text_preview=seg.text[:80],
                        )
                    )

        # Verse leak: segment text matches anchor line but isn't commentary
        if len(seg.text) > 30 and not looks_like_commentary(seg.text):
            for line in anchor.get(n, []):
                if len(line) > 20 and line[:30].lower() in seg.text.lower()[:100]:
                    report.verse_leak += 1
                    if len(report.issues) < max_issues:
                        report.issues.append(
                            Issue(
                                kind="verse_leak",
                                source=source_key,
                                poem_num=n,
                                line_start=seg.line_start,
                                page=seg.page,
                                detail="segment looks like poem verse, not commentary",
                                text_preview=seg.text[:80],
                            )
                        )
                    break

    # Sonnet quality
    for n in range(1, MAX_POEM + 1):
        if poem_forms.get(n) != "sonnet":
            continue
        psegs = by_poem.get(n, [])
        if not psegs:
            continue
        line_segs = [s for s in psegs if s.line_start]
        if not line_segs:
            continue
        nums = {s.line_start for s in line_segs}
        pages = len({s.page for s in psegs if s.page})
        mx = max(nums)
        if mx <= 14 and len(nums) <= 14:
            report.clean_sonnets += 1
        else:
            report.suspect_sonnets += 1
        if mx > 14 or len(line_segs) > 50 or pages > 8:
            report.runaway_sonnets.append(
                {
                    "poem_num": n,
                    "line_segments": len(line_segs),
                    "distinct_lines": len(nums),
                    "max_line": mx,
                    "pdf_pages": pages,
                    "total_segments": len(psegs),
                }
            )

    report.runaway_sonnets.sort(key=lambda x: (-x["line_segments"], -x["max_line"]))
    return report


def print_report(reports: dict[str, SourceReport]) -> None:
    print("=" * 72)
    print("AUDIT ПРИВЯЗКИ КОММЕНТАРИЕВ")
    print("=" * 72)

    for key, r in reports.items():
        print(f"\n## {r.author} ({key})")
        print(f"   сегментов: {r.segments} | поэм: {r.poems_covered}/{MAX_POEM}")
        if r.missing_poems:
            miss = r.missing_poems
            preview = miss if len(miss) <= 12 else miss[:12] + [f"...+{len(miss)-12}"]
            print(f"   пропуски: {preview}")
        print(f"   построчных: {r.line_segments} | общих: {r.poem_level_segments}")
        if r.line_segments:
            pct = 100 * r.line_in_bounds / (r.line_in_bounds + r.line_out_of_bounds)
            print(f"   line в пределах стихотворения: {r.line_in_bounds}/{r.line_segments} ({pct:.1f}%)")
        print(f"   gloss-заголовок ≠ metadata: {r.header_mismatch}")
        print(f"   номер страницы как строка: {r.page_as_line}")
        print(f"   OCR page header ≠ segment poem: {r.page_header_mismatch}")
        print(f"   утечка текста стихотворения: {r.verse_leak}")
        print(f"   сонеты «чистые» (gloss 1–14): {r.clean_sonnets} | подозрительные: {r.suspect_sonnets}")
        if r.runaway_sonnets:
            print("   runaway-сонеты (>50 gloss или line>14 или >8 pdf-стр):")
            for row in r.runaway_sonnets[:8]:
                print(
                    f"      стихотворение {row['poem_num']:>3}: {row['line_segments']:>3} gloss, "
                    f"max line {row['max_line']:>3}, {row['pdf_pages']:>3} pdf-стр"
                )

    print("\n" + "=" * 72)
    print("ИТОГО ПО РИСКАМ")
    total_oob = sum(r.line_out_of_bounds for r in reports.values())
    total_line = sum(r.line_segments for r in reports.values())
    total_phm = sum(r.page_header_mismatch for r in reports.values())
    total_runaway = sum(len(r.runaway_sonnets) for r in reports.values())
    print(f"  построчных out-of-bounds: {total_oob}/{total_line} ({100*total_oob/max(total_line,1):.1f}%)")
    print(f"  page-header mismatch (OCR): {total_phm}")
    print(f"  runaway-сонетов: {total_runaway}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Write full report as JSON")
    parser.add_argument("--poem", type=int, help="Audit one poem only")
    parser.add_argument("--source", choices=list(SOURCES), help="Audit one source only")
    args = parser.parse_args()

    lens = poem_lengths()
    forms = poem_forms()
    anchor = anchor_lines()

    keys = [args.source] if args.source else list(SOURCES)
    reports = {k: audit_source(k, lens, forms, anchor, poem_filter=args.poem) for k in keys}
    print_report(reports)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: asdict(v) for k, v in reports.items()}
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
