"""Loaders for the agentic Meridian dataset.

Returns typed Python objects from disk. Storage-layer fan-out (SQLite for
structured entities, ChromaDB for documents) lands in Phase 2 alongside the
agent's tool surface; Phase 1 delivers the in-memory representation and the
integrity contract these loaders enforce by construction.

Documents marked `TEST_POISONED: true` in frontmatter are excluded from the
default `load_documents()` result. Use `load_documents(include_poisoned=True)`
to retrieve them — only red-team tooling and integrity tests should do so.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "meridian"

CLASSIFICATION_NAMES = {
    1: "PUBLIC",
    2: "INTERNAL",
    3: "CONFIDENTIAL",
    4: "RESTRICTED",
}
CLASSIFICATION_VALUES = {v: k for k, v in CLASSIFICATION_NAMES.items()}


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    title: str
    department: str
    manager_id: str | None
    clearance_level: int
    location: str
    hire_date: date
    email: str
    salary: int
    is_active: bool


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    owner_id: str
    members: tuple[str, ...]
    classification: str
    status: str
    start_date: date
    description: str


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    title: str
    owner_id: str
    assignee_id: str
    status: str
    classification: int
    project_id: str | None
    created_at: date
    type: str


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    organizer_id: str
    attendees: tuple[str, ...]
    subject: str
    classification: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Document:
    path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("title", self.path.stem))

    @property
    def classification(self) -> str:
        return str(self.frontmatter.get("classification", "INTERNAL"))

    @property
    def is_poisoned(self) -> bool:
        return bool(self.frontmatter.get("TEST_POISONED", False))


def load_employees(root: Path = DATA_ROOT) -> list[Employee]:
    raw = json.loads((root / "employees.json").read_text())
    return [
        Employee(
            employee_id=row["employee_id"],
            name=row["name"],
            title=row["title"],
            department=row["department"],
            manager_id=row.get("manager_id"),
            clearance_level=int(row["clearance_level"]),
            location=row["location"],
            hire_date=date.fromisoformat(row["hire_date"]),
            email=row["email"],
            salary=int(row["salary"]),
            is_active=bool(row["is_active"]),
        )
        for row in raw
    ]


def load_projects(root: Path = DATA_ROOT) -> list[Project]:
    raw = json.loads((root / "projects.json").read_text())
    return [
        Project(
            project_id=row["project_id"],
            name=row["name"],
            owner_id=row["owner_id"],
            members=tuple(row["members"]),
            classification=row["classification"],
            status=row["status"],
            start_date=date.fromisoformat(row["start_date"]),
            description=row["description"],
        )
        for row in raw
    ]


def load_tickets(root: Path = DATA_ROOT) -> list[Ticket]:
    out: list[Ticket] = []
    with (root / "tickets.csv").open(newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            out.append(
                Ticket(
                    ticket_id=row["ticket_id"],
                    title=row["title"],
                    owner_id=row["owner_id"],
                    assignee_id=row["assignee_id"],
                    status=row["status"],
                    classification=int(row["classification"]),
                    project_id=row["project_id"] or None,
                    created_at=date.fromisoformat(row["created_at"]),
                    type=row["type"],
                )
            )
    return out


def load_calendar(root: Path = DATA_ROOT) -> list[CalendarEvent]:
    raw = json.loads((root / "calendar.json").read_text())
    return [
        CalendarEvent(
            event_id=row["event_id"],
            organizer_id=row["organizer_id"],
            attendees=tuple(row["attendees"]),
            subject=row["subject"],
            classification=int(row["classification"]),
            start=_parse_iso8601(row["start"]),
            end=_parse_iso8601(row["end"]),
        )
        for row in raw
    ]


def load_documents(
    root: Path = DATA_ROOT, *, include_poisoned: bool = False
) -> list[Document]:
    docs_root = root / "documents"
    out: list[Document] = []
    for path in sorted(docs_root.rglob("*.md")) + sorted(docs_root.rglob("*.txt")):
        text = path.read_text()
        frontmatter, body = _split_frontmatter(text)
        doc = Document(path=path, frontmatter=frontmatter, body=body)
        if doc.is_poisoned and not include_poisoned:
            continue
        out.append(doc)
    return out


def _parse_iso8601(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a `---`-fenced YAML-ish frontmatter block.

    Supports the shapes used in this dataset only: scalars, booleans,
    integers, inline lists `[a, b, c]`, block lists, and `|` literal blocks.
    Anything more exotic should fail loudly so we notice and either extend
    this parser or pull in PyYAML.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    return _parse_frontmatter(raw), body


def _parse_frontmatter(raw: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            raise ValueError(f"unparseable frontmatter line: {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if value == "":
            i += 1
            block_lines: list[str] = []
            while i < len(lines) and (lines[i].startswith(" ") or lines[i] == ""):
                block_lines.append(lines[i])
                i += 1
            out[key] = _parse_block(block_lines)
            continue

        if value == "|":
            i += 1
            block_lines = []
            while i < len(lines) and (lines[i].startswith("  ") or lines[i] == ""):
                block_lines.append(lines[i][2:] if lines[i].startswith("  ") else "")
                i += 1
            out[key] = "\n".join(block_lines).rstrip() + "\n"
            continue

        out[key] = _parse_scalar(value)
        i += 1
    return out


def _parse_block(block_lines: list[str]) -> Any:
    stripped = [ln for ln in block_lines if ln.strip()]
    if not stripped:
        return None
    if all(ln.lstrip().startswith("- ") for ln in stripped):
        return [_parse_scalar(ln.lstrip()[2:].strip()) for ln in stripped]
    raise ValueError(f"unsupported block structure: {block_lines!r}")


def _parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lstrip("-").isdigit():
        return int(value)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value
