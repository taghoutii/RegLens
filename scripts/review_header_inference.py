"""Visual check of header-row inference on tables where Docling detected no header.

Usage (repository root, virtual environment active, after scripts/parse_corpus.py):
    python scripts/review_header_inference.py --n 10 --seed 42

Counts, over every report table with 0 detected header rows, how many header rows the
rule of reglens/ingestion/table_rows.infer_header_rows gives; then draws a reproducible
sample of tables that receive a header and writes data/cache/header_inference_review/
(PDF region next to the grid, inferred header rows in bold). That page contains report
content, so it stays in data/cache/. The labels are added to the result by hand.
"""

import argparse
import html
import random
import sys
from collections import Counter
from datetime import date

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument

from reglens.config import get_settings
from reglens.evaluation.results import write_result
from reglens.evaluation.table_check import render_region, tables_of
from reglens.ingestion.models import ParsedDocument, TableData
from reglens.ingestion.parse_pdf import cache_paths
from reglens.ingestion.table_rows import header_paths, infer_header_rows, with_header


def grid_html(table: TableData, n_header: int) -> str:
    rows = []
    for r, row in enumerate(table.cells):
        tag = " class=h" if r < n_header else ""
        cells = "".join(f"<td{tag}>{html.escape(x)}</td>" for x in row)
        rows.append(f"<tr><th>r{r}</th>{cells}</tr>")
    return "<table class=grid>" + "\n".join(rows) + "</table>"


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    candidates: list[tuple[TableData, str, int]] = []
    counts: Counter[int] = Counter()
    for path in sorted((settings.processed_dir / "parsed").glob("*.json")):
        doc = ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if doc.type_doc != "reports":
            continue
        for block in doc.blocks:
            t = block.table
            if t is None or not t.cells or t.header_rows:
                continue
            n = infer_header_rows(t, doc.langue)
            counts[n] += 1
            if n:
                candidates.append((t, doc.langue, n))
    if not candidates:
        print("no table without a header: run scripts/parse_corpus.py")
        return 1
    sample = random.Random(args.seed).sample(candidates, min(args.n, len(candidates)))

    out_dir = settings.docling_cache_dir.parent / "header_inference_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    docs: dict[str, DoclingDocument] = {}
    sections = []
    for table, langue, n in sample:
        doc_id, index = table.table_id.split("#tbl-")
        if doc_id not in docs:
            docs[doc_id] = DoclingDocument.load_from_json(
                cache_paths(settings.docling_cache_dir, doc_id)[0]
            )
        item = tables_of(docs[doc_id])[int(index) - 1]
        pdf = pdfium.PdfDocument(settings.raw_dir / "reports" / f"{doc_id}.pdf")
        png = f"{doc_id}_tbl-{index}.png"
        render_region(pdf, docs[doc_id], item, out_dir / png)
        paths = header_paths(with_header(table, langue))
        sections.append(
            f"<section><h2>{html.escape(table.table_id)}: {n} inferred header row(s)</h2>"
            f"<div class=pair><div><img src='{png}'></div><div>{grid_html(table, n)}"
            f"<p>Header paths: {html.escape(' | '.join(paths))}</p></div></div></section>"
        )
        print(f"{table.table_id}: {n} rows -> {' | '.join(paths)[:160]}")
    style = (
        "body{font:14px system-ui;margin:16px} .pair{display:flex;gap:16px;flex-wrap:wrap}"
        ".pair>div{flex:1 1 480px;overflow-x:auto} img{max-width:100%;border:1px solid #999}"
        ".grid{border-collapse:collapse;font-size:12px} .grid td,.grid th{border:1px solid "
        "#bbb;padding:2px 6px} td.h{font-weight:bold;background:#f6f6ff}"
    )
    (out_dir / "index.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>Header inference</title><style>{style}"
        f"</style><h1>Inferred header rows (bold)</h1>{''.join(sections)}",
        encoding="utf-8",
    )

    result = {
        "name": "header_inference",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": "tables with 0 detected header rows: leading rows with text beyond the "
        "first column and no number except years become header rows (at most 4), only if a "
        "row with numbers follows. Sample drawn with random.Random(seed).sample.",
        "seed": args.seed,
        "inferred_header_rows": {str(k): v for k, v in sorted(counts.items())},
        "tables_without_header": sum(counts.values()),
        "sample": [t.table_id for t, _, _ in sample],
    }
    print(f"written: {write_result(settings.results_dir, result)}\nreview: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
