"""Bounded local error records shared by HTTP and background workflows."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import threading
import traceback

FILE = Path(__file__).resolve().parents[2] / "raw" / "errors.jsonl"
_lock = threading.Lock()
_logger = None


def redact(text: str) -> str:
    for name, value in os.environ.items():
        if len(value) >= 8 and any(key in name.upper() for key in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(Bearer\s+)[\w.\-]+", r"\1[redacted]", text)
    text = re.sub(r"\b(?:sk-[\w-]+|gh[pousr]_[\w]+|github_pat_[\w]+)\b", "[redacted]", text)
    return re.sub(r'(?i)((?:access[_-]?token|refresh[_-]?token|api[_-]?key|password|secret)[\"\s]*[:=]\s*[\"\']?)[^\s\"\',}]+',
                  r"\1[redacted]", text)


def record(source: str, error, **context) -> None:
    """No request bodies, prompts, headers or frame locals; logging fails open."""
    global _logger
    try:
        row = {"source": source, "error": redact(str(error)[:8000]), **context}
        if isinstance(error, BaseException):
            row["type"] = type(error).__name__
            row["traceback"] = redact("".join(traceback.format_exception(error))[-16000:])
        with _lock:
            if _logger is None or Path(_logger.handlers[0].baseFilename) != FILE.resolve():
                if _logger:
                    for handler in _logger.handlers:
                        handler.close()
                FILE.parent.mkdir(parents=True, exist_ok=True)
                _logger = logging.Logger("wiki-agent.errors", level=logging.ERROR)
                handler = RotatingFileHandler(FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter('%(message)s'))
                _logger.addHandler(handler)
            from datetime import datetime, timezone
            row["ts"] = datetime.now(timezone.utc).isoformat()
            _logger.error(redact(json.dumps(row, ensure_ascii=False)))
    except Exception:
        pass
