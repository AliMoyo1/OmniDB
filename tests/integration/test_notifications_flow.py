"""Integration tests for the notification inbox (real Postgres + Redis).

Covers the inbox in its own right (privacy, read/mark, unread count) and the
first real producer: workforce-import decisions/commits notifying the uploader.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.flags import service as flags_service
from app.notifications import service as notifications_service
from tests.integration.conftest import csrf_headers, login, make_user, make_user_with_role

pytestmark = pytest.mark.integration


def _enable_workforce_import() -> None:
    flag_toggler_id = make_user(f"notif-flag-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db:
        flags_service.set_flag(
            db, "workforce_import_enabled", True, actor_id=flag_toggler_id,
            reason_code="test_setup",
        )
        db.commit()


@pytest.fixture(autouse=True)
def _flag_on():
    _enable_workforce_import()


def _client_for(email: str) -> TestClient:
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    login(client, email)
    return client


def _manager(prefix: str = "notifmgr") -> tuple[TestClient, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user_with_role(email, "manager")
    return _client_for(email), str(user_id)


def _upload_users_create(client: TestClient, headers: dict) -> str:
    wid = f"notif-{uuid.uuid4().hex[:8]}"
    csv_text = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    files = {"file": ("f.csv", csv_text.encode("utf-8"), "text/csv")}
    resp = client.post(
        "/api/v1/workforce/imports", files=files, data={"import_type": "users"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_notifications_are_private_to_their_recipient():
    """A notification is private: another user cannot see it or mark it read,
    and probing another's notification id is indistinguishable from a missing
    one (404 either way)."""
    owner, owner_id = _manager(prefix="notifowner")
    other, _ = _manager(prefix="notifother")

    with SessionLocal() as db:
        n = notifications_service.notify(
            db, recipient_id=uuid.UUID(owner_id), category="test",
            title="A private message", body="only for the owner",
        )
        db.commit()
        notification_id = str(n.id)

    # The other user sees nothing and cannot mark it read.
    assert other.get("/api/v1/notifications").json() == []
    other_headers = csrf_headers(other)
    denied = other.post(f"/api/v1/notifications/{notification_id}/read", headers=other_headers)
    assert denied.status_code == 404, denied.text

    # The owner sees it, and its unread count is 1.
    owner_list = owner.get("/api/v1/notifications").json()
    assert [n["id"] for n in owner_list] == [notification_id]
    assert owner.get("/api/v1/notifications/unread-count").json()["unread"] == 1

    # The owner marks it read; the count drops.
    owner_headers = csrf_headers(owner)
    ok = owner.post(f"/api/v1/notifications/{notification_id}/read", headers=owner_headers)
    assert ok.status_code == 200, ok.text
    assert owner.get("/api/v1/notifications/unread-count").json()["unread"] == 0
    assert owner.get("/api/v1/notifications?unread_only=true").json() == []


def test_import_decision_by_another_user_notifies_the_uploader():
    """The first real producer: when someone other than the uploader records a
    decision on an import, the uploader is notified, with a pointer back to the
    job."""
    uploader, _uploader_id = _manager(prefix="notifuploader")
    up_headers = csrf_headers(uploader)
    job_id = _upload_users_create(uploader, up_headers)

    approver, _ = _manager(prefix="notifapprover")
    ap_headers = csrf_headers(approver)
    decision = approver.patch(
        f"/api/v1/workforce/imports/{job_id}/decisions",
        json={"decision": "approve", "decision_tier": "standard"}, headers=ap_headers,
    )
    assert decision.status_code == 200, decision.text

    inbox = uploader.get("/api/v1/notifications").json()
    assert len(inbox) == 1, inbox
    note = inbox[0]
    assert note["category"] == "workforce_import.decision"
    assert "approved" in note["title"]
    assert note["related_entity_type"] == "workforce_import_job"
    assert note["related_entity_id"] == job_id
    assert note["read_at"] is None


def test_self_decision_does_not_notify():
    """No self-notification: if the uploader records their own decision, they
    already know - no inbox noise."""
    uploader, _ = _manager(prefix="notifselfdecide")
    headers = csrf_headers(uploader)
    job_id = _upload_users_create(uploader, headers)

    decision = uploader.patch(
        f"/api/v1/workforce/imports/{job_id}/decisions",
        json={"decision": "approve", "decision_tier": "standard"}, headers=headers,
    )
    assert decision.status_code == 200, decision.text
    assert uploader.get("/api/v1/notifications").json() == []


def test_inbox_page_renders_and_mark_all_read_clears_the_badge():
    """The server-rendered inbox lists the user's notifications, and mark-all-
    read clears the unread badge that the shared shell shows on every page."""
    owner, owner_id = _manager(prefix="notifpage")
    with SessionLocal() as db:
        for i in range(3):
            notifications_service.notify(
                db, recipient_id=uuid.UUID(owner_id), category="test",
                title=f"Message {i}", body="body",
            )
        db.commit()

    page = owner.get("/notifications")
    assert page.status_code == 200
    assert "Message 0" in page.text
    assert "Your inbox." in page.text

    # The dashboard shell shows the unread badge (3) too.
    dash = owner.get("/dashboard")
    assert "Inbox" in dash.text

    # Web form endpoints take the CSRF token as a form field (verify_form_csrf),
    # not the x-csrf-token header the JSON API uses.
    cleared = owner.post(
        "/notifications/read-all", data={"csrf_token": owner.cookies.get("cc_csrf")}
    )
    assert cleared.status_code == 303
    assert owner.get("/api/v1/notifications/unread-count").json()["unread"] == 0


def test_email_channel_is_dormant_by_default(monkeypatch):
    """The email channel ships off (Phase 0 "dormant email capability"): notify()
    creates the in-app row and hands nothing to the sender."""
    from app.notifications import email as email_channel

    sent: list = []
    monkeypatch.setattr(email_channel, "_deliver", lambda to, subject, body: sent.append(to))

    recipient_id = make_user(f"notif-email-off-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db:
        note = notifications_service.notify(
            db, recipient_id=recipient_id, category="test", title="Hi", body="b",
        )
        db.commit()
        assert note.id is not None  # the in-app row is still created
    assert sent == []  # dormant: nothing handed to the sender


def test_email_channel_when_enabled_hands_the_recipient_address_to_the_sender(monkeypatch):
    """When the channel is switched on, notify() resolves the recipient's address
    and hands it, with the title and body, to the single send point a future SMTP
    build replaces."""
    from app.notifications import email as email_channel

    monkeypatch.setattr(email_channel, "_channel_enabled", lambda: True)
    captured: list = []
    monkeypatch.setattr(
        email_channel, "_deliver",
        lambda to, subject, body: captured.append((to, subject, body)),
    )

    email_addr = f"notif-email-on-{uuid.uuid4().hex[:8]}@example.com"
    recipient_id = make_user(email_addr)
    with SessionLocal() as db:
        notifications_service.notify(
            db, recipient_id=recipient_id, category="test",
            title="You have mail", body="body text",
        )
        db.commit()

    assert captured == [(email_addr, "You have mail", "body text")]


def _needs_review(client: TestClient, job_id: str) -> list:
    return [
        n
        for n in client.get("/api/v1/notifications").json()
        if n["category"] == "workforce_import.needs_review" and n["related_entity_id"] == job_id
    ]


def test_high_risk_import_broadcasts_to_qualified_non_uploader_approvers():
    """When a parsed import genuinely needs a second person (it has high-risk
    rows), the people who can actually reach and approve it are pinged - the
    push half of the two-person workflow. The uploader is excluded (separation
    of duties), and someone with no approval capability is never a candidate."""
    from app.authz.capabilities import ROLE_AGENT
    from app.workforce import service as workforce_service

    uploader, uploader_id = _manager(prefix="brdcstuploader")
    up_headers = csrf_headers(uploader)

    # A target holding an org-scoped agent role, so deactivating it is high-risk
    # and approving it needs authority over that org scope.
    twid = f"brdcst-target-{uuid.uuid4().hex[:8]}"
    target_id = make_user(f"{twid}@example.com")
    with SessionLocal() as db:
        workforce_service.assign_role(
            db, target_user_id=target_id, role_code=ROLE_AGENT, scope_type="organization",
            scope_id=None, appointed_by=uuid.UUID(uploader_id), reason_code="test_setup",
        )
        db.commit()

    # A second manager (org-wide authority -> can access + approve), created
    # before the upload so the broadcast can reach them, and a plain agent who
    # holds no approval capability at all.
    approver, _ = _manager(prefix="brdcstapprover")
    agent_email = f"brdcst-agent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(agent_email, "agent")
    agent = _client_for(agent_email)

    deact_csv = f"external_workforce_id,reason_code\r\n{twid},performance_review\r\n"
    files = {"file": ("d.csv", deact_csv.encode("utf-8"), "text/csv")}
    up = uploader.post(
        "/api/v1/workforce/imports", files=files,
        data={"import_type": "explicit_deactivations"}, headers=up_headers,
    )
    assert up.status_code == 200, up.text
    job_id = up.json()["id"]
    assert uploader.get(f"/api/v1/workforce/imports/{job_id}").json()["high_risk_rows"] == 1

    assert len(_needs_review(approver, job_id)) == 1
    assert _needs_review(uploader, job_id) == []  # uploader excluded (two-person rule)
    assert _needs_review(agent, job_id) == []  # agent holds no approval capability


def test_routine_only_import_does_not_broadcast_for_review():
    """A routine-only import (no high-risk rows) the uploader can carry themselves
    needs no second person - so it pings nobody for review. (Here the uploader is
    an org-wide manager and the rows are plain user creations, which name no
    existing target, so the uploader is authorized to approve them alone.)"""
    watcher, _ = _manager(prefix="brdcstwatcher")
    uploader, _ = _manager(prefix="brdcstroutine")
    headers = csrf_headers(uploader)
    job_id = _upload_users_create(uploader, headers)
    assert uploader.get(f"/api/v1/workforce/imports/{job_id}").json()["high_risk_rows"] == 0
    assert _needs_review(watcher, job_id) == []


def _org_with_two_teams() -> tuple[uuid.UUID, uuid.UUID]:
    from app.models.identity import Organization, Team

    with SessionLocal() as db:
        org = Organization(name=f"Rt org {uuid.uuid4().hex[:8]}", status="active")
        db.add(org)
        db.flush()
        a = Team(organization_id=org.id, external_code=f"rta-{uuid.uuid4().hex[:6]}", name="RA")
        b = Team(organization_id=org.id, external_code=f"rtb-{uuid.uuid4().hex[:6]}", name="RB")
        db.add_all([a, b])
        db.commit()
        return a.id, b.id


def test_routine_import_uploader_cannot_self_approve_broadcasts_to_qualified_approver():
    """A routine-only import still needs a second person when the uploader lacks
    authority over its rows - e.g. a team-membership add for a team they do not
    manage. The qualified approver (that team's leader) is pinged; a capable leader
    of an unrelated team is filtered out by the real access check, and the uploader
    is excluded."""
    from app.models.workforce_imports import WorkforceImportJob, WorkforceImportRow
    from app.workforce_imports import service as import_service

    team_a, team_b = _org_with_two_teams()
    leader_a = make_user_with_role(
        f"rtb-la-{uuid.uuid4().hex[:8]}@example.com", "team_leader",
        scope_type="team", scope_id=team_a,
    )
    leader_b = make_user_with_role(
        f"rtb-lb-{uuid.uuid4().hex[:8]}@example.com", "team_leader",
        scope_type="team", scope_id=team_b,
    )
    uploader_id = make_user(f"rtb-up-{uuid.uuid4().hex[:8]}@example.com")
    target_id = make_user(f"rtb-tg-{uuid.uuid4().hex[:8]}@example.com")

    with SessionLocal() as db:
        job = WorkforceImportJob(
            import_type="team_memberships", uploader_id=uploader_id,
            source_filename_display="routine.csv",
            generated_storage_key=f"routine-{uuid.uuid4().hex}", file_hash="cafe",
            state="parsed", total_rows=1, valid_rows=1, warning_rows=0, invalid_rows=0,
            high_risk_rows=0,
            # over_cap forces access to resolve from the row, which backs it.
            authorization_footprint={"over_cap": True, "requirements": []},
        )
        db.add(job)
        db.flush()
        db.add(
            WorkforceImportRow(
                import_job_id=job.id, row_number=1, action="add",
                external_workforce_id="x", normalized_identity=target_id,
                parsed_values={"team_id": str(team_a)}, validation_result="valid",
                risk_level="routine",
            )
        )
        db.flush()
        import_service.notify_pending_approvers(db, job)
        db.commit()
        job_id = job.id

    with SessionLocal() as db:
        def needs_review(user_id: uuid.UUID) -> list:
            return [
                n
                for n in notifications_service.list_for_user(db, user_id)
                if n.category == "workforce_import.needs_review"
                and n.related_entity_id == job_id
            ]

        assert len(needs_review(leader_a)) == 1  # the team's leader can approve it
        assert needs_review(leader_b) == []  # a capable but unauthorized leader is not pinged
        assert needs_review(uploader_id) == []  # the uploader is excluded
