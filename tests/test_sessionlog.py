import logging
from datetime import datetime

from pam.sessionlog import close_session_log, open_session_log


def test_creates_dated_log_file(tmp_path):
    logger, path = open_session_log(tmp_path)
    try:
        assert path.parent == tmp_path
        assert path.suffix == ".log"
        logger.info("hello from the test")
    finally:
        close_session_log(logger)
    assert "hello from the test" in path.read_text(encoding="utf-8")


def test_close_releases_handlers(tmp_path):
    logger, _ = open_session_log(tmp_path)
    close_session_log(logger)
    assert logger.handlers == []


def test_two_sessions_two_files(tmp_path):
    logger1, path1 = open_session_log(tmp_path, suffix="a")
    close_session_log(logger1)
    logger2, path2 = open_session_log(tmp_path, suffix="b")
    close_session_log(logger2)
    assert path1 != path2


def test_logger_is_the_pam_logger(tmp_path):
    logger, _ = open_session_log(tmp_path)
    try:
        assert logger is logging.getLogger("pam")
    finally:
        close_session_log(logger)


def test_no_propagation_to_root_logger(tmp_path):
    # Prevents duplicate lines when console logging is added in later milestones.
    logger, _ = open_session_log(tmp_path)
    try:
        assert logger.propagate is False
    finally:
        close_session_log(logger)


def test_same_second_sessions_get_distinct_files(tmp_path, monkeypatch):
    class FrozenDatetime:
        @staticmethod
        def now():
            return datetime(2026, 7, 23, 12, 0, 0)

    monkeypatch.setattr("pam.sessionlog.datetime", FrozenDatetime)
    logger1, path1 = open_session_log(tmp_path)
    close_session_log(logger1)
    logger2, path2 = open_session_log(tmp_path)
    close_session_log(logger2)
    assert path1 != path2
    assert path1.exists() and path2.exists()
