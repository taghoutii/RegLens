"""Check masked documents against the ground truth: did any planted value survive?

A value "leaks" if, after masking, it still appears in the document it was planted in:
  - the full value (an e-mail, an IBAN, a key, a full name), or
  - for names, any part of the name (e.g. the surname left over when NER masked only
    the first name). Titles such as "Dr" are ignored.
Only this module and the tests read data/ground_truth/; ingestion never does.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reglens.ingestion.models import ParsedDocument

NAME_TITLES = {"Dr", "Mr", "Mrs", "Ms", "Miss", "Mx", "Prof", "Mme", "Mlle", "M.", "Pr"}


@dataclass
class Planted:
    doc_id: str  # where the value was planted ("POL-005", "incident_log")
    kind: str  # names | emails | phones | ibans | secrets
    value: str
    where: str | None = None  # for log people: the incident id


@dataclass
class LeakReport:
    total: Counter[str] = field(default_factory=Counter)
    leaked: Counter[str] = field(default_factory=Counter)
    failures: list[tuple[Planted, str]] = field(default_factory=list)  # (value, how it leaked)

    def recall(self) -> dict[str, float]:
        return {k: 1 - self.leaked[k] / n for k, n in self.total.items() if n}

    def summary(self) -> dict[str, Any]:
        """Counts and ids only: safe to commit (no planted value is written)."""
        return {
            "planted": dict(self.total),
            "leaked": dict(self.leaked),
            "recall": {k: round(v, 4) for k, v in self.recall().items()},
            "failures": [
                {"doc_id": p.doc_id, "kind": p.kind, "where": p.where, "how": how}
                for p, how in self.failures
            ],
        }


def load_planted(truth_dir: Path) -> list[Planted]:
    planted: list[Planted] = []
    policies = json.loads((truth_dir / "policies.json").read_text(encoding="utf-8"))
    for p in policies:
        for kind, values in p["pii"].items():
            planted += [Planted(p["policy_id"], kind, v) for v in values]
        planted += [Planted(p["policy_id"], "secrets", v) for v in p["secrets"]]
    incidents = json.loads((truth_dir / "incidents.json").read_text(encoding="utf-8"))
    for i in incidents:
        planted.append(Planted("incident_log", "names", i["on_call_engineer"], i["incident_id"]))
        planted.append(Planted("incident_log", "emails", i["on_call_email"], i["incident_id"]))
    return planted


def document_text(doc: ParsedDocument) -> str:
    """Every string a masked document will ever expose (text, sections, cells, records)."""
    parts = [doc.title or ""]
    for b in doc.blocks:
        parts += [b.text, b.section or ""]
        if b.table:
            parts += [c for row in b.table.cells for c in row] + [b.table.caption or ""]
        if b.records:
            parts += [str(v) for rec in b.records for v in rec.values()]
    return "\n".join(parts)


def name_parts(name: str) -> list[str]:
    return [p for p in re.split(r"[\s-]+", name) if len(p) > 2 and p not in NAME_TITLES]


def how_it_leaks(value: str, kind: str, text: str) -> str | None:
    """None if the value is gone; otherwise a short reason (no value in it)."""
    if value in text:
        return "full value"
    if kind == "names":
        left = [p for p in name_parts(value) if re.search(rf"\b{re.escape(p)}\b", text)]
        if left:
            return f"{len(left)} of {len(name_parts(value))} name parts left"
    return None


def check_leaks(docs: dict[str, ParsedDocument], planted: list[Planted]) -> LeakReport:
    """docs: masked documents keyed by doc_id (the log's text is checked per incident)."""
    report = LeakReport()
    texts = {doc_id: document_text(d) for doc_id, d in docs.items()}
    incident_texts: dict[str, str] = {}
    if "incident_log" in docs:
        for b in docs["incident_log"].blocks:
            incident_texts[b.parent_id.split("#", 1)[1]] = "\n".join(
                [b.text] + [str(v) for rec in b.records or [] for v in rec.values()]
            )
    for p in planted:
        if p.doc_id not in docs:
            continue
        report.total[p.kind] += 1
        text = incident_texts.get(p.where, "") if p.where else texts[p.doc_id]
        how = how_it_leaks(p.value, p.kind, text)
        if how:
            report.leaked[p.kind] += 1
            report.failures.append((p, how))
    return report
