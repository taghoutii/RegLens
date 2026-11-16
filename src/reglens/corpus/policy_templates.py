"""Text templates for the synthetic internal policies"""

from dataclasses import dataclass, field
from typing import Literal

Lang = Literal["en", "fr"]
ParamValues = dict[str, int]

COMPANY = "Velora Fintech SAS"
EMAIL_DOMAIN = "velora-fintech.example"  # .example is reserved: can never be a real domain


@dataclass(frozen=True)
class Unit:
    """A business unit. The code is identical in every language, so an English log line
    ("Payments (BU-PAY)") can be matched to a French policy ("Paiements (BU-PAY)")."""

    code: str
    name: dict[Lang, str]

    def label(self, lang: Lang) -> str:
        return f"{self.name[lang]} ({self.code})"


UNITS: list[Unit] = [
    Unit("BU-PAY", {"en": "Payments", "fr": "Paiements"}),
    Unit("BU-LEN", {"en": "Consumer Lending", "fr": "Crédit à la consommation"}),
    Unit("BU-CRY", {"en": "Crypto Services", "fr": "Services crypto"}),
    Unit("BU-GIT", {"en": "Group IT", "fr": "DSI Groupe"}),
    Unit("BU-OPS", {"en": "Customer Operations", "fr": "Relations clients"}),
]
UNITS_BY_CODE: dict[str, Unit] = {u.code: u for u in UNITS}

# Which business unit owns each technical system (used by the incident log).
SYSTEM_OWNERS: dict[str, str] = {
    "payments-gateway": "BU-PAY",
    "card-processing": "BU-PAY",
    "core-banking-api": "BU-LEN",  # loan accounts live in core banking
    "crypto-wallet": "BU-CRY",
    "identity-provider": "BU-GIT",
    "data-warehouse": "BU-GIT",
    "kyc-service": "BU-OPS",
    "mobile-app-backend": "BU-OPS",
}

# Each unit has exactly one ict_incident policy, with these deadlines (hours after
# classification / hours after detection). Set explicitly rather than drawn at random,
# so that "late per DORA" and "late per policy" differ in both directions:
#   lenient (non-compliant) policies forgive some DORA-late incidents,
#   the strict (still compliant) policy flags some incidents DORA accepts.
INCIDENT_POLICY_DEADLINES: dict[str, ParamValues] = {
    "BU-PAY": {"initial_hours": 48, "initial_cap_hours": 72},  # lenient -> non-compliant
    "BU-LEN": {"initial_hours": 12, "initial_cap_hours": 48},  # lenient -> non-compliant
    "BU-CRY": {"initial_hours": 4, "initial_cap_hours": 24},  # exactly DORA
    "BU-GIT": {"initial_hours": 4, "initial_cap_hours": 24},  # exactly DORA
    "BU-OPS": {"initial_hours": 2, "initial_cap_hours": 12},  # stricter than DORA -> compliant
}


@dataclass(frozen=True)
class ComplianceCheck:
    """A rule the policy may or may not respect."""

    rule: str  # what the regulation requires, in plain words
    reference: str  # where the requirement comes from (to verify when writing the golden set)
    compliant: list[ParamValues]  # parameter sets that respect the rule
    non_compliant: list[ParamValues]  # parameter sets that break it


@dataclass(frozen=True)
class PolicyTemplate:
    topic: str
    title: dict[Lang, str]
    owner_title: dict[Lang, str]
    sections: dict[Lang, list[tuple[str, str]]]  # (heading, body) pairs
    params: dict[str, list[int]] = field(default_factory=dict)  # free parameters (no check)
    check: ComplianceCheck | None = None


FOOTER: dict[Lang, list[tuple[str, str]]] = {
    "en": [
        (
            "Document control",
            "Owner: {owner_name} ({owner_title}), {owner_email}, {owner_phone}.\n"
            "Deputy: {deputy_name}, {deputy_email}.\n"
            "This policy is reviewed every {review_months} months. "
            "Version {version}, effective {effective_date}.",
        ),
        (
            "Annex — Operational details",
            "Costs related to this policy are charged to the {unit} operating account "
            "(IBAN {iban}). The automation service used by this process authenticates with "
            "the key `{api_key}`; this key must be rotated every 90 days.",
        ),
    ],
    "fr": [
        (
            "Gestion documentaire",
            "Responsable : {owner_name} ({owner_title}), {owner_email}, {owner_phone}.\n"
            "Suppléant : {deputy_name}, {deputy_email}.\n"
            "La présente politique est révisée tous les {review_months} mois. "
            "Version {version}, en vigueur depuis le {effective_date}.",
        ),
        (
            "Annexe — Informations opérationnelles",
            "Les coûts liés à cette politique sont imputés au compte d'exploitation {unit} "
            "(IBAN {iban}). Le service d'automatisation utilisé par ce processus s'authentifie "
            "avec la clé `{api_key}` ; cette clé doit être renouvelée tous les 90 jours.",
        ),
    ],
}

HEADER_LABELS: dict[Lang, dict[str, str]] = {
    "en": {
        "id": "Policy ID",
        "version": "Version",
        "date": "Effective date",
        "unit": "Business unit",
        "company": "Company",
    },
    "fr": {
        "id": "Identifiant",
        "version": "Version",
        "date": "Date d'entrée en vigueur",
        "unit": "Unité",
        "company": "Société",
    },
}


TEMPLATES: list[PolicyTemplate] = [
    # ------------------------------------------------------------------ DORA incidents
    PolicyTemplate(
        topic="ict_incident",
        title={
            "en": "ICT Incident Management and Reporting Policy",
            "fr": "Politique de gestion et de déclaration des incidents liés aux TIC",
        },
        owner_title={"en": "Head of ICT Risk", "fr": "Responsable des risques TIC"},
        sections={
            "en": [
                (
                    "Purpose and scope",
                    "This policy sets out how {company} ({unit}) detects, classifies, handles and "
                    "reports ICT-related incidents. It applies to all staff, contractors and ICT "
                    "third-party service providers supporting the {unit} business line.",
                ),
                (
                    "Detection and classification",
                    "Every ICT-related incident is logged in the incident register as soon as it is "
                    "detected. The incident manager classifies the incident within two hours of "
                    "detection, using the criteria of Commission Delegated Regulation (EU) "
                    "2024/1772: clients affected, duration, geographical spread, data losses, "
                    "criticality of the services affected and economic impact.",
                ),
                (
                    "Reporting of major incidents",
                    "When an incident is classified as major, the Head of ICT Risk submits the "
                    "initial notification to the competent authority within {initial_hours} hours "
                    "of the classification, and no later than {initial_cap_hours} hours after the "
                    "incident was detected. An intermediate report follows within 72 hours of the "
                    "initial notification, and a final report within one month of the latest "
                    "intermediate report.",
                ),
                (
                    "Communication and lessons learned",
                    "Clients whose financial interests are affected are informed without undue "
                    "delay. Within 20 working days after closure, a post-incident review identifies "
                    "root causes and remediation actions, which are tracked by the ICT risk "
                    "committee.",
                ),
            ],
            "fr": [
                (
                    "Objet et périmètre",
                    "La présente politique décrit comment {company} ({unit}) détecte, classe, traite "
                    "et déclare les incidents liés aux TIC. Elle s'applique à l'ensemble du "
                    "personnel, aux sous-traitants et aux prestataires tiers de services TIC qui "
                    "soutiennent l'activité {unit}.",
                ),
                (
                    "Détection et classification",
                    "Tout incident lié aux TIC est inscrit au registre des incidents dès sa "
                    "détection. Le responsable des incidents le classe dans les deux heures suivant "
                    "la détection, selon les critères du règlement délégué (UE) 2024/1772 : clients "
                    "touchés, durée, étendue géographique, pertes de données, criticité des services "
                    "touchés et impact économique.",
                ),
                (
                    "Déclaration des incidents majeurs",
                    "Lorsqu'un incident est classé comme majeur, le responsable des risques TIC "
                    "adresse la notification initiale à l'autorité compétente dans un délai de "
                    "{initial_hours} heures à compter de la classification, et au plus tard "
                    "{initial_cap_hours} heures après la détection de l'incident. Un rapport "
                    "intermédiaire suit dans les 72 heures après la notification initiale, puis un "
                    "rapport final dans un délai d'un mois après le dernier rapport intermédiaire.",
                ),
                (
                    "Communication et retour d'expérience",
                    "Les clients dont les intérêts financiers sont affectés sont informés dans les "
                    "meilleurs délais. Dans les 20 jours ouvrés suivant la clôture, une revue "
                    "post-incident identifie les causes profondes et les actions correctives, "
                    "suivies par le comité des risques TIC.",
                ),
            ],
        },
        check=ComplianceCheck(
            rule="Initial notification of a major ICT incident within 4 hours of classification "
            "and at the latest 24 hours after becoming aware of the incident.",
            reference="Delegated Regulation (EU) 2025/301 (DORA incident reporting time limits)",
            compliant=[
                {"initial_hours": 4, "initial_cap_hours": 24},
                {"initial_hours": 2, "initial_cap_hours": 12},  # stricter is still compliant
            ],
            non_compliant=[
                {"initial_hours": 24, "initial_cap_hours": 72},
                {"initial_hours": 48, "initial_cap_hours": 72},
                {"initial_hours": 12, "initial_cap_hours": 48},
            ],
        ),
    ),
    # ------------------------------------------------------------------ GDPR breaches
    PolicyTemplate(
        topic="data_breach",
        title={
            "en": "Personal Data Breach Notification Policy",
            "fr": "Politique de notification des violations de données à caractère personnel",
        },
        owner_title={"en": "Data Protection Officer", "fr": "Délégué à la protection des données"},
        sections={
            "en": [
                (
                    "Purpose and scope",
                    "This policy explains how {company} ({unit}) identifies, assesses and notifies "
                    "personal data breaches, in line with the General Data Protection Regulation "
                    "(EU) 2016/679.",
                ),
                (
                    "Identification and assessment",
                    "Any employee who suspects a personal data breach reports it immediately to the "
                    "Data Protection Officer. The DPO assesses the likely risk to the rights and "
                    "freedoms of the persons concerned and records every breach, including those "
                    "that are not notified, in the breach register.",
                ),
                (
                    "Notification to the supervisory authority",
                    "Unless the breach is unlikely to result in a risk to individuals, the DPO "
                    "notifies the supervisory authority within {breach_hours} hours of becoming "
                    "aware of it. If the notification is made later, it states the reasons for the "
                    "delay.",
                ),
                (
                    "Communication to data subjects",
                    "When the breach is likely to result in a high risk, the persons concerned are "
                    "informed without undue delay, in clear and plain language, of the likely "
                    "consequences and of the measures taken.",
                ),
            ],
            "fr": [
                (
                    "Objet et périmètre",
                    "La présente politique explique comment {company} ({unit}) identifie, évalue et "
                    "notifie les violations de données à caractère personnel, conformément au "
                    "règlement général sur la protection des données (UE) 2016/679.",
                ),
                (
                    "Identification et évaluation",
                    "Tout salarié qui soupçonne une violation de données la signale immédiatement "
                    "au délégué à la protection des données (DPO). Le DPO évalue le risque probable "
                    "pour les droits et libertés des personnes concernées et consigne chaque "
                    "violation, y compris celles qui ne sont pas notifiées, dans le registre des "
                    "violations.",
                ),
                (
                    "Notification à l'autorité de contrôle",
                    "Sauf si la violation n'est pas susceptible d'engendrer un risque pour les "
                    "personnes, le DPO la notifie à l'autorité de contrôle dans un délai de "
                    "{breach_hours} heures après en avoir pris connaissance. Si la notification est "
                    "plus tardive, elle indique les motifs du retard.",
                ),
                (
                    "Communication aux personnes concernées",
                    "Lorsque la violation est susceptible d'engendrer un risque élevé, les personnes "
                    "concernées sont informées dans les meilleurs délais, en des termes clairs et "
                    "simples, des conséquences probables et des mesures prises.",
                ),
            ],
        },
        check=ComplianceCheck(
            rule="Notify the supervisory authority without undue delay and, where feasible, "
            "not later than 72 hours after becoming aware of the breach.",
            reference="GDPR (EU) 2016/679, Article 33",
            compliant=[{"breach_hours": 72}],
            non_compliant=[{"breach_hours": 96}, {"breach_hours": 120}],
        ),
    ),
    # ------------------------------------------------------------------ AMLR
    PolicyTemplate(
        topic="aml_kyc",
        title={
            "en": "Anti-Money Laundering and Customer Due Diligence Policy",
            "fr": "Politique de lutte contre le blanchiment et de vigilance à l'égard "
            "de la clientèle",
        },
        owner_title={"en": "AML Compliance Officer", "fr": "Responsable de la conformité LCB-FT"},
        sections={
            "en": [
                (
                    "Purpose and scope",
                    "This policy sets out the measures {company} ({unit}) applies to prevent money "
                    "laundering and terrorist financing, under Regulation (EU) 2024/1624 (AMLR).",
                ),
                (
                    "Customer due diligence",
                    "Before entering into a business relationship, the {unit} team identifies and "
                    "verifies the customer and its beneficial owners, assesses the purpose of the "
                    "relationship and assigns a risk score. High-risk customers are subject to "
                    "enhanced due diligence and to approval by senior management.",
                ),
                (
                    "Ongoing monitoring and suspicious transactions",
                    "Transactions are monitored continuously against the customer's risk profile. "
                    "Any suspicious transaction is reported to the Financial Intelligence Unit by "
                    "the AML Compliance Officer, and the customer is never informed of the report.",
                ),
                (
                    "Record keeping",
                    "Customer due diligence documents and transaction records are kept for "
                    "{retention_years} years after the end of the business relationship or after "
                    "the date of the occasional transaction.",
                ),
            ],
            "fr": [
                (
                    "Objet et périmètre",
                    "La présente politique décrit les mesures appliquées par {company} ({unit}) "
                    "pour prévenir le blanchiment de capitaux et le financement du terrorisme, en "
                    "application du règlement (UE) 2024/1624 (AMLR).",
                ),
                (
                    "Vigilance à l'égard de la clientèle",
                    "Avant d'entrer en relation d'affaires, l'équipe {unit} identifie et vérifie le "
                    "client et ses bénéficiaires effectifs, évalue l'objet de la relation et "
                    "attribue un score de risque. Les clients à haut risque font l'objet de mesures "
                    "de vigilance renforcées et d'une approbation par la direction.",
                ),
                (
                    "Surveillance continue et opérations suspectes",
                    "Les opérations sont surveillées en continu au regard du profil de risque du "
                    "client. Toute opération suspecte est déclarée à la cellule de renseignement "
                    "financier par le responsable de la conformité LCB-FT, et le client n'est "
                    "jamais informé de cette déclaration.",
                ),
                (
                    "Conservation des documents",
                    "Les documents de vigilance et les enregistrements des opérations sont "
                    "conservés pendant {retention_years} ans après la fin de la relation d'affaires "
                    "ou après la date de l'opération occasionnelle.",
                ),
            ],
        },
        check=ComplianceCheck(
            rule="Keep customer due diligence documents and transaction records for five years "
            "after the end of the business relationship or the occasional transaction.",
            reference="AMLR (EU) 2024/1624, record retention",
            compliant=[{"retention_years": 5}],
            non_compliant=[{"retention_years": 3}, {"retention_years": 2}],
        ),
    ),
    # ------------------------------------------------------------------ DORA third parties
    PolicyTemplate(
        topic="ict_third_party",
        title={
            "en": "ICT Third-Party Risk Management Policy",
            "fr": "Politique de gestion du risque lié aux prestataires tiers de services TIC",
        },
        owner_title={
            "en": "Head of Vendor Management",
            "fr": "Responsable de la gestion des prestataires",
        },
        sections={
            "en": [
                (
                    "Purpose and scope",
                    "This policy governs how {company} ({unit}) selects, contracts, monitors and "
                    "exits ICT third-party service providers, in line with Regulation (EU) "
                    "2022/2554 (DORA).",
                ),
                (
                    "Register of information",
                    "All contractual arrangements with ICT third-party service providers are "
                    "recorded in the register of information, which identifies the arrangements "
                    "supporting critical or important functions. The register is updated within "
                    "{register_update_days} days of any new or amended contract.",
                ),
                (
                    "Due diligence and contracts",
                    "Before signing, the {unit} team assesses the provider's security, financial "
                    "soundness and concentration risk. Contracts include service levels, audit and "
                    "access rights, data location, assistance during ICT incidents and termination "
                    "rights.",
                ),
                (
                    "Exit strategies",
                    "For each ICT service supporting a critical or important function, an exit plan "
                    "is documented and tested every {exit_test_months} months, so that the service "
                    "can be transferred to another provider or brought in-house without "
                    "disruption.",
                ),
            ],
            "fr": [
                (
                    "Objet et périmètre",
                    "La présente politique encadre la manière dont {company} ({unit}) sélectionne, "
                    "contractualise, surveille et quitte les prestataires tiers de services TIC, "
                    "conformément au règlement (UE) 2022/2554 (DORA).",
                ),
                (
                    "Registre d'informations",
                    "Tous les accords contractuels conclus avec des prestataires tiers de services "
                    "TIC sont inscrits au registre d'informations, qui identifie les accords "
                    "soutenant des fonctions critiques ou importantes. Le registre est mis à jour "
                    "dans les {register_update_days} jours suivant tout contrat nouveau ou modifié.",
                ),
                (
                    "Diligence préalable et contrats",
                    "Avant signature, l'équipe {unit} évalue la sécurité, la solidité financière et "
                    "le risque de concentration du prestataire. Les contrats prévoient les niveaux "
                    "de service, les droits d'audit et d'accès, la localisation des données, "
                    "l'assistance en cas d'incident lié aux TIC et les droits de résiliation.",
                ),
                (
                    "Stratégies de sortie",
                    "Pour chaque service TIC soutenant une fonction critique ou importante, un plan "
                    "de sortie est documenté et testé tous les {exit_test_months} mois, afin que le "
                    "service puisse être transféré à un autre prestataire ou réintégré sans "
                    "interruption.",
                ),
            ],
        },
        params={"register_update_days": [5, 10, 15], "exit_test_months": [12, 24]},
    ),
    # ------------------------------------------------------------------ AI Act
    PolicyTemplate(
        topic="ai_governance",
        title={
            "en": "AI Credit Scoring Governance Policy",
            "fr": "Politique de gouvernance du modèle d'IA de notation de crédit",
        },
        owner_title={"en": "Head of Model Risk", "fr": "Responsable du risque de modèle"},
        sections={
            "en": [
                (
                    "Purpose and scope",
                    "This policy governs the credit scoring model used by {company} ({unit}) to "
                    "assess the creditworthiness of natural persons. Under Regulation (EU) "
                    "2024/1689 (AI Act), such a system is classified as high-risk.",
                ),
                (
                    "Human oversight",
                    "No credit application is refused on the sole basis of the model score. A "
                    "trained credit analyst reviews every negative decision and can override the "
                    "score; each override is documented with its justification.",
                ),
                (
                    "Logging and record keeping",
                    "Logs automatically generated by the scoring system are kept for "
                    "{log_retention_months} months, so that decisions can be traced and incidents "
                    "investigated.",
                ),
                (
                    "Monitoring and incidents",
                    "Model performance and bias indicators are monitored monthly. Any serious "
                    "incident or significant drift is reported to the AI governance committee "
                    "within five working days, and the provider of the system is informed.",
                ),
            ],
            "fr": [
                (
                    "Objet et périmètre",
                    "La présente politique encadre le modèle de notation de crédit utilisé par "
                    "{company} ({unit}) pour évaluer la solvabilité des personnes physiques. Au "
                    "sens du règlement (UE) 2024/1689 (AI Act), ce système est classé à haut "
                    "risque.",
                ),
                (
                    "Contrôle humain",
                    "Aucune demande de crédit n'est refusée sur la seule base du score du modèle. "
                    "Un analyste crédit formé examine chaque décision défavorable et peut passer "
                    "outre le score ; chaque dérogation est documentée et justifiée.",
                ),
                (
                    "Journalisation et conservation",
                    "Les journaux générés automatiquement par le système de notation sont conservés "
                    "pendant {log_retention_months} mois, afin de pouvoir retracer les décisions et "
                    "analyser les incidents.",
                ),
                (
                    "Surveillance et incidents",
                    "Les indicateurs de performance et de biais du modèle sont suivis chaque mois. "
                    "Tout incident grave ou dérive significative est signalé au comité de "
                    "gouvernance de l'IA dans un délai de cinq jours ouvrés, et le fournisseur du "
                    "système en est informé.",
                ),
            ],
        },
        check=ComplianceCheck(
            rule="Deployers of high-risk AI systems keep the automatically generated logs for "
            "at least six months.",
            reference="AI Act (EU) 2024/1689, obligations of deployers (Article 26)",
            compliant=[{"log_retention_months": 6}, {"log_retention_months": 12}],
            non_compliant=[{"log_retention_months": 1}, {"log_retention_months": 3}],
        ),
    ),
    # ------------------------------------------------------------------ MiCA
    PolicyTemplate(
        topic="crypto_custody",
        title={
            "en": "Crypto-Asset Custody and Safeguarding Policy",
            "fr": "Politique de conservation et de protection des crypto-actifs",
        },
        owner_title={
            "en": "Head of Digital Asset Operations",
            "fr": "Responsable des opérations sur actifs numériques",
        },
        sections={
            "en": [
                (
                    "Purpose and scope",
                    "This policy describes how {company} ({unit}) holds and safeguards crypto-assets "
                    "on behalf of clients, as a crypto-asset service provider under Regulation (EU) "
                    "2023/1114 (MiCA).",
                ),
                (
                    "Segregation of client assets",
                    "Client crypto-assets are held in wallets that are legally and operationally "
                    "segregated from the company's own assets, and the register of client "
                    "positions is reconciled every {reconciliation_hours} hours.",
                ),
                (
                    "Key management",
                    "Private keys of cold wallets are generated and stored in hardware security "
                    "modules. Withdrawals above the daily threshold require the approval of two "
                    "authorised officers.",
                ),
                (
                    "Client statements",
                    "Each client receives a statement of positions at least every three months, "
                    "and on request.",
                ),
            ],
            "fr": [
                (
                    "Objet et périmètre",
                    "La présente politique décrit comment {company} ({unit}) détient et protège les "
                    "crypto-actifs pour le compte de ses clients, en tant que prestataire de "
                    "services sur crypto-actifs au sens du règlement (UE) 2023/1114 (MiCA).",
                ),
                (
                    "Ségrégation des actifs des clients",
                    "Les crypto-actifs des clients sont détenus dans des portefeuilles juridiquement "
                    "et opérationnellement séparés des actifs propres de la société, et le registre "
                    "des positions des clients est rapproché toutes les {reconciliation_hours} "
                    "heures.",
                ),
                (
                    "Gestion des clés",
                    "Les clés privées des portefeuilles froids sont générées et stockées dans des "
                    "modules matériels de sécurité (HSM). Les retraits supérieurs au seuil "
                    "journalier nécessitent l'approbation de deux responsables habilités.",
                ),
                (
                    "Relevés clients",
                    "Chaque client reçoit un relevé de ses positions au moins tous les trois mois, "
                    "ainsi que sur demande.",
                ),
            ],
        },
        params={"reconciliation_hours": [12, 24]},
    ),
]
