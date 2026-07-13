"""Gateway provider cache must respect credential rotation."""

from app.model_gateway.gateway import Gateway


def test_provider_cache_uses_non_secret_key_fingerprint():
    gateway = Gateway()
    gateway._providers.clear()
    first = gateway.get_provider(
        "openai_compatible",
        base_url="https://example.test/v1",
        api_key="old-secret-key",
        model="model-a",
    )
    same = gateway.get_provider(
        "openai_compatible",
        base_url="https://example.test/v1",
        api_key="old-secret-key",
        model="model-a",
    )
    rotated = gateway.get_provider(
        "openai_compatible",
        base_url="https://example.test/v1",
        api_key="new-secret-key",
        model="model-a",
    )

    assert same is first
    assert rotated is not first
    assert rotated.api_key == "new-secret-key"
    assert all("old-secret-key" not in key for key in gateway._providers)
    assert all("new-secret-key" not in key for key in gateway._providers)
    gateway._providers.clear()
