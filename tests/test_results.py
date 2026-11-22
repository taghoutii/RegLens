"""Result files are evidence: a second run never overwrites the first."""

from pathlib import Path

from reglens.evaluation.results import append_row, write_result


def test_same_day_runs_get_new_files(tmp_path: Path) -> None:
    first = write_result(tmp_path, {"date": "2026-01-01", "name": "x", "v": 1})
    second = write_result(tmp_path, {"date": "2026-01-01", "name": "x", "v": 2})
    third = write_result(tmp_path, {"date": "2026-01-01", "name": "x", "v": 3})
    assert [first.name, second.name, third.name] == [
        "2026-01-01_x.json", "2026-01-01_x_2.json", "2026-01-01_x_3.json",
    ]  # fmt: skip
    assert '"v": 1' in first.read_text(encoding="utf-8")


def test_archived_names_are_not_reused(tmp_path: Path) -> None:
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "2026-01-01_x.json").write_text("{}", encoding="utf-8")
    assert write_result(tmp_path, {"date": "2026-01-01", "name": "x"}).name == "2026-01-01_x_2.json"


def test_rows_go_to_one_unfiled_section_at_the_end(tmp_path: Path) -> None:
    (tmp_path / "EXPERIMENTS.md").write_text("# Experiments\n\n## Phase 2\n", encoding="utf-8")
    append_row(tmp_path, "| a |")
    append_row(tmp_path, "| b |\n")
    text = (tmp_path / "EXPERIMENTS.md").read_text(encoding="utf-8")
    assert text.count("## Unfiled runs") == 1
    assert text.index("## Phase 2") < text.index("## Unfiled runs") < text.index("| a |")
    assert text.endswith("| a |\n| b |\n")
