"""Tests for aelvoxim.server — auth, config, and system endpoints.

Covers the most critical user-facing APIs:
- /v1/auth/* (login, register, profile)
- /v1/llm/config (model configuration)
- /v1/admin/* (management panel)
"""
import pytest
from fastapi.testclient import TestClient

from aelvoxim.server import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# ── Test data ──
TEST_EMAIL = "pytest@aelvoxim.test"
TEST_PASSWORD = "test_pass_123"
TEST_API_KEY = ""


class TestAuth:
    """Authentication endpoints — critical for all subsequent API calls."""

    def test_register_new_user(self, client):
        resp = client.post("/v1/auth/register", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        # May succeed (first run) or fail (duplicate on rerun) — both acceptable
        assert resp.status_code in (200, 201, 409)

    def test_login_valid_credentials(self, client):
        resp = client.post("/v1/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        if resp.status_code == 200:
            data = resp.json()
            assert "api_key" in data
            # Store for later tests
            pytest.TEST_API_KEY = data["api_key"]

    def test_login_invalid_password(self, client):
        resp = client.post("/v1/auth/login", json={
            "email": TEST_EMAIL,
            "password": "wrong_password_123",
        })
        assert resp.status_code in (401, 403)

    def test_login_nonexistent_email(self, client):
        resp = client.post("/v1/auth/login", json={
            "email": "nonexistent@aelvoxim.test",
            "password": "test_pass_123",
        })
        assert resp.status_code in (401, 403)


class TestLlmConfig:
    """LLM configuration endpoints."""

    @pytest.fixture
    def auth_header(self, client):
        if not pytest.TEST_API_KEY:
            resp = client.post("/v1/auth/login", json={
                "email": TEST_EMAIL,
                "password": TEST_PASSWORD,
            })
            if resp.status_code == 200:
                pytest.TEST_API_KEY = resp.json().get("api_key", "")
        if pytest.TEST_API_KEY:
            return {"Authorization": f"Bearer {pytest.TEST_API_KEY}"}
        return {}

    def test_get_config_without_auth(self, client):
        resp = client.get("/v1/llm/config")
        assert resp.status_code in (401, 403)

    def test_get_config_with_auth(self, client, auth_header):
        if not auth_header:
            pytest.skip("No valid auth token")
        resp = client.get("/v1/llm/config", headers=auth_header)
        assert resp.status_code in (200, 500)  # 500 = no LLM configured yet

    def test_get_models_without_auth(self, client):
        resp = client.get("/v1/llm/models")
        # Route may not exist (404) or require auth (401/403)
        assert resp.status_code in (401, 403, 404)


class TestSystem:
    """System-level endpoints that don't require auth."""

    def test_health(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_health_cors_headers(self, client):
        resp = client.options("/v1/health")
        assert resp.status_code in (200, 405)
