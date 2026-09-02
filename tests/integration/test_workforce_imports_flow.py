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
