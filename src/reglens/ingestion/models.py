"""The unified parsed-document model.

Every parser produces the
same structure, so chunking works on one model, not four file formats.

A document is a list of *blocks*. Each block knows where it comes from (provenance):
its logical unit (`parent_id`, e.g. "dora#art-19"), and its article / paragraph /
page / section when they exist. Citations later point back to exactly this.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reglens.corpus.sources import DocType, Language

BlockKind = Literal["title", "text", "table", "records"]


# One number: optional sign/brackets, thousands grouped by "," or by a space (French
# reports write "1 234 567"), optional decimals and %. E.g. "(1,043)", "12,9 %", "1 234".
_SPACES = r"[ \u00a0\u202f]"  # space, no-break space, narrow no-break space
_ONE_NUMBER = re.compile(
    rf"[-+(]?\d{{1,3}}(?:(?:,\d{{3}})+|(?:{_SPACES}\d{{3}})+)?(?:[.,]\d+)?\s?%?\)?"
    rf"|[-+(]?\d+(?:[.,]\d+)?\s?%?\)?"
)
_NUMBER_TOKEN = re.compile(r"\d")


def has_several_numbers(text: str) -> bool:
    """True for a cell with no letters that is not one number: "1,571 898 92" is three
    values from three rows fused into one cell (a known table-structure error).
    Limitation: "674 340" reads as one space-grouped number, so it is not flagged."""
    text = text.strip()
    if not text or any(ch.isalpha() for ch in text) or not _NUMBER_TOKEN.search(text):
        return False
    return _ONE_NUMBER.fullmatch(text) is None and len(text.split()) >= 2


class TableData(BaseModel):
    """A table as a grid of cell strings (row 0 may be the header)."""

    model_config = ConfigDict(extra="forbid")

    table_id: str  # globally unique, e.g. "ing_group_pillar3_2025#tbl-12"
    caption: str | None = None
    n_rows: int
    n_cols: int
    cells: list[list[str]]  # cells[row][col]; merged cells repeat their text
    header_rows: int = 0  # how many leading rows are column headers
    # (row, col) of cells that look like several values fused together: kept, but flagged
    # so an answer built on them can say the value is uncertain.
    low_confidence: list[tuple[int, int]] = Field(default_factory=list)
    # Why the header may be wrong (see ingestion/table_quality.py); empty = no warning.
    header_issues: list[str] = Field(default_factory=list)

    @property
    def header_uncertain(self) -> bool:
        return bool(self.header_issues)

    def to_markdown(self) -> str:
        """Markdown rendering: what a table chunk will contain in Phase 3."""
        if not self.cells:
            return ""
        lines = []
        for r, row in enumerate(self.cells):
            lines.append(
                "| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in row) + " |"
            )
            if r == max(self.header_rows, 1) - 1:
                lines.append("|" + "---|" * self.n_cols)
        return "\n".join(lines)


class Block(BaseModel):
    """One piece of a document with its provenance."""

    model_config = ConfigDict(extra="forbid")

    block_id: str  # unique in the document, e.g. "dora#art-19/p2/1"
    parent_id: str  # logical unit, globally unique: "<doc_id>#<unit>"
    kind: BlockKind
    text: str  # PII-masked before the document is written anywhere
    page_number: int | None = None  # PDF page (1-based); None for HTML/MD/JSONL
    article: str | None = None  # article number for regulations ("19", "5a")
    paragraph: int | None = None  # numbered paragraph inside the article
    section: str | None = None  # heading path, e.g. "Chapter III > Section 1"
    table: TableData | None = None  # only for kind="table"
    records: list[dict[str, Any]] | None = None  # only for kind="records" (log events)


class ParsedDocument(BaseModel):
    """One raw file, parsed. Regulations exist twice (EN, FR) with the same doc_id."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str  # language-independent: "dora", "POL-005", "incident_log"
    source: str  # path of the raw file, relative to the repository root
    type_doc: DocType
    langue: Language
    reglement: str | None = None  # regulation short name ("DORA", "AMLR")
    title: str | None = None
    parser: str  # which parser produced it ("eurlex", "docling", "policy_md", "incident_jsonl")
    blocks: list[Block] = Field(default_factory=list)
    masked: bool = False  # True once Presidio has run on every text field
    pii_counts: dict[str, int] = Field(default_factory=dict)  # entity type -> spans masked
    parse_seconds: float | None = None

    @property
    def output_name(self) -> str:
        """File name in data/processed/parsed/ (one file per raw file)."""
        return f"{self.doc_id}__{self.langue}.json"
