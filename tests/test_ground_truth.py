"""Ground truth must never sit under data/raw/ (ingestion reads all of data/raw/),
and the incident -> unit -> policy links must hold."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from reglens.config import Settings
from reglens.corpus.policy_templates import INCIDENT_POLICY_DEADLINES, SYSTEM_OWNERS, UNITS
from reglens.corpus.synthetic import generate_all, is_inside, is_reported_late


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def generated(tmp_path: Path) -> tuple[Path, Path]:
    raw, truth = tmp_path / "raw", tmp_path / "ground_truth"
    generate_all(raw, truth, seed=11, n_policies=12, n_incidents=60)
    return raw, truth


# ---------------------------------------------------------------- location


def test_configured_ground_truth_is_not_under_raw() -> None:
    settings = Settings(_env_file=None)
    assert not is_inside(settings.ground_truth_dir, settings.raw_dir)


def test_generator_refuses_ground_truth_under_raw(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be inside"):
        generate_all(tmp_path / "raw", tmp_path / "raw" / "ground_truth", n_policies=6)


def test_no_ground_truth_file_is_written_under_raw(generated: tuple[Path, Path]) -> None:
    raw, truth = generated
    assert {p.name for p in truth.iterdir()} == {"policies.json", "incidents.json"}
    assert not list(raw.rglob("*.json")), "only .md and .jsonl corpus files belong in raw/"


# ---------------------------------------------------------------- people


def _people(truth: Path) -> tuple[set[str], set[str]]:
    """(people planted in policies, people planted in logs), as names + emails, lowercased."""
    in_policies = {
        v.lower()
        for p in _load(truth / "policies.json")
        for v in p["pii"]["names"] + p["pii"]["emails"]
    }
    in_logs = {
        v.lower()
        for i in _load(truth / "incidents.json")
        for v in (i["on_call_engineer"], i["on_call_email"])
    }
    return in_policies, in_logs


def test_no_person_appears_in_both_policies_and_logs(tmp_path: Path) -> None:
    """The only intended policy<->log link is the business unit. A shared person would be
    an accidental link (and confuse multi-hop questions). Checked on the real corpus size
    and seed, so the committed defaults are protected."""
    truth = tmp_path / "ground_truth"
    generate_all(tmp_path / "raw", truth, seed=42, n_policies=50, n_incidents=200)
    in_policies, in_logs = _people(truth)
    assert in_policies and in_logs
    assert sorted(in_policies & in_logs) == []


# ---------------------------------------------------------------- links


def test_every_unit_has_exactly_one_incident_policy(generated: tuple[Path, Path]) -> None:
    _, truth = generated
    policies = _load(truth / "policies.json")
    incident_units = [p["unit_code"] for p in policies if p["topic"] == "ict_incident"]
    assert sorted(incident_units) == sorted(u.code for u in UNITS)


def test_incidents_point_to_their_unit_policy(generated: tuple[Path, Path]) -> None:
    _, truth = generated
    policies = {p["policy_id"]: p for p in _load(truth / "policies.json")}
    for inc in _load(truth / "incidents.json"):
        assert inc["owning_unit_code"] == SYSTEM_OWNERS[inc["system"]]
        policy = policies[inc["applicable_policy_id"]]
        assert policy["topic"] == "ict_incident"
        assert policy["unit_code"] == inc["owning_unit_code"]
        assert policy["params"] == inc["policy_deadlines"]


def test_late_per_policy_uses_the_policy_deadlines(generated: tuple[Path, Path]) -> None:
    _, truth = generated
    for inc in _load(truth / "incidents.json"):
        if inc["reported_at"] is None:
            assert inc["reported_late_per_policy"] is None
            continue
        d = inc["policy_deadlines"]
        expected = is_reported_late(
            _ts(inc["detected_at"]),
            _ts(inc["classified_at"]),
            _ts(inc["reported_at"]),
            max_hours_after_classification=d["initial_hours"],
            max_hours_after_detection=d["initial_cap_hours"],
        )
        assert inc["reported_late_per_policy"] == expected


def test_link_is_findable_in_the_documents(generated: tuple[Path, Path]) -> None:
    """The unit code in a log line must also appear in the header of that unit's policy."""
    raw, truth = generated
    policies = {p["policy_id"]: p for p in _load(truth / "policies.json")}
    first_line = json.loads(
        (raw / "logs" / "incident_log.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    inc = next(
        i for i in _load(truth / "incidents.json") if i["incident_id"] == first_line["incident_id"]
    )
    code = inc["owning_unit_code"]
    assert code in first_line["owning_unit"]
    policy_text = (raw / "policies" / policies[inc["applicable_policy_id"]]["file"]).read_text(
        encoding="utf-8"
    )
    assert f"({code})" in policy_text.splitlines()[2]  # the metadata line


def test_incident_policy_compliance_flag_matches_dora(generated: tuple[Path, Path]) -> None:
    """Lenient deadlines are non-compliant; equal or stricter ones are compliant."""
    _, truth = generated
    for p in _load(truth / "policies.json"):
        if p["topic"] != "ict_incident":
            continue
        d = p["params"]
        assert d == INCIDENT_POLICY_DEADLINES[p["unit_code"]]
        within_dora = d["initial_hours"] <= 4 and d["initial_cap_hours"] <= 24
        assert p["compliant"] == within_dora
