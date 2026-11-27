"""Readers for the synthetic documents

Policies: one document per file, doc_id = policy id ("POL-005").
    "# title" + metadata line  -> parent_id "POL-005#s0" (the header, with the unit code)
    "## 3. Heading"            -> parent_id "POL-005#s3"
Log: one document, doc_id "incident_log"; one block per incident
    -> parent_id "incident_log#INC-0010", with all its events as structured records.
    (Phase 3 decides which events go into the incident's chunk.)
Register: one document, doc_id "register"; one block per provider (CSV row)
    -> parent_id "register#PRV-003", text = one line "Column: value | Column: value ...".
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from reglens.corpus.sources import Language
from reglens.ingestion.models import Block, ParsedDocument

POLICY_FILE = re.compile(r"^(POL-\d{3})_[a-z_]+_(en|fr)\.md$")
SECTION_HEADING = re.compile(r"^##\s+(\d+)\.\s+(.+)$")
LOG_DOC_ID = "incident_log"
REGISTER_DOC_ID = "register"
REGISTER_FILE = "register_of_information.csv"


def parse_policy(path: Path, *, source: str) -> ParsedDocument:
    match = POLICY_FILE.match(path.name)
    if not match:
        raise ValueError(f"unexpected policy file name: {path.name}")
    doc_id, lang = match.group(1), match.group(2)
    langue: Language = "en" if lang == "en" else "fr"

    title: str | None = None
    sections: list[tuple[int, str | None, list[str]]] = [(0, None, [])]  # (number, heading, lines)
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = SECTION_HEADING.match(line)
        if heading:
            sections.append((int(heading.group(1)), heading.group(2).strip(), []))
        elif line.startswith("# ") and title is None:
            title = line[2:].strip()
            sections[0][2].append(title)
        elif line.strip():
            sections[-1][2].append(line.strip())

    blocks = []
    for number, section_heading, lines in sections:
        parent_id = f"{doc_id}#s{number}"
        blocks.append(
            Block(
                block_id=f"{parent_id}/t",
                parent_id=parent_id,
                kind="title" if number == 0 else "text",
                text="\n".join(lines),
                section=section_heading,
            )
        )
    return ParsedDocument(
        doc_id=doc_id,
        source=source,
        type_doc="policies",
        langue=langue,
        title=title,
        parser="policy_md",
        blocks=blocks,
    )


def parse_incident_log(path: Path, *, source: str) -> ParsedDocument:
    by_incident: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                event = json.loads(line)
                by_incident[event["incident_id"]].append(event)

    blocks = []
    for incident_id in sorted(by_incident):
        events = sorted(by_incident[incident_id], key=lambda e: e["timestamp"])
        first = events[0]
        parent_id = f"{LOG_DOC_ID}#{incident_id}"
        header = (
            f"{incident_id} | system {first['system']} | owning unit {first['owning_unit']} | "
            f"severity {first['severity']} | detected {first['detected_at']}"
        )
        blocks.append(
            Block(
                block_id=f"{parent_id}/events",
                parent_id=parent_id,
                kind="records",
                text=header,
                records=events,
            )
        )
    return ParsedDocument(
        doc_id=LOG_DOC_ID,
        source=source,
        type_doc="logs",
        langue="en",
        title="ICT incident log",
        parser="incident_jsonl",
        blocks=blocks,
    )


def parse_register(path: Path, *, source: str) -> ParsedDocument:
    """One block per provider: every non-empty column, in file order, on one line."""
    blocks = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            parent_id = f"{REGISTER_DOC_ID}#{row['Provider ID']}"
            text = " | ".join(f"{column}: {value}" for column, value in row.items() if value)
            blocks.append(
                Block(block_id=f"{parent_id}/row", parent_id=parent_id, kind="text", text=text)
            )
    return ParsedDocument(
        doc_id=REGISTER_DOC_ID,
        source=source,
        type_doc="register",
        langue="en",
        title="Register of information on ICT third-party service providers",
        parser="register_csv",
        blocks=blocks,
    )
