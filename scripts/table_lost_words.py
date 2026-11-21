"""Which PDF words does each table variant lose? (coverage diagnosis)

Usage (repository root, virtual environment active, after scripts/run_docling.py):
    python scripts/table_lost_words.py             # same 20 tables as check_tables.py

For the 20 sampled tables, converts the pages again with cell matching off, then lists
the words of the PDF table region that the extracted cells do not contain, for both
variants. Each lost word gets a type from where it sits in the cell-matching-on result:
    header / label / number   it is in a cell of that kind
    footnote_or_caption       it is in a caption or footnote text on the same page
    other                     nowhere else (text at the edge of the region, page furniture)
Writes results/<date>_table_lost_words.json (counts per type only: no report text).
The words themselves are printed on the console, for local inspection.
"""

import argparse
import logging
import sys
import warnings
from collections import Counter
from datetime import date

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import TextItem
from docling_core.types.doc.labels import DocItemLabel

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.evaluation.results import append_row, write_result
from reglens.evaluation.table_check import (
    cell_kind,
    matching_table,
    normalize,
    pick_tables,
    region_text,
)
from reglens.ingestion.parse_pdf import cache_paths, convert_pdf, make_converter

NOTE_LABELS = {DocItemLabel.CAPTION, DocItemLabel.FOOTNOTE}


def lost_words(region: str, table: TableItem) -> list[str]:
    extracted = normalize("".join(c.text for c in table.data.table_cells))
    return [w for w in region.split() if normalize(w) and normalize(w) not in extracted]


def word_type(word: str, reference: TableItem, notes: str) -> str:
    """Kind of the reference cell holding the word, else caption/footnote, else other."""
    w = normalize(word)
    for cell in reference.data.table_cells:
        if w in normalize(cell.text):
            return cell_kind(cell.text, cell.column_header)
    return "footnote_or_caption" if w in notes else "other"


def page_notes(doc: DoclingDocument, page: int) -> str:
    return normalize(" ".join(
        item.text for item, _ in doc.iterate_items()
        if isinstance(item, TextItem) and item.label in NOTE_LABELS
        and item.prov and item.prov[0].page_no == page
    ))  # fmt: skip


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sample", type=int, default=8, help="words printed per table")
    args = parser.parse_args()
    warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.ERROR)

    cached: dict[str, DoclingDocument] = {}
    pdfs: dict[str, pdfium.PdfDocument] = {}
    paths = {}
    for src in load_corpus(settings.sources_file).reports:
        doc_file = cache_paths(settings.docling_cache_dir, src.id)[0]
        if doc_file.exists():
            cached[src.id] = DoclingDocument.load_from_json(doc_file)
            paths[src.id] = settings.raw_dir / "reports" / f"{src.id}.pdf"
            pdfs[src.id] = pdfium.PdfDocument(paths[src.id])
    sample = pick_tables(cached, args.n, args.seed)
    converter = make_converter(do_ocr=settings.docling_do_ocr,
                               table_mode=settings.docling_table_mode,
                               num_threads=settings.docling_num_threads,
                               do_cell_matching=False)  # fmt: skip

    totals = {"on": Counter[str](), "off": Counter[str]()}
    per_table = []
    for doc_id, index, on_table in sample:
        pages = sorted({p.page_no for p in on_table.prov})
        off_doc, _ = convert_pdf(paths[doc_id], converter, (pages[0], pages[-1]))
        off_table = matching_table(on_table, off_doc)
        region = region_text(pdfs[doc_id], cached[doc_id], on_table)
        notes = page_notes(cached[doc_id], pages[0])
        row: dict[str, object] = {
            "table": f"{doc_id}#tbl-{index}",
            "region_words": len(region.split()),
        }
        print(f"\n{doc_id}#tbl-{index} (page {pages}, {len(region.split())} words)")
        for variant, table in (("on", on_table), ("off", off_table)):
            if table is None:
                print(f"  {variant}: table not found")
                row[variant] = "table not found"
                continue
            lost = lost_words(region, table)
            types = Counter(word_type(w, on_table, notes) for w in lost)
            totals[variant].update(types)
            row[variant] = dict(types)
            shown = ", ".join(f"{w} [{word_type(w, on_table, notes)}]" for w in lost[: args.sample])
            print(f"  {variant}: {len(lost)} lost {dict(types)}  e.g. {shown}")
        per_table.append(row)

    result = {
        "name": "table_lost_words",
        "date": date.today().isoformat(),
        "method": "PDF words in the table region missing from the extracted cells, typed by "
        "the cell-matching-on result (header, label, number), caption/footnote, or other",
        "seed": args.seed,
        "lost_by_type": {v: dict(c) for v, c in totals.items()},
        "tables": per_table,
    }
    out = write_result(settings.results_dir, result)
    append_row(
        settings.results_dir,
        f"| {result['date']} | table_lost_words | 2 | Words of the PDF table region missing from "
        f"the cells, by type, cell matching on vs off ({len(sample)} tables) | on: "
        f"{dict(totals['on'])}; off: {dict(totals['off'])} | `{out.as_posix()}` |",
    )
    print(f"\nlost by type: on {dict(totals['on'])}; off {dict(totals['off'])}\nwritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
