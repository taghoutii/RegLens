"""Load and validate `data/sources.yaml`."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

DocType = Literal["regulations", "policies", "logs", "register"]
Language = Literal["en", "fr"]
FileFormat = Literal["html", "md", "jsonl", "csv"]


class Source(BaseModel):
    """One entry of sources.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str | None = None
    short_name: str | None = None  # regulation short name ("DORA"), stored as `reglement`
    what: str
    format: FileFormat
    mode: Literal["auto", "generated"]
    # Regulations exist in several languages (one URL each); a single-language source
    # has one URL.
    urls: dict[Language, HttpUrl] | None = None
    url: HttpUrl | None = None
    language: Language | None = None

    @model_validator(mode="after")
    def _check_urls(self) -> "Source":
        if self.mode == "auto" and (self.url is None) == (self.urls is None):
            raise ValueError(f"{self.id}: an 'auto' source needs exactly one of 'url' or 'urls'")
        if self.mode == "generated" and (self.url or self.urls):
            raise ValueError(f"{self.id}: a 'generated' source must not have URLs")
        return self


class Corpus(BaseModel):
    """The whole sources.yaml file: one list per document type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    regulations: list[Source] = []
    policies: list[Source] = []
    logs: list[Source] = []
    register_of_information: list[Source] = []

    def by_type(self) -> dict[DocType, list[Source]]:
        return {
            "regulations": self.regulations,
            "policies": self.policies,
            "logs": self.logs,
            "register": self.register_of_information,
        }


@dataclass(frozen=True)
class DownloadTarget:
    """One file to download: where it comes from and where it goes."""

    source_id: str
    doc_type: DocType
    file_format: FileFormat
    url: str
    path: Path
    language: Language | None


def load_corpus(path: Path) -> Corpus:
    """Read and validate the YAML source list."""
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Corpus.model_validate(raw)


def download_targets(corpus: Corpus, raw_dir: Path) -> list[DownloadTarget]:
    """Expand 'auto' sources into one target per file.

    File naming: data/raw/<type>/<id>_<lang>.<format> when a source has several
    languages, data/raw/<type>/<id>.<format> otherwise.
    """
    targets: list[DownloadTarget] = []
    for doc_type, sources in corpus.by_type().items():
        for src in sources:
            if src.mode != "auto":
                continue
            if src.urls is not None:
                for lang, url in src.urls.items():
                    path = raw_dir / doc_type / f"{src.id}_{lang}.{src.format}"
                    targets.append(
                        DownloadTarget(src.id, doc_type, src.format, str(url), path, lang)
                    )
            elif src.url is not None:
                path = raw_dir / doc_type / f"{src.id}.{src.format}"
                targets.append(
                    DownloadTarget(src.id, doc_type, src.format, str(src.url), path, src.language)
                )
    return targets
