"""Integration tests for staged bulk-workforce import (real Postgres + Redis).

Celery runs in eager (synchronous) mode for tests (tests/conftest.py), so parsing
completes before the upload endpoint returns, same as tests/integration/test_imports_flow.py.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.flags import service as flags_service
from app.models.identity import User
from tests.integration.conftest import csrf_headers, login, make_user, make_user_with_role

pytestmark = pytest.mark.integration


def _enable_workforce_import() -> None:
    # set_flag writes actor_id into FeatureFlag.updated_by, a real FK to users.id -
    # needs an actual user row, not a bare uuid4() (caught by CI, not locally: no
    # live Postgres this session to enforce the constraint against).
    flag_toggler_id = make_user(f"wfi-flagtoggler-{uuid.uuid4().hex[:8]}@example.com")
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


def _manager(prefix: str = "wfimgr") -> tuple[TestClient, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user_with_role(email, "manager")
    return _client_for(email), str(user_id)


def _team_leader(team_id: uuid.UUID | None, prefix: str = "wfitl") -> tuple[TestClient, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user_with_role(
        email, "team_leader", scope_type="team" if team_id else "organization", scope_id=team_id
    )
    return _client_for(email), str(user_id)


def _upload(client: TestClient, headers: dict, import_type: str, csv_text: str):
    files = {"file": ("f.csv", csv_text.encode("utf-8"), "text/csv")}
    return client.post(
        "/api/v1/workforce/imports", files=files, data={"import_type": import_type},
        headers=headers,
    )


def _decide(
    client: TestClient, headers: dict, job_id: str, *, tier: str, decision: str = "approve"
):
    return client.patch(
        f"/api/v1/workforce/imports/{job_id}/decisions",
        json={"decision": decision, "decision_tier": tier}, headers=headers,
    )


def _commit(
    client: TestClient, headers: dict, job_id: str, decision_version: int, idem: str | None = None
):
    return client.post(
        f"/api/v1/workforce/imports/{job_id}/commit",
        json={"decision_version": decision_version, "idempotency_key": idem or str(uuid.uuid4())},
        headers=headers,
    )


def _user_row(external_id: str) -> User | None:
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.workforce_id == external_id))


def _is_active(external_id: str) -> bool:
    row = _user_row(external_id)
    assert row is not None
    return row.active


def _make_team(prefix: str = "wfiteam") -> tuple[uuid.UUID, str]:
    from app.workforce import service as workforce_service

    creator_id = make_user(f"{prefix}-creator-{uuid.uuid4().hex[:8]}@example.com")
    code = f"{prefix}-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        team = workforce_service.create_team(
            db, name=f"Team {code}", external_code=code, parent_team_id=None,
            default_timezone="Africa/Harare", created_by=creator_id,
        )
        db.commit()
        return team.id, code


def _make_campaign(
    *, owning_scope_type: str = "organization", owning_scope_id: uuid.UUID | None = None,
    prefix: str = "wficampaign",
) -> tuple[uuid.UUID, str]:
    from datetime import date

    from app.campaigns import service as campaign_service

    creator_id = make_user(f"{prefix}-creator-{uuid.uuid4().hex[:8]}@example.com")
    code = f"{prefix}-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        campaign = campaign_service.create_campaign(
            db, created_by=creator_id, external_code=code, name=f"Campaign {code}",
            description=None, owning_scope_type=owning_scope_type,
            owning_scope_id=owning_scope_id, default_region="ZW", timezone="Africa/Harare",
            purpose="Test", data_source="Test", data_obtained_at=date(2026, 1, 1),
            lawful_basis_or_consent_reference="test-consent",
        )
        db.commit()
        return campaign.id, code


def _has_active_role(user_id: uuid.UUID, role_code: str, scope_type: str,
                      scope_id: uuid.UUID | None) -> bool:
    from app.models.authz import RoleAssignment

    with SessionLocal() as db:
        return db.scalar(
            select(RoleAssignment.id).where(
                RoleAssignment.user_id == user_id, RoleAssignment.role_code == role_code,
                RoleAssignment.scope_type == scope_type, RoleAssignment.scope_id == scope_id,
                RoleAssignment.status == "active", RoleAssignment.effective_to.is_(None),
            )
        ) is not None


def _has_active_membership(user_id: uuid.UUID, team_id: uuid.UUID) -> bool:
    from app.models.identity import TeamMembership

    with SessionLocal() as db:
        return db.scalar(
            select(TeamMembership.id).where(
                TeamMembership.team_id == team_id, TeamMembership.user_id == user_id,
                TeamMembership.membership_status == "active",
                TeamMembership.effective_to.is_(None),
            )
        ) is not None


def _current_supervisor(subordinate_id: uuid.UUID) -> uuid.UUID | None:
    from app.models.authz import ReportingAssignment

    with SessionLocal() as db:
        return db.scalar(
            select(ReportingAssignment.supervisor_user_id).where(
                ReportingAssignment.subordinate_user_id == subordinate_id,
                ReportingAssignment.context_type == "organization",
                ReportingAssignment.context_id.is_(None),
                ReportingAssignment.assignment_type == "primary",
                ReportingAssignment.status == "active",
                ReportingAssignment.effective_to.is_(None),
            )
        )


def _current_primary_campaign(user_id: uuid.UUID) -> uuid.UUID | None:
    from app.models.campaign import CampaignUserAssignment

    with SessionLocal() as db:
        return db.scalar(
            select(CampaignUserAssignment.campaign_id).where(
                CampaignUserAssignment.user_id == user_id,
                CampaignUserAssignment.assignment_type == "primary",
                CampaignUserAssignment.status == "active",
                CampaignUserAssignment.effective_to.is_(None),
            )
        )


def test_users_create_update_reactivate_flow_end_to_end():
    client, _ = _manager()
    headers = csrf_headers(client)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    csv_text = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{wid}@example.com,First Name,,\r\n"
    )
    upload = _upload(client, headers, "users", csv_text)
    assert upload.status_code == 200, upload.text
    job_id = upload.json()["id"]

    status = client.get(f"/api/v1/workforce/imports/{job_id}").json()
    assert status["state"] == "parsed", status
    assert status["total_rows"] == 1
    assert status["valid_rows"] == 1
    assert status["high_risk_rows"] == 0

    decide = _decide(client, headers, job_id, tier="standard")
    assert decide.status_code == 200, decide.text
    version = decide.json()["decision_version"]

    commit = _commit(client, headers, job_id, version)
    assert commit.status_code == 200, commit.text
    result = commit.json()
    assert result["outcomes"] == [{"row_number": 1, "outcome": "created"}]
    assert wid in result["activation_tokens"]
    assert len(result["activation_tokens"][wid]) > 10

    created = _user_row(wid)
    assert created is not None
    assert created.display_name == "First Name"
    assert created.active is True

    # A second file updates display_name and reactivates would-be-inactive fields.
    csv_update = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"update,{wid},,Updated Name,2026-01-01,\r\n"
    )
    upload2 = _upload(client, headers, "users", csv_update)
    job2 = upload2.json()["id"]
    version2 = _decide(client, headers, job2, tier="standard").json()["decision_version"]
    commit2 = _commit(client, headers, job2, version2)
    assert commit2.status_code == 200, commit2.text
    assert commit2.json()["outcomes"] == [{"row_number": 1, "outcome": "updated"}]

    updated = _user_row(wid)
    assert updated is not None
    assert updated.display_name == "Updated Name"
    assert updated.start_date is not None
    assert updated.start_date.isoformat() == "2026-01-01"


def test_users_wrong_action_and_duplicate_in_file_are_invalid():
    client, _ = _manager()
    headers = csrf_headers(client)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    csv_text = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"deactivate,{wid},{wid}@example.com,Name,,\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    upload = _upload(client, headers, "users", csv_text)
    job_id = upload.json()["id"]
    status = client.get(f"/api/v1/workforce/imports/{job_id}").json()
    # Row 1 (bad action) never marks wid as seen, so row 2 is a legitimate first
    # create and only row 3 collides with it.
    assert status["invalid_rows"] == 2, status
    assert status["valid_rows"] == 1, status

    preview = client.get(f"/api/v1/workforce/imports/{job_id}/preview").json()
    details = {e["row_number"]: e for e in preview["invalid_examples"]}
    assert "action" in details[1]["detail"]
    assert details[3]["conflict_type"] == "duplicate_in_file"


def test_missing_required_column_fails_parse_cleanly():
    client, _ = _manager()
    headers = csrf_headers(client)
    upload = _upload(client, headers, "users", "not_the_right_header\r\nfoo\r\n")
    job_id = upload.json()["id"]
    status = client.get(f"/api/v1/workforce/imports/{job_id}").json()
    assert status["state"] == "failed"


def test_deactivation_is_high_risk_and_needs_a_second_qualified_approver():
    # scope_id on RoleAssignment carries no FK constraint (plan 6) - a bare UUID is
    # enough to exercise scope-matching logic without a real Team row.
    team_a = uuid.uuid4()
    team_b = uuid.uuid4()
    manager, manager_id = _manager()
    headers = csrf_headers(manager)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    created_email = f"{wid}@example.com"
    create_csv = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{created_email},Target User,,\r\n"
    )
    up = _upload(manager, headers, "users", create_csv)
    job1 = up.json()["id"]
    v1 = _decide(manager, headers, job1, tier="standard").json()["decision_version"]
    assert _commit(manager, headers, job1, v1).status_code == 200
    target = _user_row(wid)
    assert target is not None

    from app.authz.capabilities import ROLE_AGENT
    from app.workforce import service as workforce_service

    with SessionLocal() as db:
        workforce_service.assign_role(
            db, target_user_id=target.id, role_code=ROLE_AGENT, scope_type="team",
            scope_id=team_a, appointed_by=uuid.UUID(manager_id), reason_code="test_setup",
        )
        db.commit()

    uploader, _ = _team_leader(team_a, prefix="wfiuploader")
    up_headers = csrf_headers(uploader)
    deactivate_csv = f"external_workforce_id,reason_code\r\n{wid},performance_review\r\n"
    up2 = _upload(uploader, up_headers, "explicit_deactivations", deactivate_csv)
    assert up2.status_code == 200, up2.text
    job2 = up2.json()["id"]
    status2 = uploader.get(f"/api/v1/workforce/imports/{job2}").json()
    assert status2["high_risk_rows"] == 1

    standard = _decide(uploader, up_headers, job2, tier="standard")
    assert standard.status_code == 200, standard.text
    version2 = standard.json()["decision_version"]

    # The uploader cannot also be the high-risk approver.
    self_approve = _decide(uploader, up_headers, job2, tier="high_risk")
    assert self_approve.status_code == 403, self_approve.text

    # A team leader scoped to a different team lacks authority over this target.
    unqualified, _ = _team_leader(team_b, prefix="wfiunqual")
    unqualified_headers = csrf_headers(unqualified)
    bad_approve = _decide(unqualified, unqualified_headers, job2, tier="high_risk")
    assert bad_approve.status_code == 403, bad_approve.text

    # Cannot commit without the high-risk decision recorded yet.
    premature = _commit(uploader, up_headers, job2, version2)
    assert premature.status_code == 409, premature.text

    # A team leader scoped to the right team qualifies.
    approver, _ = _team_leader(team_a, prefix="wfiapprover")
    approver_headers = csrf_headers(approver)
    good_approve = _decide(approver, approver_headers, job2, tier="high_risk")
    assert good_approve.status_code == 200, good_approve.text
    # decision_version is one shared counter across both tiers - this approval
    # bumped it again, so the version to commit against is this response's, not
    # the standard decision's earlier one.
    version2 = good_approve.json()["decision_version"]

    commit2 = _commit(uploader, up_headers, job2, version2)
    assert commit2.status_code == 200, commit2.text
    assert commit2.json()["outcomes"] == [{"row_number": 1, "outcome": "deactivated"}]
    assert _is_active(wid) is False


def test_idempotent_commit_replay_does_not_reissue_tokens():
    client, _ = _manager()
    headers = csrf_headers(client)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    csv_text = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    job_id = _upload(client, headers, "users", csv_text).json()["id"]
    version = _decide(client, headers, job_id, tier="standard").json()["decision_version"]
    idem = str(uuid.uuid4())
    first = _commit(client, headers, job_id, version, idem)
    assert first.status_code == 200
    assert first.json()["activation_tokens"]

    second = _commit(client, headers, job_id, version, idem)
    assert second.status_code == 200
    assert second.json()["outcomes"] == first.json()["outcomes"]
    assert second.json()["activation_tokens"] == {}


def test_reverse_restores_state_but_skips_conflicting_rows():
    manager, _ = _manager()
    headers = csrf_headers(manager)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    create_csv = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    job1 = _upload(manager, headers, "users", create_csv).json()["id"]
    v1 = _decide(manager, headers, job1, tier="standard").json()["decision_version"]
    assert _commit(manager, headers, job1, v1).status_code == 200

    deactivate_csv = f"external_workforce_id,reason_code\r\n{wid},performance_review\r\n"
    job2 = _upload(manager, headers, "explicit_deactivations", deactivate_csv).json()["id"]
    v2 = _decide(manager, headers, job2, tier="standard").json()["decision_version"]
    approve_hr = _decide(manager, headers, job2, tier="high_risk")
    # The uploader (manager) is also the only actor here, so self-approval blocks -
    # use a second manager as the qualified, non-uploader approver.
    assert approve_hr.status_code == 403, approve_hr.text
    approver, _ = _manager(prefix="wfireverser")
    approver_headers = csrf_headers(approver)
    hr_approve = _decide(approver, approver_headers, job2, tier="high_risk")
    assert hr_approve.status_code == 200, hr_approve.text
    # decision_version is one shared counter across both tiers - use this
    # approval's version, not the earlier standard decision's.
    v2 = hr_approve.json()["decision_version"]
    commit2 = _commit(manager, headers, job2, v2)
    assert commit2.status_code == 200, commit2.text
    assert _is_active(wid) is False

    reverse1 = manager.post(f"/api/v1/workforce/imports/{job2}/reverse", headers=headers)
    # Reversal of a high-risk job needs the same qualified-approver bar as commit.
    assert reverse1.status_code == 403, reverse1.text
    reverse_ok = approver.post(
        f"/api/v1/workforce/imports/{job2}/reverse", headers=approver_headers
    )
    assert reverse_ok.status_code == 200, reverse_ok.text
    assert reverse_ok.json()["reversed"] == [{"row_number": 1, "outcome": "reactivated"}]
    assert _is_active(wid) is True

    # Reversing the same job again is rejected outright.
    reverse_again = approver.post(
        f"/api/v1/workforce/imports/{job2}/reverse", headers=approver_headers
    )
    assert reverse_again.status_code == 409, reverse_again.text


def test_workforce_import_disabled_flag_blocks_new_uploads():
    client, manager_id = _manager()
    headers = csrf_headers(client)
    with SessionLocal() as db:
        flags_service.set_flag(
            db, "workforce_import_enabled", False, actor_id=uuid.UUID(manager_id),
            reason_code="test",
        )
        db.commit()
    header_only = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
    )
    resp = _upload(client, headers, "users", header_only)
    assert resp.status_code == 409, resp.text


def test_team_membership_add_end_and_reversal():
    manager, _ = _manager()
    headers = csrf_headers(manager)
    team_id, team_code = _make_team()
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    make_user(f"{wid}@example.com")

    add_csv = f"action,external_workforce_id,team_code,reason_code\r\nadd,{wid},{team_code},\r\n"
    job1 = _upload(manager, headers, "team_memberships", add_csv).json()["id"]
    status1 = manager.get(f"/api/v1/workforce/imports/{job1}").json()
    assert status1["high_risk_rows"] == 0, status1
    v1 = _decide(manager, headers, job1, tier="standard").json()["decision_version"]
    commit1 = _commit(manager, headers, job1, v1)
    assert commit1.status_code == 200, commit1.text
    assert commit1.json()["outcomes"] == [{"row_number": 1, "outcome": "added"}]

    target = _user_row(wid)
    assert target is not None
    assert _has_active_membership(target.id, team_id) is True

    reverse1 = manager.post(f"/api/v1/workforce/imports/{job1}/reverse", headers=headers)
    assert reverse1.status_code == 200, reverse1.text
    assert reverse1.json()["reversed"] == [{"row_number": 1, "outcome": "ended"}]
    assert _has_active_membership(target.id, team_id) is False

    # Re-establish membership so an "end" row (and its own reversal) can be tested.
    readd_csv = f"action,external_workforce_id,team_code,reason_code\r\nadd,{wid},{team_code},\r\n"
    job2 = _upload(manager, headers, "team_memberships", readd_csv).json()["id"]
    v2 = _decide(manager, headers, job2, tier="standard").json()["decision_version"]
    assert _commit(manager, headers, job2, v2).status_code == 200
    assert _has_active_membership(target.id, team_id) is True

    end_csv = (
        f"action,external_workforce_id,team_code,reason_code\r\nend,{wid},{team_code},offboard\r\n"
    )
    job3 = _upload(manager, headers, "team_memberships", end_csv).json()["id"]
    v3 = _decide(manager, headers, job3, tier="standard").json()["decision_version"]
    commit3 = _commit(manager, headers, job3, v3)
    assert commit3.status_code == 200, commit3.text
    assert commit3.json()["outcomes"] == [{"row_number": 1, "outcome": "ended"}]
    assert _has_active_membership(target.id, team_id) is False

    # Reversing an "end" row re-adds the membership.
    reverse3 = manager.post(f"/api/v1/workforce/imports/{job3}/reverse", headers=headers)
    assert reverse3.status_code == 200, reverse3.text
    assert reverse3.json()["reversed"] == [{"row_number": 1, "outcome": "added"}]
    assert _has_active_membership(target.id, team_id) is True


def test_role_assignment_assign_is_high_risk_and_needs_qualified_approver():
    uploader, _ = _manager(prefix="wfiroleuploader")
    headers = csrf_headers(uploader)
    team_id, _ = _make_team()
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    target_id = make_user(f"{wid}@example.com")

    csv_text = (
        "action,external_workforce_id,role_code,scope_type,scope_code,reason_code\r\n"
        f"assign,{wid},agent,organization,,onboarding\r\n"
    )
    job = _upload(uploader, headers, "role_assignments", csv_text).json()["id"]
    status = uploader.get(f"/api/v1/workforce/imports/{job}").json()
    assert status["high_risk_rows"] == 1, status

    v = _decide(uploader, headers, job, tier="standard").json()["decision_version"]

    self_approve = _decide(uploader, headers, job, tier="high_risk")
    assert self_approve.status_code == 403, self_approve.text

    # A team leader scoped to one specific team lacks authority over an
    # organization-scoped grant, regardless of which role it grants.
    unqualified, _ = _team_leader(team_id, prefix="wfiroleunqual")
    unqualified_headers = csrf_headers(unqualified)
    bad_approve = _decide(unqualified, unqualified_headers, job, tier="high_risk")
    assert bad_approve.status_code == 403, bad_approve.text

    approver, _ = _manager(prefix="wfiroleapprover")
    approver_headers = csrf_headers(approver)
    good_approve = _decide(approver, approver_headers, job, tier="high_risk")
    assert good_approve.status_code == 200, good_approve.text
    v = good_approve.json()["decision_version"]

    commit = _commit(uploader, headers, job, v)
    assert commit.status_code == 200, commit.text
    assert commit.json()["outcomes"] == [{"row_number": 1, "outcome": "assigned"}]
    assert _has_active_role(target_id, "agent", "organization", None) is True

    reverse = uploader.post(f"/api/v1/workforce/imports/{job}/reverse", headers=headers)
    # Reversal needs the same qualified, non-uploader approver bar as commit.
    assert reverse.status_code == 403, reverse.text
    reverse_ok = approver.post(f"/api/v1/workforce/imports/{job}/reverse", headers=approver_headers)
    assert reverse_ok.status_code == 200, reverse_ok.text
    assert reverse_ok.json()["reversed"] == [{"row_number": 1, "outcome": "ended"}]
    assert _has_active_role(target_id, "agent", "organization", None) is False


def test_role_assignment_end_is_routine_not_high_risk():
    from app.workforce import service as workforce_service

    manager, manager_id = _manager()
    headers = csrf_headers(manager)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    target_id = make_user(f"{wid}@example.com")
    with SessionLocal() as db:
        workforce_service.assign_role(
            db, target_user_id=target_id, role_code="agent", scope_type="organization",
            scope_id=None, appointed_by=uuid.UUID(manager_id), reason_code="test_setup",
        )
        db.commit()
    assert _has_active_role(target_id, "agent", "organization", None) is True

    csv_text = (
        "action,external_workforce_id,role_code,scope_type,scope_code,reason_code\r\n"
        f"end,{wid},agent,organization,,offboarding\r\n"
    )
    job = _upload(manager, headers, "role_assignments", csv_text).json()["id"]
    status = manager.get(f"/api/v1/workforce/imports/{job}").json()
    assert status["high_risk_rows"] == 0, status

    v = _decide(manager, headers, job, tier="standard").json()["decision_version"]
    commit = _commit(manager, headers, job, v)
    assert commit.status_code == 200, commit.text
    assert commit.json()["outcomes"] == [{"row_number": 1, "outcome": "ended"}]
    assert _has_active_role(target_id, "agent", "organization", None) is False


def test_reporting_assignment_set_and_reversal():
    manager, _ = _manager()
    headers = csrf_headers(manager)
    sub_wid = f"wfi-{uuid.uuid4().hex[:8]}"
    sup1_wid = f"wfi-{uuid.uuid4().hex[:8]}"
    sup2_wid = f"wfi-{uuid.uuid4().hex[:8]}"
    sub_id = make_user(f"{sub_wid}@example.com")
    sup1_id = make_user(f"{sup1_wid}@example.com")
    make_user(f"{sup2_wid}@example.com")

    csv1 = f"external_workforce_id,supervisor_workforce_id,reason_code\r\n{sub_wid},{sup1_wid},\r\n"
    job1 = _upload(manager, headers, "reporting_assignments", csv1).json()["id"]
    status1 = manager.get(f"/api/v1/workforce/imports/{job1}").json()
    assert status1["high_risk_rows"] == 0, status1
    v1 = _decide(manager, headers, job1, tier="standard").json()["decision_version"]
    commit1 = _commit(manager, headers, job1, v1)
    assert commit1.status_code == 200, commit1.text
    assert commit1.json()["outcomes"] == [{"row_number": 1, "outcome": "set"}]
    assert _current_supervisor(sub_id) == sup1_id

    csv2 = f"external_workforce_id,supervisor_workforce_id,reason_code\r\n{sub_wid},{sup2_wid},\r\n"
    job2 = _upload(manager, headers, "reporting_assignments", csv2).json()["id"]
    v2 = _decide(manager, headers, job2, tier="standard").json()["decision_version"]
    commit2 = _commit(manager, headers, job2, v2)
    assert commit2.status_code == 200, commit2.text
    assert _current_supervisor(sub_id) != sup1_id

    # Reversing the second import restores the prior supervisor (sup1), not a bare removal.
    reverse2 = manager.post(f"/api/v1/workforce/imports/{job2}/reverse", headers=headers)
    assert reverse2.status_code == 200, reverse2.text
    assert reverse2.json()["reversed"] == [{"row_number": 1, "outcome": "restored"}]
    assert _current_supervisor(sub_id) == sup1_id

    # Reversing job1 now is correctly refused: job1's own reporting-assignment row
    # was superseded by job2 and is no longer active - reversing job2 created a
    # brand new row for sup1 rather than resurrecting job1's original one, so
    # job1's row itself really has "changed since commit," even though the net
    # effect coincidentally matches. History is never rewritten.
    reverse1 = manager.post(f"/api/v1/workforce/imports/{job1}/reverse", headers=headers)
    assert reverse1.status_code == 200, reverse1.text
    assert reverse1.json()["reversed"] == []
    assert len(reverse1.json()["skipped"]) == 1
    assert reverse1.json()["skipped"][0]["row_number"] == 1
    assert _current_supervisor(sub_id) == sup1_id


def test_routine_rows_still_need_real_per_row_authority_not_just_the_blanket_gate():
    """A routine (non-high-risk) row was previously only gated by "holds any
    appointment capability at all" - the same blanket bar create_user's own manual
    screen uses. That's the right bar for users/create, but team_memberships,
    role_assignments/end, users/reactivate, and reporting_assignments all have a
    real manual-screen equivalent that checks a specific scope or target, and bulk
    import must not be a looser path to the same effect. Team membership is the
    clearest case to prove: a Team Leader scoped to team B holds every blanket
    appointment capability a Team Leader gets, but has no authority over team A at
    all - the standard decision must still be refused."""
    uploader, _ = _manager(prefix="wfiscopeuploader")
    headers = csrf_headers(uploader)
    team_a_id, team_a_code = _make_team(prefix="wfiscopea")
    team_b_id, _ = _make_team(prefix="wfiscopeb")
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    make_user(f"{wid}@example.com")

    add_csv = (
        f"action,external_workforce_id,team_code,reason_code\r\nadd,{wid},{team_a_code},\r\n"
    )
    job = _upload(uploader, headers, "team_memberships", add_csv).json()["id"]

    wrong_team, _ = _team_leader(team_b_id, prefix="wfiscopewrong")
    wrong_team_headers = csrf_headers(wrong_team)
    denied = _decide(wrong_team, wrong_team_headers, job, tier="standard")
    assert denied.status_code == 403, denied.text

    right_team, _ = _team_leader(team_a_id, prefix="wfiscoperight")
    right_team_headers = csrf_headers(right_team)
    approved = _decide(right_team, right_team_headers, job, tier="standard")
    assert approved.status_code == 200, approved.text
    v = approved.json()["decision_version"]

    commit = _commit(uploader, headers, job, v)
    assert commit.status_code == 200, commit.text


def test_campaign_assignment_assign_end_and_reversal():
    manager, _ = _manager()
    headers = csrf_headers(manager)
    campaign_id, campaign_code = _make_campaign()
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    user_id = make_user(f"{wid}@example.com")

    assign_csv = (
        "action,external_workforce_id,campaign_code,team_code,reason_code\r\n"
        f"assign,{wid},{campaign_code},,\r\n"
    )
    job1 = _upload(manager, headers, "campaign_user_assignments", assign_csv).json()["id"]
    status1 = manager.get(f"/api/v1/workforce/imports/{job1}").json()
    assert status1["high_risk_rows"] == 0, status1
    v1 = _decide(manager, headers, job1, tier="standard").json()["decision_version"]
    commit1 = _commit(manager, headers, job1, v1)
    assert commit1.status_code == 200, commit1.text
    assert commit1.json()["outcomes"] == [{"row_number": 1, "outcome": "assigned"}]
    assert _current_primary_campaign(user_id) == campaign_id

    # A second campaign, assigning the already-assigned agent again is a
    # blocking classify error, not a silent no-op or a commit-time surprise.
    _, other_code = _make_campaign(prefix="wficampaign2")
    reassign_csv = (
        "action,external_workforce_id,campaign_code,team_code,reason_code\r\n"
        f"assign,{wid},{other_code},,\r\n"
    )
    job_bad = _upload(manager, headers, "campaign_user_assignments", reassign_csv).json()["id"]
    status_bad = manager.get(f"/api/v1/workforce/imports/{job_bad}").json()
    assert status_bad["invalid_rows"] == 1, status_bad
    assert _current_primary_campaign(user_id) == campaign_id  # unaffected

    reverse1 = manager.post(f"/api/v1/workforce/imports/{job1}/reverse", headers=headers)
    assert reverse1.status_code == 200, reverse1.text
    assert reverse1.json()["reversed"] == [{"row_number": 1, "outcome": "ended"}]
    assert _current_primary_campaign(user_id) is None

    # Re-establish, then exercise "end" and its own reversal.
    job2 = _upload(manager, headers, "campaign_user_assignments", assign_csv).json()["id"]
    v2 = _decide(manager, headers, job2, tier="standard").json()["decision_version"]
    assert _commit(manager, headers, job2, v2).status_code == 200
    assert _current_primary_campaign(user_id) == campaign_id

    end_csv = (
        "action,external_workforce_id,campaign_code,team_code,reason_code\r\n"
        f"end,{wid},{campaign_code},,offboard\r\n"
    )
    job3 = _upload(manager, headers, "campaign_user_assignments", end_csv).json()["id"]
    v3 = _decide(manager, headers, job3, tier="standard").json()["decision_version"]
    commit3 = _commit(manager, headers, job3, v3)
    assert commit3.status_code == 200, commit3.text
    assert commit3.json()["outcomes"] == [{"row_number": 1, "outcome": "ended"}]
    assert _current_primary_campaign(user_id) is None

    reverse3 = manager.post(f"/api/v1/workforce/imports/{job3}/reverse", headers=headers)
    assert reverse3.status_code == 200, reverse3.text
    assert reverse3.json()["reversed"] == [{"row_number": 1, "outcome": "assigned"}]
    assert _current_primary_campaign(user_id) == campaign_id


def test_campaign_assignment_requires_campaign_scoped_authority():
    uploader, _ = _manager(prefix="wficampuploader")
    headers = csrf_headers(uploader)
    team_a_id, _ = _make_team(prefix="wficampa")
    team_b_id, _ = _make_team(prefix="wficampb")
    _, campaign_code = _make_campaign(
        owning_scope_type="team", owning_scope_id=team_a_id, prefix="wficampscoped"
    )
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    make_user(f"{wid}@example.com")

    assign_csv = (
        "action,external_workforce_id,campaign_code,team_code,reason_code\r\n"
        f"assign,{wid},{campaign_code},,\r\n"
    )
    job = _upload(uploader, headers, "campaign_user_assignments", assign_csv).json()["id"]

    wrong_team, _ = _team_leader(team_b_id, prefix="wficampwrong")
    wrong_team_headers = csrf_headers(wrong_team)
    denied = _decide(wrong_team, wrong_team_headers, job, tier="standard")
    assert denied.status_code == 403, denied.text

    right_team, _ = _team_leader(team_a_id, prefix="wficampright")
    right_team_headers = csrf_headers(right_team)
    approved = _decide(right_team, right_team_headers, job, tier="standard")
    assert approved.status_code == 200, approved.text
    v = approved.json()["decision_version"]

    commit = _commit(uploader, headers, job, v)
    assert commit.status_code == 200, commit.text
    assert commit.json()["outcomes"] == [{"row_number": 1, "outcome": "assigned"}]


def test_out_of_scope_operator_cannot_view_or_commit_anothers_job():
    """Review finding #1: import jobs lacked object-level authorization. A Team
    Leader scoped to a different team holds the same blanket workforce-import
    capability as one scoped to the right team, but must not be able to view,
    preview, or commit a job whose rows are entirely outside their own scope -
    that gap previously let any capability holder commit someone else's already-
    approved job and collect its one-time activation tokens."""
    uploader, _ = _manager(prefix="wfiobjupload")
    headers = csrf_headers(uploader)
    team_a_id, team_a_code = _make_team(prefix="wfiobja")
    team_b_id, _ = _make_team(prefix="wfiobjb")
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    make_user(f"{wid}@example.com")

    add_csv = f"action,external_workforce_id,team_code,reason_code\r\nadd,{wid},{team_a_code},\r\n"
    job = _upload(uploader, headers, "team_memberships", add_csv).json()["id"]

    outsider, _ = _team_leader(team_b_id, prefix="wfiobjoutsider")
    outsider_headers = csrf_headers(outsider)

    get_denied = outsider.get(f"/api/v1/workforce/imports/{job}")
    assert get_denied.status_code == 404, get_denied.text
    preview_denied = outsider.get(f"/api/v1/workforce/imports/{job}/preview")
    assert preview_denied.status_code == 404, preview_denied.text

    insider, _ = _team_leader(team_a_id, prefix="wfiobjinsider")
    insider_headers = csrf_headers(insider)
    get_ok = insider.get(f"/api/v1/workforce/imports/{job}")
    assert get_ok.status_code == 200, get_ok.text

    approve = _decide(insider, insider_headers, job, tier="standard")
    assert approve.status_code == 200, approve.text
    version = approve.json()["decision_version"]

    # The outsider still cannot commit even a fully-approved job, and does not
    # receive its activation tokens.
    commit_denied = _commit(outsider, outsider_headers, job, version)
    assert commit_denied.status_code == 409, commit_denied.text
    assert commit_denied.json()["detail"]["code"] == "approval_authority_changed"

    commit_ok = _commit(uploader, headers, job, version)
    assert commit_ok.status_code == 200, commit_ok.text


def test_blocking_errors_prevent_approval_not_just_commit():
    """Review finding #2: commit_job silently excluded invalid rows from its own
    row query with no gate anywhere - the UI could still offer approval and
    claimed the transaction "cannot be partially applied" even though invalid
    rows were quietly dropped. Approval itself must now refuse outright while
    any row in the job has a blocking error."""
    client, _ = _manager(prefix="wfiblocking")
    headers = csrf_headers(client)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    csv_text = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"deactivate,{wid},{wid}@example.com,Name,,\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    job_id = _upload(client, headers, "users", csv_text).json()["id"]
    status = client.get(f"/api/v1/workforce/imports/{job_id}").json()
    assert status["invalid_rows"] == 2, status

    denied = _decide(client, headers, job_id, tier="standard")
    assert denied.status_code == 409, denied.text


def test_warnings_must_be_explicitly_acknowledged_to_approve():
    """Review finding #2 (second half): plan 11.2's "uploader explicitly accepts
    warnings" was previously UI copy only, backed by nothing server-side.
    Approval must now refuse a warning-only file until acknowledge_warnings is
    set, then succeed once it is."""
    client, _ = _manager(prefix="wfiwarn")
    headers = csrf_headers(client)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    create_csv = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    job1 = _upload(client, headers, "users", create_csv).json()["id"]
    v1 = _decide(client, headers, job1, tier="standard").json()["decision_version"]
    assert _commit(client, headers, job1, v1).status_code == 200

    # Reactivating an already-active user is a warning, not a blocking error.
    reactivate_csv = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"reactivate,{wid},,,,\r\n"
    )
    job2 = _upload(client, headers, "users", reactivate_csv).json()["id"]
    status2 = client.get(f"/api/v1/workforce/imports/{job2}").json()
    assert status2["warning_rows"] == 1, status2
    assert status2["invalid_rows"] == 0, status2

    unacknowledged = client.patch(
        f"/api/v1/workforce/imports/{job2}/decisions",
        json={"decision": "approve", "decision_tier": "standard"},
        headers=headers,
    )
    assert unacknowledged.status_code == 409, unacknowledged.text

    acknowledged = client.patch(
        f"/api/v1/workforce/imports/{job2}/decisions",
        json={
            "decision": "approve", "decision_tier": "standard", "acknowledge_warnings": True,
        },
        headers=headers,
    )
    assert acknowledged.status_code == 200, acknowledged.text


def test_role_assignment_end_reversal_does_not_overwrite_a_newer_grant():
    """Review finding #4: reversing an old "end" row checked only whether the
    original historical assignment row remained ended - always true, since a
    re-grant creates a brand new row rather than reactivating the old one. If
    the role was legitimately re-granted after the end-import committed,
    reversal must report a conflict instead of calling assign_role and ending
    the newer grant."""
    manager, manager_id = _manager(prefix="wfirolereversal")
    headers = csrf_headers(manager)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    target_id = make_user(f"{wid}@example.com")

    from app.workforce import service as workforce_service

    with SessionLocal() as db:
        workforce_service.assign_role(
            db, target_user_id=target_id, role_code="agent", scope_type="organization",
            scope_id=None, appointed_by=uuid.UUID(manager_id), reason_code="test_setup",
        )
        db.commit()
    assert _has_active_role(target_id, "agent", "organization", None) is True

    end_csv = (
        "action,external_workforce_id,role_code,scope_type,scope_code,reason_code\r\n"
        f"end,{wid},agent,organization,,offboarding\r\n"
    )
    job = _upload(manager, headers, "role_assignments", end_csv).json()["id"]
    v = _decide(manager, headers, job, tier="standard").json()["decision_version"]
    commit = _commit(manager, headers, job, v)
    assert commit.status_code == 200, commit.text
    assert _has_active_role(target_id, "agent", "organization", None) is False

    # Re-granted by something else entirely - not job's own reversal.
    with SessionLocal() as db:
        workforce_service.assign_role(
            db, target_user_id=target_id, role_code="agent", scope_type="organization",
            scope_id=None, appointed_by=uuid.UUID(manager_id), reason_code="re_grant",
        )
        db.commit()
    assert _has_active_role(target_id, "agent", "organization", None) is True

    reverse = manager.post(f"/api/v1/workforce/imports/{job}/reverse", headers=headers)
    assert reverse.status_code == 200, reverse.text
    assert reverse.json()["reversed"] == []
    assert len(reverse.json()["skipped"]) == 1
    assert reverse.json()["skipped"][0]["row_number"] == 1
    assert _has_active_role(target_id, "agent", "organization", None) is True


def test_team_membership_end_reversal_does_not_overwrite_a_newer_membership():
    """Review finding #4 (team-membership instance of the same bug): see
    test_role_assignment_end_reversal_does_not_overwrite_a_newer_grant - the old
    check only looked at whether the specific dead membership row itself was
    still ended, which is always true once a row is ended. Reversing an old
    "end" row must not blindly re-add membership that was legitimately re-added
    by something else since."""
    manager, _ = _manager(prefix="wfitmreversal")
    headers = csrf_headers(manager)
    team_id, team_code = _make_team(prefix="wfitmrev")
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    target_id = make_user(f"{wid}@example.com")

    add_csv = f"action,external_workforce_id,team_code,reason_code\r\nadd,{wid},{team_code},\r\n"
    job1 = _upload(manager, headers, "team_memberships", add_csv).json()["id"]
    v1 = _decide(manager, headers, job1, tier="standard").json()["decision_version"]
    assert _commit(manager, headers, job1, v1).status_code == 200
    assert _has_active_membership(target_id, team_id) is True

    end_csv = (
        f"action,external_workforce_id,team_code,reason_code\r\nend,{wid},{team_code},offboard\r\n"
    )
    job2 = _upload(manager, headers, "team_memberships", end_csv).json()["id"]
    v2 = _decide(manager, headers, job2, tier="standard").json()["decision_version"]
    assert _commit(manager, headers, job2, v2).status_code == 200
    assert _has_active_membership(target_id, team_id) is False

    # Re-added by something else entirely - not job2's own reversal.
    job3 = _upload(manager, headers, "team_memberships", add_csv).json()["id"]
    v3 = _decide(manager, headers, job3, tier="standard").json()["decision_version"]
    assert _commit(manager, headers, job3, v3).status_code == 200
    assert _has_active_membership(target_id, team_id) is True

    reverse2 = manager.post(f"/api/v1/workforce/imports/{job2}/reverse", headers=headers)
    assert reverse2.status_code == 200, reverse2.text
    assert reverse2.json()["reversed"] == []
    assert len(reverse2.json()["skipped"]) == 1
    assert reverse2.json()["skipped"][0]["row_number"] == 1
    assert _has_active_membership(target_id, team_id) is True


def test_unknown_column_in_header_fails_parse_cleanly():
    """Review finding A1: the header check previously validated only missing
    required columns - an unrecognized column (the review's own example: an
    accidental "password" column) was accepted and stored in quarantine
    instead of being rejected."""
    client, _ = _manager(prefix="wfiunknowncol")
    headers = csrf_headers(client)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    # The header check only runs once parsing reaches a first data row (an
    # empty file, header-only, never even reads the header itself), so this
    # needs a row shaped to match the bad header, not just the header alone.
    bad_csv = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date,"
        "password\r\n"
        f"create,{wid},{wid}@example.com,Name,,,badpassword123\r\n"
    )
    upload = _upload(client, headers, "users", bad_csv)
    job_id = upload.json()["id"]
    status = client.get(f"/api/v1/workforce/imports/{job_id}").json()
    assert status["state"] == "failed"


def test_team_deactivated_after_preview_is_revalidated_at_commit():
    """Review finding A2: an active team was validated at preview time but
    never re-checked at commit - a team deactivated in between would previously
    still receive a fresh, silently-accepted membership row."""
    from app.models.identity import Team

    manager, _ = _manager(prefix="wfistaleteam")
    headers = csrf_headers(manager)
    team_id, team_code = _make_team(prefix="wfistale")
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    make_user(f"{wid}@example.com")

    add_csv = f"action,external_workforce_id,team_code,reason_code\r\nadd,{wid},{team_code},\r\n"
    job = _upload(manager, headers, "team_memberships", add_csv).json()["id"]
    version = _decide(manager, headers, job, tier="standard").json()["decision_version"]

    with SessionLocal() as db:
        team = db.get(Team, team_id)
        assert team is not None
        team.status = "inactive"
        db.commit()

    commit = _commit(manager, headers, job, version)
    assert commit.status_code == 200, commit.text
    assert commit.json()["outcomes"] == [{"row_number": 1, "outcome": "skipped_conflict"}]

    target = _user_row(wid)
    assert target is not None
    assert _has_active_membership(target.id, team_id) is False


def test_supervisor_deactivated_after_preview_is_revalidated_at_commit():
    """Review finding A2 (reporting-assignment instance): same gap as
    test_team_deactivated_after_preview_is_revalidated_at_commit, for a
    supervisor deactivated after preview but before commit."""
    manager, _ = _manager(prefix="wfistalesup")
    headers = csrf_headers(manager)
    sub_wid = f"wfi-{uuid.uuid4().hex[:8]}"
    sup_wid = f"wfi-{uuid.uuid4().hex[:8]}"
    sub_id = make_user(f"{sub_wid}@example.com")
    sup_id = make_user(f"{sup_wid}@example.com")

    csv_text = (
        f"external_workforce_id,supervisor_workforce_id,reason_code\r\n{sub_wid},{sup_wid},\r\n"
    )
    job = _upload(manager, headers, "reporting_assignments", csv_text).json()["id"]
    version = _decide(manager, headers, job, tier="standard").json()["decision_version"]

    with SessionLocal() as db:
        supervisor = db.get(User, sup_id)
        assert supervisor is not None
        supervisor.active = False
        db.commit()

    commit = _commit(manager, headers, job, version)
    assert commit.status_code == 200, commit.text
    assert commit.json()["outcomes"] == [{"row_number": 1, "outcome": "skipped_conflict"}]
    assert _current_supervisor(sub_id) is None


def test_commit_and_reverse_emit_row_level_audit_events_linked_to_the_job():
    """Review finding A4: mutation audit events previously stayed generic (e.g.
    workforce.user.disable) with no import_job_id anywhere in the audit stream
    itself - the relationship was reconstructable from import tables, but not
    directly from the audit trail."""
    from app.models.audit import AuditEvent

    manager, _ = _manager(prefix="wfiaudit")
    headers = csrf_headers(manager)
    wid = f"wfi-{uuid.uuid4().hex[:8]}"
    csv_text = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    job_id = _upload(manager, headers, "users", csv_text).json()["id"]
    version = _decide(manager, headers, job_id, tier="standard").json()["decision_version"]
    commit = _commit(manager, headers, job_id, version)
    assert commit.status_code == 200, commit.text

    with SessionLocal() as db:
        commit_events = list(
            db.scalars(select(AuditEvent).where(AuditEvent.action == "workforce_import.row_commit"))
        )
    matching = [e for e in commit_events if e.event_metadata.get("import_job_id") == job_id]
    assert len(matching) == 1, matching
    assert matching[0].event_metadata["row_number"] == 1
    assert matching[0].event_metadata["import_type"] == "users"

    reverse = manager.post(f"/api/v1/workforce/imports/{job_id}/reverse", headers=headers)
    assert reverse.status_code == 200, reverse.text

    with SessionLocal() as db:
        reverse_events = list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.action == "workforce_import.row_reverse")
            )
        )
    matching_reverse = [
        e for e in reverse_events if e.event_metadata.get("import_job_id") == job_id
    ]
    assert len(matching_reverse) == 1, matching_reverse
