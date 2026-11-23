"""Download corpus files safely.

Each file is streamed to `<name>.part`, checked, and only then renamed to its
final name. So a file in data/raw/ is always complete and of the right type
"""

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from reglens.corpus.sources import DownloadTarget, FileFormat

logger = logging.getLogger(__name__)

USER_AGENT = "RegLens-corpus-downloader/0.1"
CHUNK_SIZE = 64 * 1024
TRANSIENT_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class FormatRule:
    """What a valid file of a given format looks like."""

    content_types: tuple[str, ...]
    magic: bytes | None  # expected first bytes ("magic number"), if the format has one
    min_bytes: int  # smaller than this = probably an error or captcha page
    max_bytes: int  # larger than this = refuse (protects the disk)
    must_contain: bytes | None = None  # a marker the real document always has


RULES: dict[FileFormat, FormatRule] = {
    # Real EUR-Lex regulation pages are hundreds of KB and always contain "Article".
    # Bot-protection / error pages are small and do not.
    "html": FormatRule(
        content_types=("text/html", "application/xhtml+xml"),
        magic=None,
        min_bytes=50_000,
        max_bytes=50_000_000,
        must_contain=b"Article",
    ),
    # Some servers label PDFs as octet-stream; the %PDF- magic bytes are the real proof.
    "pdf": FormatRule(
        content_types=("application/pdf", "application/octet-stream"),
        magic=b"%PDF-",
        min_bytes=100_000,
        max_bytes=200_000_000,
    ),
    # An .xlsx file is a zip archive: it starts with the zip signature "PK\x03\x04".
    "xlsx": FormatRule(
        content_types=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/octet-stream",
        ),
        magic=b"PK\x03\x04",
        min_bytes=10_000,
        max_bytes=50_000_000,
        must_contain=b"xl/workbook.xml",
    ),
}


class InvalidContentError(Exception):
    """The server answered, but not with the expected document."""


Status = Literal["downloaded", "skipped", "failed"]


@dataclass(frozen=True)
class DownloadResult:
    target: DownloadTarget
    status: Status
    detail: str = ""
    size_bytes: int = 0


def check_content_type(header: str, rule: FormatRule) -> None:
    media_type = header.split(";")[0].strip().lower()
    if media_type not in rule.content_types:
        raise InvalidContentError(f"unexpected content type {media_type!r}")


def check_file(path: Path, rule: FormatRule) -> int:
    """Check a fully downloaded file against the rule; return its size."""
    size = path.stat().st_size
    if size < rule.min_bytes:
        raise InvalidContentError(f"file too small ({size} bytes < {rule.min_bytes})")
    data = path.read_bytes()
    if rule.magic is not None and not data.startswith(rule.magic):
        raise InvalidContentError(f"file does not start with {rule.magic!r}")
    if rule.must_contain is not None and rule.must_contain not in data:
        raise InvalidContentError(f"expected marker {rule.must_contain!r} not found")
    return size


# EUR-Lex web pages sit behind bot protection (HTTP 202 + JavaScript challenge).
# The same documents are served to programs by CELLAR, the EU Publications Office
# repository behind EUR-Lex: one URI per CELEX number, language chosen by header.
EURLEX_CELEX = re.compile(r"eur-lex\.europa\.eu/.*[?&]uri=CELEX:([0-9A-Z]+)", re.IGNORECASE)
CELLAR_URI = "https://publications.europa.eu/resource/celex/{celex}"
CELLAR_LANGUAGE = {"en": "eng", "fr": "fra"}  # CELLAR uses 3-letter language codes


def fetch_request(target: DownloadTarget) -> tuple[str, dict[str, str]]:
    """Return the URL and headers to actually request for a target.

    EUR-Lex links (kept in sources.yaml because humans can open them) are
    translated to their CELLAR equivalent; any other URL is used as is.
    """
    match = EURLEX_CELEX.search(target.url)
    if match and target.language is not None:
        headers = {
            "Accept": "application/xhtml+xml, text/html;q=0.9",
            "Accept-Language": CELLAR_LANGUAGE[target.language],
        }
        return CELLAR_URI.format(celex=match.group(1).upper()), headers
    return target.url, {}


def _stream_to_file(
    client: httpx.Client, target: DownloadTarget, part: Path, rule: FormatRule
) -> None:
    url, headers = fetch_request(target)
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code in TRANSIENT_STATUS:
            response.raise_for_status()  # caught by the retry loop
        if response.status_code != 200:
            raise InvalidContentError(f"HTTP {response.status_code}")
        check_content_type(response.headers.get("content-type", ""), rule)
        written = 0
        with part.open("wb") as f:
            for chunk in response.iter_bytes(CHUNK_SIZE):
                written += len(chunk)
                if written > rule.max_bytes:
                    raise InvalidContentError(f"file larger than {rule.max_bytes} bytes")
                f.write(chunk)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    return isinstance(exc, httpx.TransportError)  # timeouts, connection resets, DNS…


def download_one(
    client: httpx.Client,
    target: DownloadTarget,
    *,
    force: bool = False,
    attempts: int = 3,
    backoff_s: float = 1.0,
) -> DownloadResult:
    """Download one target. Never raises: the outcome is in the result."""
    rule = RULES.get(target.file_format)
    if rule is None:
        return DownloadResult(target, "failed", f"no download rule for {target.file_format!r}")

    if target.path.exists() and not force:
        try:
            size = check_file(target.path, rule)
            return DownloadResult(target, "skipped", "already present", size)
        except InvalidContentError as exc:
            logger.warning("%s exists but is invalid (%s); downloading again", target.path, exc)

    target.path.parent.mkdir(parents=True, exist_ok=True)
    part = target.path.with_name(target.path.name + ".part")
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            _stream_to_file(client, target, part, rule)
            size = check_file(part, rule)
            part.replace(target.path)  # atomic rename: the final file is always complete
            return DownloadResult(target, "downloaded", "", size)
        except InvalidContentError as exc:
            last_error = str(exc)  # wrong content will not fix itself: no retry
            break
        except (httpx.HTTPError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if not _is_transient(exc) or attempt == attempts:
                break
            wait = backoff_s * 2 ** (attempt - 1)
            logger.info(
                "%s: attempt %d failed (%s), retrying in %.0fs",
                target.path.name,
                attempt,
                last_error,
                wait,
            )
            time.sleep(wait)
    part.unlink(missing_ok=True)  # never leave a partial or rejected file behind
    return DownloadResult(target, "failed", last_error)


def make_client(timeout_s: float = 60.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s, connect=10.0),
    )


def download_all(
    targets: list[DownloadTarget], *, force: bool = False, client: httpx.Client | None = None
) -> list[DownloadResult]:
    """Download every target; one failure never stops the others."""
    own_client = client is None
    http = client or make_client()
    try:
        results = []
        for target in targets:
            result = download_one(http, target, force=force)
            logger.info("%-10s %s %s", result.status, target.path.name, result.detail)
            results.append(result)
        return results
    finally:
        if own_client:
            http.close()
