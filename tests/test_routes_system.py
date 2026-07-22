"""Tests for aelvoxim.server.routes_system — admin/system endpoints.

Covers the admin panel, dashboard, and system configuration APIs.
"""
import pytest
from fastapi.testclient import TestClient

from aelvoxim.server import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestAdminPanel:
    """Admin panel endpoints must not 500."""

    def test_admin_panel_returns_html(self, client):
        resp = client.get("/v1/admin/panel")
        # Should redirect to login or return HTML
        assert resp.status_code in (200, 302, 307)

    def test_admin_data_without_auth(self, client):
        """Admin data endpoint should reject missing auth."""
        resp = client.get("/v1/admin/data")
        assert resp.status_code in (401, 403)

    def test_admin_overview_without_auth(self, client):
        resp = client.get("/v1/admin/overview")
        assert resp.status_code in (401, 403)

    def test_admin_users_without_auth(self, client):
        resp = client.get("/v1/admin/users")
        assert resp.status_code in (401, 403)


class TestHealthEndpoint:
    """Health endpoint is critical for monitoring."""

    def test_health_returns_ok(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_health_includes_service_name(self, client):
        resp = client.get("/v1/health")
        data = resp.json()
        assert "service" in data


class TestLogs:
    """Log viewer endpoints."""

    def test_logs_without_auth(self, client):
        resp = client.get("/v1/logs")
        assert resp.status_code in (401, 403)

    def test_logs_with_bad_token(self, client):
        resp = client.get("/v1/logs", headers={"Authorization": "Bearer bad_token"})
        assert resp.status_code in (401, 403)
