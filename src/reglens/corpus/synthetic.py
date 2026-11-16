"""Generate the synthetic part of the corpus: internal policies and an ICT incident log.
Everything is driven by one seed
"""

import json
import random
import string
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from faker import Faker

from reglens.corpus.policy_templates import (
    COMPANY,
    EMAIL_DOMAIN,
    FOOTER,
    HEADER_LABELS,
    INCIDENT_POLICY_DEADLINES,
    SYSTEM_OWNERS,
    TEMPLATES,
    UNITS,
    UNITS_BY_CODE,
    Lang,
    ParamValues,
    PolicyTemplate,
    Unit,
)

FAKER_LOCALES: dict[Lang, str] = {"en": "en_GB", "fr": "fr_FR"}
NON_COMPLIANT_RATE = 0.3  # share of policies (with a random check) that break the rule
INCIDENT_TOPIC = "ict_incident"

# The ict_incident policies are always the first ones, one per unit in UNITS order,
# so their IDs are fixed (POL-001 ... POL-005) whatever the seed.
INCIDENT_POLICY_IDS: dict[str, str] = {u.code: f"POL-{k + 1:03d}" for k, u in enumerate(UNITS)}


# ============================================================================ helpers


def _ascii_slug(text: str) -> str:
    """'Élodie Le Gall' -> 'elodie.le.gall' (for fake e-mail addresses)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return ".".join("".join(c for c in w if c.isalnum()) for w in plain.lower().split())


def fake_api_key(rng: random.Random) -> str:
    """One fake secret in one of three common shapes (for Presidio custom recognizers)."""
    alnum = string.ascii_letters + string.digits
    kind = rng.randrange(3)
    if kind == 0:  # company-internal style
        return "vlr_live_" + "".join(rng.choices(alnum, k=32))
    if kind == 1:  # AWS access key id shape
        return "AKIA" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=16))
    return "tok_" + "".join(rng.choices("0123456789abcdef", k=40))  # generic hex token


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def is_inside(path: Path, folder: Path) -> bool:
    """True if `path` is `folder` itself or somewhere below it."""
    return path.resolve().is_relative_to(folder.resolve())


# ============================================================================ policies


@dataclass
class PolicyTruth:
    """Ground truth for one generated policy."""

    file: str
    policy_id: str
    topic: str
    language: Lang
    unit: str
    unit_code: str
    version: str
    effective_date: str
    params: ParamValues
    compliance_rule: str | None
    compliance_reference: str | None
    compliant: bool | None  # None = this topic has no compliance check
    pii: dict[str, list[str]]
    secrets: list[str]


def plan_policies(n: int, rng: random.Random) -> list[tuple[PolicyTemplate, Lang, Unit]]:
    """Decide topic, language and unit of every policy.

    First one ict_incident policy per unit (languages alternate, so some are French),
    then the other topics in turn, each in both languages, with a random unit.
    """
    if n < len(UNITS):
        raise ValueError(f"need at least {len(UNITS)} policies (one ict_incident per unit)")
    incident_tpl = next(t for t in TEMPLATES if t.topic == INCIDENT_TOPIC)
    others = [t for t in TEMPLATES if t.topic != INCIDENT_TOPIC]

    plan: list[tuple[PolicyTemplate, Lang, Unit]] = [
        (incident_tpl, "en" if k % 2 == 0 else "fr", unit) for k, unit in enumerate(UNITS)
    ]
    for k in range(n - len(UNITS)):
        lang: Lang = "en" if (k // len(others)) % 2 == 0 else "fr"
        plan.append((others[k % len(others)], lang, rng.choice(UNITS)))
    return plan


def _choose_params(
    tpl: PolicyTemplate, unit: Unit, rng: random.Random
) -> tuple[ParamValues, bool | None]:
    params: ParamValues = {name: rng.choice(values) for name, values in tpl.params.items()}
    if tpl.check is None:
        return params, None
    if tpl.topic == INCIDENT_TOPIC:  # fixed per unit, see INCIDENT_POLICY_DEADLINES
        params.update(INCIDENT_POLICY_DEADLINES[unit.code])
        return params, params in tpl.check.compliant
    compliant = rng.random() >= NON_COMPLIANT_RATE
    options = tpl.check.compliant if compliant else tpl.check.non_compliant
    params.update(rng.choice(options))
    return params, compliant


def render_policy(tpl: PolicyTemplate, lang: Lang, values: dict[str, Any], policy_id: str) -> str:
    """Build the Markdown text: a title, a metadata line, then numbered sections."""
    labels = HEADER_LABELS[lang]
    lines = [
        f"# {tpl.title[lang]} — {values['unit']}",
        "",
        f"**{labels['id']}:** {policy_id} · **{labels['version']}:** {values['version']} · "
        f"**{labels['date']}:** {values['effective_date']} · "
        f"**{labels['unit']}:** {values['unit']} ({values['unit_code']}) · "
        f"**{labels['company']}:** {COMPANY}",
    ]
    for number, (heading, body) in enumerate(tpl.sections[lang] + FOOTER[lang], start=1):
        lines += ["", f"## {number}. {heading}", "", body.format(**values)]
    return "\n".join(lines) + "\n"


def generate_policies(out_dir: Path, truth_path: Path, *, n: int = 50, seed: int = 42) -> int:
    """Write `n` Markdown policies into out_dir and their ground truth into truth_path."""
    rng = random.Random(seed)
    fakers = {lang: Faker(locale) for lang, locale in FAKER_LOCALES.items()}
    for i, f in enumerate(fakers.values()):
        f.seed_instance(seed + i)

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("POL-*.md"):  # regenerating replaces the previous set
        old.unlink()

    truths: list[PolicyTruth] = []
    for i, (tpl, lang, unit) in enumerate(plan_policies(n, rng)):
        fake = fakers[lang]
        policy_id = f"POL-{i + 1:03d}"

        params, compliant = _choose_params(tpl, unit, rng)
        owner, deputy = fake.name(), fake.name()
        values: dict[str, Any] = {
            "company": COMPANY,
            "unit": unit.name[lang],
            "unit_code": unit.code,
            "version": f"{rng.randint(1, 3)}.{rng.randint(0, 4)}",
            "effective_date": (datetime(2024, 6, 1) + timedelta(days=rng.randrange(580)))
            .date()
            .isoformat(),
            "review_months": rng.choice([12, 24]),
            "owner_name": owner,
            "owner_title": tpl.owner_title[lang],
            "owner_email": f"{_ascii_slug(owner)}@{EMAIL_DOMAIN}",
            "owner_phone": fake.phone_number(),
            "deputy_name": deputy,
            "deputy_email": f"{_ascii_slug(deputy)}@{EMAIL_DOMAIN}",
            "iban": fake.iban(),
            "api_key": fake_api_key(rng),
            **params,
        }

        file_name = f"{policy_id}_{tpl.topic}_{lang}.md"
        (out_dir / file_name).write_text(
            render_policy(tpl, lang, values, policy_id), encoding="utf-8"
        )
        truths.append(
            PolicyTruth(
                file=file_name,
                policy_id=policy_id,
                topic=tpl.topic,
                language=lang,
                unit=values["unit"],
                unit_code=unit.code,
                version=values["version"],
                effective_date=values["effective_date"],
                params=params,
                compliance_rule=tpl.check.rule if tpl.check else None,
                compliance_reference=tpl.check.reference if tpl.check else None,
                compliant=compliant,
                pii={
                    "names": [owner, deputy],
                    "emails": [values["owner_email"], values["deputy_email"]],
                    "phones": [values["owner_phone"]],
                    "ibans": [values["iban"]],
                },
                secrets=[values["api_key"]],
            )
        )
    _write_json(truth_path, [asdict(t) for t in truths])
    return n


# ============================================================================ incident log

SYSTEMS = list(SYSTEM_OWNERS)
SEVERITIES = ["low", "medium", "high", "critical"]
SEVERITY_WEIGHTS = [40, 30, 20, 10]
LATE_RATE = 0.25  # share of major incidents deliberately reported late (against DORA)
LOG_FAKER_SEED_OFFSET = 100  # log people come from a different Faker stream than policy people

# DORA time limits for the initial notification (see policy_templates, ict_incident check)
DORA_HOURS_AFTER_CLASSIFICATION = 4
DORA_HOURS_AFTER_DETECTION = 24

LOG_START = datetime(2025, 1, 17, tzinfo=UTC)  # DORA applies from 17 January 2025
LOG_DAYS = 348

MONITORING_MESSAGES = [
    "Error rate at {pct}% on {system}",
    "p95 latency {ms} ms on {system}",
    "Health check failed on {system} node {node}",
    "Health check recovered on {system} node {node}",
    "Retry queue depth {depth} on {system}",
    "Investigating logs of {system} node {node}",
    "Traffic shifted {pct}% away from {system} node {node}",
    "Customer support received {depth} tickets related to {system}",
]


@dataclass
class IncidentTruth:
    incident_id: str
    system: str
    owning_unit: str  # English name of the unit that owns the system
    owning_unit_code: str
    applicable_policy_id: str  # that unit's ict_incident policy
    policy_deadlines: ParamValues  # the deadlines that policy sets
    severity: str
    classification: str  # "major" or "non-major" (DORA)
    detected_at: str
    classified_at: str
    reported_at: str | None  # initial notification to the authority (major only)
    resolved_at: str
    hours_detection_to_report: float | None
    hours_classification_to_report: float | None
    reported_late: bool | None  # judged against DORA; None = not major, no report due
    reported_late_per_policy: bool | None  # judged against the applicable policy
    on_call_engineer: str
    on_call_email: str
    n_lines: int


def is_reported_late(
    detected: datetime,
    classified: datetime,
    reported: datetime,
    *,
    max_hours_after_classification: int = DORA_HOURS_AFTER_CLASSIFICATION,
    max_hours_after_detection: int = DORA_HOURS_AFTER_DETECTION,
) -> bool:
    """The lateness rule, written once. Defaults are DORA's limits; pass a policy's own
    deadlines to judge against that policy instead."""
    return reported - classified > timedelta(hours=max_hours_after_classification) or (
        reported - detected > timedelta(hours=max_hours_after_detection)
    )


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(
    base: dict[str, Any],
    reported: datetime | None,
    ts: datetime,
    status: str,
    kind: str,
    message: str,
) -> dict[str, Any]:
    """One log line. `reported_at` only appears once the report has actually been sent."""
    sent = reported is not None and ts >= reported
    return {
        "timestamp": _iso(ts),
        **base,
        "status": status,
        "reported_at": _iso(reported) if sent and reported is not None else None,
        "event": kind,
        "message": message,
    }


def _report_time(
    rng: random.Random, detected: datetime, classified: datetime, late: bool
) -> datetime:
    """Pick the notification time: inside both DORA limits, or deliberately outside one."""
    deadline = min(
        classified + timedelta(hours=DORA_HOURS_AFTER_CLASSIFICATION),
        detected + timedelta(hours=DORA_HOURS_AFTER_DETECTION),
    )
    if late:
        return deadline + timedelta(minutes=rng.randint(30, 40 * 60))
    window = int((deadline - classified).total_seconds() // 60)
    return classified + timedelta(minutes=rng.randint(10, max(11, window - 5)))


def generate_logs(
    out_file: Path,
    truth_path: Path,
    *,
    n_incidents: int = 200,
    mean_lines: int = 250,
    seed: int = 42,
) -> int:
    """Write the incident log as JSON Lines (one event per line); return the line count."""
    rng = random.Random(seed)
    fake = Faker(FAKER_LOCALES["en"])
    # Own Faker seed: with the policies' seed, both generators would draw the same name
    # sequence and create accidental "links" (same person in a policy and in the log).
    fake.seed_instance(seed + LOG_FAKER_SEED_OFFSET)

    events: list[dict[str, Any]] = []
    truths: list[IncidentTruth] = []
    for i in range(n_incidents):
        incident_id = f"INC-{i + 1:04d}"
        system = rng.choice(SYSTEMS)
        unit = UNITS_BY_CODE[SYSTEM_OWNERS[system]]
        deadlines = INCIDENT_POLICY_DEADLINES[unit.code]
        severity = rng.choices(SEVERITIES, SEVERITY_WEIGHTS)[0]
        major = severity == "critical" or (severity == "high" and rng.random() < 0.5)
        detected = LOG_START + timedelta(minutes=rng.randrange(LOG_DAYS * 24 * 60))
        classified = detected + timedelta(minutes=rng.randint(10, 6 * 60))
        late = major and rng.random() < LATE_RATE
        reported = _report_time(rng, detected, classified, late) if major else None
        resolved = detected + timedelta(minutes=rng.randint(60, 72 * 60))
        closed = max(resolved, reported or resolved) + timedelta(hours=rng.randint(1, 48))
        engineer = fake.name()
        email = f"{_ascii_slug(engineer)}@{EMAIL_DOMAIN}"
        base = {
            "incident_id": incident_id,
            "severity": severity,
            "system": system,
            "owning_unit": unit.label("en"),  # e.g. "Payments (BU-PAY)": the link to policies
            "detected_at": _iso(detected),
        }
        event = partial(_event, base, reported)  # fixes the per-incident arguments

        lifecycle = [
            event(
                detected,
                "detected",
                "status_change",
                f"Alert raised on {system}, owned by {unit.label('en')}",
            ),
            event(
                detected + timedelta(minutes=rng.randint(1, 9)),
                "investigating",
                "status_change",
                f"On-call {engineer} <{email}> acknowledged the page",
            ),
            event(
                classified,
                "classified",
                "classification",
                f"Incident classified as {'major' if major else 'non-major'} (severity {severity})",
            ),
            event(resolved, "resolved", "status_change", "Service restored"),
            event(closed, "closed", "status_change", "Post-incident review scheduled"),
        ]
        if reported is not None:
            lifecycle.append(
                event(
                    reported,
                    "reported",
                    "regulatory_report",
                    "Initial notification sent to the competent authority",
                )
            )

        n_monitoring = max(0, rng.randint(mean_lines // 2, mean_lines * 3 // 2) - len(lifecycle))
        span = int((resolved - detected).total_seconds())
        for _ in range(n_monitoring):
            ts = detected + timedelta(seconds=rng.randrange(max(1, span)))
            status = "investigating" if ts < classified else "mitigating"
            msg = rng.choice(MONITORING_MESSAGES).format(
                system=system,
                pct=rng.randint(1, 95),
                ms=rng.randint(120, 9000),
                node=rng.randint(1, 12),
                depth=rng.randint(5, 5000),
            )
            lifecycle.append(event(ts, status, "monitoring", msg))

        events.extend(lifecycle)
        truths.append(
            IncidentTruth(
                incident_id=incident_id,
                system=system,
                owning_unit=unit.name["en"],
                owning_unit_code=unit.code,
                applicable_policy_id=INCIDENT_POLICY_IDS[unit.code],
                policy_deadlines=deadlines,
                severity=severity,
                classification="major" if major else "non-major",
                detected_at=_iso(detected),
                classified_at=_iso(classified),
                reported_at=_iso(reported) if reported else None,
                resolved_at=_iso(resolved),
                hours_detection_to_report=(
                    round((reported - detected).total_seconds() / 3600, 2) if reported else None
                ),
                hours_classification_to_report=(
                    round((reported - classified).total_seconds() / 3600, 2) if reported else None
                ),
                reported_late=(
                    is_reported_late(detected, classified, reported) if reported else None
                ),
                reported_late_per_policy=(
                    is_reported_late(
                        detected,
                        classified,
                        reported,
                        max_hours_after_classification=deadlines["initial_hours"],
                        max_hours_after_detection=deadlines["initial_cap_hours"],
                    )
                    if reported
                    else None
                ),
                on_call_engineer=engineer,
                on_call_email=email,
                n_lines=len(lifecycle),
            )
        )

    # A real log interleaves incidents in time order.
    events.sort(key=lambda e: (e["timestamp"], e["incident_id"]))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8", newline="\n") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    _write_json(truth_path, [asdict(t) for t in truths])
    return len(events)


# ============================================================================ both


@dataclass(frozen=True)
class GenerationReport:
    n_policies: int | None
    n_log_lines: int | None


def generate_all(
    raw_dir: Path,
    truth_dir: Path,
    *,
    seed: int = 42,
    n_policies: int = 50,
    n_incidents: int = 200,
    only: str | None = None,
) -> GenerationReport:
    """Generate policies and/or logs into raw_dir, ground truth into truth_dir.

    Refuses a truth_dir inside raw_dir: ingestion reads everything under data/raw/,
    and the answers must never be indexed next to the questions' sources.
    """
    if is_inside(truth_dir, raw_dir):
        raise ValueError(f"ground truth dir {truth_dir} must not be inside {raw_dir}")
    n_pol = n_lines = None
    if only in (None, "policies"):
        n_pol = generate_policies(
            raw_dir / "policies", truth_dir / "policies.json", n=n_policies, seed=seed
        )
    if only in (None, "logs"):
        n_lines = generate_logs(
            raw_dir / "logs" / "incident_log.jsonl",
            truth_dir / "incidents.json",
            n_incidents=n_incidents,
            seed=seed,
        )
    return GenerationReport(n_pol, n_lines)
