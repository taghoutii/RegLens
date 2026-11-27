# Fiche de Cadrage d'Ingénierie — Projet Fédérateur AI-BD_BI
## Spécification du Moteur RAG / Data Intelligent

---

### 1. Fiche d'Identité du Projet

- **Nom du Projet :** `RegLens — Assistant de conformité réglementaire pour fintechs européennes`
- **Membres de l'Équipe :**
  1. *[Eya Taghouti]*
  2. *[Med Aziz Mimouni]*  
- **Lien du Dépôt GitHub :** `https://github.com/taghoutii/reglens`
- **Branche de travail principale :** `main` (protégée : merge uniquement via Pull Request, CI verte obligatoire)

---

### 2. Cadrage du Problème Métier ("Problem-First")

#### 2.1 Persona Cible & Contexte Opérationnel
- **Utilisateur visé :** Responsable conformité dans une fintech de paiement ou de monnaie électronique (ou une néobanque) européenne de taille moyenne — l'entreprise fictive *Velora Fintech*. Elle applique DORA et ses normes techniques, la DSP2 et l'authentification forte, la directive monnaie électronique, MiCA, l'AI Act, le RGPD et l'AMLR, avec une équipe juridique de 2 à 5 personnes.
- **Point de douleur majeur (*Pain point*) :** Pour répondre à une question comme *« Quel est le délai pour déclarer un incident informatique majeur, et notre politique interne est-elle conforme ? »*, l'analyste doit lire DORA, suivre les renvois vers les normes techniques, comparer avec la politique interne, puis vérifier le journal des incidents ou le registre des prestataires. Une question peut prendre plusieurs heures, et une erreur a des conséquences réglementaires.
- **Limite des approches classiques (Pourquoi l'IA générative ?) :**
  - **Ctrl+F / BM25** : trouve « article 19 », mais pas une reformulation (« délai de déclaration » vs « notification initiale »).
  - **Recherche vectorielle seule** : comprend le sens, mais confond des numéros d'articles proches.
  - **Chatbot généraliste** : invente des articles (hallucination) et oblige à envoyer des documents internes à un service externe (fuite de données).
  - **Aucune** ne relie une exigence réglementaire à la politique interne, au journal des incidents et au registre des prestataires.

#### 2.2 Proposition de Valeur
Une interface de question-réponse en streaming qui interroge la réglementation européenne et les documents internes de l'entreprise (politiques, journal des incidents TIC, registre des prestataires TIC). Chaque affirmation est **citée avec sa source exacte** (texte, article ou section, enregistrement). Le système suit les renvois entre articles et **refuse de répondre** quand le corpus ne permet pas de conclure. Objectif : passer de plusieurs heures à moins d'une minute par question, avec une Faithfulness Ragas ≥ 0,85 et zéro fuite de données personnelles.

**Cas d'usage :** recherche réglementaire ; renvois entre articles ; conformité d'une politique interne ; délais de déclaration des incidents (journal vs politique vs DORA) ; surveillance des prestataires TIC (registre vs DORA) ; obligations de paiement (DSP2, normes SCA, monnaie électronique) ; refus hors corpus.

**Périmètre final :** les rapports bancaires et l'extraction de tableaux sont hors périmètre. Les tableaux des annexes EUR-Lex sont conservés sous forme de texte (une ligne par rangée).

---

### 3. Données & Volumétrie

- **Format des données sources :** `[ ] PDF multi-colonnes` `[x] JSON/CSV` `[ ] Tables relationnelles SQL` `[x] Markdown` `[x] HTML` `[ ] Images/Scans`

| Type de document | Source | Format | Difficulté |
| :--- | :--- | :--- | :--- |
| Textes réglementaires (DORA, RTS 2024/1772, RTS 2025/301, RTS 2024/1774, ITS 2024/2956, DSP2, RTS SCA 2018/389, DME2, AI Act, MiCA, RGPD, AMLR) | EUR-Lex (via CELLAR) — publics, FR + EN | HTML | Structure profonde (Titre > Article > paragraphe), nombreux renvois, y compris anciens styles de citation (« directive 2009/110/CE ») |
| Politiques internes (8 thèmes, dont sécurité des paiements et protection des fonds en variantes laxiste / égale / plus stricte) | **Synthétiques** (Faker), avec fausses PII et faux secrets | Markdown, FR + EN | Règles chiffrées à comparer aux textes ; test des garde-fous PII |
| Journal des incidents TIC | **Synthétique**, inspiré de DORA | JSONL | Semi-structuré, horodaté, ~50 000 lignes |
| Registre d'informations des prestataires TIC | **Synthétique**, colonnes inspirées de l'ITS 2024/2956 | CSV | Liens prestataire → systèmes → unités ; personnes de contact (fausses PII) |

> Toutes les données sont publiques ou synthétiques.

- **Volumétrie initiale (PoC) :** `24 textes réglementaires (12 × FR/EN) + 62 politiques + 1 journal (200 incidents) + 1 registre (18 prestataires) ≈ 7 000 chunks par stratégie`
- **Volumétrie cible en production :** `~5 000 000 chunks`
  - Toute la réglementation financière UE + les documents internes de ~300 établissements.
  - Conséquences : compression (quantification) et répartition (sharding) dans Qdrant, ingestion incrémentale, réplicas Kubernetes.
- **Fréquence de mise à jour des données :** `[x] Batch quotidien` *(nouveaux textes EUR-Lex)* `[x] Temps réel / Streaming` *(dépôt d'un document interne)* `[ ] Statique`

---

### 4. Architecture Technique & Composants RAG

#### 4.1 Trois flux découplés
1. **Ingestion asynchrone :** `POST /api/v1/documents` répond tout de suite `202` + `job_id`. Le document attend dans une file **Redis Streams**, puis un worker le traite : lecture (HTML EUR-Lex, Markdown, JSONL, CSV) → Presidio (masquage des PII) → chunking → bge-m3 (vecteurs) → stockage dans Qdrant. Suivi via `GET /api/v1/jobs/{id}` (statut conservé dans Redis).
2. **Retrieval :** cache Redis → recherche hybride → reranker (top-20 → top-5).
3. **Génération en streaming SSE :** `POST /api/v1/chat/stream` envoie la réponse mot par mot, puis les citations (document, article ou section).

#### 4.2 Composants
- **Framework RAG :** LlamaIndex (rôle exact à décider avant la Phase 5).
- **Lecture des documents :** parseur EUR-Lex propre (articles, paragraphes, considérants, annexes), lecteurs Markdown, JSONL et CSV.
- **Stratégie de Chunking :** `[ ] Recursive Character` `[x] Semantic Chunking` `[x] Parent-Document / Hierarchical`
  - *Taille de chunk visée :* `512 tokens` (hypothèse testée, voir validation) | *Overlap :* `64 tokens` (uniquement sur les coupures sémantiques)
  - **Textes réglementaires — parcours de décision :**
    1. Toujours : découpage par la structure (article, paragraphe numéroté).
    2. Si un morceau dépasse 512 tokens : coupure sémantique là où le sujet change (seuil calibré sur les textes et enregistré à chaque exécution).
    3. Si un morceau fait moins de ~100 tokens : fusion avec son voisin du même article.
    4. Chaque chunk garde l'identifiant de son article parent. La recherche se fait sur les petits chunks (précision) ; le LLM reçoit l'article parent (contexte), limité à ~1 500 tokens.
  - **Autres types :** politiques internes = 1 chunk par section ; 1 incident = 1 chunk ; 1 prestataire du registre = 1 chunk (une ligne avec ses champs).
  - **Validation :** comparaison de 3 stratégies (récursif 512, sémantique seul, structure + sémantique) sur le Recall@5 ; tests complémentaires : overlap 64 vs 0, article parent avec vs sans (Ragas).
  - *Métadonnées :* `chunk_id, doc_id, parent_id, source, type_doc, langue, reglement, article, references, context, strategy, kind, position, n_tokens`.
- **Modèle d'Embeddings :** `BAAI/bge-m3` (Dimension : `1024`) — un vecteur **dense** (le sens) et un vecteur **sparse** (les mots-clés, comme un BM25 appris), en français et en anglais. La question est vectorisée une seule fois, pour le cache et la recherche.
- **Base Vectorielle retenue :** `[x] Qdrant` `[ ] Milvus` `[ ] pgvector`
  - *Justification du choix :* recherche hybride dense + sparse native (fusion RRF), filtrage par métadonnées pendant la recherche HNSW, quantification pour atteindre 5 M de chunks. pgvector n'a pas de sparse natif ; Milvus est surdimensionné pour le PoC.
- **Extensions optionnelles (Could Have) :**
  - **Neo4j (GraphRAG) :** graphe des renvois entre articles (`Article` —`REFERENCE`→ `Article`), construit à partir des références extraites par regex.
  - **Routeur + text-to-SQL + calculatrice :** agrégations sur le journal des incidents et le registre (« combien d'incidents majeurs déclarés en retard par unité ? »).
- **Reranker (Cross-Encoder) :** `BAAI/bge-reranker-v2-m3`, exécuté localement ; top-20 → top-5.
- **LLM :** `Mistral Small via l'API Mistral` (rapide, fournisseur européen ; pas de GPU nécessaire). Appelé derrière une interface unique, remplaçable par un modèle local (vLLM / Ollama) par configuration.
  - **Rôle du LLM :** générer la réponse citée. Aucun appel au LLM pendant l'ingestion.
- **Cache sémantique :** Redis — question similaire à ≥ 0,95 (mêmes filtres) → réponse en < 50 ms. Vidé à chaque nouvelle ingestion (TTL 24 h).
- **API & UI :** FastAPI async + Pydantic v2 (validation stricte) ; Next.js avec affichage en streaming et citations cliquables.
- **Observabilité & Évaluation :** Langfuse (traces et latence de chaque étape) ; Ragas (évaluation continue, avec un modèle juge différent du modèle qui répond).

---

### 5. Métriques Quantitatives & SLAs

**Golden set :** 150 questions annotées à la main (unité de vérité + réponse de référence) : 60 factuelles, 30 multi-sauts (renvois), 15 sur les logs, **15 hors corpus** (bonne réponse = refus), et 30 questions dont la catégorie remplace l'ancienne catégorie « tableaux » (registre des prestataires ou conformité des politiques ; décision au début de la Phase 4).

| Métrique | Définition / Outil | Objectif Cible (KPI) |
| :--- | :--- | :---: |
| **Recall@5 (Retrieval)** | Le bon chunk est dans le top-5 envoyé au LLM | $\ge 80\%$ |
| **Recall@10 (Retrieval)** | Le bon chunk est dans le top-10 | $\ge 85\%$ |
| **MRR@10 (Retrieval)** | Moyenne de 1/rang du premier bon chunk (est-il en tête ?) | $\ge 0.70$ |
| **Faithfulness (Ragas)** | Part des affirmations appuyées par les extraits | $\ge 0.85$ / 1.00 |
| **Answer Relevance (Ragas)** | La réponse répond-elle bien à la question ? | $\ge 0.80$ / 1.00 |
| **Taux de refus correct** | Questions hors corpus correctement refusées | $\ge 90\%$ |
| **Fuite PII / secrets** | Sur 50 requêtes d'attaque (*red-team*) | $= 0$ |
| **Latence First-Token (TTFT)** | Temps avant le premier mot affiché (P95, Langfuse) | $< 800$ ms |
| **Latence E2E P95** | Temps total de la réponse | $< 3.5$ s |
| **Taux de hit du cache** | Requêtes servies par le cache Redis | $\ge 30\%$ |

**Budget TTFT :** cache 10 ms → embedding 40 ms → Qdrant 30 ms → reranking ~100 ms → premier token LLM ~400 ms ≈ **580 ms** (marge de 220 ms). Le reranker local est le principal risque : il est mesuré dès le début ; solutions de repli : GPU ou reranker plus petit.

**Évaluation continue :** à chaque Pull Request, Ragas tourne sur 30 questions ; **merge bloqué si Faithfulness < 0,80**. Évaluation complète (150 questions) chaque nuit, scores historisés dans Langfuse.

---

### 6. Gestion des Risques & Garde-fous

- **Prévention des Hallucinations :**
  - Directive systématique : *"Si l'information n'est pas présente dans les extraits ci-dessus, répondez explicitement que le corpus ne permet pas de conclure."*
  - **Seuil de pertinence :** si le meilleur score du reranker est trop bas, refus **avant** l'appel au LLM.
  - **Citations obligatoires :** chaque phrase doit citer un chunk.
  - **Chiffres :** les valeurs citées viennent des extraits (texte réglementaire, politique, incident, prestataire) ; les agrégations sur de nombreux enregistrements demandent un text-to-SQL (extension future, US-13/14).
- **Fuite de données confidentielles (PII / secrets) :**
  - **À l'ingestion :** Presidio (noms, e-mails, IBAN, téléphones) + détection de secrets → masquage avant stockage. Le LLM ne voit jamais de PII.
  - **En sortie (filet de sécurité) :** la réponse est envoyée par petits blocs, chacun vérifié par Presidio.
  - **Injection de prompt :** le contenu des documents est traité comme une donnée, jamais comme une instruction.
- **Fiabilité des appels (LLM et bases) :**
  - **Timeouts :** connexion 2 s, premier token 5 s, entre deux tokens 10 s, total 30 s ; ~1 s pour Qdrant et Redis.
  - **Retries :** uniquement sur erreurs temporaires (429, 5xx, timeout), avec attente croissante (0,5 s → 1 s → 2 s), et seulement avant le début du streaming. Après le début, une coupure envoie un événement SSE `error`.
  - **Limitation du débit :** nombre d'appels simultanés limité vers l'API Mistral ; limite de requêtes par utilisateur sur FastAPI ; `max_tokens` fixé.
  - **Repli :** modèle plus petit ou message « service occupé » après échec.
  - **Ingestion :** aucun appel LLM ; jobs rejouables sans doublon.
- **Protection des Secrets :** variables d'environnement via `.env` uniquement (jamais dans le code), `.env` dans `.gitignore`, scan `gitleaks` en CI.
- **Isolation Réseau :** Qdrant et Redis dans un réseau Docker interne ; seuls l'API et le front sont exposés.
- **Limite connue :** pas d'extraction de rapports ni de tableaux ; une agrégation sur de nombreux enregistrements nécessite un text-to-SQL (US-13/14, futur).
