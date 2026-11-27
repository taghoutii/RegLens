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


def reference_counts(chunks: Iterable[Chunk]) -> dict[str, dict[str, int]]:
    """Per cited act: chunks citing it, split into chunks of that act itself (its own
    articles) and chunks of other acts; and the total number of references (one chunk can
    cite several articles of the same act). Both languages are counted."""
    out: dict[str, dict[str, int]] = defaultdict(
        lambda: {"chunks": 0, "own_act_chunks": 0, "other_act_chunks": 0, "references": 0}
    )
    for c in chunks:
        own = c.reglement.replace(" ", "") if c.reglement else None  # as references.ref_key
        cited: dict[str, int] = defaultdict(int)
        for ref in c.references:
            cited[ref.split(":", 1)[0]] += 1
        for act, n in cited.items():
            row = out[act]
            row["chunks"] += 1
            row["own_act_chunks" if act == own else "other_act_chunks"] += 1
            row["references"] += n
    return dict(sorted(out.items()))
