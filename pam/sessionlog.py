"""Per-session dated log files under the workspace logs directory."""

import logging
from datetime import datetime
from pathlib import Path


def open_session_log(logs_dir: Path, suffix: str = "") -> tuple[logging.Logger, Path]:
    """Open the per-session log file and return (logger, path).

    The logger is the shared 'pam' logger; modules log via logging.getLogger('pam').
    Two sessions in the same second get distinct files via a numeric suffix.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = f"{stamp}{'_' + suffix if suffix else ''}"
    path = logs_dir / f"{base}.log"
    counter = 2
    while path.exists():
        path = logs_dir / f"{base}_{counter}.log"
        counter += 1
    logger = logging.getLogger("pam")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # keep root logger quiet; avoids future duplicate output
    logger.handlers.clear()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger, path


def close_session_log(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
