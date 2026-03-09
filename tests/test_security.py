"""Tests for sensitive data redaction and security."""

from hermes_migrate.migrate import (
    REDACT_VALUE,
    MigrationLogger,
    is_sensitive_field,
    redact_sensitive_fields,
)


class TestIsSensitiveField:
    def test_obvious_sensitive_fields(self):
        assert is_sensitive_field("token") is True
        assert is_sensitive_field("apiKey") is True
        assert is_sensitive_field("api_key") is True
        assert is_sensitive_field("secret") is True
        assert is_sensitive_field("password") is True
        assert is_sensitive_field("credential") is True
        assert is_sensitive_field("botToken") is True
        assert is_sensitive_field("bot_token") is True
        assert is_sensitive_field("access_token") is True
        assert is_sensitive_field("accessToken") is True
        assert is_sensitive_field("private_key") is True
        assert is_sensitive_field("privateKey") is True

    def test_case_insensitive(self):
        assert is_sensitive_field("TOKEN") is True
        assert is_sensitive_field("ApiKey") is True
        assert is_sensitive_field("PASSWORD") is True

    def test_non_sensitive_fields(self):
        assert is_sensitive_field("name") is False
        assert is_sensitive_field("model") is False
        assert is_sensitive_field("enabled") is False
        assert is_sensitive_field("workspace") is False
        assert is_sensitive_field("id") is False

    def test_allowlisted_fields_not_sensitive(self):
        assert is_sensitive_field("maxTokens") is False
        assert is_sensitive_field("max_tokens") is False
        assert is_sensitive_field("contextTokens") is False
        assert is_sensitive_field("contextWindow") is False
        assert is_sensitive_field("totalTokens") is False

    def test_auth_pattern_matches(self):
        assert is_sensitive_field("auth") is True
        assert is_sensitive_field("authHeader") is True
        assert is_sensitive_field("authorization") is True


class TestRedactSensitiveFields:
    def test_redacts_top_level(self):
        data = {"name": "bot", "token": "secret-value", "model": "gpt-4"}
        result = redact_sensitive_fields(data)
        assert result["name"] == "bot"
        assert result["token"] == REDACT_VALUE
        assert result["model"] == "gpt-4"

    def test_redacts_nested(self):
        data = {
            "channel": {"name": "telegram", "botToken": "123:ABC", "settings": {"apiKey": "sk-123"}}
        }
        result = redact_sensitive_fields(data)
        assert result["channel"]["name"] == "telegram"
        assert result["channel"]["botToken"] == REDACT_VALUE
        assert result["channel"]["settings"]["apiKey"] == REDACT_VALUE

    def test_redacts_in_list_of_dicts(self):
        data = {
            "accounts": [
                {"id": "a1", "accessToken": "tok-123"},
                {"id": "a2", "accessToken": "tok-456"},
            ]
        }
        result = redact_sensitive_fields(data)
        assert result["accounts"][0]["id"] == "a1"
        assert result["accounts"][0]["accessToken"] == REDACT_VALUE
        assert result["accounts"][1]["accessToken"] == REDACT_VALUE

    def test_preserves_non_dict_values_in_lists(self):
        data = {"tags": ["prod", "staging"], "name": "test"}
        result = redact_sensitive_fields(data)
        assert result["tags"] == ["prod", "staging"]

    def test_empty_dict(self):
        assert redact_sensitive_fields({}) == {}

    def test_non_dict_input(self):
        assert redact_sensitive_fields("string") == "string"


class TestMigrationLoggerRedaction:
    def test_redacts_telegram_token(self):
        logger = MigrationLogger(verbose=False)
        result = logger._redact("Found token 1234567890:ABCdef-xyz_123")
        assert "1234567890:ABCdef-xyz_123" not in result
        assert REDACT_VALUE in result

    def test_redacts_openai_key(self):
        logger = MigrationLogger()
        result = logger._redact("Using key sk-abc123def456")
        assert "sk-abc123def456" not in result
        assert REDACT_VALUE in result

    def test_redacts_slack_token(self):
        logger = MigrationLogger()
        result = logger._redact("Slack token xoxb-12345-abcdef")
        assert "xoxb-12345-abcdef" not in result
        assert REDACT_VALUE in result

    def test_redacts_google_api_key(self):
        logger = MigrationLogger()
        result = logger._redact("Google key AIzaSyD-example-key")
        assert "AIzaSyD-example-key" not in result
        assert REDACT_VALUE in result

    def test_preserves_safe_text(self):
        logger = MigrationLogger()
        msg = "Migrated SOUL.md to ~/.hermes/SOUL.md"
        assert logger._redact(msg) == msg
