from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import config

LOG_NAME = "photosort"
_APP_LOG = "app.log"
_MAX_BYTES = 2_000_000


def app_log_path() -> Path:
    return Path(config.DATA_DIR) / "logs" / _APP_LOG


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return
    bak = path.with_name(path.name + ".1")
    try:
        if bak.exists():
            bak.unlink()
        path.rename(bak)
    except OSError:
        pass


class _AppFileHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("PHOTOSORT_LOG_FILE"):
            return
        try:
            path = app_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(self.format(record) + "\n")
        except OSError:
            pass


def get_logger() -> logging.Logger:
    log = logging.getLogger(LOG_NAME)
    if getattr(log, "_photosort_ready", False):
        return log
    log.setLevel(logging.INFO)
    log.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_h = _AppFileHandler()
    file_h.setFormatter(fmt)
    log.addHandler(file_h)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        log.addHandler(stream)
    log._photosort_ready = True  # type: ignore[attr-defined]
    return log


def warning(msg: str, *args: Any) -> None:
    get_logger().warning(msg, *args)


def exception(msg: str, *args: Any) -> None:
    get_logger().exception(msg, *args)


def save_failed(action: str, **fields: Any) -> None:
    labels = {"cluster_id": "cluster", "photo_id": "photo"}
    bits = [f"save {action} failed"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        bits.append(f"{labels.get(key, key)}={value}")
    warning(" ".join(bits))
