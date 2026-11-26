# Experiments

Every measurement behind a reported number for the current corpus, one section per
pipeline stage. Each run writes `results/<date>_<name>.json` (configuration, metrics,
timing; a second run on the same day gets `_2`, ...). Each item gives what it measures,
the result, why it is measured, and its file.

A target counts as met only when a file here shows it. The KPIs of the question path
(Recall@k, MRR, faithfulness, refusals, leaks, latency, cache) are not yet measured.

## Download

### Sources fetched and validated
- **Measures:** files fetched from `data/sources.yaml` and checked (content type, size, expected marker).
- **Result:** 12 regulations in EN and FR: 24 HTML files, 16 MB.
- **Why:** the corpus is exactly what `sources.yaml` lists; outputs of unlisted documents are deleted by the build.
- **File:** none (printed by `scripts/download_corpus.py`).

## Parse: masking

### PII recall on planted values and false positives
- **Measures:** recall per PII type on every value planted in the synthetic data (policies, incident log, register of ICT providers; seed 42, and a held-out seed 43 generated into a temporary folder), planted values found in the masked output, masks in the register against its planted contacts, and detections on the regulations (all false positives or public data).
- **Result:** recall 1.0 for names (342), e-mails (342), phones (80), IBANs (62) and secrets (62) on both seeds; 0 leaks.
- **Result (register):** 18 contact persons, 18 e-mails and 18 phones planted; exactly 18 of each masked. Provider names are kept: NER read 2 of them as person names ("Keystone Identity", "Ledgerwise Software"), so PERSON spans inside the `Provider name:` field are dropped.
- **Result (regulations):** 108 PERSON spans over the 24 files (6.5 M characters; e.g. "Alphanumerical" and "Instructions" in the ITS 2024/2956 templates, "Titles III", "Charter"); 725 more removed by the person filter.
- **Why:** Phase 2 acceptance: 0 planted values in processed data, recall 100 % on seeds 42 and 43. False positives are reported because they remove words from the indexed text.
- **File:** `2026-11-26_pii_masking.json`

## Chunk

### Chunks and tokens per strategy and type
- **Measures:** chunk counts and token lengths (bge-m3 tokenizer, without special tokens) per strategy and document type, context lines, semantic-split calibration, bge-m3 CPU cost.
- **Result (structure + semantic, main):** 6,886 chunks over 88 documents, median 195 tokens, P95 456, max 512. Regulations 6,222 (median 208), policy sections 446 (median 65), incidents 200 (median 180), providers 18 (median 320). Context lines on 4,483 chunks, median 16 tokens.
- **Result (semantic split):** threshold 0.4561 = 5th percentile of 4,673 consecutive-sentence similarities in regulation pieces over 512 tokens; 712 cuts, 89 below the threshold (the others forced by the 512 limit). The threshold used is also written to `data/processed/chunks/run.json` at every run; `CHUNK_SEMANTIC_THRESHOLD` fixes it instead of calibrating.
- **Result (comparison strategies):** recursive 4,075 chunks, median 430; semantic-only 5,538, median 250 (one regulation chunk of 513 tokens).
- **Result (cost):** bge-m3 on CPU embeds 3.7 sentences/s; 2,166 MB of weights, peak process memory 3.2 GB; semantic-only takes 66 min. Speed varies with machine load: an earlier run of the same corpus on the same machine measured 5.2 sentences/s (60 min).
- **Why:** Phase 3 acceptance (token statistics per strategy and type); sizes the Phase 5 embedding; input of the Phase 6 strategy comparison.
- **File:** `2026-11-26_chunking_stats.json`

### Cross-references per cited act
- **Measures:** per cited act, the chunks citing it (split into chunks of the act itself and chunks of other acts) and the number of references, both languages, main strategy.
- **Result:** most cited: MiCA 1,342 references in 540 chunks, PSD2 1,151 in 322, AMLR 1,049 in 295, GDPR 881 in 319, AI Act 780 in 351, DORA 572 in 397 (171 of them chunks of other acts). The E-Money Directive is cited 178 times in 97 chunks: 128 references in 55 chunks of the Directive itself, 50 in 42 chunks of other acts.
- **Why:** sizes the multi-hop questions and the reference graph; separating own-act from other-act citations shows which acts are linked to each other.
- **File:** `2026-11-26_chunking_stats.json` (`references_by_act`)

### Regression after the scope change
- **Measures:** parsed documents and chunks of the regulations, the 50 drawn policies and the incident log, compared with the outputs saved before the report and table pipeline was removed, ignoring only the removed table fields.
- **Result:** 75 / 75 documents identical in parsing and in each of the 3 strategies; the threshold is unchanged (0.4561). Added: 12 policies (2 new topics x 3 variants x 2 languages) and the register.
- **Result (provider-name rule):** after the rule was added, 87 / 88 documents identical; in the register, only the text of the 2 affected providers changed, in all 3 strategies.
- **Why:** removing a pipeline or adding a masking rule must change only the documents it targets.
- **File:** none (comparison run once, outputs not kept).
