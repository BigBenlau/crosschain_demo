"""共享的後端文件 logger 建立工具。"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def build_backend_file_logger(name: str, filename: str = "indexer.log") -> logging.Logger:
    """建立寫入 `src/backend/logs/<filename>` 的 logger。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / filename

    handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
