# RegLens

**Cited answers on EU financial regulation, in under a minute.**

## The problem and the user
  
RegLens is a retrieval-augmented question-answering system that answers such questions, **cites the exact source of every claim** (document, article, page, table) and **refuses** when the corpus does not support a conclusion. It cites and summarizes; humans decide.

## Architecture overview

Three decoupled parts: ingestion never slows down a question.

```
1. Ingestion (asynchronous)
   POST /api/v1/documents ─► 202 + job_id ─► Redis Streams ─► worker
   worker: parse (Docling / EUR-Lex HTML) ─► mask PII + secrets (Presidio)
           ─► chunk by structure ─► embed (bge-m3 dense + sparse)
           ─► Qdrant (chunks + vectors) | PostgreSQL (exact table values)

2. Question (live, streamed)
   Next.js ─► FastAPI /api/v1/chat/stream
   embed question ─► semantic cache (Redis) ─► hybrid search in Qdrant (dense + sparse, RRF)
   ─► cross-encoder reranker (top-20 → top-5) ─► refuse if relevance too low
   ─► LLM answer with citations ─► output PII guard ─► SSE events

3. Stores
   Qdrant      chunks + vectors      → hybrid search
   PostgreSQL  exact table values    → SQL
```

## Tech stack

| Layer | Choice |
| :--- | :--- |
| Backend | FastAPI (async), Pydantic v2 |
| RAG framework | LlamaIndex |
| Parsing | Docling |
| Embeddings | BAAI/bge-m3 (dense + sparse, FR + EN) |
| Vector database | Qdrant |
| Reranker | BAAI/bge-reranker-v2-m3 |
| LLM | Mistral Small (API) |
| Queue & cache | Redis |
| Tables | PostgreSQL |
| PII | Microsoft Presidio |
| Observability | Langfuse |
| Evaluation | Ragas + Recall@k / MRR script |
| Frontend | Next.js |
| Quality | pytest, ruff, mypy, GitHub Actions |
| Infrastructure | Docker Compose |

## Folder structure

```
RegLens/
├── data/
│   ├── sources.yaml        # list of corpus documents (committed)
│   ├── raw/                # downloaded + generated files (not committed) — the only folder ingested
│   ├── cache/              # raw parser output, e.g. Docling JSON (not committed, never ingested)
│   ├── processed/          # parsed documents and chunks, always PII-masked (not committed)
│   ├── ground_truth/       # what the synthetic generator planted (not committed, never ingested)
│   └── golden_set/         # evaluation questions
├── docs/                   # project scoping and backlog
├── results/                # experiment results (committed) + EXPERIMENTS.md
├── scripts/                # corpus download, synthetic data, parsing, measurements
├── src/reglens/
│   ├── config.py           # settings from environment variables
│   ├── corpus/             # source list, downloader, synthetic data generator
│   ├── ingestion/          # parsing, PII masking, table store, chunking, embedding, indexing
│   ├── evaluation/         # PII leak checks, retrieval metrics
│   ├── retrieval/          # hybrid search, reranking
│   ├── generation/         # LLM client, prompts, guards
│   ├── api/                # FastAPI app and schemas
│   └── observability/      # tracing helpers
├── tests/
├── frontend/               # Next.js app
├── docker-compose.yml      # development infrastructure
└── pyproject.toml          # dependencies + tool configuration
```

## Getting started (Windows, PowerShell)

Requirements: Python 3.11, Docker Desktop.

```powershell
# 1. Configuration (fake local values; edit as needed)
Copy-Item .env.example .env

# 2. Infrastructure: Qdrant, Redis, PostgreSQL
docker compose up -d
docker compose ps            # all services should be "healthy"
# Qdrant dashboard: http://localhost:6333/dashboard

# 3. Python environment
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,models]"   # "models" = spaCy NER models for PII masking (EN + FR)

# 4. Checks
ruff check .
mypy
pytest -m "not integration"      # unit tests (what CI runs)
pytest                           # + integration tests (need the models, corpus, PostgreSQL)
```

The spaCy models are not published on PyPI: spaCy distributes them as wheel files on GitHub. Their exact URLs and versions are pinned in `pyproject.toml` (the `models` extra), so the install is reproducible. Docling downloads its own layout and table models from Hugging Face on first use.

Stop the infrastructure with `docker compose down` (data is kept in Docker volumes; add `-v` to delete it).

## Corpus

The corpus is listed in [`data/sources.yaml`](data/sources.yaml):

- **regulations**: EU texts from EUR-Lex (HTML, French + English);
- **reports**: Pillar 3 risk reports of European banks (PDF);
- **policies** and **logs**: internal documents and an ICT incident log of a fictional fintech, generated with fake personal data.

Raw files live in `data/raw/<type>/` and are **not committed** (bank reports are copyrighted). Rebuild them with:

```powershell
python scripts/download_corpus.py      # regulations + reports (skips files already present)
python scripts/generate_synthetic.py   # 50 policies + ~50 000 log lines, reproducible (seed 42)
```

Then parse and mask them into `data/processed/parsed/` (report tables also go to PostgreSQL):

```powershell
python scripts/parse_corpus.py --only regulations policies logs
python scripts/run_docling.py                  # PDF reports -> data/cache/docling/ (hours on CPU, resumable)
python scripts/parse_corpus.py --only reports  # reads the cache, masks, writes, stores the tables
python scripts/check_tables.py                 # extracted cells vs the PDF text layer + review page
```

ING also publishes its Pillar 3 templates as a spreadsheet. To map it to the extracted tables (a measurement; nothing in the corpus changes), download `2025-ing-groep-nv-appendices-additional-pillar-iii-disclosures.xlsx` from ING's annual-report page to `data/raw/structured/ing_group_pillar3_2025.xlsx`, then run `python scripts/map_structured_tables.py`.

PDF parsing takes hours on a CPU, so its raw output is cached in `data/cache/docling/` (not committed, never ingested); an interrupted run resumes where it stopped.

Every derived file is masked before it is written: names, e-mail addresses, IBANs, phone numbers and API keys become placeholders such as `<PERSON>`.

Finally, cut the parsed documents into chunks, one folder per chunking strategy (`data/processed/chunks/<strategy>/`):

```powershell
python scripts/chunk_corpus.py   # 3 strategies; downloads BAAI/bge-m3 (~2.3 GB) on first run
```

Every download is checked (content type, file signature, size) before it is kept; failures are listed at the end and the script exits with code 1. Regulations are fetched through the EU Publications Office repository (CELLAR), which serves the same documents as the EUR-Lex links in `sources.yaml` in a machine-readable form.

The synthetic documents are linked the way real ones would be. Every log line names the business unit that owns the failing system, e.g. `"owning_unit": "Payments (BU-PAY)"`. Each unit has exactly one ICT incident policy, whose header carries the same unit code. That policy sets the unit's own reporting deadline, which may be stricter or more lenient than DORA's.

The generator also writes `data/ground_truth/` (not committed): every planted fake personal data value and secret, which policies break a regulatory rule, the policy that applies to each incident, and whether each incident was reported late under DORA and under that policy. It sits outside `data/raw/` on purpose: ingestion reads only `data/raw/`, so these answers can never end up in the search index. Later phases test against it.

## Project status

| Phase | Status |
| :--- | :--- |
| 0. Setup (repository, tooling, Docker infrastructure, CI) | done |
| 1. Corpus download and synthetic data | done |
| 2. Parsing and PII masking | done; table-cell criterion not met (see `results/`) |
| 3. Chunking | done; strategy comparison in Phase 6 |
| 4–11. Evaluation, indexing, retrieval, API, generation, observability, frontend, production | planned |
