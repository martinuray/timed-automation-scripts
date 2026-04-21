import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from libs.job_logging import configure_job_logging
from libs.scholar_metrics import ScholarMetricsBot


def main() -> None:
    log_path = configure_job_logging("scholar_metric_bot")
    logging.info("Writing combined CLI log to %s", log_path)
    ScholarMetricsBot().run()


if __name__ == "__main__":
    main()