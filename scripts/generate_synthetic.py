"""Generate the synthetic corpus: internal policies (Markdown) and the ICT incident log (JSONL).

Usage (from the repository root, with the virtual environment active):
    python scripts/generate_synthetic.py                    # policies + logs, seed 42
    python scripts/generate_synthetic.py --only policies
    python scripts/generate_synthetic.py --seed 7           # a different (still reproducible) set

Outputs:
    data/raw/policies/POL-XXX_<topic>_<lang>.md
    data/raw/logs/incident_log.jsonl
    data/raw/ground_truth/{policies,incidents}.json   planted PII, secrets, compliance, delays
"""

import argparse
import sys
from pathlib import Path

from reglens.config import get_settings
from reglens.corpus.synthetic import generate_logs, generate_policies


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=settings.raw_dir)
    parser.add_argument("--seed", type=int, default=settings.synthetic_seed)
    parser.add_argument("--only", choices=["policies", "logs"])
    parser.add_argument("--policies", type=int, default=50, help="number of policies")
    parser.add_argument("--incidents", type=int, default=200, help="number of incidents")
    args = parser.parse_args()

    raw: Path = args.raw_dir
    truth_dir = raw / "ground_truth"

    if args.only in (None, "policies"):
        n = generate_policies(
            raw / "policies", truth_dir / "policies.json", n=args.policies, seed=args.seed
        )
        print(f"policies: {n} files in {raw / 'policies'}")
    if args.only in (None, "logs"):
        lines = generate_logs(
            raw / "logs" / "incident_log.jsonl",
            truth_dir / "incidents.json",
            n_incidents=args.incidents,
            seed=args.seed,
        )
        print(f"logs:     {lines} lines for {args.incidents} incidents in {raw / 'logs'}")
    print(f"ground truth in {truth_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
