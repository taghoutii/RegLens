"""Tests for the synthetic corpus generator (small sizes, so they run in milliseconds)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from reglens.corpus.synthetic import generate_logs, generate_policies, is_reported_late


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _policies(tmp_path: Path, seed: int = 1) -> list[dict[str, Any]]:
    generate_policies(tmp_path / "p", tmp_path / "p.json", n=24, seed=seed)
    data: list[dict[str, Any]] = _load(tmp_path / "p.json")
    return data


def test_same_seed_gives_identical_files(tmp_path: Path) -> None:
    generate_policies(tmp_path / "a", tmp_path / "a.json", n=12, seed=7)
    generate_policies(tmp_path / "b", tmp_path / "b.json", n=12, seed=7)
    for file_a in (tmp_path / "a").iterdir():
        assert file_a.read_bytes() == (tmp_path / "b" / file_a.name).read_bytes()


def test_every_planted_pii_and_secret_is_in_its_policy(tmp_path: Path) -> None:
    for truth in _policies(tmp_path):
        text = (tmp_path / "p" / truth["file"]).read_text(encoding="utf-8")
        planted = [v for values in truth["pii"].values() for v in values] + truth["secrets"]
        for value in planted:
            assert value in text, f"{value!r} missing from {truth['file']}"


def test_policies_cover_both_languages_and_a_mix_of_compliance(tmp_path: Path) -> None:
    truths = _policies(tmp_path)
    assert {t["language"] for t in truths} == {"en", "fr"}
    flags = [t["compliant"] for t in truths if t["compliant"] is not None]
    assert True in flags and False in flags


def test_no_placeholder_left_unfilled(tmp_path: Path) -> None:
    for truth in _policies(tmp_path):
        text = (tmp_path / "p" / truth["file"]).read_text(encoding="utf-8")
        assert "{" not in text and "}" not in text


def test_logs_are_valid_jsonl_and_late_flags_follow_the_rule(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    n_lines = generate_logs(log, tmp_path / "inc.json", n_incidents=40, mean_lines=20, seed=3)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_lines
    events = [json.loads(line) for line in lines]
    assert [e["timestamp"] for e in events] == sorted(e["timestamp"] for e in events)

    truths: list[dict[str, Any]] = _load(tmp_path / "inc.json")
    majors = [t for t in truths if t["classification"] == "major"]
    assert majors, "expected at least one major incident"

    def ts(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    for t in majors:
        late = is_reported_late(ts(t["detected_at"]), ts(t["classified_at"]), ts(t["reported_at"]))
        assert t["reported_late"] == late
    for t in truths:
        if t["classification"] == "non-major":
            assert t["reported_at"] is None and t["reported_late"] is None


def test_reported_at_only_appears_after_the_report(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    generate_logs(log, tmp_path / "inc.json", n_incidents=20, mean_lines=20, seed=5)
    for line in log.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["reported_at"] is not None:
            assert e["timestamp"] >= e["reported_at"]


def test_dora_rule() -> None:
    detected = datetime(2025, 3, 1, 8, 0)
    assert not is_reported_late(detected, datetime(2025, 3, 1, 9, 0), datetime(2025, 3, 1, 12, 0))
    # 5 h after classification: late
    assert is_reported_late(detected, datetime(2025, 3, 1, 9, 0), datetime(2025, 3, 1, 14, 0))
