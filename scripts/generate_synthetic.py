"""Generate the synthetic corpus: policies (Markdown), incident log (JSONL), register (CSV).

Usage (from the repository root, with the virtual environment active):
    python scripts/generate_synthetic.py                    # everything, seed 42
    python scripts/generate_synthetic.py --only policies
    python scripts/generate_synthetic.py --seed 7           # a different (still reproducible) set

Outputs:
    data/raw/policies/POL-XXX_<topic>_<lang>.md        corpus (ingested)
    data/raw/logs/incident_log.jsonl                   corpus (ingested)
    data/raw/register/register_of_information.csv      corpus (ingested)
    data/ground_truth/{policies,incidents,register}.json  answers (never ingested)
"""

import argparse
import sys
from pathlib import Path

from reglens.config import get_settings
from reglens.corpus.synthetic import generate_all


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=settings.raw_dir)
    parser.add_argument("--truth-dir", type=Path, default=settings.ground_truth_dir)
    parser.add_argument("--seed", type=int, default=settings.synthetic_seed)
    parser.add_argument("--only", choices=["policies", "logs", "register"])
    parser.add_argument(
        "--policies",
        type=int,
        default=50,
        help="number of drawn policies (the fixed-variant ones are added)",
    )
    parser.add_argument("--incidents", type=int, default=200, help="number of incidents")
    args = parser.parse_args()

    try:
        report = generate_all(
            args.raw_dir,
            args.truth_dir,
            seed=args.seed,
            n_policies=args.policies,
            n_incidents=args.incidents,
            only=args.only,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if report.n_policies is not None:
        print(f"policies: {report.n_policies} files in {args.raw_dir / 'policies'}")
    if report.n_log_lines is not None:
        print(f"logs:     {report.n_log_lines} lines for {args.incidents} incidents")
    if report.n_providers is not None:
        print(f"register: {report.n_providers} providers in {args.raw_dir / 'register'}")
    print(f"ground truth in {args.truth_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
