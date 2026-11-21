"""Parser for EUR-Lex regulations (XHTML served by CELLAR).

The official markup already encodes the legal structure, so this is parsing, not
guessing:
    div#art_19            an article      -> parent_id "dora#art-19"
      p.oj-ti-art         "Article 19"
      div.eli-title       the article's title
      div#019.001         numbered paragraph 1
    div#rct_12            recital 12      -> parent_id "dora#rct-12"
    div#anx_I             annex I         -> parent_id "dora#anx-I"
    div#cpt_III(.sct_1)   chapter / section, used as the block's `section` path
Points such as "(a) ..." are laid out with two-column *layout* tables; each row is
flattened to one line ("(a) text"), so they stay with their paragraph.
"""

import re
from pathlib import Path

import lxml.html
from lxml.html import HtmlElement

from reglens.corpus.sources import Language
from reglens.ingestion.models import Block, ParsedDocument

ARTICLE_ID = re.compile(r"^art_(\d+[a-z]*)$")
PARAGRAPH_ID = re.compile(r"^\d{3}\.(\d{3})$")
RECITAL_ID = re.compile(r"^rct_(\d+)$")
ANNEX_ID = re.compile(r"^anx_([A-Za-z0-9]+)$")


def _norm(text: str) -> str:
    """Collapse whitespace (including non-breaking spaces) into single spaces."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _classes(el: HtmlElement) -> set[str]:
    return set((el.get("class") or "").split())


def _row_line(tr: HtmlElement) -> str:
    """One table row as one line: "(a) text" for point lists, "x | y | z" for data rows."""
    cells = [_norm(" ".join(td.itertext())) for td in tr.xpath("./td|./th")]
    cells = [c for c in cells if c]
    if len(cells) <= 2:
        return " ".join(cells)
    return " | ".join(cells)


def _lines(el: HtmlElement) -> list[str]:
    """Readable lines of an element, in document order."""
    if el.tag == "table":
        rows = el.xpath("./tbody/tr|./tr|./thead/tr")
        return [line for tr in rows if (line := _row_line(tr))]
    if el.tag == "p":
        text = _norm(" ".join(el.itertext()))
        return [text] if text else []
    lines: list[str] = []
    for child in el:
        if isinstance(child, HtmlElement):
            lines.extend(_lines(child))
    return lines


def _section_path(el: HtmlElement) -> str | None:
    """'CHAPTER III Title > SECTION 1 Title' from the chapter/section ancestors."""
    parts = []
    for anc in reversed(list(el.iterancestors("div"))):
        anc_id = anc.get("id") or ""
        # chapter / title / part containers; skip their ".tit_1" title wrappers
        if not re.match(r"^(cpt|tit|prt)_", anc_id) or re.search(r"\.tit_\d+$", anc_id):
            continue
        heading = [
            _norm(" ".join(p.itertext()))
            for p in anc.xpath("./p | ./div[contains(@class,'eli-title')]/p")
            if _classes(p) & {"oj-ti-section-1", "oj-ti-section-2"}
        ]
        if heading:
            parts.append(" ".join(heading))
    return " > ".join(parts) or None


def _article_blocks(doc_id: str, div: HtmlElement, number: str) -> list[Block]:
    parent_id = f"{doc_id}#art-{number}"
    section = _section_path(div)
    heading = [
        _norm(" ".join(p.itertext()))
        for p in div.xpath("./p | ./div[contains(@class,'eli-title')]/p")
        if _classes(p) & {"oj-ti-art", "oj-sti-art"}
    ]
    blocks = [
        Block(
            block_id=f"{parent_id}/title",
            parent_id=parent_id,
            kind="title",
            text=" — ".join(heading),
            article=number,
            section=section,
        )
    ]
    loose: list[str] = []  # text directly under the article (no numbered paragraph)

    def flush_loose() -> None:
        if loose:
            blocks.append(
                Block(
                    block_id=f"{parent_id}/t{len(blocks)}",
                    parent_id=parent_id,
                    kind="text",
                    text="\n".join(loose),
                    article=number,
                    section=section,
                )
            )
            loose.clear()

    for child in div:
        if not isinstance(child, HtmlElement):
            continue
        if _classes(child) & {"oj-ti-art", "eli-title"}:
            continue
        match = PARAGRAPH_ID.match(child.get("id") or "")
        if match:
            flush_loose()
            n = int(match.group(1))
            blocks.append(
                Block(
                    block_id=f"{parent_id}/p{n}",
                    parent_id=parent_id,
                    kind="text",
                    text="\n".join(_lines(child)),
                    article=number,
                    paragraph=n,
                    section=section,
                )
            )
        else:
            loose.extend(_lines(child))
    flush_loose()
    return blocks


def parse_eurlex(
    path: Path, *, doc_id: str, langue: Language, reglement: str | None, source: str
) -> ParsedDocument:
    """Parse one EUR-Lex XHTML file (one language of one regulation)."""
    root = lxml.html.fromstring(path.read_bytes())
    title_parts = [_norm(" ".join(p.itertext())) for p in root.xpath("//p[@class='oj-doc-ti']")]
    blocks: list[Block] = []

    for div in root.xpath("//div[starts-with(@id,'rct_')]"):
        match = RECITAL_ID.match(div.get("id"))
        if match:
            parent_id = f"{doc_id}#rct-{match.group(1)}"
            text = "\n".join(_lines(div))
            if text:
                blocks.append(
                    Block(block_id=f"{parent_id}/t", parent_id=parent_id, kind="text", text=text)
                )

    for div in root.xpath("//div[starts-with(@id,'art_')]"):
        match = ARTICLE_ID.match(div.get("id"))
        if match:
            blocks.extend(_article_blocks(doc_id, div, match.group(1)))

    for div in root.xpath("//div[starts-with(@id,'anx_')]"):
        # Some acts write the id with a non-breaking space ("anx_\xa0I"): normalise it.
        match = ANNEX_ID.match(re.sub(r"\s", "", div.get("id").replace("\xa0", "")))
        if not match:
            continue
        parent_id = f"{doc_id}#anx-{match.group(1)}"
        for i, child in enumerate(c for c in div if isinstance(c, HtmlElement)):
            text = "\n".join(_lines(child))
            if text:
                blocks.append(
                    Block(block_id=f"{parent_id}/t{i}", parent_id=parent_id, kind="text", text=text)
                )

    return ParsedDocument(
        doc_id=doc_id,
        source=source,
        type_doc="regulations",
        langue=langue,
        reglement=reglement,
        title=" ".join(title_parts[:3]) or None,
        parser="eurlex",
        blocks=blocks,
    )
