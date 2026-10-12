"""Deployment report sanitizer tests."""

from app.report_sanitizer import (
    MAX_DEPTH,
    MAX_DICT_KEYS,
    MAX_LIST_ITEMS,
    MAX_STRING_LENGTH,
    REDACTION_MARKER,
    sanitize_evidence,
    sanitize_text,
)


def test_sanitizes_nested_sensitive_keys() -> None:
    """Sensitive key variants should be redacted recursively."""

    sanitized = sanitize_evidence(
        {
            "metadata": {
                "dbPassword": "synthetic-password",
                "api_key": "synthetic-api-key",
                "headers": {"Authorization": "Bearer synthetic-token"},
            }
        }
    )

    assert sanitized["metadata"]["dbPassword"] == REDACTION_MARKER
    assert sanitized["metadata"]["api_key"] == REDACTION_MARKER
    assert sanitized["metadata"]["headers"]["Authorization"] == REDACTION_MARKER


def test_sanitizes_secret_text_patterns() -> None:
    """Common secret-bearing text patterns should be redacted inside strings."""

    text = (
        "Authorization: Bearer synthetic-token "
        "password=synthetic-password token:synthetic-token api_key=synthetic-key"
    )

    sanitized = sanitize_text(text)

    assert "synthetic-token" not in sanitized
    assert "synthetic-password" not in sanitized
    assert "synthetic-key" not in sanitized
    assert REDACTION_MARKER in sanitized


def test_sanitizes_credential_bearing_urls() -> None:
    """Credential-bearing connection URLs should redact username and password."""

    sanitized = sanitize_text("postgresql://demo-user:demo-password@localhost:5432/infrawatch")

    assert "demo-user" not in sanitized
    assert "demo-password" not in sanitized
    assert sanitized == f"postgresql://{REDACTION_MARKER}:{REDACTION_MARKER}@localhost:5432/infrawatch"


def test_sanitizes_private_key_pem_blocks() -> None:
    """Private key PEM blocks should be replaced entirely."""

    sanitized = sanitize_text(
        "before -----BEGIN PRIVATE KEY-----\nsynthetic-key-material\n-----END PRIVATE KEY----- after"
    )

    assert "synthetic-key-material" not in sanitized
    assert sanitized == f"before {REDACTION_MARKER} after"


def test_preserves_ordinary_non_sensitive_content() -> None:
    """Normal observability evidence should remain useful."""

    evidence = {
        "namespace": "infrawatch",
        "ready_replicas": 2,
        "metrics_source": "prometheus",
        "logs": ["started", "healthy"],
    }

    assert sanitize_evidence(evidence) == evidence


def test_bounds_excessive_nesting() -> None:
    """Excessively deep structures should fail closed."""

    value = "leaf"
    for _ in range(MAX_DEPTH + 2):
        value = {"nested": value}

    sanitized = sanitize_evidence(value)

    cursor = sanitized
    for _ in range(MAX_DEPTH + 1):
        cursor = cursor["nested"]
    assert cursor == "[MAX_DEPTH_EXCEEDED]"


def test_bounds_collection_sizes_and_string_lengths() -> None:
    """Large collections and strings should be bounded."""

    evidence = {f"key-{index}": index for index in range(MAX_DICT_KEYS + 5)}
    evidence["items"] = list(range(MAX_LIST_ITEMS + 5))
    evidence["long_string"] = "x" * (MAX_STRING_LENGTH + 20)

    sanitized = sanitize_evidence(evidence)

    assert sanitized["__truncated__"] is True
    bounded = sanitize_evidence(
        {
            "items": list(range(MAX_LIST_ITEMS + 5)),
            "long_string": "x" * (MAX_STRING_LENGTH + 20),
        }
    )
    assert len(bounded["items"]) == MAX_LIST_ITEMS
    assert len(bounded["long_string"]) == MAX_STRING_LENGTH


def test_unsupported_structures_fail_closed() -> None:
    """Unsupported values should not be persisted through repr leakage."""

    class Unsupported:
        pass

    sanitized = sanitize_evidence({"value": Unsupported()})

    assert sanitized["value"] == "[UNSUPPORTED]"
