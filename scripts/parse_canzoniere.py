#!/usr/bin/env python3
"""Parse Canzoniere anchor text from TEI (Petrarchive) or build fallback corpus."""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.fallback_canzoniere import FALLBACK_POEMS
from petrarch_search.models import Poem, save_canzoniere
from petrarch_search.paths import CANZONIERE_JSON, RAW_CANZONIERE_DIR

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    parent_map: dict[ET.Element, ET.Element] = {}
    for node in root.iter():
        for child in node:
            parent_map[child] = node
    return parent_map


def _ancestors(element: ET.Element, parent_map: dict[ET.Element, ET.Element]) -> list[ET.Element]:
    chain: list[ET.Element] = []
    current = element
    while current in parent_map:
        current = parent_map[current]
        chain.append(current)
    return chain


def parse_tei(tei_path: Path) -> list[Poem]:
    tree = ET.parse(tei_path)
    root = tree.getroot()
    parent_map = _build_parent_map(root)
    poems: list[Poem] = []

    for lg in root.findall(".//tei:lg", TEI_NS):
        poem_num = None
        for ancestor in _ancestors(lg, parent_map):
            xml_id = ancestor.attrib.get("{http://www.w3.org/XML/1998/namespace}id") or ancestor.attrib.get("xml:id")
            if xml_id:
                match = re.search(r"(\d+)", xml_id)
                if match:
                    poem_num = int(match.group(1))
                    break
            n_attr = ancestor.attrib.get("n")
            if n_attr and n_attr.isdigit():
                poem_num = int(n_attr)
                break

        lines = [
            normalize_text("".join(line.itertext()))
            for line in lg.findall("tei:l", TEI_NS)
            if normalize_text("".join(line.itertext()))
        ]
        if not lines:
            continue

        if poem_num is None:
            poem_num = len(poems) + 1

        form = lg.attrib.get("type")
        section = None
        for ancestor in _ancestors(lg, parent_map):
            div_type = ancestor.attrib.get("type")
            if div_type:
                section = div_type
                break

        poems.append(
            Poem(
                poem_num=poem_num,
                incipit=lines[0],
                lines=lines,
                form=form,
                section=section,
            )
        )

    poems.sort(key=lambda p: p.poem_num)
    return poems


def find_tei_file(source: Path) -> Path | None:
    if source.is_file() and source.suffix.lower() in {".xml", ".tei"}:
        return source
    if source.is_dir():
        for pattern in ("*.xml", "*.tei"):
            matches = sorted(source.glob(pattern))
            if matches:
                return matches[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=RAW_CANZONIERE_DIR,
        help="TEI file or directory with TEI/XML",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=CANZONIERE_JSON,
        help="Output canzoniere.json path",
    )
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="Write built-in fallback poems instead of parsing TEI",
    )
    args = parser.parse_args()

    if args.fallback:
        poems = FALLBACK_POEMS
        source = "fallback"
    else:
        tei_path = find_tei_file(args.input)
        if tei_path is None:
            print(f"No TEI found in {args.input}; using fallback corpus.", file=sys.stderr)
            poems = FALLBACK_POEMS
            source = "fallback"
        else:
            poems = parse_tei(tei_path)
            source = str(tei_path)
            if len(poems) < 10:
                print(
                    f"TEI parse yielded only {len(poems)} poems; merging fallback.",
                    file=sys.stderr,
                )
                existing = {p.poem_num for p in poems}
                poems.extend(p for p in FALLBACK_POEMS if p.poem_num not in existing)
                poems.sort(key=lambda p: p.poem_num)

    save_canzoniere(poems, args.output)
    print(f"Wrote {len(poems)} poems to {args.output} (source: {source})")


if __name__ == "__main__":
    main()
