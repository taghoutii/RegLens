"""Hand-built tables shared by the table tests."""

from reglens.ingestion.models import TableData


def make(cells: list[list[str]], header_rows: int) -> TableData:
    return TableData(table_id="rep#tbl-1", n_rows=len(cells), n_cols=len(cells[0]),
                     cells=cells, header_rows=header_rows)  # fmt: skip
