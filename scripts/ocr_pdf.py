#!/usr/bin/env python3
"""Extract text from commentary PDFs (text layer or OCR)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from petrarch_search.paths import PAGES_DIR, RAW_COMMENTARIES_DIR

try:
    import fitz
except ImportError as exc:
    raise SystemExit("Install pymupdf: pip install pymupdf") from exc


def readable_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = sum(ch.isalpha() for ch in text)
    return letters / max(len(text), 1)


def check_ocr_dependencies(lang: str) -> None:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise SystemExit(
            "OCR dependencies missing.\n"
            "Run: pip install pytesseract Pillow\n"
            "Or:  pip install -e \".[ocr]\""
        ) from exc

    if shutil.which("tesseract") is None:
        raise SystemExit(
            "Tesseract binary not found.\n"
            "Run: brew install tesseract tesseract-lang"
        )

    import pytesseract

    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise SystemExit(f"Cannot run tesseract: {exc}") from exc

    missing_langs = []
    try:
        available = set(pytesseract.get_languages())
        for part in lang.split("+"):
            if part and part not in available:
                missing_langs.append(part)
    except Exception:
        pass

    if missing_langs:
        print(
            f"Warning: language pack(s) not found: {', '.join(missing_langs)}",
            file=sys.stderr,
        )
        print("Install with: brew install tesseract-lang", file=sys.stderr)


def extract_text_layer(page: fitz.Page) -> str:
    return page.get_text("text").strip()


def ocr_page(page: fitz.Page, lang: str, zoom: float = 2.0) -> str:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(image, lang=lang).strip()


def process_pdf(
    pdf_path: Path,
    output_dir: Path,
    *,
    lang: str = "ita+lat+eng",
    force_ocr: bool = False,
    min_readable: float = 0.25,
    start_page: int = 1,
    end_page: int | None = None,
    zoom: float = 2.0,
) -> dict:
    doc_id = pdf_path.stem
    page_dir = output_dir / doc_id
    page_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    last_page = end_page or doc.page_count
    last_page = min(last_page, doc.page_count)
    start_index = max(start_page - 1, 0)

    metadata = {
        "doc_id": doc_id,
        "source_pdf": str(pdf_path),
        "page_count": doc.page_count,
        "processed_range": [start_page, last_page],
        "pages": [],
    }
    ocr_failures = 0

    for page_index in range(start_index, last_page):
        page = doc[page_index]
        page_number = page_index + 1
        text = ""
        method = "none"

        if not force_ocr:
            text = extract_text_layer(page)
            if readable_ratio(text) >= min_readable:
                method = "text_layer"
            else:
                text = ""

        if not text:
            try:
                text = ocr_page(page, lang=lang, zoom=zoom)
                method = "ocr"
            except Exception as exc:
                ocr_failures += 1
                if ocr_failures == 1:
                    raise SystemExit(
                        f"OCR failed on page {page_number}: {exc}\n"
                        "Fix dependencies, then re-run."
                    ) from exc
                text = ""
                method = "ocr_failed"

        out_path = page_dir / f"page_{page_number:04d}.txt"
        out_path.write_text(text + "\n", encoding="utf-8")
        metadata["pages"].append(
            {
                "page": page_number,
                "method": method,
                "chars": len(text),
                "readable_ratio": round(readable_ratio(text), 3),
                "file": str(out_path.relative_to(output_dir.parent.parent)),
            }
        )

        if page_number % 25 == 0 or page_number == last_page:
            print(f"  page {page_number}/{last_page}", file=sys.stderr)

    meta_path = page_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdf",
        nargs="?",
        type=Path,
        help="PDF file to process (default: all in data/raw/commentaries)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PAGES_DIR,
        help="Output directory for page text files",
    )
    parser.add_argument("--lang", default="ita+lat+eng", help="Tesseract language(s)")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=None)
    parser.add_argument("--zoom", type=float, default=2.0, help="Render scale for OCR")
    args = parser.parse_args()

    pdfs: list[Path]
    if args.pdf:
        pdfs = [args.pdf]
    else:
        pdfs = sorted(RAW_COMMENTARIES_DIR.glob("*.pdf"))
        if not pdfs:
            print(f"No PDFs found in {RAW_COMMENTARIES_DIR}", file=sys.stderr)
            raise SystemExit(1)

    check_ocr_dependencies(args.lang)

    for pdf_path in pdfs:
        if not pdf_path.exists():
            print(f"Missing file: {pdf_path}", file=sys.stderr)
            continue
        print(f"Processing {pdf_path.name}...", file=sys.stderr)
        meta = process_pdf(
            pdf_path,
            args.output,
            lang=args.lang,
            force_ocr=args.force_ocr,
            start_page=args.start_page,
            end_page=args.end_page,
            zoom=args.zoom,
        )
        avg_ratio = sum(p["readable_ratio"] for p in meta["pages"]) / max(
            len(meta["pages"]), 1
        )
        ocr_pages = sum(1 for p in meta["pages"] if p["method"] == "ocr")
        print(
            f"{pdf_path.name}: {len(meta['pages'])} pages processed, "
            f"{ocr_pages} via OCR, avg readable ratio {avg_ratio:.1%}"
        )


if __name__ == "__main__":
    main()
