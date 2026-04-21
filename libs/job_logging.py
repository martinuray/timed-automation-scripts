from __future__ import annotations

import atexit
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class TeeStream:
    def __init__(self, primary: TextIO, mirror: TextIO) -> None:
        self.primary = primary
        self.mirror = mirror

    def write(self, data: str) -> int:
        self.primary.write(data)
        self.mirror.write(data)
        return len(data)

    def flush(self) -> None:
        self.primary.flush()
        self.mirror.flush()

    def isatty(self) -> bool:
        return self.primary.isatty()

    def fileno(self) -> int:
        return self.primary.fileno()

    @property
    def encoding(self) -> str:
        return self.primary.encoding


def _restore_streams(stdout: TextIO, stderr: TextIO, log_file: TextIO) -> None:
    sys.stdout = stdout
    sys.stderr = stderr
    log_file.flush()
    log_file.close()


def configure_job_logging(job_name: str, repo_root: Path | None = None) -> Path:
    base_dir = repo_root or REPO_ROOT
    log_dir = base_dir / "logs" / job_name
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{timestamp}.log"
    log_file = log_path.open("a", encoding="utf-8", buffering=1)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeStream(original_stdout, log_file)
    sys.stderr = TeeStream(original_stderr, log_file)

    atexit.register(_restore_streams, original_stdout, original_stderr, log_file)

    logger = logging.getLogger()
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(console_handler)

    return log_path
from __future__ import annotations

import atexit
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class TeeStream:
    def __init__(self, primary: TextIO, mirror: TextIO) -> None:
        self.primary = primary
        self.mirror = mirror

    def write(self, data: str) -> int:
        self.primary.write(data)
        self.mirror.write(data)
        return len(data)

    def flush(self) -> None:
        self.primary.flush()
        self.mirror.flush()

    def isatty(self) -> bool:
        return self.primary.isatty()

    def fileno(self) -> int:
        return self.primary.fileno()

    @property
    def encoding(self) -> str:
        return self.primary.encoding


def _restore_stream(name: str, original: TextIO, log_file: TextIO) -> None:
    setattr(sys, name, original)
    log_file.flush()
    log_file.close()


def configure_job_logging(job_name: str, repo_root: Path | None = None) -> Path:
    base_dir = repo_root or REPO_ROOT
    log_dir = base_dir / "logs" / job_name
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{timestamp}.log"
    log_file = log_path.open("a", encoding="utf-8", buffering=1)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeStream(original_stdout, log_file)
    sys.stderr = TeeStream(original_stderr, log_file)

    atexit.register(_restore_stream, "stderr", original_stderr, log_file)
    atexit.register(_restore_stream, "stdout", original_stdout, log_file)

    logger = logging.getLogger()
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(console_handler)

    return log_path

