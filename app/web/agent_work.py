"""Keyboard-first browser workflow for agents.

The page deliberately exposes only the currently leased contact. Callback lists
remain masked, and every mutation delegates to the same work service used by the
JSON API so authorization, DNC handling, idempotency, and lease rules cannot drift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from datetime import tzinfo as TZInfo
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz import service as authz
from app.authz.capabilities import WORK_QUEUE
from app.campaigns.standard_dispositions import DISPOSITION_HELP_TEXT, POLICY_VERSION
from app.db import get_session
from app.flags import service as flags
from app.flags.service import FeatureDisabledError
from app.gamification import service as gamification_service
from app.gamification.service import GamificationPolicyError
from app.gamification.tasks import refresh_agent_achievements_task
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.identity import User
from app.reporting import agent_stats
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import page_context, templates
from app.work import service as work_service
from app.work.service import (
    CompletionResult,
    DispositionMismatch,
    IdempotencyConflict,
    LeaseConflict,
    MissingRequiredField,
    WorkItemError,
)

router = APIRouter(prefix="/agent/work", tags=["web-agent-work"])


def _redirect(
    *, success: str | None = None, error: str | None = None, path: str = "/agent/work"
) -> RedirectResponse:
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    url = path + ("?" + urlencode(params) if params else "")
    return RedirectResponse(url, status_code=303)


def _authorized(db: Session, user: User) -> bool:
    return authz.has_assigned_capability(db, user.id, WORK_QUEUE)


def _parse_callback(value: str, timezone_name: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        local_value = datetime.fromisoformat(value)
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise MissingRequiredField("callback time is invalid") from exc
    if local_value.tzinfo is None:
        local_value = local_value.replace(tzinfo=timezone)
    return local_value.astimezone(UTC)


def _disposition_help_text(disposition: CampaignDispositionDefinition) -> str:
    template = DISPOSITION_HELP_TEXT.get(disposition.stable_semantic_code)
    if template is None:
        return ""
    if "{minutes}" in template and disposition.retry_delay_minutes is not None:
        return template.format(minutes=disposition.retry_delay_minutes)
    return template


def _completion_message(result: CompletionResult, timezone_name: str) -> str:
    zone: TZInfo
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = UTC
    if result.next_step == "suppressed":
        return "Do-not-call recorded. This number will not be called again."
    if result.next_step == "callback_scheduled":
        if result.callback_at is not None:
            local = result.callback_at.astimezone(zone)
            return f"Callback scheduled for {local.strftime('%d %b %Y, %H:%M')}."
        return "Callback scheduled."
    if result.next_step == "retry_scheduled":
        if result.retry_at is not None:
            local = result.retry_at.astimezone(zone)
            return f"This number will return to the pool at {local.strftime('%d %b %Y, %H:%M')}."
        return "Returned to the shared queue."
    if result.next_step == "redial_ready":
        return "Outcome saved. This contact is ready for immediate redial - press Redial now."
    if result.next_step == "review":
        return "Attempt limit reached. This number has gone to manager review."
    if result.next_step == "complete":
        return "Number completed."
    return "Disposition saved."


@router.get("")
def workbench(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _authorized(db, user):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+agent+work.", 303)

    try:
        lease = work_service.get_active_lease(db, user.id)
        db.commit()
    except WorkItemError:
        db.rollback()
        lease = None

    campaign = db.get(Campaign, lease.campaign_id) if lease else None
    dispositions = []
    if campaign is not None:
        dispositions = list(
            db.scalars(
                select(CampaignDispositionDefinition)
                .where(
                    CampaignDispositionDefinition.campaign_id == campaign.id,
                    CampaignDispositionDefinition.active.is_(True),
                )
                .order_by(
                    CampaignDispositionDefinition.display_order,
                    CampaignDispositionDefinition.label,
                )
            )
        )

    gamification_flag_enabled = flags.is_enabled(db, "agent_gamification_enabled")
    gamification_preference = (
        gamification_service.get_preference(db, user.id) if gamification_flag_enabled else None
    )
    gamification_active = (
        gamification_flag_enabled
        and gamification_preference is not None
        and gamification_preference.enabled
    )
    daily_progress = None
    daily_goal_percent = None
    campaign_progress = None
    achievements: list = []
    recent_achievement = None
    if gamification_active:
        daily_progress = gamification_service.get_daily_progress(db, user.id)
        if daily_progress.daily_goal:
            daily_goal_percent = min(
                100,
                int(daily_progress.unique_contacts_handled_today / daily_progress.daily_goal * 100),
            )
        active_campaign = campaign or gamification_service.get_agent_primary_campaign(
            db, user.id
        )
        if active_campaign is not None:
            campaign_progress = gamification_service.get_campaign_progress(
                db, active_campaign.id
            )
        achievements = gamification_service.list_achievements(db, user.id)
        if achievements and achievements[0].awarded_at >= datetime.now(UTC) - timedelta(
            minutes=2
        ):
            recent_achievement = achievements[0]

    context = page_context(
        request,
        db,
        user,
        active_section="workbench",
        lease=lease,
        campaign=campaign,
        dispositions=dispositions,
        is_standard_policy=(
            campaign is not None and campaign.disposition_policy_version == POLICY_VERSION
        ),
        disposition_help={d.id: _disposition_help_text(d) for d in dispositions},
        callbacks=work_service.list_agent_callbacks(db, user.id),
        stats=agent_stats.get_today_stats(db, user.id),
        gamification_flag_enabled=gamification_flag_enabled,
        gamification_preference=gamification_preference,
        gamification_active=gamification_active,
        daily_progress=daily_progress,
        daily_goal_percent=daily_goal_percent,
        campaign_progress=campaign_progress,
        achievements=achievements,
        recent_achievement=recent_achievement,
        achievement_catalogue=gamification_service.ACHIEVEMENTS_BY_CODE,
        idempotency_key=str(uuid.uuid4()),
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "agent_work.html", context)


@router.post("/next", dependencies=[Depends(verify_form_csrf)])
def next_contact(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    try:
        result = work_service.lease_next(db, user.id)
    except FeatureDisabledError as exc:
        return _redirect(error=str(exc))
    db.commit()
    if result is None:
        return _redirect(error="No contact is available in your assigned campaign.")
    return _redirect(success="Contact secured. Complete or release it before moving on.")


@router.post("/{work_item_id}/complete", dependencies=[Depends(verify_form_csrf)])
def complete_contact(
    work_item_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    lease_id: uuid.UUID = Form(...),
    disposition_id: uuid.UUID = Form(...),
    notes: str = Form(""),
    callback_at: str = Form(""),
    duration_seconds: int | None = Form(None),
    idempotency_key: str = Form(...),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    campaign_id = db.scalar(
        select(CampaignDispositionDefinition.campaign_id).where(
            CampaignDispositionDefinition.id == disposition_id
        )
    )
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    try:
        callback = _parse_callback(callback_at, campaign.timezone if campaign else "UTC")
        result = work_service.complete_work_item(
            db,
            work_item_id=work_item_id,
            agent_id=user.id,
            lease_id=lease_id,
            disposition_id=disposition_id,
            notes=notes.strip() or None,
            callback_at=callback,
            self_reported_duration_seconds=duration_seconds,
            idempotency_key=idempotency_key,
        )
    except (
        LeaseConflict, DispositionMismatch, MissingRequiredField, IdempotencyConflict,
        FeatureDisabledError,
    ) as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    if flags.is_enabled(db, "agent_gamification_enabled"):
        refresh_agent_achievements_task.delay(str(user.id))
    return _redirect(success=_completion_message(result, campaign.timezone if campaign else "UTC"))


@router.post("/{work_item_id}/skip", dependencies=[Depends(verify_form_csrf)])
def skip_contact(
    work_item_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    lease_id: uuid.UUID = Form(...),
    reason: str = Form(...),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    try:
        work_service.skip_work_item(
            db,
            work_item_id=work_item_id,
            agent_id=user.id,
            lease_id=lease_id,
            reason=reason,
        )
    except (LeaseConflict, MissingRequiredField) as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success="Contact released with a recorded reason.")


@router.post("/{work_item_id}/renew", dependencies=[Depends(verify_form_csrf)])
def renew_contact(
    work_item_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    lease_id: uuid.UUID = Form(...),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    try:
        work_service.renew_lease(db, work_item_id, user.id, lease_id)
    except LeaseConflict as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success="Contact hold extended.")


@router.get("/preferences")
def preferences(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _authorized(db, user):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+agent+work.", 303)
    context = page_context(
        request,
        db,
        user,
        active_section="workbench",
        gamification_flag_enabled=flags.is_enabled(db, "agent_gamification_enabled"),
        gamification_preference=gamification_service.get_preference(db, user.id),
        min_daily_goal=gamification_service.MIN_DAILY_GOAL,
        max_daily_goal=gamification_service.MAX_DAILY_GOAL,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "agent_preferences.html", context)


@router.post("/preferences", dependencies=[Depends(verify_form_csrf)])
def update_preferences(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    enabled: str = Form(""),
    celebrations_enabled: str = Form(""),
    daily_goal: str = Form(""),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.", path="/agent/work/preferences")
    goal_text = daily_goal.strip()
    try:
        goal_value = int(goal_text) if goal_text else None
    except ValueError:
        return _redirect(
            error="Daily goal must be a whole number.", path="/agent/work/preferences"
        )
    try:
        gamification_service.set_preference(
            db,
            user.id,
            enabled=enabled == "true",
            celebrations_enabled=celebrations_enabled == "true",
            daily_goal=goal_value,
            clear_daily_goal=goal_value is None,
        )
    except GamificationPolicyError as exc:
        db.rollback()
        return _redirect(error=str(exc), path="/agent/work/preferences")
    db.commit()
    return _redirect(success="Preferences updated.", path="/agent/work/preferences")
