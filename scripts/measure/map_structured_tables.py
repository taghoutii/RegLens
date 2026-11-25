"""Measure the spreadsheet replacement of report tables (the pipeline's own step).

Usage (repository root, virtual environment active, after scripts/run_docling.py):
    python scripts/measure/map_structured_tables.py

For every report with a spreadsheet appendix in sources.yaml, the PDF tables (Docling
cache, header rows inferred as in the pipeline) go through
reglens/ingestion/structured_tables.apply_spreadsheet, exactly as scripts/parse_corpus.py
does: block match, row alignment, value check. Counts per outcome and the reasons a
table keeps its PDF content are recorded.

Writes results/<date>_structured_tables.json: table ids, sheet names and counts only.
"""

import sys
from collections import Counter
from datetime import date
from typing import Any

from reglens.config import get_settings
from reglens.corpus.sources import load_corpus
from reglens.evaluation.report_tables import cached_reports
from reglens.evaluation.results import write_result
from reglens.ingestion.parse_pdf import from_docling
from reglens.ingestion.structured_tables import apply_spreadsheet, read_blocks


def main() -> int:
    settings = get_settings()
    reports = [s for s in load_corpus(settings.sources_file).reports if s.appendix is not None]
    cached = cached_reports(settings, {s.id for s in reports})
    per_report: dict[str, Any] = {}
    for src in reports:
        assert src.appendix is not None
        xlsx = settings.raw_dir / "reports" / f"{src.id}.{src.appendix.format}"
        if src.id not in cached or not xlsx.exists():
            print(f"{src.id}: missing Docling cache or {xlsx}")
            return 1
        docling, _ = cached[src.id]
        doc = from_docling(docling, doc_id=src.id, langue=src.language or "en", source=src.id,
                           max_header_rows=settings.table_max_inferred_header_rows)  # fmt: skip
        replacements = apply_spreadsheet(doc, xlsx, min_numbers=settings.spreadsheet_min_numbers,
                                         min_share=settings.spreadsheet_min_share,
                                         margin=settings.spreadsheet_margin)  # fmt: skip
        blocks = read_blocks(xlsx)
        reasons = Counter(r.reason for r in replacements)
        per_report[src.id] = {
            "spreadsheet_blocks": len(blocks),
            "sheets": len({b.sheet for b in blocks}),
            "tables": len(replacements),
            "outcomes": dict(reasons),
            "replaced": [r.table_id for r in replacements if r.replaced],
            # a replaced table must keep column labels (the spreadsheet's header rows)
            "replaced_without_header_rows": [
                b.table.table_id
                for b in doc.blocks
                if b.table is not None
                and b.table.content_source == "spreadsheet"
                and b.table.header_rows == 0
            ],  # fmt: skip
            "certain_not_replaced": [
                {
                    "table": r.table_id,
                    "block": r.match.best,
                    "rows_aligned": r.rows_aligned,
                    "reason": r.reason,
                }
                for r in replacements
                if r.match.certain and not r.replaced
            ],  # fmt: skip
        }
        print(f"{src.id}: {dict(reasons)}, replaced without header rows: "
              f"{len(per_report[src.id]['replaced_without_header_rows'])}")  # fmt: skip
    result = {
        "name": "structured_tables",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": "PDF tables (Docling cache, inferred header rows) through apply_spreadsheet: "
        f"certain block (>= {settings.spreadsheet_min_numbers} informative numbers, >= "
        f"{settings.spreadsheet_min_share:.0%} in the best block, runner-up >= "
        f"{settings.spreadsheet_margin} lower), every row aligned in order, every PDF number "
        "printed by the rebuilt table; otherwise the PDF content is kept",
        "reports": per_report,
    }
    print(f"written: {write_result(settings.results_dir, result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
