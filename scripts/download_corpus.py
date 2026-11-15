"""Download every 'auto' source of data/sources.yaml into data/raw/<type>/.

Usage (from the repository root, with the virtual environment active):
    python scripts/download_corpus.py                 # download what is missing
    python scripts/download_corpus.py --only dora     # one source
    python scripts/download_corpus.py --force         # download again even if present
    python scripts/download_corpus.py --dry-run       # list targets, download nothing

Exit code 1 if at least one file failed, so a CI job or a script can notice.
"""

import argparse
import logging
import sys
from pathlib import Path

from reglens.config import get_settings
from reglens.corpus.download import download_all
from reglens.corpus.sources import download_targets, load_corpus


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", type=Path, default=settings.sources_file)
    parser.add_argument("--raw-dir", type=Path, default=settings.raw_dir)
    parser.add_argument("--only", nargs="+", metavar="ID", help="source ids to download")
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument("--dry-run", action="store_true", help="list targets only")
    args = parser.parse_args()

    logging.basicConfig(level=settings.log_level, format="%(levelname)-7s %(message)s")

    targets = download_targets(load_corpus(args.sources), args.raw_dir)
    if args.only:
        unknown = set(args.only) - {t.source_id for t in targets}
        if unknown:
            parser.error(f"unknown source id(s): {', '.join(sorted(unknown))}")
        targets = [t for t in targets if t.source_id in args.only]

    if args.dry_run:
        for t in targets:
            print(f"{t.path}  <-  {t.url}")
        return 0

    results = download_all(targets, force=args.force)

    print(f"\n{'status':<11}{'size':>10}  file")
    for r in results:
        size = f"{r.size_bytes / 1e6:.1f} MB" if r.size_bytes else ""
        print(f"{r.status:<11}{size:>10}  {r.target.path}  {r.detail}")
    failed = [r for r in results if r.status == "failed"]
    counts = {s: sum(r.status == s for r in results) for s in ("downloaded", "skipped", "failed")}
    print(
        f"\n{counts['downloaded']} downloaded, {counts['skipped']} skipped, "
        f"{counts['failed']} failed"
    )
    for r in failed:
        print(f"  FAILED {r.target.source_id} ({r.target.url}): {r.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
