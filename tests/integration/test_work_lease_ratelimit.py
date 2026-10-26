"""Integration tests for per-agent work-lease rate limiting (risk finding 3).

Leasing is the one-contact-at-a-time disclosure vector, so a per-agent velocity cap
is the anti-automation control against scripted complete-then-lease scraping. The cap
is enforced in the service (work_service.lease_next), so both the JSON API and the web
workbench are covered; these tests prove the limiter, the API mapping (429) and the web
mapping (flash).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import ratelimit
from tests.integration.conftest import csrf_headers, login, make_user_with_role

pytestmark = pytest.mark.integration


def test_work_lease_limiter_trips_after_the_per_agent_cap(monkeypatch):
    monkeypatch.setattr(ratelimit, "_WORK_LEASE_LIMIT", 2)
    agent = f"agent-{uuid.uuid4().hex}"
    assert ratelimit.check_and_increment_work_lease(agent) is True
    assert ratelimit.check_and_increment_work_lease(agent) is True
    assert ratelimit.check_and_increment_work_lease(agent) is False  # one past the cap
    # A different agent has its own independent budget.
    assert ratelimit.check_and_increment_work_lease(f"agent-{uuid.uuid4().hex}") is True


def test_api_lease_is_rate_limited(monkeypatch):
    from app.main import app

    monkeypatch.setattr(ratelimit, "check_and_increment_work_lease", lambda agent_id: False)
    email = f"wlrl-agent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = TestClient(app)
    login(client, email)

    resp = client.post("/api/v1/work/next", headers=csrf_headers(client))
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"]["code"] == "rate_limited"


def test_web_lease_is_rate_limited(monkeypatch):
    from app.main import app

    monkeypatch.setattr(ratelimit, "check_and_increment_work_lease", lambda agent_id: False)
    email = f"wlrl-web-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = TestClient(app, follow_redirects=False)
    login(client, email)

    resp = client.post("/agent/work/next", data={"csrf_token": client.cookies.get("cc_csrf")})
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]
