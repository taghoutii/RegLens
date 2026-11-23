"""Loading report tables for the measurement scripts (one loader instead of one per script).

Two views of the same tables:
- the parsed (masked) tables in data/processed/parsed/, as the pipeline stores them;
- Docling's raw output in data/cache/docling/ with the PDF itself, for checks against
  the PDF text layer (unmasked: the result files keep ids and counts only).
"""

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument

from reglens.config import Settings
from reglens.corpus.sources import Language, load_corpus
from reglens.ingestion.models import ParsedDocument, TableData
from reglens.ingestion.parse_pdf import cache_paths


@dataclass
class ReportTable:
    table: TableData
    doc_id: str
    langue: Language
    page_number: int | None
    section: str | None


def report_tables(processed_dir: Path, doc_ids: set[str] | None = None) -> list[ReportTable]:
    """Every non-empty table of the parsed reports (optionally only some documents)."""
    out = []
    for path in sorted((processed_dir / "parsed").glob("*.json")):
        doc = ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if doc.type_doc != "reports" or (doc_ids is not None and doc.doc_id not in doc_ids):
            continue
        for b in doc.blocks:
            if b.table is not None and b.table.cells:
                out.append(ReportTable(b.table, doc.doc_id, doc.langue, b.page_number, b.section))
    return out


def cached_reports(
    settings: Settings, doc_ids: set[str] | None = None
) -> dict[str, tuple[DoclingDocument, pdfium.PdfDocument]]:
    """Docling output and PDF of every report already in the Docling cache."""
    out = {}
    for src in load_corpus(settings.sources_file).reports:
        if doc_ids is not None and src.id not in doc_ids:
            continue
        doc_file = cache_paths(settings.docling_cache_dir, src.id)[0]
        if doc_file.exists():
            out[src.id] = (
                DoclingDocument.load_from_json(doc_file),
                pdfium.PdfDocument(settings.raw_dir / "reports" / f"{src.id}.pdf"),
            )
    return out
