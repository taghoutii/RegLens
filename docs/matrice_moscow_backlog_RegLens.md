# Backlog Technique MoSCoW — Projet Fédérateur AI-BD_BI
## Découpage Agile des User Stories & Critères d'Acceptation

**Projet :** RegLens — Assistant de conformité réglementaire pour fintechs européennes
**Corpus :** textes réglementaires UE, rapports Pillar 3 (PDF multi-colonnes), politiques internes synthétiques (avec fausses PII), logs d'incidents synthétiques.
**Golden set :** 150 questions annotées — 60 factuelles, 30 multi-sauts, 30 tableaux, 15 logs, 15 hors corpus.

---

## 1. MUST HAVE (Indispensables pour la Livraison Finale S7)

*Ce sont les fonctionnalités non négociables qui conditionnent la validation du diplôme d'ingénieur.*

| ID | User Story Technique | Critères d'Acceptation (DoD) | Séance Cible |
| :-: | :--- | :--- | :-: |
| **US-01** | **Ingestion & Chunking Structuré**<br>En tant que Data Engineer, je veux découper le corpus en chunks avec métadonnées pour un indexage précis et des citations exactes. | - Worker asynchrone alimenté par Redis Streams : `POST /api/v1/documents` → `202` + `job_id` ; statut via `GET /api/v1/jobs/{id}`.<br>- Erreurs gérées par document (3 essais, puis statut `failed`) ; un échec ne bloque pas les autres ; jobs rejouables sans doublon.<br>- Docling pour les PDF ; chunking en 4 niveaux : structure → coupure sémantique si > 512 tokens (overlap 64) → fusion si < ~100 tokens → lien vers l'article parent.<br>- Stratégie validée : 3 stratégies comparées sur le Recall@5.<br>- Métadonnées : `doc_id`, `page_number`, `chunk_id`, `source` + `reglement`, `article`, `parent_id`, `type_doc`, `langue`.<br>- Pas de perte sur les tableaux : 1 tableau = 1 chunk (Markdown + résumé généré par le LLM, appels limités en débit) + copie dans PostgreSQL ; 100 % des cellules retrouvées sur 20 tableaux tests.<br>- PII masquées par Presidio avant stockage : 0 PII dans Qdrant. | **S2** |
| **US-02** | **Indexation Vectorielle HNSW**<br>En tant que système RAG, je veux insérer les vecteurs dans Qdrant pour une recherche rapide et filtrable. | - Qdrant lancé via Docker Compose (réseau interne).<br>- Vecteur dense `bge-m3` (1024) en index HNSW, distance Cosine, + vecteur sparse dans la même collection.<br>- Index sur `reglement`, `type_doc`, `langue` pour filtrer pendant la recherche.<br>- Insertion $< 50$ ms par batch de 64 chunks (vecteurs déjà calculés). | **S2** |
| **US-03** | **Retrieval Hybride & Reranking**<br>En tant que moteur de recherche, je veux combiner sens et mots-clés puis reranker, pour trouver un article par son sens *ou* par son numéro. | - Top-20 en recherche hybride (dense + sparse `bge-m3`, fusion RRF), comparée à une baseline BM25 ; filtres par métadonnées.<br>- Cross-Encoder `bge-reranker-v2-m3` → Top-5.<br>- Recall@5 $\ge 80\%$ (et Recall@10 $\ge 85\%$, MRR@10 $\ge 0.70$) sur le golden set, avec un script reproductible. | **S3** |
| **US-04** | **API REST FastAPI & Schémas Pydantic v2**<br>En tant qu'application cliente, je veux des endpoints typés et validés. | - Schémas stricts (`strict=True`, `extra="forbid"`) : `QueryRequest` (`question` 3–1 000 caractères, `filters` optionnels, `top_k` 1–10) et `QueryResponse` (`answer`, `citations`, `refused`).<br>- Code 422 immédiat si question vide ou hors limites.<br>- Documentation Swagger générée (`/docs`). | **S4** |
| **US-05** | **Génération de Réponse en Streaming SSE**<br>En tant que responsable conformité, je veux la réponse mot par mot avec ses sources, et un refus plutôt qu'une invention. | - `/api/v1/chat/stream` émet les événements `token`, `citations`, `done` (+ `refused`, `error`).<br>- Citations : document, article, page, score du reranker.<br>- TTFT $< 800$ ms (P95, mesuré dans Langfuse).<br>- Appels LLM protégés : timeouts (premier token 5 s, entre tokens 10 s, total 30 s), retries avec attente croissante sur 429 / 5xx avant le début du streaming, appels simultanés limités.<br>- Score du reranker trop bas → « le corpus ne permet pas de conclure » sans appel au LLM ; refus correct $\ge 90\%$ hors corpus.<br>- Sortie vérifiée par Presidio par petits blocs : 0 fuite sur 50 requêtes d'attaque. | **S4 / S5** |
| **US-06** | **Interface Web Réactive (Next.js)**<br>En tant qu'analyste, je veux poser ma question et voir la réponse s'écrire avec ses sources. | - Chat avec états : attente, chargement, streaming, erreur, refus.<br>- Citations cliquables ouvrant un panneau latéral : texte exact du chunk, document, article, page.<br>- Aucun rechargement de page. | **S5** |
| **US-07** | **Assurance Qualité & CI/CD Automatisée**<br>En tant que Lead Developer, je veux que chaque commit lance les tests et le linter Ruff. | - 4 tests Pytest (LLM et Qdrant simulés, sans coût) :<br>1. le chunking ne coupe pas un article et remplit les métadonnées ;<br>2. l'API renvoie 422 sur une question vide ;<br>3. le retrieval renvoie un Top-5 trié par le reranker ;<br>4. le SSE émet `token` → `citations` → `done`, et `refused` sous le seuil.<br>- `ruff`, `mypy` et `gitleaks` dans GitHub Actions ; merge bloqué si échec. | **S6** |
| **US-08** | **Conteneurisation de Production Multi-Stage**<br>En tant qu'ingénieur DevOps, je veux déployer tout le système via un Docker Compose de production sécurisé. | - Dockerfiles multi-stage, utilisateur non-root (API, worker, front).<br>- Bases sur un réseau Docker interne ; seuls l'API et le front sont exposés.<br>- Secrets uniquement par variables d'environnement ; healthchecks sur chaque service. | **S7** |

---

## 2. SHOULD HAVE (Forte Valeur Ajoutée Industrielle)

*Fonctionnalités renforçant la robustesse et la maturité MLOps de la solution.*

| ID | User Story Technique | Critères d'Acceptation | Séance Cible |
| :-: | :--- | :--- | :-: |
| **US-09** | **Tracing MLOps Distribué (Langfuse)**<br>En tant que responsable IA, je veux tracer chaque étape pour trouver ce qui ralentit le système. | - Latence par étape : cache, routeur, embedding, recherche, reranking, LLM.<br>- Tokens consommés et coût estimé par requête.<br>- Suivi du TTFT P95. | **S3 / S4** |
| **US-10** | **Cache Sémantique Redis**<br>En tant qu'architecte, je veux réutiliser les réponses aux questions similaires pour gagner du temps et des appels LLM. | - Similarité $> 0.95$ (mêmes filtres) → réponse en $< 50$ ms.<br>- Cache vidé à chaque nouvelle ingestion (TTL 24 h configurable).<br>- Taux de hit suivi dans Langfuse. | **S4** |
| **US-11** | **Évaluation Continue Ragas**<br>En tant que Data Scientist, je veux évaluer automatiquement les réponses sur le golden set. | - Évaluation chaque nuit sur 150 questions : Faithfulness $\ge 0.85$, Answer Relevance $\ge 0.80$.<br>- 30 questions en CI : merge bloqué si Faithfulness $< 0.80$.<br>- Modèle juge différent du modèle qui répond ; scores historisés dans Langfuse. | **S3** |

---

## 3. COULD HAVE (Différenciateurs M2 Optionnels)

*Fonctionnalités avancées pour les équipes visant l'excellence d'ingénierie.*

| ID | User Story Technique | Critères d'Acceptation |
| :-: | :--- | :--- |
| **US-12** | **Manifestes Kubernetes de Déploiement** | Deployments (API, worker, front), Services, ConfigMaps, Secrets, Ingress et HPA ; testé sur kind ou minikube. |
| **US-13** | **Routage Agentique Multi-Outils (Query Routing)** | Routeur sémantique sans LLM (< 50 ms) : compare la question à 10–20 exemples par route. Routes : recherche hybride (par défaut), requête SQL sur les tableaux (text-to-SQL par le LLM), calculatrice (variations de ratios), graphe (US-15). Bonne route choisie sur $\ge 90\%$ du golden set. |
| **US-14** | **Support Multimodal (Tableaux)** | Tableaux complexes extraits par Docling, résumés et indexés, valeurs stockées dans PostgreSQL ; réponses chiffrées exactes sur $\ge 90\%$ des 30 questions « tableaux ». Images et graphiques hors périmètre. |
| **US-15** | **GraphRAG des Renvois Réglementaires** *(ajout RegLens)* | Graphe Neo4j des renvois entre articles (regex) et des obligations (LLM) ; Neo4j renvoie les articles liés, leur texte est lu dans Qdrant. Gain de Recall@5 mesuré sur les 30 questions multi-sauts par rapport à la recherche hybride seule. |

---

## 4. WON'T HAVE (Exclus du Périmètre Projet)

*Ces éléments sont explicitement exclus pour éviter la dispersion :*
- Entraînement complet *from scratch* ou pré-entraînement de fondations de modèles LLM 70B.
- Facturation SaaS complète avec paiement Stripe et gestion d'abonnements complexes.
- Interface mobile native iOS / Android (l'interface web responsive suffit amplement).
- Description automatique des images et graphiques des rapports.
- Avis juridique engageant : RegLens cite et résume, la décision reste humaine.
- Données clients réelles : uniquement des données publiques et synthétiques.
