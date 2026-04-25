from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined


_TEMPLATE_ENV = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)
_SENSITIVE_KEY_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "AUTH", "COOKIE", "HEADER")
_SENSITIVE_SHELL_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<lead>^|[\s;|&()])(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_-]*)(?P<sep>=)(?P<value>\"(?:[^\"\\]|\\.)*\"|'[^']*'|`[^`]*`|[^\s;|&()]+)"
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def render_template(template_text: str, context: dict[str, Any]) -> str:
    if not isinstance(template_text, str):
        # If template_text is not a string (e.g. a dict), return its JSON representation
        # or string representation instead of letting Jinja2 crash with TypeError.
        try:
            return json.dumps(template_text, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(template_text)

    template = _TEMPLATE_ENV.from_string(template_text)
    return template.render(**context)


def path_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def looks_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return any(part in upper for part in _SENSITIVE_KEY_PARTS)


def _redacted_shell_assignment_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        quote = value[0]
        return f"{quote}<redacted>{quote}"
    return "<redacted>"


def redact_sensitive_shell_text(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group("key")
        if not looks_sensitive_key(key):
            return match.group(0)
        lead = match.group("lead")
        export = match.group("export") or ""
        sep = match.group("sep")
        value = match.group("value")
        return f"{lead}{export}{key}{sep}{_redacted_shell_assignment_value(value)}"

    return _SENSITIVE_SHELL_ASSIGNMENT_PATTERN.sub(_replace, text)


def redact_sensitive_shell_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_shell_text(value)
    if isinstance(value, list):
        return [redact_sensitive_shell_value(item) for item in value]
    return value
