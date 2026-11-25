"""Writing experiment results: one JSON file per run.

A result file is evidence, so it is never overwritten: a second run of the same
experiment on the same day gets `_2`, then `_3`, ...
"""

import json
from pathlib import Path
from typing import Any


def new_result_path(results_dir: Path, day: str, name: str) -> Path:
    """`<day>_<name>.json`, or `<day>_<name>_<n>.json` if that name is already used."""
    results_dir.mkdir(parents=True, exist_ok=True)
    file, n = f"{day}_{name}.json", 2
    while (results_dir / file).exists():
        file = f"{day}_{name}_{n}.json"
        n += 1
    return results_dir / file


def write_result(results_dir: Path, result: dict[str, Any]) -> Path:
    """Write `result` (which has "date" and "name") to a new file; return its path."""
    path = new_result_path(results_dir, result["date"], result["name"])
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
