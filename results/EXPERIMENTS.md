# Experiments
 
The measurements behind every number reported for the current corpus, by pipeline stage.
Each run writes `results/<date>_<name>.json` (configuration, metrics, timing). A target
counts as met only when a file here shows it.
 
Not yet measured: retrieval (Recall@k, MRR), answers (faithfulness, refusals, leaks),
latency and cache.
 
## Download
 
12 regulations in EN and FR: 24 HTML files, 16 MB. Each file is checked for content type,
size and an expected marker. Printed by `scripts/download_corpus.py`; no result file.
 
## Masking
 
**Goal:** no planted personal data reaches the processed data.
**File:** `2026-11-26_pii_masking.json`
 
| Measure | Result |
| :--- | :--- |
| Recall on planted values (seed 42 and held-out seed 43) | 1.0 for names (342), e-mails (342), phones (80), IBANs (62) and secrets (62) |
| Planted values left in processed data | 0 |
| Register of ICT providers | 18 contacts, 18 e-mails and 18 phones, all masked; every provider name kept |
| False positives in the regulations | 108 words masked as person names over the 24 files (e.g. "Instructions", "Charter") |
 
Name detection is skipped in the register's `Provider name:` field, where it read two
company names as people.
 
## Chunking
 
**Goal:** token statistics per strategy and document type, used to size the embedding and
compare strategies.
**File:** `2026-11-26_chunking_stats.json`
 
### Main strategy (structure + semantic)
 
| Measure | Result |
| :--- | :--- |
| Chunks | 6,886 over 88 documents |
| Tokens per chunk | median 195, P95 456, max 512 |
| By type (chunks, median tokens) | regulations 6,222 (208), policy sections 446 (65), incidents 200 (180), providers 18 (320) |
| Context line | on 4,483 chunks, median 16 tokens |
| Semantic threshold | 0.4561, the 5th percentile of 4,673 neighbour-sentence similarities |
| Cuts in units over 512 tokens | 712: 89 at a topic change, 623 forced by the 512 limit |
 
### Comparison strategies
 
| Strategy | Chunks | Median tokens |
| :--- | ---: | ---: |
| Recursive | 4,075 | 430 |
| Semantic-only | 5,538 | 250 |
 
### Cost
 
bge-m3 on CPU: 3.7 sentences/s, 2.2 GB of weights, 3.2 GB peak memory. Sentence vectors
are cached in `data/cache/`, so a rebuild only embeds new or changed sentences.
 
### Cross-references
 
Most cited acts (main strategy, both languages). Used to size the multi-hop questions.
 
| Act | References | Citing chunks |
| :--- | ---: | ---: |
| MiCA | 1,342 | 540 |
| PSD2 | 1,151 | 322 |
| AMLR | 1,049 | 295 |
| GDPR | 881 | 319 |
| AI Act | 780 | 351 |
| DORA | 572 | 397 (171 in other acts) |