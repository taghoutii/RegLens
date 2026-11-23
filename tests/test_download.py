"""Tests for the downloader, with a fake HTTP server (httpx.MockTransport): no network."""

from collections.abc import Callable
from pathlib import Path

import httpx

from reglens.corpus.download import download_one
from reglens.corpus.sources import DownloadTarget, FileFormat

GOOD_PDF = b"%PDF-1.7\n" + b"0" * 200_000
GOOD_HTML = b"<html><body>" + b"Article 1 " * 10_000 + b"</body></html>"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _target(tmp_path: Path, fmt: FileFormat = "pdf") -> DownloadTarget:
    return DownloadTarget(
        "doc", "reports", fmt, "https://example.test/doc", tmp_path / f"d.{fmt}", None
    )


def _respond(body: bytes, content_type: str, status: int = 200) -> Handler:
    return lambda request: httpx.Response(
        status, content=body, headers={"content-type": content_type}
    )


def test_valid_pdf_is_saved(tmp_path: Path) -> None:
    result = download_one(_client(_respond(GOOD_PDF, "application/pdf")), _target(tmp_path))
    assert result.status == "downloaded"
    assert (tmp_path / "d.pdf").read_bytes() == GOOD_PDF
    assert not list(tmp_path.glob("*.part"))


def test_html_error_page_disguised_as_pdf_is_rejected(tmp_path: Path) -> None:
    handler = _respond(b"<html>Access denied</html>" * 10_000, "application/octet-stream")
    result = download_one(_client(handler), _target(tmp_path))
    assert result.status == "failed"
    assert "%PDF-" in result.detail
    assert not any(tmp_path.iterdir())  # nothing left behind


def test_small_captcha_page_is_rejected(tmp_path: Path) -> None:
    handler = _respond(b"<html>Please verify you are human</html>", "text/html")
    result = download_one(_client(handler), _target(tmp_path, "html"))
    assert result.status == "failed"
    assert "too small" in result.detail


def test_valid_html_is_saved(tmp_path: Path) -> None:
    handler = _respond(GOOD_HTML, "text/html; charset=UTF-8")
    assert download_one(_client(handler), _target(tmp_path, "html")).status == "downloaded"


def test_existing_valid_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "d.pdf").write_bytes(GOOD_PDF)

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not hit the network")

    assert download_one(_client(fail), _target(tmp_path)).status == "skipped"


def test_transient_error_is_retried(tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=GOOD_PDF, headers={"content-type": "application/pdf"})

    result = download_one(_client(flaky), _target(tmp_path), backoff_s=0)
    assert result.status == "downloaded"
    assert calls["n"] == 3


def test_eurlex_link_is_fetched_through_cellar_in_the_right_language(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def cellar(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["lang"] = request.headers["accept-language"]
        return httpx.Response(
            200, content=GOOD_HTML, headers={"content-type": "application/xhtml+xml;charset=UTF-8"}
        )

    target = DownloadTarget(
        "dora",
        "regulations",
        "html",
        "https://eur-lex.europa.eu/legal-content/FR/TXT/HTML/?uri=CELEX:32022R2554",
        tmp_path / "dora_fr.html",
        "fr",
    )
    assert download_one(_client(cellar), target).status == "downloaded"
    assert seen == {
        "url": "https://publications.europa.eu/resource/celex/32022R2554",
        "lang": "fra",
    }


def test_404_is_not_retried(tmp_path: Path) -> None:
    calls = {"n": 0}

    def missing(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    result = download_one(_client(missing), _target(tmp_path), backoff_s=0)
    assert result.status == "failed"
    assert calls["n"] == 1


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_valid_spreadsheet_is_saved(tmp_path: Path) -> None:
    body = b"PK\x03\x04" + b"0" * 20_000 + b"xl/workbook.xml"
    result = download_one(_client(_respond(body, XLSX)), _target(tmp_path, "xlsx"))
    assert result.status == "downloaded"


def test_zip_that_is_not_a_workbook_is_rejected(tmp_path: Path) -> None:
    body = b"PK\x03\x04" + b"0" * 20_000
    result = download_one(_client(_respond(body, XLSX)), _target(tmp_path, "xlsx"))
    assert result.status == "failed" and "xl/workbook.xml" in result.detail
