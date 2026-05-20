"""
Config Validator Tests
───────────────────────
Tests the Pydantic field_validators on app/core/config.Settings.
Validators are called directly as classmethods — no Settings
instantiation needed, no .env file side-effects.

Covers:
  - SECRET_KEY  : minimum 32 chars enforced
  - ALGORITHM   : only HS256 / HS384 / HS512 accepted
  - ACCESS_TOKEN_EXPIRE_MINUTES : must be positive
  - ADMIN_PASSWORD : minimum 8 chars
  - Valid inputs pass through unchanged
"""

from __future__ import annotations

import pytest


# ── SECRET_KEY ────────────────────────────────────────────────────────────────

class TestSecretKeyValidator:

    def test_key_shorter_than_32_chars_raises(self):
        from app.core.config import Settings
        with pytest.raises(ValueError, match="32 characters"):
            Settings.validate_secret_key("tooshort")

    def test_exactly_31_chars_raises(self):
        from app.core.config import Settings
        with pytest.raises(ValueError, match="32 characters"):
            Settings.validate_secret_key("a" * 31)

    def test_exactly_32_chars_passes(self):
        from app.core.config import Settings
        key = "a" * 32
        assert Settings.validate_secret_key(key) == key

    def test_long_key_passes(self):
        from app.core.config import Settings
        key = "x" * 64
        assert Settings.validate_secret_key(key) == key

    def test_known_insecure_default_emits_warning(self):
        """The default placeholder key is accepted but triggers a UserWarning."""
        import warnings
        from app.core.config import Settings
        insecure = "change-me-in-production-use-secrets"  # 35 chars — passes length check
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = Settings.validate_secret_key(insecure)
        assert result == insecure
        warning_messages = [str(warning.message) for warning in w]
        assert any("insecure" in msg.lower() or "SECRET_KEY" in msg for msg in warning_messages)


# ── ALGORITHM ─────────────────────────────────────────────────────────────────

class TestAlgorithmValidator:

    @pytest.mark.parametrize("valid_algo", ["HS256", "HS384", "HS512"])
    def test_valid_algorithms_pass(self, valid_algo: str):
        from app.core.config import Settings
        assert Settings.validate_algorithm(valid_algo) == valid_algo

    @pytest.mark.parametrize("bad_algo", ["RS256", "RS512", "ES256", "none", "hs256", ""])
    def test_invalid_algorithms_raise(self, bad_algo: str):
        from app.core.config import Settings
        with pytest.raises(ValueError, match="ALGORITHM must be one of"):
            Settings.validate_algorithm(bad_algo)


# ── ACCESS_TOKEN_EXPIRE_MINUTES ───────────────────────────────────────────────

class TestExpireMinutesValidator:

    @pytest.mark.parametrize("valid_val", [1, 30, 60, 1440])
    def test_positive_values_pass(self, valid_val: int):
        from app.core.config import Settings
        assert Settings.validate_expire_minutes(valid_val) == valid_val

    @pytest.mark.parametrize("bad_val", [0, -1, -100])
    def test_zero_and_negative_raise(self, bad_val: int):
        from app.core.config import Settings
        with pytest.raises(ValueError, match="positive"):
            Settings.validate_expire_minutes(bad_val)


# ── ADMIN_PASSWORD ────────────────────────────────────────────────────────────

class TestAdminPasswordValidator:

    @pytest.mark.parametrize("short", ["", "abc", "1234567"])  # < 8 chars
    def test_passwords_shorter_than_8_raise(self, short: str):
        from app.core.config import Settings
        with pytest.raises(ValueError, match="8 characters"):
            Settings.validate_admin_password(short)

    def test_exactly_8_chars_passes(self):
        from app.core.config import Settings
        pw = "12345678"
        assert Settings.validate_admin_password(pw) == pw

    def test_strong_password_passes(self):
        from app.core.config import Settings
        pw = "Sup3r$ecur3P@ssword!"
        assert Settings.validate_admin_password(pw) == pw


# ── Settings object integrity ─────────────────────────────────────────────────

class TestSettingsObject:
    """Smoke-test that the live settings object loads and has the expected fields."""

    def test_settings_loads(self):
        from app.core.config import settings
        assert settings is not None

    def test_api_v1_prefix_is_set(self):
        from app.core.config import settings
        assert settings.API_V1_STR == "/api/v1"

    def test_algorithm_is_supported(self):
        from app.core.config import settings, _SUPPORTED_ALGORITHMS
        assert settings.ALGORITHM in _SUPPORTED_ALGORITHMS

    def test_expire_minutes_is_positive(self):
        from app.core.config import settings
        assert settings.ACCESS_TOKEN_EXPIRE_MINUTES > 0

    def test_secret_key_length(self):
        from app.core.config import settings
        assert len(settings.SECRET_KEY) >= 32

    def test_cors_origins_is_list(self):
        from app.core.config import settings
        assert isinstance(settings.CORS_ORIGINS, list)
        assert len(settings.CORS_ORIGINS) > 0
