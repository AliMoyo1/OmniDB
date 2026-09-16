# Standard dispositions and agent gamification: rollout and rollback

Implements `docs/architecture/PHASE-4D-STANDARD-DISPOSITIONS-AND-AGENT-GAMIFICATION-PLAN.md`
sections 15-19. Two independent, default-off flags govern this feature -
`standard_dispositions_enabled` (the seven-outcome call workflow: retry_wait,
immediate redial, and manager review) and `agent_gamification_enabled` (private
progress, badges, and shared campaign milestones). Enable them separately, in
that order, never together on the first attempt.

## Before touching anything

- Back up the database and confirm the backup restores (`RUNBOOKS.md`'s
  Backup and restore runbook) - this feature adds two migrations (schema
  changes to `campaign_disposition_definitions`/`work_items`/`call_attempts`,
  and two new tables) and a new work-item state (`retry_wait`).
- Inventory every draft, active, paused, completed, and archived campaign.
- Run the classification report and read it before deciding anything:
  ```
  python -m app.ops.standardize_dispositions --dry-run
  ```
  Each campaign lands in exactly one bucket:
  - `new_empty_draft` and `already_policy_v1` - actionable today via `--apply`.
  - `draft_compatible` / `draft_with_conflicts` - a draft that already has its
    own free-form dispositions. This build has no automated legacy-to-standard
    adoption path yet; moving one of these onto the standard set means
    recreating the campaign's disposition set by hand, campaign by campaign,
    with the manager UI's "Call outcome policy" panel (only available once the
    campaign has zero dispositions again) - do this deliberately, not in bulk.
  - `active_or_paused` - must be paused, and its disposition set rebuilt as
    above, before it can adopt the standard policy. An active campaign is
    never touched automatically.
  - `completed_or_archived` - reported for completeness only; never migrate a
    completed or archived campaign's history.
- Agree the two retry defaults (60 minutes for No Answer, 30 for Unavailable)
  or revise them in the plan before deploying. A campaign manager can override
  either one per campaign, within 5 minutes to 7 days, up until that
  campaign's launch.
- Pick one synthetic or low-risk campaign and one willing pilot team. Do not
  pilot on a campaign already carrying real customer call volume.

## Deploying with both flags off

1. Deploy the application image; it understands both legacy and
   standard-policy campaigns simultaneously, so this step alone changes
   nothing observable.
2. Apply migrations `0019_standard_dispositions`, `0020_retry_wait_and_redial`,
   and `0021_agent_gamification` (`alembic upgrade head`, per the Deploy
   runbook). All three are additive; existing campaigns, dispositions, and
   attempts are untouched, and both flags seed disabled.
3. Confirm legacy calling, DNC, callback, export, and retention paths still
   work exactly as before - they must, since nothing new is active yet.
4. Re-run the dry-run report to confirm the pilot campaign is
   `new_empty_draft` (a fresh draft with no dispositions), then install the
   standard policy on it:
   ```
   python -m app.ops.standardize_dispositions --apply \
     --campaign <pilot-campaign-id> --actor <your-user-id> \
     --reason "phase 4D pilot rollout"
   ```
   Equivalently, a manager can do this from the campaign's own page (the
   "Use the standard call outcomes instead?" panel on a new draft campaign).

## Workflow pilot (`standard_dispositions_enabled`)

1. Enable the flag only after the pilot campaign has a valid standard policy
   (the campaign page's policy panel shows "Valid" before you flip it):
   ```
   # from an authorized manager session, or:
   python -c "
   from app.db import SessionLocal
   from app.flags import service as flags
   with SessionLocal() as db:
       flags.set_flag(db, 'standard_dispositions_enabled', True, actor_id=<your-user-id>, reason_code='phase 4D pilot')
       db.commit()
   "
   ```
2. Launch the pilot campaign and exercise every one of the seven outcomes
   with synthetic contacts: Connected, No Answer, Call Back Later, Hung Up,
   Number Disconnected, Do Not Call, Unavailable.
3. Verify specifically:
   - No Answer / Unavailable actually wait until their configured delay
     before the number reappears in the shared pool.
   - Hung Up renews the same agent's hold immediately, with a new lease -
     the old lease ID is rejected right after.
   - An item that reaches its attempt ceiling lands in the campaign's Review
     screen (`/campaigns/<id>/review`), not silently dropped.
   - Do Not Call still suppresses synchronously across every campaign
     holding that number.
   - Export and retention still behave identically for this campaign once it
     completes.
4. Watch queue and review health for at least one full operating cycle
   before deciding the workflow half of the pilot succeeded.

## Gamification pilot (`agent_gamification_enabled`)

1. Enable this flag only after the workflow half is stable - it is
   independent, but stacking two unproven changes on the same pilot muddies
   what caused what.
2. Invite the pilot agents to opt in themselves (Workbench → Preferences).
   Do not enable anyone's preference for them; the whole point is that it is
   voluntary.
3. Collect feedback on clarity, fairness, distraction, and pressure - not
   just whether it works.
4. Compare support tickets, incomplete callbacks, and outcome-entry errors
   before and during the pilot. Short-term call volume alone is not a
   success measure - the plan explicitly rules that out.

## General rollout

- Pause and migrate additional active campaigns one controlled group at a
  time, never all at once.
- Enable `standard_dispositions_enabled`'s enforcement expectation (every
  in-scope campaign validates) only after every campaign you intend to cover
  actually does.
- Keep gamification independently reversible at every step - it must never
  become a prerequisite for the workflow half to function.
- Publish the updated role manuals (`docs/user-manuals/`) before broadening
  access beyond the pilot.
- Record the final retry defaults, the pilot decision, and both flags' final
  state in your organization's change record.

## Rollback

### Gamification rollback (always safe, always independent)

1. Disable `agent_gamification_enabled`.
2. Preference and progress reads immediately return the disabled response;
   `refresh_agent_achievements_task` and the reconciliation task both no-op
   on their own (they check the flag themselves, so an already-queued task
   from before the rollback is harmless).
3. Leave preferences and already-awarded badges in place - they contain no
   campaign or contact data, so there is nothing to clean up, and re-enabling
   later resumes exactly where it left off.
4. Confirm the standard (or legacy) calling workflow is completely unaffected.

### Workflow rollback (`standard_dispositions_enabled`)

**Do not roll the application binary back while any `work_items.state =
'retry_wait'` row exists.** An older binary has no idea what that state
means - it is not in its own state machine.

1. Disable `standard_dispositions_enabled` first. This stops new standard
   completions from being accepted; it does not touch any row already in
   `retry_wait`, `callback_wait`, `leased`, or `review`.
2. Let active leases finish naturally, or reclaim them:
   ```
   python -c "
   from app.db import SessionLocal
   from app.work import service as work_service
   with SessionLocal() as db:
       print(work_service.reclaim_expired_leases(db))
       db.commit()
   "
   ```
3. Inventory what remains:
   ```sql
   select state, count(*) from work_items
   where state in ('retry_wait', 'callback_wait', 'leased', 'review')
   group by state;
   ```
4. Convert any remaining `retry_wait` rows to `queued` under an explicit,
   audited operation - never a silent bulk `UPDATE`. Preserve the original
   `due_at` in the audit event's metadata so the operational record shows
   what was lost, not just that something changed.
5. Confirm no campaign is still on policy version 1 that the old binary would
   need to understand.
6. Only then roll back the application binary, per the standard Rollback
   runbook.
7. Prefer leaving the additive migrations in place during an emergency
   rollback. Run `alembic downgrade` only after a fresh backup and an
   explicit check that no policy-version-1 campaign or live `retry_wait` row
   still depends on the schema you are about to remove - migration
   `0020_retry_wait_and_redial`'s own downgrade will otherwise silently
   demote any surviving `retry_wait` row to `queued` for you, which is a
   last resort, not a first one.

## Operational metrics to watch

Monitor the workflow, never an individual agent's performance:

- count and oldest age of `retry_wait` items (a growing backlog means the
  due-retry query isn't keeping up, or the retry delay is set too long for
  the campaign's volume);
- count and oldest age of `review` items (a growing backlog means nobody is
  working the review screen);
- callback backlog and any overdue callback;
- percentage of in-scope campaigns on policy version 1;
- standard-policy validation failures at launch time;
- redial lease issuance and expiry counts;
- achievement-task failures and how many the reconciliation task repairs per
  run (a sustained non-zero repair count means enqueueing is failing
  somewhere upstream, not that reconciliation is doing its job well);
- agent opt-in/opt-out totals during the gamification pilot.

Never send a phone number, contact name, note, workforce ID, or per-agent
outcome total to Sentry or any shared dashboard - use the campaign ID and
stable semantic codes only.
