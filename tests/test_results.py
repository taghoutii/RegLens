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


def test_rows_are_appended(tmp_path: Path) -> None:
    append_row(tmp_path, "| a |")
    append_row(tmp_path, "| b |\n")
    assert (tmp_path / "EXPERIMENTS.md").read_text(encoding="utf-8") == "| a |\n| b |\n"
