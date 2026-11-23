"""Distribution statistics for experiment reports (one function for every script), and
token-length statistics of chunks per strategy and per (document type, chunk kind)."""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from reglens.ingestion.chunking import Chunk


def distribution(values: Sequence[float]) -> dict[str, Any]:
    """count, min, median, P95 (nearest rank), max, mean and total of a list of values."""
    s = sorted(values)
    n = len(s)
    return {
        "count": n,
        "min": s[0],
        "median": statistics.median(s),
        "p95": s[min(n - 1, int(0.95 * n))],
        "max": s[-1],
        "mean": round(statistics.fmean(s), 1),
        "total": sum(s),
    }


def length_stats(lengths: list[int], *, max_tokens: int, min_tokens: int) -> dict[str, Any]:
    d = distribution(lengths)
    return {
        "chunks": d["count"],
        "min": d["min"],
        "median": d["median"],
        "p95": d["p95"],
        "max": d["max"],
        "mean": d["mean"],
        f"over_{max_tokens}": sum(x > max_tokens for x in lengths),
        f"under_{min_tokens}": sum(x < min_tokens for x in lengths),
        "tokens_total": d["total"],
    }


def stats_by_group(chunks: Iterable[Chunk], *, max_tokens: int, min_tokens: int) -> dict[str, Any]:
    groups: dict[str, list[int]] = defaultdict(list)
    for c in chunks:
        groups[f"{c.type_doc}/{c.kind}"].append(c.n_tokens)
        groups["all"].append(c.n_tokens)
    return {
        key: length_stats(v, max_tokens=max_tokens, min_tokens=min_tokens)
        for key, v in sorted(groups.items())
    }
