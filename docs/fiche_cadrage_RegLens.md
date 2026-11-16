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
- **Utilisateur visé :** Responsable conformité ou analyste risques dans une fintech / néobanque européenne de taille moyenne. Elle doit respecter les mêmes règles qu'une grande banque (DORA, AI Act, MiCA, RGPD, AMLR), avec une équipe juridique de 2 à 5 personnes.
- **Point de douleur majeur (*Pain point*) :** Pour répondre à une question comme *« Quel est le délai pour déclarer un incident informatique majeur, et notre politique interne est-elle conforme ? »*, l'analyste doit lire DORA, suivre les renvois vers d'autres articles, puis comparer avec la politique interne. Les chiffres clés (ratios de fonds propres) sont cachés dans des tableaux de rapports PDF de 300 pages sur deux colonnes. Une question peut prendre plusieurs heures, et une erreur a des conséquences réglementaires.
- **Limite des approches classiques (Pourquoi l'IA générative ?) :**
  - **Ctrl+F / BM25** : trouve « article 19 », mais pas une reformulation (« délai de déclaration » vs « notification initiale »).
  - **Recherche vectorielle seule** : comprend le sens, mais confond des numéros d'articles proches.
  - **Chatbot généraliste** : invente des articles (hallucination) et oblige à envoyer des documents internes à un service externe (fuite de données).
  - **Aucune** ne suit une chaîne de renvois entre articles ni ne lit correctement un tableau financier.

#### 2.2 Proposition de Valeur
Une interface de question-réponse en streaming qui interroge la réglementation européenne, les rapports financiers publics et les politiques internes. Chaque affirmation est **citée avec sa source exacte** (texte, article, page, tableau). Le système suit les renvois entre articles et **refuse de répondre** quand le corpus ne permet pas de conclure. Objectif : passer de plusieurs heures à moins d'une minute par question, avec une Faithfulness Ragas ≥ 0,85 et zéro fuite de données personnelles.

---

### 3. Données & Volumétrie

- **Format des données sources :** `[x] PDF multi-colonnes` `[x] JSON/CSV` `[x] Tables relationnelles SQL` `[x] Markdown` `[ ] Images/Scans`

| Type de document | Source | Format | Difficulté |
| :--- | :--- | :--- | :--- |
| Textes réglementaires (DORA, AI Act, MiCA, RGPD, AMLR, orientations EBA) | EUR-Lex, EBA — publics, FR + EN | HTML / PDF | Structure profonde (Titre > Article > paragraphe), nombreux renvois |
| Rapports Pillar 3 / annuels de banques européennes | Sites des banques — publics | PDF multi-colonnes | 2 colonnes, tableaux sur plusieurs pages |
| Tableaux financiers | Extraits des PDF à l'ingestion | Tables SQL (PostgreSQL) | Valeurs exactes à retrouver |
| Politiques internes | **Synthétiques** (Faker), avec fausses PII et faux secrets | Markdown | Test des garde-fous PII |
| Journaux d'incidents informatiques | **Synthétiques**, inspirés de DORA | JSON | Semi-structuré, horodaté |

> Toutes les données sont publiques ou synthétiques.

- **Volumétrie initiale (PoC S1-S3) :** `~75 documents / ~4 700 pages / ~12 000 chunks (dont ~1 500 tableaux) + 50 000 lignes de logs`
- **Volumétrie cible en production :** `~15 000 documents / ~1 000 000 pages / ~5 000 000 chunks`
  - Toute la réglementation financière UE + 5 ans de rapports pour ~300 établissements.
  - Conséquences : compression (quantification) et répartition (sharding) dans Qdrant, ingestion incrémentale, réplicas Kubernetes.
- **Fréquence de mise à jour des données :** `[x] Batch quotidien` *(nouveaux textes EUR-Lex / EBA)* `[x] Temps réel / Streaming` *(dépôt d'un document interne)* `[ ] Statique`

---

### 4. Architecture Technique & Composants RAG

#### 4.1 Trois flux découplés
1. **Ingestion asynchrone :** `POST /api/v1/documents` répond tout de suite `202` + `job_id`. Le document attend dans une file **Redis Streams**, puis un worker le traite : Docling (lecture du PDF) → Presidio (masquage des PII) → chunking → bge-m3 (vecteurs) → stockage dans Qdrant, Neo4j et PostgreSQL. Suivi via `GET /api/v1/jobs/{id}`.
2. **Retrieval :** cache Redis → routeur → recherche dans la bonne base → reranker (top-20 → top-5).
3. **Génération en streaming SSE :** `GET /api/v1/chat/stream` envoie la réponse mot par mot, puis les citations (document, article, page).

#### 4.2 Composants
- **Framework RAG :** LlamaIndex (pipelines d'ingestion, intégration Qdrant hybride, reranker, Neo4j via `PropertyGraphIndex`, routeur).
- **Parsing :** Docling (PDF sur 2 colonnes, tableaux en lignes / colonnes).
- **Stratégie de Chunking :** `[ ] Recursive Character` `[x] Semantic Chunking` `[x] Parent-Document / Hierarchical`
  - *Taille de chunk visée :* `512 tokens` (hypothèse testée, voir validation) | *Overlap :* `64 tokens` (uniquement sur les coupures sémantiques)
  - **Textes réglementaires — parcours de décision :**
    1. Toujours : découpage par la structure (article, paragraphe numéroté).
    2. Si un morceau dépasse 512 tokens : coupure sémantique là où le sujet change.
    3. Si un morceau fait moins de ~100 tokens : fusion avec son voisin du même article.
    4. Chaque chunk garde l'identifiant de son article parent. La recherche se fait sur les petits chunks (précision) ; le LLM reçoit l'article parent (contexte), limité à ~1 500 tokens.
  - **Autres types :** 1 tableau = 1 chunk (Markdown + résumé) ; 1 incident = 1 chunk ; politiques internes = 1 chunk par section.
  - **Validation :** comparaison de 3 stratégies (récursif 512, sémantique seul, structure + sémantique) sur le Recall@5 ; tests complémentaires : overlap 64 vs 0, article parent avec vs sans (Ragas).
  - *Métadonnées :* `doc_id, chunk_id, page_number, source, type_doc, reglement, article, parent_id, langue`.
- **Modèle d'Embeddings :** `BAAI/bge-m3` (Dimension : `1024`) — un vecteur **dense** (le sens) et un vecteur **sparse** (les mots-clés, comme un BM25 appris), en français et en anglais. La question est vectorisée une seule fois, pour le cache, le routeur et la recherche.
- **Base Vectorielle retenue :** `[x] Qdrant` `[ ] Milvus` `[ ] pgvector`
  - *Justification du choix :* recherche hybride dense + sparse native (fusion RRF), filtrage par métadonnées pendant la recherche HNSW, quantification pour atteindre 5 M de chunks. pgvector n'a pas de sparse natif ; Milvus est surdimensionné pour le PoC.
- **Autres bases :**
  - **Neo4j (GraphRAG) :** graphe des renvois entre articles (`Article` —`REFERENCE`→ `Article`), extraits par regex ; obligations extraites par le LLM.
  - **PostgreSQL :** valeurs exactes des tableaux.
- **Routeur :** compare le vecteur de la question à 10–20 exemples écrits à la main pour chaque route (~20 ms, sans LLM) :
  - **Recherche hybride** (route par défaut) → questions de contenu, y compris sur les logs ;
  - **Graphe** → « de quels articles dépend l'article 19 ? » : regex (ou recherche hybride) pour trouver l'article de départ, Neo4j renvoie les articles liés, leur texte est récupéré dans Qdrant par filtre ;
  - **Tableaux** → questions chiffrées : text-to-SQL sur PostgreSQL + calculatrice pour les variations.
- **Reranker (Cross-Encoder) :** `BAAI/bge-reranker-v2-m3`, exécuté localement ; top-20 → top-5.
- **LLM :** `Mistral Small via l'API Mistral` (rapide, fournisseur européen ; pas de GPU nécessaire). Appelé derrière une interface unique, remplaçable par un modèle local (vLLM / Ollama) par configuration.
  - **Rôles du LLM :** générer la réponse ; text-to-SQL pour les questions chiffrées ; à l'ingestion : résumer chaque tableau (pour qu'il soit trouvable par le sens) et extraire les obligations pour le graphe.
- **Cache sémantique :** Redis — question similaire à ≥ 0,95 (mêmes filtres) → réponse en < 50 ms. Vidé à chaque nouvelle ingestion (TTL 24 h).
- **API & UI :** FastAPI async + Pydantic v2 (validation stricte) ; Next.js avec affichage en streaming et citations cliquables.
- **Observabilité & Évaluation :** Langfuse (traces et latence de chaque étape) ; Ragas (évaluation continue, avec un modèle juge différent du modèle qui répond).

---

### 5. Métriques Quantitatives & SLAs

**Golden set :** 150 questions annotées à la main (chunk de vérité + réponse de référence) : 60 factuelles, 30 multi-sauts (graphe), 30 chiffrées (tableaux), 15 sur les logs, **15 hors corpus** (bonne réponse = refus).

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

**Budget TTFT :** cache 10 ms → routeur 20 ms → embedding 40 ms → Qdrant 30 ms → reranking ~100 ms → premier token LLM ~400 ms ≈ **600 ms** (marge de 200 ms). Le reranker local est le principal risque : il est mesuré dès le début ; solutions de repli : GPU ou reranker plus petit.

**Évaluation continue :** à chaque Pull Request, Ragas tourne sur 30 questions ; **merge bloqué si Faithfulness < 0,80**. Évaluation complète (150 questions) chaque nuit, scores historisés dans Langfuse.

---

### 6. Gestion des Risques & Garde-fous

- **Prévention des Hallucinations :**
  - Directive systématique : *"Si l'information n'est pas présente dans les extraits ci-dessus, répondez explicitement que le corpus ne permet pas de conclure."*
  - **Seuil de pertinence :** si le meilleur score du reranker est trop bas, refus **avant** l'appel au LLM.
  - **Citations obligatoires :** chaque phrase doit citer un chunk.
  - **Chiffres exacts :** les nombres viennent de PostgreSQL (SQL), jamais d'un calcul du LLM.
- **Fuite de données confidentielles (PII / secrets) :**
  - **À l'ingestion :** Presidio (noms, e-mails, IBAN, téléphones) + détection de secrets → masquage avant stockage. Le LLM ne voit jamais de PII.
  - **En sortie (filet de sécurité) :** la réponse est envoyée par petits blocs, chacun vérifié par Presidio.
  - **Injection de prompt :** le contenu des documents est traité comme une donnée, jamais comme une instruction.
- **Fiabilité des appels (LLM et bases) :**
  - **Timeouts :** connexion 2 s, premier token 5 s, entre deux tokens 10 s, total 30 s ; ~1 s pour Qdrant, Neo4j et PostgreSQL.
  - **Retries :** uniquement sur erreurs temporaires (429, 5xx, timeout), avec attente croissante (0,5 s → 1 s → 2 s), et seulement avant le début du streaming. Après le début, une coupure envoie un événement SSE `error`.
  - **Limitation du débit :** nombre d'appels simultanés limité vers l'API Mistral ; limite de requêtes par utilisateur sur FastAPI ; `max_tokens` fixé.
  - **Repli :** modèle plus petit ou message « service occupé » après échec.
  - **Ingestion :** appels LLM (résumés de tableaux) limités en débit ; jobs rejouables sans doublon.
- **Protection des Secrets :** variables d'environnement via `.env` uniquement (jamais dans le code), `.env` dans `.gitignore`, scan `gitleaks` en CI.
- **Isolation Réseau :** Qdrant, Neo4j, Redis et PostgreSQL dans un réseau Docker interne ; seuls l'API et le front sont exposés.
