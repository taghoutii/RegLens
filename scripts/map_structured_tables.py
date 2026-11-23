"""Map the ING spreadsheet appendix to the ING tables Docling extracted (prototype).

Usage (repository root, virtual environment active, after scripts/parse_corpus.py):
    python scripts/map_structured_tables.py

The spreadsheet (data/raw/structured/ing_group_pillar3_2025.xlsx, the bank's "appendices
additional Pillar III disclosures") is read block by block. Each Docling table of the ING
report is matched to the block holding its numbers (reglens/ingestion/structured_tables.py).
Nothing in the corpus changes: this measures how many tables map with certainty.

Writes results/<date>_structured_tables.json: table ids, sheet names and counts only.
"""

import re
import sys
from collections import Counter
from datetime import date
from typing import Any

from reglens.config import get_settings
from reglens.evaluation.results import write_result
from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.structured_tables import (
    align_rows,
    informative_numbers,
    match_table,
    printed_forms,
    read_blocks,
)

DOC_ID = "ing_group_pillar3_2025"
_CODE = re.compile(r"\bEU[\s-]?([A-Z]{2,6}\d{0,2}[A-Z]?)\b")


def template_code(texts: list[str]) -> str | None:
    for text in texts:
        m = _CODE.search(text)
        if m:
            return m.group(1).upper()
    return None


def main() -> int:
    settings = get_settings()
    xlsx = settings.raw_dir / "structured" / f"{DOC_ID}.xlsx"
    parsed = settings.processed_dir / "parsed" / f"{DOC_ID}__en.json"
    if not xlsx.exists() or not parsed.exists():
        print(f"missing input: {xlsx} or {parsed}")
        return 1
    doc = ParsedDocument.model_validate_json(parsed.read_text(encoding="utf-8"))
    blocks = read_blocks(xlsx)
    keys = {b.name: printed_forms(b.numbers()) for b in blocks}
    print(f"{len(blocks)} spreadsheet blocks in {len({b.sheet for b in blocks})} sheets")

    rows: list[dict[str, Any]] = []
    by_block: Counter[str] = Counter()
    for block in doc.blocks:
        table = block.table
        if table is None or not table.cells:
            continue
        m = match_table(table, doc.langue, blocks, keys)
        head = [table.caption or "", *(" ".join(r) for r in table.cells[:2]), block.section or ""]
        code = template_code(head)
        sheet = m.best.split("/")[0] if m.best else None
        code_agrees = (
            None
            if code is None or sheet is None
            else (sheet.replace("-", "").upper().startswith(code.replace("-", "")))
        )
        # reverse coverage: share of the block's informative values present in the table
        table_forms = printed_forms([n.value for n in informative_numbers(table, doc.langue)])
        block_values = [
            v
            for b in blocks
            if b.name == m.best
            for v in b.numbers()
            if v and (abs(v) >= 100 or v != int(v))
        ]
        reverse = (
            (
                sum(
                    1
                    for v in block_values
                    if any(
                        (d, p, round(abs(v) * (100 if p else 1), d)) in table_forms
                        for d in range(5)
                        for p in (False, True)
                    )
                )
                / len(block_values)
            )
            if block_values
            else 0.0
        )
        best_block = next((b for b in blocks if b.name == m.best), None)
        alignment = align_rows(table, doc.langue, best_block) if best_block else None
        if m.certain and m.best:
            by_block[m.best] += 1
        rows.append(
            {
                "table": table.table_id,
                "numbers": m.numbers,
                "best": m.best,
                "best_share": m.best_share,
                "second": m.second,
                "second_share": m.second_share,
                "certain": m.certain,
                "template_code": code,
                "code_agrees_with_sheet": code_agrees,
                "block_values_in_table": round(reverse, 3),
                "rows_with_numbers": alignment.rows if alignment else 0,
                "rows_aligned": alignment.aligned if alignment else 0,
                "rows_in_order": alignment.in_order if alignment else None,
                "row_level_certain": bool(m.certain and alignment and alignment.complete),
            }
        )

    certain = [r for r in rows if r["certain"]]
    shared = {b: n for b, n in by_block.items() if n > 1}
    whole = [r for r in certain if r["block_values_in_table"] >= 0.95]
    summary = {
        "tables": len(rows),
        "tables_with_5_or_more_informative_numbers": sum(r["numbers"] >= 5 for r in rows),
        "certain": len(certain),
        "certain_and_every_row_aligned_in_order": sum(r["row_level_certain"] for r in rows),
        "certain_whole_block (>= 95 % of the block's values in the table)": len(whole),
        "certain_part_of_a_block": len(certain) - len(whole),
        "blocks_matched_by_several_tables": len(shared),
        "certain_with_a_template_code": sum(r["template_code"] is not None for r in certain),
        "certain_code_disagrees_with_sheet": sum(
            r["code_agrees_with_sheet"] is False for r in certain
        ),
        "best_share_95_but_not_unique": sum(
            r["best_share"] >= 0.95 and not r["certain"] and r["numbers"] >= 5 for r in rows
        ),
        "best_share_80_to_95": sum(0.8 <= r["best_share"] < 0.95 for r in rows),
    }
    for k, v in summary.items():
        print(f"{k}: {v}")
    result = {
        "name": "structured_tables",
        "date": date.today().isoformat(),
        "phase": 3,
        "method": "ING xlsx appendix read block by block (openpyxl, values only); each Docling "
        "table matched to the block holding its informative numbers (non-zero, not a bare "
        "integer < 100, not a year) once spreadsheet values are rounded as printed "
        "(0-4 decimals, fraction or percentage, sign ignored). Certain = >= 5 informative "
        "numbers, >= 95 % of them in the best block, runner-up block >= 0.2 lower. Row level: "
        "every Docling row with an informative number finds the one spreadsheet row of that "
        "block holding all of its numbers, in the same order.",
        "source": f"data/raw/structured/{DOC_ID}.xlsx",
        "blocks": len(blocks),
        "summary": summary,
        "blocks_matched_by_several_tables": shared,
        "tables": rows,
    }
    out = write_result(settings.results_dir, result)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
