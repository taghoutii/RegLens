"""Measure header spans rebuilt from the PDF geometry against the current headers.

Usage (repository root, virtual environment active, after scripts/parse_corpus.py):
    python scripts/measure_header_geometry.py

Baseline: the stored table with header-row inference (reglens/ingestion/table_rows.py).
Candidate: the same table with its header rebuilt by reglens/ingestion/header_geometry.py
(baseline kept where the method returns None).

1. Header-path accuracy on the value columns of the 10 visually labelled tables, against
   gold paths written from the PDF images before the method was run
   (data/cache/table_review/header_gold.json; report content, so not committed).
2. Value check on the 20 test tables of results/2026-11-20_table_cells_2.json: non-empty
   cells found in the PDF text layer of the table region, number cells found, and the
   share of the region's words present in the table (coverage).
3. Impact on every report table: handled by the method, header paths changed.

Adoption rule (fixed before the first run): accuracy at least 10 points above the
baseline, no column correct under the baseline becoming wrong on the 4 tables labelled
correct, and no drop on any of the three value-check numbers.

Writes results/<date>_header_geometry.json: ids and counts only.
"""

import json
import re
import sys
from collections import defaultdict
from datetime import date
from typing import Any

import pypdfium2 as pdfium
from docling_core.types.doc.document import DoclingDocument

from reglens.config import get_settings
from reglens.evaluation.results import write_result
from reglens.evaluation.table_check import normalize, region_text, tables_of
from reglens.ingestion.header_geometry import PageGeometry, geometric_header, read_page
from reglens.ingestion.models import ParsedDocument, TableData
from reglens.ingestion.table_rows import header_paths, with_header

LABELS = "2026-11-20_table_header_flags.json"
TEST_TABLES = "2026-11-20_table_cells_2.json"
_NUMBER = re.compile(r"[-+()\d.,%\s]+")


def path_key(labels: list[str] | str) -> str:
    text = "".join(labels) if isinstance(labels, list) else labels.replace(" > ", "")
    return normalize(text).lower()


def value_check(table: TableData, region: str) -> dict[str, int]:
    reference = normalize(region)
    out = {"cells": 0, "found": 0, "number_cells": 0, "number_found": 0}
    for row in table.cells:
        previous = None
        for text in row:
            if not normalize(text) or text == previous:  # a span repeats its text
                previous = text
                continue
            previous = text
            found = normalize(text) in reference
            out["cells"] += 1
            out["found"] += found
            if _NUMBER.fullmatch(text):
                out["number_cells"] += 1
                out["number_found"] += found
    extracted = normalize("".join(x for row in table.cells for x in row))
    words = [w for w in (normalize(w) for w in region.split()) if w]
    out["words"] = len(words)
    out["covered"] = sum(1 for w in words if w in extracted)
    return out


def main() -> int:
    settings = get_settings()
    stored: dict[str, tuple[TableData, str]] = {}
    for path in sorted((settings.processed_dir / "parsed").glob("*.json")):
        doc = ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if doc.type_doc == "reports":
            for b in doc.blocks:
                if b.table is not None and b.table.cells:
                    stored[b.table.table_id] = (b.table, doc.langue)
    if not stored:
        print("no parsed report yet: run scripts/parse_corpus.py")
        return 1

    docs: dict[str, DoclingDocument] = {}
    pdfs: dict[str, pdfium.PdfDocument] = {}
    pages: dict[tuple[str, int], PageGeometry] = {}

    def both(table_id: str) -> tuple[TableData, TableData | None, str]:
        """(baseline, geometric or None, region text) for one table."""
        table, langue = stored[table_id]
        doc_id, index = table_id.split("#tbl-")
        if doc_id not in docs:
            docs[doc_id] = DoclingDocument.load_from_json(
                settings.docling_cache_dir / f"{doc_id}.json"
            )
            pdfs[doc_id] = pdfium.PdfDocument(settings.raw_dir / "reports" / f"{doc_id}.pdf")
        item = tables_of(docs[doc_id])[int(index) - 1]
        page_no = item.prov[0].page_no
        if (doc_id, page_no) not in pages:
            pages[doc_id, page_no] = read_page(pdfs[doc_id][page_no - 1])
        height = docs[doc_id].pages[page_no].size.height
        geo = geometric_header(item, table, langue, pages[doc_id, page_no], height)
        return with_header(table, langue), geo, region_text(pdfs[doc_id], docs[doc_id], item)

    # 1. accuracy on the labelled tables
    gold = json.loads(
        (settings.docling_cache_dir.parent / "table_review" / "header_gold.json").read_text(
            encoding="utf-8"
        )
    )["tables"]
    labels = {
        x["table"]: x
        for x in json.loads((settings.results_dir / LABELS).read_text(encoding="utf-8"))["labels"]
    }
    per_table: list[dict[str, Any]] = []
    totals: dict[str, int] = defaultdict(int)
    for table_id, columns in gold.items():
        base, geo, _ = both(table_id)
        cand = geo or base
        bp, cp = header_paths(base), header_paths(cand)
        ok_b = {
            c: c_int < len(bp) and path_key(bp[c_int]) == path_key(g)
            for c, g in columns.items()
            for c_int in [int(c)]
        }
        ok_c = {
            c: c_int < len(cp) and path_key(cp[c_int]) == path_key(g)
            for c, g in columns.items()
            for c_int in [int(c)]
        }
        broken = [c for c in columns if ok_b[c] and not ok_c[c]]
        error = labels[table_id]["header_error"]
        per_table.append(
            {
                "table": table_id,
                "header_error_label": error,
                "value_columns": len(columns),
                "geometry_applied": geo is not None,
                "baseline_correct": sum(ok_b.values()),
                "geometry_correct": sum(ok_c.values()),
                "correct_became_wrong": broken,
            }
        )
        totals["columns"] += len(columns)
        totals["baseline_correct"] += sum(ok_b.values())
        totals["geometry_correct"] += sum(ok_c.values())
        if not error:
            totals["broken_on_correct_tables"] += len(broken)
        print(
            f"{table_id}: baseline {sum(ok_b.values())}/{len(columns)}, geometry "
            f"{sum(ok_c.values())}/{len(columns)}{' (not applied)' if geo is None else ''}"
        )
        for c in columns:
            if not ok_c[c]:
                print(f"   c{c} got: {cp[int(c)][:110]!r}")

    # 2. value check on the 20 test tables
    test_ids = [
        t["table"]
        for t in json.loads((settings.results_dir / TEST_TABLES).read_text(encoding="utf-8"))[
            "tables"
        ]
    ]
    check: dict[str, dict[str, int]] = {"baseline": defaultdict(int), "geometry": defaultdict(int)}
    applied_on_test = 0
    for table_id in test_ids:
        base, geo, region = both(table_id)
        applied_on_test += geo is not None
        for name, t in (("baseline", base), ("geometry", geo or base)):
            for k, v in value_check(t, region).items():
                check[name][k] += v
    value_summary = {
        name: {
            **dict(v),
            "found_rate": round(v["found"] / v["cells"], 4),
            "coverage": round(v["covered"] / v["words"], 4),
        }
        for name, v in check.items()
    }
    print(f"20 test tables (geometry applied on {applied_on_test}): {value_summary}")

    # 3. impact on all tables
    applied = changed = 0
    for table_id in stored:
        base, geo, _ = both(table_id)
        applied += geo is not None
        changed += geo is not None and header_paths(geo) != header_paths(base)
    print(f"all tables: {len(stored)}, geometry applied {applied}, header paths changed {changed}")

    accuracy_b = totals["baseline_correct"] / totals["columns"]
    accuracy_c = totals["geometry_correct"] / totals["columns"]
    vb, vc = value_summary["baseline"], value_summary["geometry"]
    rule = {
        "accuracy_gain_at_least_10_points": accuracy_c - accuracy_b >= 0.10,
        "no_regression_on_correct_tables": totals["broken_on_correct_tables"] == 0,
        "no_drop_in_cells_found": vc["found_rate"] >= vb["found_rate"],
        "no_drop_in_number_cells_found": vc["number_found"] >= vb["number_found"]
        and vc["number_found"] == vc["number_cells"],
        "no_drop_in_coverage": vc["coverage"] >= vb["coverage"],
    }
    result = {
        "name": "header_geometry",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": "baseline = stored table + header-row inference; geometry = header rows "
        "rebuilt from pypdfium2 characters, filled boxes and rules, columns from Docling's "
        "body cell boxes (reglens/ingestion/header_geometry.py). Accuracy: value columns "
        "whose full header path equals the gold path (whitespace-insensitive).",
        "adoption_rule": "accuracy >= baseline + 10 points; 0 columns correct under the "
        "baseline become wrong on the tables labelled correct; on the 20 test tables no drop "
        "in cells found, number cells found or word coverage",
        "labelled_tables": {
            "gold": "data/cache/table_review/header_gold.json (not committed)",
            "value_columns": totals["columns"],
            "baseline_accuracy": round(accuracy_b, 3),
            "geometry_accuracy": round(accuracy_c, 3),
            "tables": per_table,
        },
        "test_tables": {"geometry_applied": f"{applied_on_test}/{len(test_ids)}", **value_summary},
        "all_tables": {
            "tables": len(stored),
            "geometry_applied": applied,
            "header_paths_changed": changed,
        },
        "adoption": {**rule, "adopted": all(rule.values())},
    }
    print(json.dumps(result["adoption"], indent=1))
    print(f"written: {write_result(settings.results_dir, result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
