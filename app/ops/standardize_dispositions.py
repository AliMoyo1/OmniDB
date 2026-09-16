"""Operator command for the standard-dispositions rollout (phase 4D plan 10.2).

Dry-run (default) - classify every campaign, act on nothing:
    python -m app.ops.standardize_dispositions --dry-run

Apply to one campaign - installs the standard policy only where that is
actually possible today (a brand-new draft campaign with no dispositions of
its own yet), or confirms an already-adopted one is still valid:
    python -m app.ops.standardize_dispositions --apply --campaign <id> \\
        --actor <user-id> --reason "pilot rollout"

Every other classification (a draft that already has free-form dispositions,
an active/paused campaign, or a completed/archived one) is reported but not
acted on - this build has no legacy-to-standard adoption path yet (the
"Adopt standard outcomes" migration flow described in the plan's frontend
section), and this tool refuses to guess at one.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.campaigns import service as campaigns_service
from app.campaigns.service import DispositionPolicyError
from app.campaigns.standard_dispositions import POLICY_VERSION, STANDARD_DISPOSITIONS_BY_CODE
from app.models.campaign import Campaign, CampaignDispositionDefinition

NEW_EMPTY_DRAFT = "new_empty_draft"
DRAFT_COMPATIBLE = "draft_compatible"
DRAFT_WITH_CONFLICTS = "draft_with_conflicts"
ACTIVE_OR_PAUSED = "active_or_paused"
COMPLETED_OR_ARCHIVED = "completed_or_archived"
ALREADY_POLICY_V1 = "already_policy_v1"

REPORT_ORDER = (
    NEW_EMPTY_DRAFT, DRAFT_COMPATIBLE, DRAFT_WITH_CONFLICTS,
    ACTIVE_OR_PAUSED, ALREADY_POLICY_V1, COMPLETED_OR_ARCHIVED,
)

# Only these two are actionable by --apply today - see the module docstring.
ACTIONABLE_CLASSIFICATIONS = frozenset({NEW_EMPTY_DRAFT, ALREADY_POLICY_V1})


@dataclass(frozen=True)
class CampaignClassification:
    campaign_id: uuid.UUID
    external_code: str
    name: str
    status: str
    classification: str
    conflicting_codes: tuple[str, ...] = ()


def _conflicting_codes(existing: list[CampaignDispositionDefinition]) -> tuple[str, ...]:
    """Which of a draft campaign's own disposition codes collide with a
    standard code under different behaviour - a code that already matches
    the manifest exactly is not a conflict, just a coincidence."""
    conflicting = []
    for row in existing:
        entry = STANDARD_DISPOSITIONS_BY_CODE.get(row.stable_semantic_code)
        if entry is None:
            continue
        matches = (
            row.label == entry.label
            and row.next_action == entry.next_action
            and row.causes_dnc == entry.causes_dnc
            and row.requires_callback_time == entry.requires_callback_time
            and row.counts_as_connected == entry.counts_as_connected
        )
        if not matches:
            conflicting.append(row.stable_semantic_code)
    return tuple(sorted(conflicting))


def classify_campaign(db: Session, campaign: Campaign) -> CampaignClassification:
    conflicting: tuple[str, ...] = ()
    if campaign.status in ("completed", "archived"):
        classification = COMPLETED_OR_ARCHIVED
    elif campaign.disposition_policy_version == POLICY_VERSION:
        classification = ALREADY_POLICY_V1
    elif campaign.status in ("active", "paused"):
        classification = ACTIVE_OR_PAUSED
    else:
        existing = list(
            db.scalars(
                select(CampaignDispositionDefinition).where(
                    CampaignDispositionDefinition.campaign_id == campaign.id
                )
            )
        )
        if not existing:
            classification = NEW_EMPTY_DRAFT
        else:
            conflicting = _conflicting_codes(existing)
            classification = DRAFT_WITH_CONFLICTS if conflicting else DRAFT_COMPATIBLE
    return CampaignClassification(
        campaign_id=campaign.id, external_code=campaign.external_code, name=campaign.name,
        status=campaign.status, classification=classification, conflicting_codes=conflicting,
    )


def classify_all_campaigns(db: Session) -> list[CampaignClassification]:
    campaigns = list(db.scalars(select(Campaign).order_by(Campaign.created_at.asc())))
    return [classify_campaign(db, campaign) for campaign in campaigns]


def apply_to_campaign(
    db: Session, campaign: Campaign, *, actor_id: uuid.UUID, reason: str
) -> CampaignClassification:
    """Idempotent - an already-policy-v1 campaign is validated, never
    reinstalled. Raises DispositionPolicyError for any classification this
    tool does not automate; it never silently skips or guesses."""
    result = classify_campaign(db, campaign)
    if result.classification == ALREADY_POLICY_V1:
        validation = campaigns_service.validate_standard_dispositions(db, campaign)
        if not validation.valid:
            raise DispositionPolicyError(
                "already policy version 1 but invalid: " + "; ".join(validation.errors)
            )
    elif result.classification == NEW_EMPTY_DRAFT:
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
    else:
        raise DispositionPolicyError(
            f"campaign {campaign.external_code} is classified '{result.classification}' - "
            "this tool only automates a new, empty draft campaign or one already on policy "
            "version 1; every other case needs a legacy-adoption decision this build does "
            "not yet automate"
        )
    record_audit(
        db, action="ops.standardize_dispositions.apply", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id, reason_code=reason[:50],
        event_metadata={
            "campaign_external_code": campaign.external_code,
            "classification": result.classification,
        },
    )
    return classify_campaign(db, campaign)


def _print_report(classifications: list[CampaignClassification]) -> None:
    by_bucket: dict[str, list[CampaignClassification]] = {}
    for row in classifications:
        by_bucket.setdefault(row.classification, []).append(row)

    print(f"Classified {len(classifications)} campaign(s):\n")
    for bucket in REPORT_ORDER:
        rows = by_bucket.get(bucket, [])
        note = (
            "actionable by --apply" if bucket in ACTIONABLE_CLASSIFICATIONS
            else "not yet automated"
        )
        print(f"{bucket} ({note}): {len(rows)}")
        for row in rows:
            conflict_note = (
                f" [conflicts: {', '.join(row.conflicting_codes)}]" if row.conflicting_codes else ""
            )
            print(f"  - {row.external_code} ({row.status}) {row.name}{conflict_note}")
        print()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify campaigns for the phase 4D standard-dispositions rollout, and apply "
            "the standard policy where that is actually possible today."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (default)")
    mode.add_argument("--apply", action="store_true", help="install the standard policy")
    parser.add_argument("--campaign", help="campaign ID, required with --apply")
    parser.add_argument("--actor", help="acting user ID, required with --apply")
    parser.add_argument("--reason", help="audited reason, required with --apply")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from app.db import SessionLocal

    if args.apply:
        if not (args.campaign and args.actor and args.reason):
            print("ERROR: --apply requires --campaign, --actor, and --reason", file=sys.stderr)
            return 2
        try:
            campaign_id = uuid.UUID(args.campaign)
            actor_id = uuid.UUID(args.actor)
        except ValueError:
            print("ERROR: --campaign and --actor must be valid UUIDs", file=sys.stderr)
            return 2
        with SessionLocal() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign is None:
                print(f"ERROR: campaign {campaign_id} not found", file=sys.stderr)
                return 2
            try:
                result = apply_to_campaign(db, campaign, actor_id=actor_id, reason=args.reason)
                db.commit()
            except DispositionPolicyError as exc:
                db.rollback()
                print(f"ERROR: {exc}", file=sys.stderr)
                return 3
        print(f"Campaign {result.external_code}: {result.classification}")
        return 0

    with SessionLocal() as db:
        classifications = classify_all_campaigns(db)
    _print_report(classifications)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
