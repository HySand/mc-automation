from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import TypeAlias
from urllib.parse import urlsplit, urlunsplit

LOGGER_NAME = "mc_automation.steps"
REDACTED = "[REDACTED]"

SafeScalar: TypeAlias = str | int | float | bool | None
SafeValue: TypeAlias = SafeScalar | list[SafeScalar] | dict[str, SafeScalar]

# Step metadata is deliberately closed: adding a new field requires deciding that it is safe.
ALLOWED_METADATA_KEYS = frozenset(
    {
        "action",
        "adapter_count",
        "already_clear",
        "attempts",
        "browser_fallback",
        "challenge_count",
        "challenge_kind",
        "cleanup_ok",
        "code_length",
        "confidence",
        "content_length",
        "content_type",
        "control_count",
        "control_kind",
        "cookie_count",
        "cooldown_active",
        "distance",
        "duration_ms",
        "elapsed_ms",
        "enabled_sites",
        "exception_type",
        "exit_code",
        "field_count",
        "field_names",
        "format_valid",
        "form_count",
        "handle_height",
        "handle_width",
        "headless",
        "image_bytes",
        "initial_count",
        "inventory_total",
        "item_types",
        "json_response",
        "link_count",
        "max_attempts",
        "method",
        "model",
        "navigation_timeout_ms",
        "normal_thread_count",
        "owned",
        "path_points",
        "rank",
        "recovered",
        "redirect_count",
        "redirect_target",
        "refreshed_count",
        "remaining_seconds",
        "requires_interaction",
        "resolved",
        "response_count",
        "result_count",
        "result_status",
        "session_synced",
        "state_exists",
        "status_code",
        "submit_method",
        "success_marker",
        "suspended",
        "threshold",
        "track_width",
        "url",
        "visits",
    }
)


def safe_url(value: str) -> str:
    """Retain only the public origin and path of an HTTP(S) URL."""

    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return REDACTED
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return REDACTED
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), host, parsed.path or "/", "", ""))


def _scalar(value: object) -> SafeScalar:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    return type(value).__name__


def _safe_value(key: str, value: object) -> SafeValue:
    if key in {"url", "redirect_target"}:
        return safe_url(str(value))
    if isinstance(value, Mapping):
        return {str(name): _scalar(item) for name, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_scalar(item) for item in value]
    return _scalar(value)


def log_step(
    phase: str,
    *,
    site: str = "system",
    status: str = "info",
    **metadata: object,
) -> None:
    """Emit one stable JSON log record without accepting arbitrary metadata."""

    safe_metadata = {
        key: _safe_value(key, value)
        for key, value in metadata.items()
        if key in ALLOWED_METADATA_KEYS
    }
    rejected = sorted(set(metadata) - ALLOWED_METADATA_KEYS)
    if rejected:
        safe_metadata["rejected_fields"] = [REDACTED for _key in rejected]
    record = {
        "event": "step",
        "timestamp": datetime.now(UTC).isoformat(),
        "site": site,
        "phase": phase,
        "status": status,
        "metadata": safe_metadata,
    }
    logging.getLogger(LOGGER_NAME).info(
        json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
