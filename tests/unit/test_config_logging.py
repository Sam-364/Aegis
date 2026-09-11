from __future__ import annotations

import logging

import pytest

from aegis.config import Settings
from aegis.logging import REDACTED, configure_logging, redact_processor, redact_value


def test_settings_require_key_for_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGIS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AEGIS_LLM_API_KEY_FILE", raising=False)
    with pytest.raises(ValueError, match="requires"):
        Settings(llm_provider="openai", _env_file=None)  # type: ignore[call-arg]


def test_settings_read_key_file(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "key"  # type: ignore[operator]
    p.write_text("sk-test-123\n")
    s = Settings(llm_provider="openai", llm_api_key_file=p, _env_file=None)  # type: ignore[call-arg]
    assert s.llm_api_key is not None
    assert s.llm_api_key.get_secret_value() == "sk-test-123"
    assert "sk-test" not in repr(s)


def test_production_requires_auth() -> None:
    with pytest.raises(ValueError, match="authentication"):
        Settings(
            environment="production",
            llm_provider="scripted",
            api_auth_mode="disabled",
            _env_file=None,
        )  # type: ignore[call-arg]


def test_api_key_principals_parse() -> None:
    s = Settings(llm_provider="scripted", api_keys="k1:admin:root,k2:viewer", _env_file=None)  # type: ignore[call-arg]
    ps = s.api_key_principals
    assert [(p.role.value, p.name) for p in ps] == [("admin", "root"), ("viewer", "viewer")]
    assert "k1" not in repr(ps[0])


def test_redaction() -> None:
    assert redact_value("Authorization: Bearer abc.def-ghi") == f"Authorization: {REDACTED}"
    assert redact_value(
        {"api_key": "x", "nested": {"password": "y", "ok": "sk-abcdefghijklmnop"}}
    ) == {"api_key": REDACTED, "nested": {"password": REDACTED, "ok": REDACTED}}
    out = redact_processor(
        logging.getLogger(), "info", {"event": "x", "token": "t", "msg": "sk-abcdefghijkl"}
    )
    assert out["token"] == REDACTED and out["msg"] == REDACTED


def test_configure_logging_does_not_raise() -> None:
    configure_logging("DEBUG", "console")
    configure_logging("INFO", "json")


def test_connection_string_passwords_are_redacted() -> None:
    """A DSN reaches the logs through exception messages, not through a field named 'password'."""
    dsn = "postgresql+asyncpg://aegis:sup3r-s3cret@db.internal:5432/aegis"
    out = redact_value(f"could not connect to {dsn}: timeout")
    assert "sup3r-s3cret" not in out
    assert "postgresql+asyncpg://aegis:[REDACTED]@db.internal:5432/aegis" in out
    assert redact_value("rediss://:tok3n-here@cache:6379/0").count("tok3n-here") == 0
    nested = redact_value({"error": {"dsn": dsn, "note": "retrying"}})
    assert "sup3r-s3cret" not in str(nested) and nested["error"]["note"] == "retrying"
    # a URL without credentials is untouched
    assert redact_value("http://api:8600/ready") == "http://api:8600/ready"
    assert redact_value("redis://redis:6379/0") == "redis://redis:6379/0"
