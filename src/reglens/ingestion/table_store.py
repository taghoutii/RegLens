"""Store exact final table values in PostgreSQL.

Two tables: one row per table (where it comes from) and one row per cell
(row index, column index, value). Exact numbers are later read from here, never
re-computed or paraphrased by the LLM.

Idempotent: storing a document first deletes its previous tables, inside the same
transaction, so re-running never creates duplicates and a crash never leaves half a
document. Only masked documents are accepted (masking rule: nothing unmasked is
written anywhere).

This is a synchronous client, used by batch scripts. The Phase 7 worker will use
psycopg's async connection with the same SQL.
"""

import psycopg

from reglens.config import Settings
from reglens.ingestion.models import ParsedDocument

SCHEMA = """
CREATE TABLE IF NOT EXISTS report_tables (
    table_id     TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    langue       TEXT NOT NULL,
    source       TEXT NOT NULL,
    page_number  INTEGER,
    section      TEXT,
    caption      TEXT,
    n_rows       INTEGER NOT NULL,
    n_cols       INTEGER NOT NULL,
    header_rows  INTEGER NOT NULL,
    header_uncertain BOOLEAN NOT NULL DEFAULT FALSE,  -- see ingestion/table_quality.py
    header_issues    TEXT[] NOT NULL DEFAULT '{}',
    header_inferred  BOOLEAN NOT NULL DEFAULT FALSE,  -- header rows inferred, not marked
    content_source   TEXT NOT NULL DEFAULT 'pdf'      -- 'pdf' or 'spreadsheet'
);
CREATE INDEX IF NOT EXISTS report_tables_doc ON report_tables (doc_id);
CREATE TABLE IF NOT EXISTS report_table_cells (
    table_id   TEXT NOT NULL REFERENCES report_tables (table_id) ON DELETE CASCADE,
    row_idx    INTEGER NOT NULL,
    col_idx    INTEGER NOT NULL,
    value      TEXT NOT NULL,
    is_header  BOOLEAN NOT NULL,
    low_confidence BOOLEAN NOT NULL DEFAULT FALSE,  -- looks like several fused values
    PRIMARY KEY (table_id, row_idx, col_idx)
);
-- databases created before the column existed
ALTER TABLE report_table_cells
    ADD COLUMN IF NOT EXISTS low_confidence BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE report_tables
    ADD COLUMN IF NOT EXISTS header_uncertain BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE report_tables ADD COLUMN IF NOT EXISTS header_issues TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE report_tables
    ADD COLUMN IF NOT EXISTS header_inferred BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE report_tables ADD COLUMN IF NOT EXISTS content_source TEXT NOT NULL DEFAULT 'pdf';
"""


def conninfo(settings: Settings) -> str:
    return psycopg.conninfo.make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        connect_timeout=5,
    )


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(SCHEMA)


def store_tables(conn: psycopg.Connection, doc: ParsedDocument) -> int:
    """Replace all tables of one document; return the number of cells written."""
    if not doc.masked:
        raise ValueError(f"{doc.doc_id}: refusing to store an unmasked document")
    n_cells = 0
    with conn.transaction():
        conn.execute("DELETE FROM report_tables WHERE doc_id = %s", (doc.doc_id,))
        for block in doc.blocks:
            t = block.table
            if t is None:
                continue
            conn.execute(
                "INSERT INTO report_tables (table_id, doc_id, langue, source, page_number, "
                "section, caption, n_rows, n_cols, header_rows, header_uncertain, header_issues, "
                "header_inferred, content_source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (t.table_id, doc.doc_id, doc.langue, doc.source, block.page_number,
                 block.section, t.caption, t.n_rows, t.n_cols, t.header_rows,
                 t.header_uncertain, t.header_issues, t.header_inferred, t.content_source),
            )  # fmt: skip
            flagged = set(t.low_confidence)
            rows = [
                (t.table_id, r, c, value, r < t.header_rows, (r, c) in flagged)
                for r, row in enumerate(t.cells)
                for c, value in enumerate(row)
            ]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO report_table_cells "
                    "(table_id, row_idx, col_idx, value, is_header, low_confidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    rows,
                )
            n_cells += len(rows)
    return n_cells


def delete_other_documents(conn: psycopg.Connection, keep: set[str]) -> list[str]:
    """Delete the tables of every document not in `keep` (reports no longer listed in
    sources.yaml); their cells go with them (ON DELETE CASCADE). Returns the doc_ids."""
    with conn.transaction():
        rows = conn.execute(
            "DELETE FROM report_tables WHERE NOT (doc_id = ANY(%s)) RETURNING doc_id",
            (sorted(keep),),
        ).fetchall()
    return sorted({r[0] for r in rows})


def load_cells(conn: psycopg.Connection, table_id: str) -> list[list[str]]:
    """Read a table back as a grid (used by the cell-accuracy check)."""
    rows = conn.execute(
        "SELECT row_idx, col_idx, value FROM report_table_cells WHERE table_id = %s "
        "ORDER BY row_idx, col_idx",
        (table_id,),
    ).fetchall()
    grid: dict[int, dict[int, str]] = {}
    for r, c, v in rows:
        grid.setdefault(r, {})[c] = v
    return [[grid[r][c] for c in sorted(grid[r])] for r in sorted(grid)]
