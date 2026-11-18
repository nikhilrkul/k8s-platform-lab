"""Runtime configuration, read from environment variables.

Kubernetes hands config to the container as env vars (from a ConfigMap) and
secrets as env vars (from a Secret). Everything arrives as a *string*, so this
module parses strictly and fails fast at startup. A pod that crashes on boot
with a clear message is far better than one that starts with
`FEATURE_DISCOUNTS="False"` silently read as truthy.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


class ConfigError(ValueError):
    """Raised when a required setting is missing or malformed."""


def parse_bool(name: str, raw: str | None, default: bool | None = None) -> bool:
    """Parse a boolean env var strictly. Unknown spellings are an error, not False."""
    if raw is None or raw.strip() == "":
        if default is None:
            raise ConfigError(f"{name} is required (expected true/false)")
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name}={raw!r} is not a boolean (expected true/false)")


def parse_int(name: str, raw: str | None, default: int, minimum: int = 0) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        # Catches Helm's classic float rendering such as "1e+06".
        raise ConfigError(f"{name}={raw!r} is not an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name}={value} must be >= {minimum}")
    return value


def require(name: str, raw: str | None) -> str:
    if raw is None or raw.strip() == "" or raw.strip().lower() in {"null", "none", "<nil>"}:
        raise ConfigError(f"{name} is required but was empty or null")
    return raw.strip()


@dataclass(frozen=True)
class Settings:
    inventory_url: str
    api_key: str
    feature_discounts: bool
    discount_percent: int
    request_timeout_ms: int
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        log_level = e.get("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigError(f"LOG_LEVEL={log_level!r} is invalid")
        discounts = parse_bool("FEATURE_DISCOUNTS", e.get("FEATURE_DISCOUNTS"), default=False)
        percent = parse_int("DISCOUNT_PERCENT", e.get("DISCOUNT_PERCENT"), default=0)
        if percent > 100:
            raise ConfigError("DISCOUNT_PERCENT must be <= 100")
        return cls(
            inventory_url=require("INVENTORY_URL", e.get("INVENTORY_URL")).rstrip("/"),
            api_key=require("API_KEY", e.get("API_KEY")),
            feature_discounts=discounts,
            discount_percent=percent,
            request_timeout_ms=parse_int(
                "REQUEST_TIMEOUT_MS", e.get("REQUEST_TIMEOUT_MS"), default=2000, minimum=1
            ),
            log_level=log_level,
        )
