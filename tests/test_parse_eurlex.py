"""EUR-Lex parser on a small inline fixture that copies the real CELLAR markup."""

from pathlib import Path

import pytest

from reglens.ingestion.models import ParsedDocument
from reglens.ingestion.parse_eurlex import parse_eurlex

FIXTURE = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p class="oj-doc-ti">REGULATION (EU) 2022/2554</p>
<div id="rct_1"><table><tr><td><p class="oj-normal">(1)</p></td>
  <td><p class="oj-normal">A recital about resilience.</p></td></tr></table></div>
<div id="enc_1">
 <div id="cpt_III">
  <p class="oj-ti-section-1">CHAPTER III</p>
  <div class="eli-title" id="cpt_III.tit_1"><p class="oj-ti-section-2">Incident reporting</p></div>
  <div class="eli-subdivision" id="art_19">
   <p class="oj-ti-art">Article&#160;19</p>
   <div class="eli-title" id="art_19.tit_1"><p class="oj-sti-art">Reporting of major incidents</p></div>
   <div id="019.001"><p class="oj-normal">1. Financial entities shall report incidents.</p>
     <table><tr><td><p class="oj-normal">(a)</p></td><td><p class="oj-normal">initial notification;</p></td></tr>
            <tr><td><p class="oj-normal">(b)</p></td><td><p class="oj-normal">final report.</p></td></tr></table>
   </div>
   <div id="019.002"><p class="oj-normal">2. They may notify cyber threats.</p></div>
  </div>
  <div class="eli-subdivision" id="art_20">
   <p class="oj-ti-art">Article 20</p>
   <p class="oj-normal">Text without numbered paragraphs.</p>
  </div>
 </div>
</div>
<div id="anx_&#160;I"><p class="oj-doc-ti">ANNEX I</p><p class="oj-normal">Template fields.</p></div>
</body></html>"""


@pytest.fixture
def doc(tmp_path: Path) -> ParsedDocument:
    path = tmp_path / "dora_en.html"
    path.write_text(FIXTURE, encoding="utf-8")
    return parse_eurlex(path, doc_id="dora", langue="en", reglement="DORA", source="x")


def test_units_are_articles_recitals_and_annexes(doc: ParsedDocument) -> None:
    assert {b.parent_id for b in doc.blocks} == {
        "dora#rct-1",
        "dora#art-19",
        "dora#art-20",
        "dora#anx-I",  # the non-breaking space in the id is normalised
    }


def test_numbered_paragraphs_keep_their_number_and_points(doc: ParsedDocument) -> None:
    p1 = next(b for b in doc.blocks if b.block_id == "dora#art-19/p1")
    assert p1.paragraph == 1 and p1.article == "19"
    assert "(a) initial notification;" in p1.text  # layout table row -> one line
    assert "(b) final report." in p1.text
    assert next(b for b in doc.blocks if b.block_id == "dora#art-19/p2").paragraph == 2


def test_article_title_and_section_path(doc: ParsedDocument) -> None:
    title = next(b for b in doc.blocks if b.block_id == "dora#art-19/title")
    assert title.kind == "title"
    assert title.text == "Article 19 — Reporting of major incidents"
    assert title.section == "CHAPTER III Incident reporting"


def test_article_without_numbered_paragraphs(doc: ParsedDocument) -> None:
    body = [b for b in doc.blocks if b.parent_id == "dora#art-20" and b.kind == "text"]
    assert [b.text for b in body] == ["Text without numbered paragraphs."]
    assert body[0].paragraph is None


def test_html_has_no_page_numbers(doc: ParsedDocument) -> None:
    assert all(b.page_number is None for b in doc.blocks)
    assert doc.type_doc == "regulations" and doc.reglement == "DORA"
