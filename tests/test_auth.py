"""Tests for aelvoxim.server.auth — authentication, password, API key management."""

import pytest


class TestPassword:
    def test_hash_and_verify(self):
        from aelvoxim.server.auth import hash_password, verify_password
        pw = "my_secret_password_123"
        hashed = hash_password(pw)
        assert hashed != pw
        assert verify_password(pw, hashed) is True

    def test_wrong_password(self):
        from aelvoxim.server.auth import hash_password, verify_password
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_empty_password(self):
        from aelvoxim.server.auth import hash_password, verify_password
        hashed = hash_password("")
        assert verify_password("", hashed) is True


class TestApiKey:
    def test_generate_key(self):
        from aelvoxim.server.auth import generate_api_key
        key = generate_api_key()
        assert len(key) > 20
        assert key.startswith("sk-")

    def test_generate_unique(self):
        from aelvoxim.server.auth import generate_api_key
        keys = {generate_api_key() for _ in range(10)}
        assert len(keys) == 10
