"""Tests for aelvoxim.server.routes_chat — chat endpoints."""

import pytest
from fastapi.testclient import TestClient

from aelvoxim.server import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestChatEndpoints:
    def test_llm_chat_missing_auth(self, client):
        """Auth-protected endpoints should reject without API key."""
        resp = client.post("/v1/llm/chat", json={"messages": []})
        assert resp.status_code in (401, 403)

    def test_llm_chat_stream_missing_auth(self, client):
        resp = client.post("/v1/llm/chat/stream", json={"messages": []})
        assert resp.status_code in (401, 403)

    def test_orchestrate_no_auth(self, client):
        resp = client.post("/v1/orchestrate", json={"query": "hello"})
        assert resp.status_code in (200, 401, 403)

    def test_sessions_endpoint(self, client):
        resp = client.get("/v1/chat/sessions")
        assert resp.status_code in (401, 403)

    @pytest.mark.skip(reason="requires valid auth token")
    def test_llm_test_endpoint(self, client):
        pass
