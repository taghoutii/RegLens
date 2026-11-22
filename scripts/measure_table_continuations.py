"""Measure how many report tables look continued on the next page (nothing is merged).

Usage (repository root, virtual environment active, after scripts/run_docling.py):
    python scripts/measure_table_continuations.py --review 10 --seed 42
    # second check: "Total" filter on, pairs reviewed before left out of the sample
    python scripts/measure_table_continuations.py --skip-total --seed 43 \
        --exclude results/2026-11-22_table_continuations.json

Rules: see reglens/evaluation/table_continuations.py. Writes
results/<date>_table_continuations.json (ids, pages and counts only: no table content)
and, for a sample of candidates, data/cache/continuation_review/index.html with the end
of table A (page N) above the start of table B (page N+1), to judge by eye. That page
shows unmasked report content, so it stays in data/cache/ (not committed, never ingested).
"""

import argparse
import html
import json
import random
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.table.table import TableItem

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.evaluation.results import write_result
from reglens.evaluation.table_check import tables_of
from reglens.evaluation.table_continuations import (
    adjacent_pairs,
    candidates,
    check_pair,
    items_of,
)
from reglens.ingestion.parse_pdf import cache_paths

SCALE = 1.5


def render_strip(
    pdf: pdfium.PdfDocument, doc: DoclingDocument, table: TableItem, *, end: bool, out: Path
) -> None:
    """The table's box extended to the page bottom (end=True, table A) or from the page
    top (end=False, table B), so whatever lies between the two parts is visible."""
    prov = table.prov[0]
    height = doc.pages[prov.page_no].size.height
    box = prov.bbox.to_top_left_origin(height)
    image = pdf[prov.page_no - 1].render(scale=SCALE).to_pil()
    top, bottom = (box.t, height) if end else (0.0, box.b)
    image.crop((0, int(top * SCALE), image.width, int(bottom * SCALE))).save(out)


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--review", type=int, default=10, help="candidates rendered for review")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--skip-total",
        action="store_true",
        help="drop candidates whose first table ends with a 'Total' row",
    )
    parser.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="result file whose review_sample is left out of the new sample",
    )
    args = parser.parse_args()
    reviewed = {p for f in args.exclude for p in json.loads(f.read_text("utf-8"))["review_sample"]}

    docs: dict[str, DoclingDocument] = {}
    for src in load_corpus(settings.sources_file).reports:
        path = cache_paths(settings.docling_cache_dir, src.id)[0]
        if path.exists():
            docs[src.id] = DoclingDocument.load_from_json(path)
    if not docs:
        print("no report in the Docling cache yet: run scripts/run_docling.py")
        return 1

    per_report: dict[str, dict[str, int]] = {}
    found: list[tuple[str, dict[str, object]]] = []
    for doc_id, doc in docs.items():
        items = items_of(doc)
        checks = [check_pair(a, b) for a, b in adjacent_pairs(items)]
        cands = candidates(items)
        per_report[doc_id] = {
            "tables": sum(1 for i in items if i.label == "table"),
            "adjacent_pairs_on_next_page": len(checks),
            "same_columns": sum(c.same_cols for c in checks),
            "columns_within_one": sum(c.cols_within_one for c in checks),
            "caption_absent_or_continued": sum(c.caption_ok for c in checks),
            "candidates": len(cands),
            "candidate_caption_says_continued": sum(c.caption_says_continued for c in cands),
        }
        found += [(doc_id, asdict(c)) for c in cands]
        print(f"{doc_id:32} {per_report[doc_id]}")

    totals = {k: sum(r[k] for r in per_report.values()) for k in next(iter(per_report.values()))}
    tables_in_candidates = len({(d, c[k]) for d, c in found for k in ("first", "second")})
    print(f"total: {totals}; tables involved: {tables_in_candidates}")

    kept = [(d, c) for d, c in found if not (args.skip_total and c["first_ends_with_total"])]
    print(f"after the 'Total' filter: {len(kept)}" if args.skip_total else "")

    # Review sample: spread over the reports (round robin), random inside each (fixed seed),
    # leaving out pairs already reviewed.
    rng = random.Random(args.seed)
    by_report: dict[str, list[dict[str, object]]] = {}
    for doc_id, c in kept:
        if f"{doc_id}#tbl-{c['first']}->tbl-{c['second']}" not in reviewed:
            by_report.setdefault(doc_id, []).append(c)
    pool = sum(len(v) for v in by_report.values())
    for cands_of in by_report.values():
        rng.shuffle(cands_of)
    sample: list[tuple[str, dict[str, object]]] = []
    while len(sample) < min(args.review, pool):
        for doc_id in sorted(by_report):
            if by_report[doc_id] and len(sample) < args.review:
                sample.append((doc_id, by_report[doc_id].pop()))

    review_dir = settings.docling_cache_dir.parent / "continuation_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    sections = []
    for doc_id, c in sample:
        pdf = pdfium.PdfDocument(settings.raw_dir / "reports" / f"{doc_id}.pdf")
        tables = tables_of(docs[doc_id])
        a, b = int(str(c["first"])), int(str(c["second"]))
        stem = f"{doc_id}_tbl-{a}-{b}"
        render_strip(pdf, docs[doc_id], tables[a - 1], end=True, out=review_dir / f"{stem}_a.png")
        render_strip(pdf, docs[doc_id], tables[b - 1], end=False, out=review_dir / f"{stem}_b.png")
        sections.append(
            f"<section><h2>{html.escape(doc_id)}: tbl-{a} (page {c['page']}) → tbl-{b} "
            f"(page {int(str(c['page'])) + 1}), {c['n_cols']} columns</h2>"
            f"<img src='{stem}_a.png'><hr><img src='{stem}_b.png'></section>"
        )
    (review_dir / "index.html").write_text(
        "<!doctype html><html><head><meta charset=utf-8><title>Continuation review</title>"
        "<style>body{font:14px system-ui;margin:16px}img{max-width:100%;border:1px solid #999}"
        "section{border-top:2px solid #ccc;padding:12px 0}</style></head><body>"
        "<h1>Is table B the continuation of table A?</h1>" + "\n".join(sections) + "</body></html>",
        encoding="utf-8",
    )

    result = {
        "name": "table_continuations",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": "candidate = table B is the next content item after table A (only page "
        "header/footer, footnote or caption between), on the next page, same column count, "
        "B without a caption or with a 'continued' caption (Docling reading order)",
        "seed": args.seed,
        "per_report": per_report,
        "total": {**totals, "tables_involved": tables_in_candidates},
        "candidates": [{"doc_id": d, **c} for d, c in found],
        "skip_total": args.skip_total,
        "candidates_after_filter": len(kept),
        "excluded_already_reviewed": sorted(reviewed),
        "sample_pool": pool,
        "review_sample": [f"{d}#tbl-{c['first']}->tbl-{c['second']}" for d, c in sample],
    }
    out = write_result(settings.results_dir, result)
    print(f"written: {out}\nreview:  {review_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
