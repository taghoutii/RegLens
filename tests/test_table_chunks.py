"""Table chunks: one chunk when the table fits, row groups otherwise, column groups for
a row too wide; the summary and quality flags on every chunk. Tokens are words
(tests/chunk_stubs.py)."""

from reglens.ingestion.chunking import Chunker, ChunkParams, table_groups
from reglens.ingestion.models import Block, ParsedDocument, TableData
from tests.chunk_stubs import WordTokens, stub_tools

TOKENS = WordTokens()


def table(n_rows: int, n_cols: int = 3) -> TableData:
    header = ["EUR m"] + [f"y{c}" for c in range(1, n_cols)]
    body = [[f"Row{r}"] + [f"{r * 10 + c},000" for c in range(1, n_cols)] for r in range(n_rows)]
    cells = [header, *body]
    return TableData(table_id="rep#tbl-1", caption="Own funds", n_rows=len(cells),
                     n_cols=n_cols, cells=cells, header_rows=1)  # fmt: skip


SUMMARY = "Table: Own funds\nSource: Report, page 4\nColumns: EUR m; y1; y2"


def test_small_table_is_one_chunk_with_summary_as_context() -> None:
    groups = table_groups(table(2), "en", SUMMARY, TOKENS, 512, 256)
    assert len(groups) == 1
    g = groups[0]
    assert g.context == SUMMARY
    assert g.text == table(2).to_markdown()  # the whole Markdown table, header included
    assert g.lines.split("\n")[0] == "Row0 — y1 = 1,000; y2 = 2,000"


def test_large_table_is_cut_into_groups_of_whole_rows() -> None:
    t = table(40)
    groups = table_groups(t, "en", SUMMARY, TOKENS, 60, 40)
    assert len(groups) > 1
    lines = [line for g in groups for line in g.lines.split("\n")]
    assert [x.split(" ")[0] for x in lines] == [f"Row{r}" for r in range(40)]  # once, in order
    markdown = t.to_markdown().split("\n")
    for g in groups:
        assert g.context == SUMMARY + "\n" + "\n".join(markdown[:2])  # summary + header rows
        assert all(line in markdown[2:] for line in g.text.split("\n"))  # exact table rows
        embedded = g.context + "\n" + g.lines
        assert TOKENS.count(embedded) <= 60


def test_context_is_shortened_when_the_header_is_too_long() -> None:
    groups = table_groups(table(40), "en", SUMMARY, TOKENS, 60, 12)
    assert groups[0].context == "Table: Own funds\nSource: Report, page 4"


def test_row_too_wide_is_cut_into_column_groups() -> None:
    t = table(1, n_cols=30)
    groups = table_groups(t, "en", SUMMARY, TOKENS, 40, 20)
    assert len(groups) > 1
    values = [cell for g in groups for cell in g.lines.split(" — ", 1)[1].split("; ")]
    assert len(values) == 29  # every value once
    assert all(g.text.startswith("| Row0 |") for g in groups)


def test_table_chunks_share_the_table_id_summary_and_flags() -> None:
    t = table(40)
    t.low_confidence = [(2, 1)]
    t.header_issues = ["same_label_cells"]
    doc = ParsedDocument(
        doc_id="rep", source="r.pdf", type_doc="reports", langue="en", parser="docling",
        masked=True, blocks=[
            Block(block_id="rep#sec-1/t1", parent_id="rep#sec-1", kind="text",
                  page_number=3, section="Capital", text="Capital ratios rose in 2025."),
            Block(block_id="rep#tbl-1/table", parent_id="rep#tbl-1", kind="table",
                  text="Own funds", page_number=4, section="Capital", table=t),
        ],
    )  # fmt: skip
    params = ChunkParams(max_tokens=60, min_tokens=10, overlap=5, table_context_max_tokens=40)
    chunker = Chunker(stub_tools(), params, titles={"rep": "Bank report 2025"})
    chunks = chunker.chunk(doc, "structure_semantic")
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) > 1
    assert {c.parent_id for c in tables} == {"rep#tbl-1"}  # = report_tables.table_id
    assert [c.position for c in tables] == list(range(len(tables)))
    assert len({c.chunk_id for c in tables}) == len(tables)
    for c in tables:
        assert c.table_source == "pdf" and c.page_number == 4
        assert c.header_uncertain is True and c.header_issues == ["same_label_cells"]
        assert c.has_fused_rows is True
        assert c.context is not None
        assert c.context.startswith("Table: Own funds\nSource: Bank report 2025, page 4")
    assert tables[0].embedding_text() == tables[0].context + "\n" + tables[0].row_lines  # type: ignore[operator]
    (text,) = [c for c in chunks if c.kind == "text"]
    assert text.header_uncertain is None and text.has_fused_rows is None
