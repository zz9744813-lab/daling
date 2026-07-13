"""Provider health must represent a real check, not mere configuration."""

from __future__ import annotations

from app.api import routes_provider
from app.db.models.provider import LlmProvider


def _provider(**overrides):
    values = {
        "name": "环境变量托管 Provider",
        "provider_type": "openai_compatible",
        "base_url": "https://example.invalid/v1/",
        "api_key_enc": None,
        "default_model": "writer-model",
        "is_active": True,
        "config": {"models": ["writer-model"]},
    }
    values.update(overrides)
    return LlmProvider(**values)


def test_keyless_saved_provider_only_inherits_matching_environment_secret(monkeypatch):
    monkeypatch.setattr(
        routes_provider.gateway,
        "get_default_config",
        lambda: {
            "provider_type": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "api_key": "env-secret",
            "model": "env-model",
        },
    )

    matching = routes_provider._resolved_saved_provider_config(_provider())
    mismatched = routes_provider._resolved_saved_provider_config(
        _provider(base_url="https://another.invalid/v1")
    )

    assert matching["api_key"] == "env-secret"
    assert matching["model"] == "writer-model"
    assert mismatched["api_key"] == ""


def test_provider_is_untested_until_a_real_health_result_exists():
    untested = routes_provider.serialize_provider(_provider())
    healthy = routes_provider.serialize_provider(
        _provider(
            config={
                "models": ["writer-model"],
                "health": {
                    "ok": True,
                    "checked_at": "2026-07-11T12:00:00+00:00",
                    "latency_ms": 321,
                    "model": "writer-model",
                    "error": None,
                },
            }
        )
    )
    failed = routes_provider.serialize_provider(
        _provider(
            config={
                "health": {
                    "ok": False,
                    "checked_at": "2026-07-11T12:01:00+00:00",
                    "latency_ms": 987,
                    "model": "writer-model",
                    "error": "authentication failed",
                }
            }
        )
    )

    assert untested.status == "untested"
    assert healthy.status == "active"
    assert healthy.latency_ms == 321
    assert failed.status == "error"
    assert failed.last_error == "authentication failed"
