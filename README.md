# RegLens

**Cited answers on EU financial regulation, in under a minute.**

## The problem and the user

Compliance officers at mid-size European fintechs must apply the same EU rules as large banks (DORA, AI Act, MiCA, GDPR, AMLR) with a legal team of 2–5 people. Answering one question, such as *"What is the deadline to report a major ICT incident, and is our internal policy compliant?"*, means reading regulations, following cross-references between articles, extracting figures from 300-page PDF reports and comparing them with internal policies. That takes hours.

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
│   ├── raw/                # downloaded + generated files (not committed)
│   └── golden_set/         # evaluation questions
├── docs/                   # project scoping and backlog
├── scripts/                # corpus download, synthetic data, evaluation
├── src/reglens/
│   ├── config.py           # settings from environment variables
│   ├── ingestion/          # parsing, PII masking, chunking, embedding, indexing
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
pip install -e ".[dev]"

# 4. Checks
ruff check .
mypy
pytest
```

Stop the infrastructure with `docker compose down` (data is kept in Docker volumes; add `-v` to delete it).

## Corpus

The corpus is listed in [`data/sources.yaml`](data/sources.yaml):

- **regulations**: EU texts from EUR-Lex (HTML, French + English);
- **reports**: Pillar 3 risk reports of European banks (PDF);
- **policies** and **logs**: internal documents and an ICT incident log of a fictional fintech, generated with fake personal data.

Raw files live in `data/raw/<type>/` and are **not committed** (bank reports are copyrighted). They are rebuilt from the source list by `scripts/download_corpus.py` (downloads) and `scripts/generate_synthetic.py` (synthetic data); both scripts arrive in the next phase.

## Project status

| Phase | Status |
| :--- | :--- |
| 0. Setup (repository, tooling, Docker infrastructure, CI) | ✅ done |
| 1. Corpus download and synthetic data | ⏳ next |
| 2–11. Parsing, chunking, evaluation, indexing, retrieval, API, generation, observability, frontend, production | planned |
