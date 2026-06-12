import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from libs.job_logging import configure_job_logging
from libs.scholar_metrics import ScholarMetricsBot


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the scholar metrics bot.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--force",
        action="store_true",
        help="Ignore cache age and fetch fresh metrics for all scholars now.",
    )
    mode_group.add_argument(
        "--force-cache",
        action="store_true",
        help="Use only cached metrics (no Google Scholar queries), even if cache age is expired.",
    )
    parser.add_argument(
        "--force-post",
        action="store_true",
        help="Always send the webhook message, even when data was read from cache and unchanged.",
    )
    args = parser.parse_args()

    log_path = configure_job_logging("scholar_metric_bot")
    logging.info("Writing combined CLI log to %s", log_path)
    ScholarMetricsBot(
        force_refresh=args.force,
        force_cache=args.force_cache,
        force_post=args.force_post,
    ).run()


if __name__ == "__main__":
    main()