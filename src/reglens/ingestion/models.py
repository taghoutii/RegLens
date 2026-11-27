"""The unified parsed-document model.

Every parser produces the
same structure, so chunking works on one model, not four file formats.

A document is a list of *blocks*. Each block knows where it comes from (provenance):
its logical unit (`parent_id`, e.g. "dora#art-19"), and its article / paragraph /
section when they exist. Citations later point back to exactly this.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from reglens.corpus.sources import DocType, Language

BlockKind = Literal["title", "text", "records"]


class Block(BaseModel):
    """One piece of a document with its provenance."""

    model_config = ConfigDict(extra="forbid")

    block_id: str  # unique in the document, e.g. "dora#art-19/p2/1"
    parent_id: str  # logical unit, globally unique: "<doc_id>#<unit>"
    kind: BlockKind
    text: str  # PII-masked before the document is written anywhere
    article: str | None = None  # article number for regulations ("19", "5a")
    paragraph: int | None = None  # numbered paragraph inside the article
    section: str | None = None  # heading path, e.g. "Chapter III > Section 1"
    records: list[dict[str, Any]] | None = None  # only for kind="records" (log events)


class ParsedDocument(BaseModel):
    """One raw file, parsed. Regulations exist twice (EN, FR) with the same doc_id."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str  # language-independent: "dora", "POL-005", "incident_log", "register"
    source: str  # path of the raw file, relative to the repository root
    type_doc: DocType
    langue: Language
    reglement: str | None = None  # regulation short name ("DORA", "AMLR")
    title: str | None = None
    parser: str  # "eurlex", "policy_md", "incident_jsonl" or "register_csv"
    blocks: list[Block] = Field(default_factory=list)
    masked: bool = False  # True once Presidio has run on every text field
    pii_counts: dict[str, int] = Field(default_factory=dict)  # entity type -> spans masked
    parse_seconds: float | None = None

    @property
    def output_name(self) -> str:
        """File name in data/processed/parsed/ (one file per raw file)."""
        return f"{self.doc_id}__{self.langue}.json"
