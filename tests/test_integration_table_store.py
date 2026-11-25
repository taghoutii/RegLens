"""PostgreSQL table store: exact round trip, idempotent re-run, refuses unmasked documents.

Needs the dev PostgreSQL (docker compose up -d). Uses its own doc_id and cleans up.
"""

from collections.abc import Iterator

import psycopg
import pytest

from reglens.config import Settings
from reglens.ingestion.models import Block, ParsedDocument, TableData
from reglens.ingestion.table_store import (
    conninfo,
    delete_other_documents,
    ensure_schema,
    load_cells,
    store_tables,
)
from tests.conftest import postgres_reachable, require

pytestmark = pytest.mark.integration
DOC_ID = "pytest_table_store"


def _doc(masked: bool = True) -> ParsedDocument:
    table = TableData(
        table_id=f"{DOC_ID}#tbl-1",
        caption="Key metrics (EU KM1)",
        n_rows=3,
        n_cols=3,
        cells=[["", "2025", "2024"], ["CET1 ratio", "13.2%", "12,9 %"], ["Leverage", "4.1", ""]],
        header_rows=1,
    )
    block = Block(
        block_id=f"{table.table_id}/table",
        parent_id=table.table_id,
        kind="table",
        text="",
        page_number=7,
        table=table,
    )
    return ParsedDocument(
        doc_id=DOC_ID, source="x.pdf", type_doc="reports", langue="en", parser="test",
        blocks=[block], masked=masked,
    )  # fmt: skip


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    require(postgres_reachable(), "PostgreSQL not running (docker compose up -d)")
    with psycopg.connect(conninfo(Settings())) as c:
        ensure_schema(c)
        yield c
        with c.transaction():
            c.execute("DELETE FROM report_tables WHERE doc_id = %s", (DOC_ID,))


def test_cells_round_trip_exactly(conn: psycopg.Connection) -> None:
    doc = _doc()
    assert store_tables(conn, doc) == 9
    assert load_cells(conn, f"{DOC_ID}#tbl-1") == doc.blocks[0].table.cells  # type: ignore[union-attr]


def test_rerun_creates_no_duplicates(conn: psycopg.Connection) -> None:
    store_tables(conn, _doc())
    store_tables(conn, _doc())
    n = conn.execute(
        "SELECT count(*) FROM report_table_cells c JOIN report_tables t USING (table_id) "
        "WHERE t.doc_id = %s",
        (DOC_ID,),
    ).fetchone()
    assert n == (9,)


def test_unmasked_document_is_refused(conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="unmasked"):
        store_tables(conn, _doc(masked=False))


def test_low_confidence_flag_is_stored(conn: psycopg.Connection) -> None:
    doc = _doc()
    table = doc.blocks[0].table
    assert table is not None
    table.low_confidence = [(2, 1)]
    store_tables(conn, doc)
    flagged = conn.execute(
        "SELECT row_idx, col_idx FROM report_table_cells WHERE table_id = %s AND low_confidence",
        (table.table_id,),
    ).fetchall()
    assert flagged == [(2, 1)]


def test_tables_of_unlisted_documents_are_deleted(conn: psycopg.Connection) -> None:
    store_tables(conn, _doc())
    listed = {r[0] for r in conn.execute("SELECT DISTINCT doc_id FROM report_tables")} - {DOC_ID}
    assert delete_other_documents(conn, listed) == [DOC_ID]
    left = conn.execute("SELECT count(*) FROM report_table_cells WHERE table_id = %s",
                        (f"{DOC_ID}#tbl-1",)).fetchone()  # fmt: skip
    assert left == (0,)  # cells deleted with their table
    assert delete_other_documents(conn, listed) == []  # the other documents are kept
