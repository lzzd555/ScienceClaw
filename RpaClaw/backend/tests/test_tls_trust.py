from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.tls_trust import configure_tls_trust


@dataclass
class FakeTruststore:
    calls: int = 0
    should_fail: bool = False

    def inject_into_ssl(self) -> None:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("boom")


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def debug(self, message: str, *args: Any) -> None:
        self.messages.append(("debug", message.format(*args)))

    def info(self, message: str, *args: Any) -> None:
        self.messages.append(("info", message.format(*args)))

    def warning(self, message: str, *args: Any) -> None:
        self.messages.append(("warning", message.format(*args)))

    def joined(self) -> str:
        return "\n".join(message for _, message in self.messages)


def _importer(fake_truststore: FakeTruststore):
    def import_module(name: str):
        assert name == "truststore"
        return fake_truststore

    return import_module


def test_false_mode_never_imports_truststore() -> None:
    imported: list[str] = []
    logger = FakeLogger()

    def import_module(name: str):
        imported.append(name)
        raise AssertionError("truststore should not be imported")

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "false"},
        platform="win32",
        import_module=import_module,
        logger=logger,
    )

    assert result == "python-default"
    assert imported == []
    assert "Python default" in logger.joined()


def test_auto_windows_without_explicit_ca_injects_truststore() -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "auto"},
        platform="win32",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "system-truststore"
    assert fake_truststore.calls == 1
    assert "system via truststore" in logger.joined()


@pytest.mark.parametrize("env_key", ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_auto_windows_with_explicit_ca_skips_truststore(env_key: str) -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "auto", env_key: "C:/certs/ca-bundle.crt"},
        platform="win32",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "explicit-ca"
    assert fake_truststore.calls == 0
    assert "explicit CA bundle environment detected" in logger.joined()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_auto_non_windows_skips_truststore(platform: str) -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "auto"},
        platform=platform,
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "python-default"
    assert fake_truststore.calls == 0


def test_true_mode_attempts_injection_even_with_explicit_ca() -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={
            "RPA_USE_SYSTEM_TRUSTSTORE": "true",
            "SSL_CERT_FILE": "C:/certs/ca-bundle.crt",
        },
        platform="linux",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "system-truststore"
    assert fake_truststore.calls == 1
    assert "explicit CA bundle environment detected" in logger.joined()


def test_injection_failure_logs_warning_and_falls_back() -> None:
    fake_truststore = FakeTruststore(should_fail=True)
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "true"},
        platform="win32",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "python-default"
    assert fake_truststore.calls == 1
    assert "truststore injection failed" in logger.joined()
