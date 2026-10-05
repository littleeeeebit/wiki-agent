"""Bounded local error records shared by HTTP and background workflows."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import traceback

FILE = Path(__file__).resolve().parents[2] / "raw" / "errors.jsonl"
_lock = threading.Lock()
_logger = None
SECRET_KEY = r"(?:access[_-]?token|refresh[_-]?token|api[_-]?key|password|secret)"


class FileHandler(RotatingFileHandler):
    def handleError(self, record):
        # The default handler swallows disk failures and prints unredacted
        # source lines. Let record() emit the already redacted fallback.
        raise


def redact(text: str) -> str:
    for name, value in os.environ.items():
        if len(value) >= 8 and any(key in name.upper() for key in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(Bearer\s+)[\w.\-]+", r"\1[redacted]", text)
    text = re.sub(r"\b(?:sk-[\w-]+|gh[pousr]_[\w]+|github_pat_[\w]+)\b", "[redacted]", text)
    text = re.sub(rf'''(?i)({SECRET_KEY}["\s]*[:=]\s*)(["'])(?:\\(?:[\s\S]|$)|(?!\2)[^\\])*(?:\2|$)''',
                  r"\1\2[redacted]\2", text)
    return re.sub(rf'(?i)({SECRET_KEY}[\"\s]*[:=]\s*[\"\']?)[^\s\"\',}}]+',
                  r"\1[redacted]", text)


def safe(value):
    """Redact decoded values, never the serialized JSON syntax."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact(key) if isinstance(key, str) else key:
                "[redacted]" if re.fullmatch(SECRET_KEY, str(key), re.I) else safe(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value if value is None or isinstance(value, (bool, int, float)) else redact(str(value))


def record(source: str, error, **context) -> None:
    """No request bodies, prompts, headers or frame locals; logging fails open."""
    global _logger
    message = ""
    try:
        row = {"source": source, "error": redact(str(error))[:8000], **context}
        if isinstance(error, BaseException):
            row["type"] = type(error).__name__
            row["traceback"] = redact("".join(traceback.format_exception(error)))[-16000:]
        from datetime import datetime, timezone
        row["ts"] = datetime.now(timezone.utc).isoformat()
        message = json.dumps(safe(row), ensure_ascii=False)
        with _lock:
            if _logger is None or Path(_logger.handlers[0].baseFilename) != FILE.resolve():
                if _logger:
                    for handler in _logger.handlers:
                        handler.close()
                _logger = None
                FILE.parent.mkdir(parents=True, exist_ok=True)
                handler = FileHandler(FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter('%(message)s'))
                _logger = logging.Logger("wiki-agent.errors", level=logging.ERROR)
                _logger.addHandler(handler)
            _logger.error(message)
    except Exception:
        try:
            sys.stderr.write((message or '{"source":"errorlog","error":"Error record serialization failed"}') + "\n")
            sys.stderr.flush()
        except Exception:
            pass  # Both sinks may be unavailable; preserve the original failure.
