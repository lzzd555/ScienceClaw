from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Protocol

from loguru import logger as default_logger


CA_BUNDLE_ENV_KEYS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
TRUSTSTORE_ENV_KEY = "RPA_USE_SYSTEM_TRUSTSTORE"
DEFAULT_TRUSTSTORE_MODE = "auto"


class _Logger(Protocol):
    def debug(self, message: str, *args: object) -> None: ...
    def info(self, message: str, *args: object) -> None: ...
    def warning(self, message: str, *args: object) -> None: ...


def _normalize_mode(value: str | None) -> str:
    mode = (value or DEFAULT_TRUSTSTORE_MODE).strip().lower()
    if mode in {"1", "yes", "y", "on"}:
        return "true"
    if mode in {"0", "no", "n", "off"}:
        return "false"
    if mode in {"auto", "true", "false"}:
        return mode
    return DEFAULT_TRUSTSTORE_MODE


def _has_explicit_ca_bundle(env: Mapping[str, str]) -> bool:
    return any((env.get(key) or "").strip() for key in CA_BUNDLE_ENV_KEYS)


def _is_windows(platform: str) -> bool:
    return platform.startswith("win")


def configure_tls_trust(
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    import_module: Callable[[str], ModuleType] = importlib.import_module,
    logger: _Logger = default_logger,
) -> str:
    """Configure process-wide TLS trust behavior for outbound Python HTTPS clients."""

    current_env = env if env is not None else os.environ
    current_platform = platform if platform is not None else sys.platform
    mode = _normalize_mode(current_env.get(TRUSTSTORE_ENV_KEY))
    has_explicit_ca = _has_explicit_ca_bundle(current_env)

    if mode == "false":
        logger.debug("TLS trust store: Python default")
        return "python-default"

    if mode == "auto" and has_explicit_ca:
        logger.info("TLS trust store: explicit CA bundle environment detected; system truststore not injected")
        return "explicit-ca"

    if mode == "auto" and not _is_windows(current_platform):
        logger.debug("TLS trust store: Python default")
        return "python-default"

    if mode == "true" and has_explicit_ca:
        logger.info("TLS trust store: explicit CA bundle environment detected; attempting system truststore injection")

    try:
        truststore = import_module("truststore")
        truststore.inject_into_ssl()
    except Exception as exc:
        logger.warning("TLS trust store: truststore injection failed; falling back to Python default ({})", exc)
        return "python-default"

    logger.info("TLS trust store: system via truststore")
    return "system-truststore"
