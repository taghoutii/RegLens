"""Writing experiment results: one JSON per run, one row per run in EXPERIMENTS.md.

A result file is evidence, so it is never overwritten: a second run of the same
experiment on the same day gets `_2`, then `_3`, ...
"""

import json
from pathlib import Path
from typing import Any


def new_result_path(results_dir: Path, day: str, name: str) -> Path:
    """`<day>_<name>.json`, or `<day>_<name>_<n>.json` if that file already exists."""
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{day}_{name}.json"
    n = 2
    while path.exists():
        path = results_dir / f"{day}_{name}_{n}.json"
        n += 1
    return path


def write_result(results_dir: Path, result: dict[str, Any]) -> Path:
    """Write `result` (which has "date" and "name") to a new file; return its path."""
    path = new_result_path(results_dir, result["date"], result["name"])
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def append_row(results_dir: Path, row: str) -> None:
    """Add one table row (a full Markdown line) to results/EXPERIMENTS.md."""
    with (results_dir / "EXPERIMENTS.md").open("a", encoding="utf-8") as fh:
        fh.write(row if row.endswith("\n") else row + "\n")
