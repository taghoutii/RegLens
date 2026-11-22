"""PDF parsing with Docling (bank Pillar 3 reports).

Docling runs a layout model on each page image (what is a heading, a paragraph, a
table, a page header?), puts the items in reading order (so 2-column pages read
column by column), and runs TableFormer on each table to recover rows and columns.
Both models run on CPU here, which is why the speed is measured, not assumed.

Mapping to the parsed-document model:
    section header      -> starts a new logical unit  "<doc_id>#sec-<n>"
    paragraph / list    -> text block in the current section, with its page number
    table               -> its own unit               "<doc_id>#tbl-<n>", cells as a grid
    page header/footer  -> dropped (repeated on every page, no content)
"""

import json
import time
from pathlib import Path
from typing import Literal

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.base import ImageRefMode
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.node import DocItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import SectionHeaderItem, TextItem
from docling_core.types.doc.labels import DocItemLabel

from reglens.config import Settings
from reglens.corpus.sources import Language
from reglens.ingestion.models import Block, ParsedDocument, TableData, has_several_numbers
from reglens.ingestion.table_quality import header_issues, header_rows

SKIPPED_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
TableMode = Literal["accurate", "fast"]


def make_converter(
    *, do_ocr: bool, table_mode: TableMode, num_threads: int, do_cell_matching: bool = True
) -> DocumentConverter:
    """One converter, reused for every PDF (loading the models takes ~20 s).

    do_cell_matching: True = fill each predicted table cell with the PDF text cells under
    it; False = use the text TableFormer predicts for the cell structure itself."""
    options = PdfPipelineOptions(do_ocr=do_ocr, do_table_structure=True)
    options.table_structure_options = TableStructureOptions(
        mode=TableFormerMode.ACCURATE if table_mode == "accurate" else TableFormerMode.FAST,
        do_cell_matching=do_cell_matching,
    )
    options.accelerator_options = AcceleratorOptions(
        num_threads=num_threads, device=AcceleratorDevice.CPU
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    converter.initialize_pipeline(InputFormat.PDF)
    return converter


def table_data(item: TableItem, table_id: str, caption: str | None) -> TableData:
    grid = item.data.grid  # rows x cols; a merged cell appears in every slot it covers
    cells = [[cell.text.strip() for cell in row] for row in grid]
    low_confidence = [
        (r, c)
        for r, row in enumerate(grid)
        for c, cell in enumerate(row)
        if not cell.column_header and has_several_numbers(cell.text)
    ]
    return TableData(
        table_id=table_id,
        caption=caption or None,
        n_rows=item.data.num_rows,
        n_cols=item.data.num_cols,
        cells=cells,
        header_rows=header_rows(grid),
        low_confidence=low_confidence,
        header_issues=header_issues(item),
    )


def convert_pdf(
    path: Path, converter: DocumentConverter, page_range: tuple[int, int] | None = None
) -> tuple[DoclingDocument, float]:
    """Run Docling on one PDF (optionally a page range). Returns the document and seconds."""
    t0 = time.perf_counter()
    if page_range is None:
        result = converter.convert(path)
    else:
        result = converter.convert(path, page_range=page_range)
    return result.document, time.perf_counter() - t0


def cache_paths(cache_dir: Path, doc_id: str) -> tuple[Path, Path]:
    """(Docling JSON, metadata JSON) for one document."""
    return cache_dir / f"{doc_id}.json", cache_dir / f"{doc_id}.meta.json"


def docling_config(settings: Settings) -> dict[str, object]:
    """The settings that change Docling's output (threads only change its speed)."""
    return {
        "do_ocr": settings.docling_do_ocr,
        "table_mode": settings.docling_table_mode,
        "do_cell_matching": settings.docling_cell_matching,
    }


def is_cached(cache_dir: Path, doc_id: str, config: dict[str, object]) -> bool:
    """True if the cache holds this report, converted with exactly these settings."""
    doc_file, meta_file = cache_paths(cache_dir, doc_id)
    if not (doc_file.exists() and meta_file.exists()):
        return False
    return bool(json.loads(meta_file.read_text(encoding="utf-8")).get("config") == config)


def cached_conversion(
    path: Path,
    converter: DocumentConverter,
    cache_dir: Path,
    doc_id: str,
    config: dict[str, object],
) -> tuple[DoclingDocument, float]:
    """Docling output for a whole PDF: read from the cache, or convert and cache it.

    `config` (see docling_config) is stored with the output: a cache made with other
    settings is converted again, never reused. The cache file is written to a temporary
    name and renamed at the end, so an interrupted run never leaves a half-written file
    that would look complete."""
    doc_file, meta_file = cache_paths(cache_dir, doc_id)
    if is_cached(cache_dir, doc_id, config):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        return DoclingDocument.load_from_json(doc_file), float(meta["convert_s"])
    doc, seconds = convert_pdf(path, converter)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = doc_file.with_suffix(".json.part")
    doc.save_as_json(tmp, image_mode=ImageRefMode.PLACEHOLDER)  # text only, no page images
    tmp.replace(doc_file)
    meta = {"doc_id": doc_id, "source": path.as_posix(), "pages": len(doc.pages),
            "convert_s": round(seconds, 1), "config": config}  # fmt: skip
    meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return doc, seconds


def parse_pdf(
    path: Path,
    converter: DocumentConverter,
    *,
    doc_id: str,
    langue: Language,
    source: str,
    page_range: tuple[int, int] | None = None,
) -> ParsedDocument:
    """Convert one PDF (optionally a page range, for samples and timing) and map it."""
    doc, seconds = convert_pdf(path, converter, page_range)
    return from_docling(doc, doc_id=doc_id, langue=langue, source=source, seconds=seconds)


def from_docling(
    doc: DoclingDocument,
    *,
    doc_id: str,
    langue: Language,
    source: str,
    seconds: float | None = None,
) -> ParsedDocument:
    """Map a DoclingDocument to parsed blocks (titles, text, tables with provenance)."""
    blocks: list[Block] = []
    section_n, table_n = 0, 0
    section_title: str | None = None
    for item, _level in doc.iterate_items():
        if not isinstance(item, DocItem):
            continue  # groups (lists, sections) carry no text of their own
        page = item.prov[0].page_no if item.prov else None
        if isinstance(item, SectionHeaderItem):
            section_n += 1
            section_title = item.text.strip()
            parent_id = f"{doc_id}#sec-{section_n}"
            blocks.append(
                Block(
                    block_id=f"{parent_id}/title",
                    parent_id=parent_id,
                    kind="title",
                    text=section_title,
                    page_number=page,
                    section=section_title,
                )
            )
        elif isinstance(item, TableItem):
            table_n += 1
            table_id = f"{doc_id}#tbl-{table_n}"
            table = table_data(item, table_id, item.caption_text(doc))
            blocks.append(
                Block(
                    block_id=f"{table_id}/table",
                    parent_id=table_id,
                    kind="table",
                    text=table.caption or "",
                    page_number=page,
                    section=section_title,
                    table=table,
                )
            )
        elif isinstance(item, TextItem) and item.label not in SKIPPED_LABELS:
            text = item.text.strip()
            if not text:
                continue
            parent_id = f"{doc_id}#sec-{section_n}"
            blocks.append(
                Block(
                    block_id=f"{parent_id}/t{len(blocks)}",
                    parent_id=parent_id,
                    kind="text",
                    text=text,
                    page_number=page,
                    section=section_title,
                )
            )

    return ParsedDocument(
        doc_id=doc_id,
        source=source,
        type_doc="reports",
        langue=langue,
        title=doc.name,
        parser="docling",
        blocks=blocks,
        parse_seconds=round(seconds, 2) if seconds is not None else None,
    )
