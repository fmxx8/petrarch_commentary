from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Segment:
    poem_nums: list[int]
    text: str
    line_start: int | None = None
    line_end: int | None = None
    page: int | None = None
    confidence: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Commentary:
    id: str
    author: str
    language: str
    segments: list[Segment] = field(default_factory=list)
    year: str | None = None
    source_pdf: str | None = None
    edition_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "author": self.author,
            "language": self.language,
            "segments": [s.to_dict() for s in self.segments],
        }
        if self.year:
            data["year"] = self.year
        if self.source_pdf:
            data["source_pdf"] = self.source_pdf
        if self.edition_note:
            data["edition_note"] = self.edition_note
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Commentary:
        segments = [
            Segment(
                poem_nums=seg["poem_nums"],
                text=seg["text"],
                line_start=seg.get("line_start"),
                line_end=seg.get("line_end"),
                page=seg.get("page"),
                confidence=seg.get("confidence", "manual"),
            )
            for seg in data.get("segments", [])
        ]
        return cls(
            id=data["id"],
            author=data["author"],
            language=data["language"],
            segments=segments,
            year=data.get("year"),
            source_pdf=data.get("source_pdf"),
            edition_note=data.get("edition_note"),
        )


@dataclass
class Poem:
    poem_num: int
    incipit: str
    lines: list[str]
    form: str | None = None
    section: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "poem_num": self.poem_num,
            "incipit": self.incipit,
            "lines": self.lines,
        }
        if self.form:
            data["form"] = self.form
        if self.section:
            data["section"] = self.section
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Poem:
        return cls(
            poem_num=data["poem_num"],
            incipit=data["incipit"],
            lines=data["lines"],
            form=data.get("form"),
            section=data.get("section"),
        )


def load_commentary(path: Path) -> Commentary:
    with path.open(encoding="utf-8") as f:
        return Commentary.from_dict(json.load(f))


def save_commentary(commentary: Commentary, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(commentary.to_dict(), f, ensure_ascii=False, indent=2)


def load_canzoniere(path: Path) -> list[Poem]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return [Poem.from_dict(item) for item in data]


def save_canzoniere(poems: list[Poem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in poems], f, ensure_ascii=False, indent=2)
