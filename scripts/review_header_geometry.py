"""Visual spot check of geometric headers on tables outside the labelled set.

Usage (repository root, virtual environment active):
    python scripts/review_header_geometry.py --n 10 --seed 42

Draws a reproducible sample of report tables whose header paths change with
reglens/ingestion/header_geometry.py, and writes data/cache/header_geometry_review/
(PDF region, baseline and geometric header path per column). The verdict per table
(better / same / worse) is judged by eye and added to the result by hand.
"""

import argparse
import html
import random
import sys

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument

from reglens.config import get_settings
from reglens.evaluation.table_check import render_region, tables_of
from reglens.ingestion.header_geometry import geometric_header, read_page
from reglens.ingestion.models import ParsedDocument, TableData
from reglens.ingestion.table_rows import header_paths, with_header


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--exclude", nargs="*", default=[], help="table ids to skip")
    args = parser.parse_args()

    out_dir = settings.docling_cache_dir.parent / "header_geometry_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    changed: list[tuple[TableData, TableData, str]] = []
    for path in sorted((settings.processed_dir / "parsed").glob("*.json")):
        doc = ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if doc.type_doc != "reports":
            continue
        docling = DoclingDocument.load_from_json(settings.docling_cache_dir / f"{doc.doc_id}.json")
        pdf = pdfium.PdfDocument(settings.raw_dir / "reports" / f"{doc.doc_id}.pdf")
        items = tables_of(docling)
        pages = {}
        for block in doc.blocks:
            t = block.table
            if t is None or not t.cells or t.table_id in args.exclude:
                continue
            item = items[int(t.table_id.split("#tbl-")[1]) - 1]
            page_no = item.prov[0].page_no
            if page_no not in pages:
                pages[page_no] = read_page(pdf[page_no - 1])
            geo = geometric_header(
                item, t, doc.langue, pages[page_no], docling.pages[page_no].size.height
            )
            base = with_header(t, doc.langue)
            if geo is not None and header_paths(geo) != header_paths(base):
                changed.append((base, geo, doc.doc_id))
    sample = random.Random(args.seed).sample(changed, min(args.n, len(changed)))
    sections = []
    for base, geo, doc_id in sample:
        docling = DoclingDocument.load_from_json(settings.docling_cache_dir / f"{doc_id}.json")
        item = tables_of(docling)[int(base.table_id.split("#tbl-")[1]) - 1]
        pdf = pdfium.PdfDocument(settings.raw_dir / "reports" / f"{doc_id}.pdf")
        png = base.table_id.replace("#", "_") + ".png"
        render_region(pdf, docling, item, out_dir / png)
        rows = "".join(
            f"<tr><td>c{c}</td><td>{html.escape(b)}</td><td>{html.escape(g)}</td></tr>"
            for c, (b, g) in enumerate(zip(header_paths(base), header_paths(geo), strict=True))
        )
        sections.append(
            f"<h2>{html.escape(base.table_id)}</h2><img src='{png}'>"
            f"<table border=1><tr><th></th><th>baseline</th><th>geometry</th></tr>"
            f"{rows}</table>"
        )
        print(f"== {base.table_id}")
        for c, (b, g) in enumerate(zip(header_paths(base), header_paths(geo), strict=True)):
            print(f"  c{c}\n    base: {b[:150]}\n    geo:  {g[:150]}")
    (out_dir / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Geometric headers</title>"
        "<style>body{font:13px system-ui;margin:16px} img{max-width:100%}</style>"
        + "".join(sections),
        encoding="utf-8",
    )
    print(f"changed tables: {len(changed)}; review: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
