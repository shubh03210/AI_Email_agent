"""
Security Unit Tests
────────────────────
Pure unit tests for app/core/security.py — no I/O, no DB, no HTTP.

Covers:
  - bcrypt password hashing / verification
  - JWT creation with role claim
  - JWT decoding and payload inspection
  - Expired token detection
  - Tampered-signature detection
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from jose import JWTError


# ── Password hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:

    def test_hash_returns_non_empty_string(self):
        from app.core.security import hash_password
        hashed = hash_password("mypassword123")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_is_not_plaintext(self):
        from app.core.security import hash_password
        plain = "mypassword123"
        assert hash_password(plain) != plain

    def test_two_hashes_of_same_password_differ(self):
        """bcrypt uses a random salt — hashes must not be identical."""
        from app.core.security import hash_password
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2

    def test_verify_correct_password_returns_true(self):
        from app.core.security import hash_password, verify_password
        plain = "correct-horse-battery-staple"
        hashed = hash_password(plain)
        assert verify_password(plain, hashed) is True

    def test_verify_wrong_password_returns_false(self):
        from app.core.security import hash_password, verify_password
        hashed = hash_password("realpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_verify_empty_password_returns_false(self):
        from app.core.security import hash_password, verify_password
        hashed = hash_password("notempty")
        assert verify_password("", hashed) is False

    def test_verify_with_tampered_hash_returns_false(self):
        from app.core.security import verify_password
        assert verify_password("password", "notabcrypthash") is False


# ── JWT creation ──────────────────────────────────────────────────────────────

class TestJWTCreation:

    def test_create_token_returns_non_empty_string(self):
        from app.core.security import create_access_token
        token = create_access_token(subject="alice", role="admin")
        assert isinstance(token, str)
        assert len(token) > 20

    def test_create_token_different_each_time(self):
        """exp timestamps will differ if called in different seconds."""
        from app.core.security import create_access_token
        t1 = create_access_token(subject="alice", role="admin")
        t2 = create_access_token(subject="alice", role="admin", expires_delta=timedelta(minutes=120))
        assert t1 != t2

    def test_decoded_sub_matches_subject(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(subject="bob", role="operator")
        payload = decode_access_token(token)
        assert payload["sub"] == "bob"

    def test_decoded_role_matches_role(self):
        from app.core.security import create_access_token, decode_access_token
        for role in ("admin", "operator"):
            token = create_access_token(subject="user", role=role)
            payload = decode_access_token(token)
            assert payload["role"] == role

    def test_decoded_payload_has_exp(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(subject="user", role="admin")
        payload = decode_access_token(token)
        assert "exp" in payload
        assert isinstance(payload["exp"], int)

    def test_custom_expiry_is_respected(self):
        """A token with a longer expiry should have a larger exp claim."""
        from app.core.security import create_access_token, decode_access_token
        short = create_access_token(subject="u", role="admin", expires_delta=timedelta(minutes=1))
        long_ = create_access_token(subject="u", role="admin", expires_delta=timedelta(minutes=120))
        assert decode_access_token(long_)["exp"] > decode_access_token(short)["exp"]


# ── JWT verification ──────────────────────────────────────────────────────────

class TestJWTVerification:

    def test_valid_token_decodes_without_error(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(subject="carol", role="admin")
        payload = decode_access_token(token)
        assert payload["sub"] == "carol"

    def test_expired_token_raises_jwt_error(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(
            subject="ghost",
            role="admin",
            expires_delta=timedelta(seconds=-1),  # already expired
        )
        with pytest.raises(JWTError):
            decode_access_token(token)

    def test_tampered_signature_raises_jwt_error(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(subject="dave", role="admin")
        # Corrupt the signature (last segment)
        parts = token.split(".")
        parts[-1] = parts[-1][:-4] + "XXXX"
        bad_token = ".".join(parts)
        with pytest.raises(JWTError):
            decode_access_token(bad_token)

    def test_random_string_raises_jwt_error(self):
        from app.core.security import decode_access_token
        with pytest.raises(JWTError):
            decode_access_token("not.a.jwt")

    def test_empty_token_raises_jwt_error(self):
        from app.core.security import decode_access_token
        with pytest.raises(JWTError):
            decode_access_token("")

    def test_token_signed_with_different_key_raises(self):
        """Token signed with a different secret must be rejected."""
        from jose import jwt
        from app.core.security import decode_access_token
        from datetime import datetime
        fake_token = jwt.encode(
            {"sub": "eve", "role": "admin", "exp": datetime.utcnow() + timedelta(minutes=30)},
            "a-completely-different-secret-key-xyz",
            algorithm="HS256",
        )
        with pytest.raises(JWTError):
            decode_access_token(fake_token)
