"""Measure the two table improvements on the parsed reports (nothing is rewritten).

Usage (repository root, virtual environment active, after scripts/parse_corpus.py):
    python scripts/measure_table_rows.py

1. Total check (reglens/ingestion/table_rows.py): per report, tables with a "Total" row,
   tables whose total could be checked, tables where a checked total does not add up;
   and the same on the 10 visually labelled tables of results/..._table_header_flags.json.
2. Row serialization: tokens (bge-m3 tokenizer) of the self-describing row lines vs the
   Markdown grid, per table.
Writes results/<date>_table_rows.json: ids, counts and token statistics only (no table
content).
"""

import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

from reglens.config import get_settings
from reglens.evaluation.results import write_result
from reglens.ingestion.encoders import Bgem3
from reglens.ingestion.models import ParsedDocument, TableData
from reglens.ingestion.table_rows import check_totals, row_lines

LABELS = "2026-11-20_table_header_flags.json"


def _stats(values: Sequence[float]) -> dict[str, float]:
    s = sorted(values)
    return {"median": statistics.median(s), "p95": s[int(0.95 * (len(s) - 1))], "max": s[-1]}


def main() -> int:
    settings = get_settings()
    tables: list[tuple[TableData, str, int | None]] = []
    for path in sorted((settings.processed_dir / "parsed").glob("*.json")):
        doc = ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if doc.type_doc == "reports":
            tables += [(b.table, doc.langue, b.page_number) for b in doc.blocks
                       if b.table is not None and b.table.cells]  # fmt: skip
    if not tables:
        print("no parsed report yet: run scripts/parse_corpus.py")
        return 1

    per_report: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    failed_ids, checkable_ids = [], set()
    by_id: dict[str, Any] = {}
    for table, langue, _page in tables:
        totals = check_totals(table, langue)
        report = per_report[table.table_id.split("#")[0]]
        report["tables"] += 1
        report["with_total_row"] += totals.total_rows > 0
        report["checkable"] += totals.checkable
        report["failed"] += totals.failed
        by_id[table.table_id] = totals
        if totals.checkable:
            checkable_ids.add(table.table_id)
        if totals.failed:
            failed_ids.append(table.table_id)
    keys = ["tables", "with_total_row", "checkable", "failed"]
    total = {k: sum(r[k] for r in per_report.values()) for k in keys}
    print(f"total check: {total}")

    labels = json.loads((settings.results_dir / LABELS).read_text(encoding="utf-8"))["labels"]
    labelled = []
    for item in labels:
        totals = by_id[item["table"]]
        labelled.append({"table": item["table"], "header_error": item["header_error"],
                         "severity": item["severity"], "total_rows": totals.total_rows,
                         "checkable": totals.checkable, "failed": totals.failed})  # fmt: skip
    caught = sum(1 for x in labelled if x["header_error"] and x["failed"])
    errors = sum(1 for x in labelled if x["header_error"])
    print(f"labelled tables: {caught}/{errors} header errors caught by the total check")

    bge = Bgem3(settings.embedding_model)  # tokenizer only: the model is loaded lazily
    growth, md_tokens, line_tokens, longest_line = [], [], [], []
    for table, langue, _page in tables:
        md = bge.count(table.to_markdown())
        lines = row_lines(table, langue)
        rows = bge.count("\n".join(lines)) if lines else 0
        md_tokens.append(md)
        line_tokens.append(rows)
        longest_line.append(max((bge.count(x) for x in lines), default=0))
        if md:
            growth.append(rows / md)
    serialization = {
        "markdown_tokens": {**_stats(md_tokens), "total": sum(md_tokens)},
        "row_line_tokens": {**_stats(line_tokens), "total": sum(line_tokens)},
        "ratio_lines_to_markdown": {k: round(v, 2) for k, v in _stats(growth).items()},
        "longest_single_row_line": _stats(longest_line),
        "tables_with_a_row_line_over_512_tokens": sum(x > 512 for x in longest_line),
    }
    print(f"serialization: {serialization}")

    result = {
        "name": "table_rows",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": "total check and row serialization of reglens/ingestion/table_rows.py on "
        "every parsed report table; tokens with the bge-m3 tokenizer",
        "total_check": {
            "per_report": {k: dict(v) for k, v in sorted(per_report.items())},
            "total": total,
            "failed_tables": failed_ids,
        },  # fmt: skip
        "labelled_tables": {
            "source": LABELS,
            "tables": labelled,
            "header_errors_caught": f"{caught}/{errors}",
        },  # fmt: skip
        "serialization": serialization,
    }
    out = write_result(settings.results_dir, result)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
