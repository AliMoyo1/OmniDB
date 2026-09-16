# Phase 4D: Standard Call Dispositions, Retry Control, and Agent Gamification

**Product:** CipherContact  
**Repository:** OmniDB  
**Status:** Proposed implementation plan; no application changes have been implemented by this document  
**Date:** 2026-09-16  
**Depends on:** Existing campaign, import, assignment, work-queue, callback, DNC, reporting, audit, retention, feature-flag, and authenticated web flows  

## 1. Executive outcome

CipherContact will provide one standard, system-managed set of seven call dispositions for every new campaign:

1. Connected
2. No Answer
3. Call Back Later
4. Hung Up
5. Number Disconnected
6. Do Not Call
7. Unavailable

The outcome selected by an agent will consistently control what happens to the number next. `No Answer` and `Unavailable` will leave the number unavailable until a configured retry time and then return it to the shared number pool. `Call Back Later` will require a future appointment and retain the callback for the same agent. `Hung Up` will save the attempt and immediately renew the secure hold for the same agent, allowing a reliable redial without exposing the number to another agent. Terminal outcomes will finish or suppress the number as appropriate.

The same delivery will introduce a deliberately small gamification layer:

- private daily progress;
- personal, neutral achievement badges;
- shared campaign progress and team milestones;
- optional, accessible celebrations; and
- an agent-controlled switch to disable the game presentation.

Gamification will not introduce public individual leaderboards, call recordings, call-quality scores, manager ratings, prizes tied to call outcomes, or additional access to contact data. Core calling, DNC, authorization, audit, and retention rules remain authoritative whether gamification is enabled or disabled.

## 2. Goals and non-goals

### 2.1 Goals

- Make the seven dispositions present in the exact order and wording above.
- Give each disposition one server-enforced semantic meaning across the browser and JSON APIs.
- Prevent `No Answer` and `Unavailable` from being selected again before their retry time.
- Let the same agent redial `Hung Up` immediately without a queue race.
- Keep `Do Not Call` synchronous, global, protected, and independent of game rewards.
- Add an operational review path when retryable numbers reach their attempt ceiling.
- Preserve old attempts, exports, audit records, and completed campaigns.
- Add motivational feedback that cannot change an agent's permissions or reveal additional customer data.
- Roll out both workflow changes and gamification independently behind default-off feature flags.

### 2.2 Non-goals

- Telephony integration or proof that a phone conversation occurred.
- Call recording, transcription, sentiment analysis, or quality assurance scoring.
- Public rankings of individual agents.
- Compensation, disciplinary, or performance-management decisions based on game data.
- Automatic dialing without an explicit agent action.
- Email notifications or dependence on an external game service.
- Rewriting historical disposition codes or completed campaign exports.
- Replacing the existing campaign assignment, DNC, retention, or audit models.

## 3. Current baseline to preserve

The existing implementation already provides important safeguards that this phase must retain:

- Disposition completion is centralized in `app/work/service.py` and shared by the browser and JSON API.
- Call attempts are immutable and protected by per-agent idempotency keys.
- Only a currently leased contact exposes its phone number to an agent.
- A callback is hidden until it is securely leased and remains assigned to the requesting agent.
- Explicit DNC synchronously suppresses the phone fingerprint across active campaigns.
- Only the protected `explicit_dnc` semantic code may trigger global DNC suppression.
- Queue allocation locks the phone fingerprint before the work row and uses row locking to prevent concurrent disclosure.
- Repeated retry outcomes reach `review` at `max_attempts`; the current default is five.
- Campaign completion and retention begin only when every `CampaignContact.completed_at` is populated.
- Feature flags are server-enforced, fail closed when unknown or absent, and produce audit events.
- Agent activity and campaign reporting are computed from immutable attempts.

The current gaps are:

- disposition definitions are free-form and campaign-specific;
- the seven required outcomes are not created automatically;
- campaign launch does not validate the outcome set;
- `requeue` is immediately eligible, so it cannot mean “try later”;
- there is no atomic same-agent immediate-redial flow;
- attempt-limit review has no complete manager-facing resolution screen; and
- the current activity cards are statistics, not a controllable gamification experience.

## 4. Standard disposition policy

### 4.1 Authoritative manifest

Create a code-owned manifest in `app/campaigns/standard_dispositions.py`. The manifest is the authoritative source for labels, stable codes, ordering, fixed behaviour flags, and allowed manager-adjustable settings.

| Order | Agent label | Stable semantic code | Result | Connected | Callback time | Retry policy | Terminal |
|---:|---|---|---|:---:|:---:|---|:---:|
| 10 | Connected | `connected` | Complete contact | Yes | No | None | Yes |
| 20 | No Answer | `no_answer` | Delayed shared-pool retry | No | No | Default 60 minutes | No |
| 30 | Call Back Later | `callback_later` | Same-agent scheduled callback | No | Yes | Agent selects future time | No |
| 40 | Hung Up | `hung_up` | Immediate same-agent redial | No | No | Renew current lease immediately | No |
| 50 | Number Disconnected | `number_disconnected` | Complete contact | No | No | None | Yes |
| 60 | Do Not Call | `explicit_dnc` | Global suppression | No | No | None | Yes |
| 70 | Unavailable | `unavailable` | Delayed shared-pool retry | No | No | Default 30 minutes | No |

The plan adopts 60 minutes for `No Answer` and 30 minutes for `Unavailable` as deployment defaults. An authorized campaign manager may change these two delays before launch within a range of 5 minutes to 7 days. The exact configured values must be visible in launch review and in the agent's post-outcome confirmation.

### 4.2 Fixed and editable fields

For standard dispositions, managers may edit only:

- the `No Answer` retry delay;
- the `Unavailable` retry delay; and
- whether notes are required for an outcome, if that optional policy is retained.

Managers may not edit:

- the seven labels or stable codes;
- their display order;
- terminal versus retry behaviour;
- connected classification;
- callback requirements;
- immediate-redial behaviour; or
- DNC semantics.

Free-form disposition creation will remain available only for legacy campaigns while the standard-disposition flag is disabled. A campaign that adopts policy version 1 must expose exactly the seven active standard dispositions to agents.

### 4.3 Attempt ceiling

- `No Answer`, `Unavailable`, and `Hung Up` increment `attempt_count` once per idempotently committed attempt.
- If the new count is below `max_attempts`, the configured retry/redial behaviour occurs.
- If the count reaches `max_attempts`, the work item enters `review` and is no longer leaseable.
- Reaching the ceiling is not an eighth agent disposition and does not fabricate a successful connection.
- An authorized reviewer may schedule another retry or close the contact using the last recorded standard outcome and a required administrative reason.
- Review actions must be audited and must never create a fake `CallAttempt` attributed to an agent.

## 5. Required state transitions

| Selected outcome | Work item after commit | Campaign contact | Number availability |
|---|---|---|---|
| Connected | `completed` | completed with final code `connected` | Never returned |
| No Answer | `retry_wait`, or `review` at limit | outstanding | Shared pool only when `due_at` is reached |
| Call Back Later | `callback_wait` | outstanding | Same agent at the requested future time |
| Hung Up | renewed `leased`, or `review` at limit | outstanding | Same agent immediately; shared pool if renewed lease expires |
| Number Disconnected | `completed` | completed with final code `number_disconnected` | Never returned |
| Do Not Call | `suppressed` | suppressed with final code `explicit_dnc` | Never returned in any active campaign |
| Unavailable | `retry_wait`, or `review` at limit | outstanding | Shared pool only when `due_at` is reached |

Queue precedence will be:

1. overdue callbacks owned by the current agent;
2. due delayed retries in the agent's active primary campaign; and
3. fresh queued numbers in the active primary campaign.

Within due retries, order by oldest `due_at`, then highest priority, then oldest creation time. Within fresh work, preserve the current priority and creation-time ordering.

## 6. Backend and data design

### 6.1 Campaign disposition model

Extend `CampaignDispositionDefinition` with:

- `is_standard: bool`, default `false` for existing rows;
- `retry_delay_minutes: int | None`;
- `immediate_redial: bool`, default `false`; and
- `policy_version: int | None` for explicit manifest compatibility.

Extend `Campaign` with:

- `disposition_policy_version: int | None`.

Version 1 means the campaign has passed validation against the seven-outcome manifest. Do not infer adoption merely because labels look similar.

Add database constraints where practical:

- retry delay is null or between 5 and 10,080 minutes;
- `immediate_redial` cannot be combined with callback or DNC flags; and
- standard codes remain unique per campaign under the existing unique constraint.

Semantic combinations that are too detailed for a portable check constraint must be validated centrally by the standard-disposition service and again at campaign launch.

### 6.2 Work item model

Add `retry_wait` to the allowed `WorkItem.state` values. Reuse the existing `due_at` field as the earliest retry eligibility time.

For a delayed shared retry:

- set `state = retry_wait`;
- set `due_at = now + retry_delay`;
- clear lease owner, lease ID, and lease expiry;
- clear `assigned_agent_id` so the due number returns to the shared pool; and
- increment the work-item version.

Do not mark the corresponding campaign contact complete. Its existing outstanding status and null `completed_at` must continue blocking campaign completion.

Add a partial index suited to due retries, for example `(due_at, priority, created_at) WHERE state = 'retry_wait'`. Confirm its query plan with realistic queue volume before finalizing the column order.

### 6.3 Immediate same-agent redial

`Hung Up` must be one atomic service transaction:

1. lock the phone fingerprint and the agent-owned work item using the established lock order;
2. validate the active lease and standard disposition;
3. write one immutable `CallAttempt` with semantic code `hung_up`;
4. increment `attempt_count`;
5. if the ceiling is reached, clear the lease and move to `review`;
6. otherwise keep the item `leased`, issue a new lease ID, set a fresh lease expiry, retain the same lease owner, and increment the version; and
7. return the renewed lease details in the completion result.

The old lease ID becomes invalid as soon as the transaction commits. The agent must explicitly press the existing telephone link or a new `Redial now` button; the application must not initiate a call automatically.

To preserve idempotency, add nullable fields to `CallAttempt`:

- `source_lease_reason`, recording whether the attempt came from normal work, a callback, a delayed retry, or an immediate redial;
- `resulting_lease_id`; and
- `resulting_lease_expires_at`.

The source reason is a non-sensitive event snapshot used for reproducible callback achievements and operational reporting. An idempotent replay of the same completion key must return the original redial result rather than create a second attempt or rotate the lease again.

If the renewed hold expires without another saved outcome, existing lease reclamation returns it to the shared queue. Disabling or transferring the agent must also release it through the existing lease-release path.

### 6.4 Delayed retry selection

Add a due-retry candidate query to `app/work/service.py`. It must:

- select only `retry_wait` rows whose `due_at <= now`;
- require an active campaign and active agent assignment;
- exclude currently suppressed phone fingerprints;
- use `FOR UPDATE SKIP LOCKED` through the existing lock path;
- preserve the phone-lock-before-work-row ordering; and
- transition directly from `retry_wait` to `leased` without a background promotion job.

This query-driven approach means a retry becomes available at the correct time even if Celery is temporarily unavailable.

### 6.5 Callback separation

Keep customer-requested callbacks distinct from system retry delays:

- callbacks remain `callback_wait`;
- callbacks require a future time entered by the agent;
- callbacks remain assigned to that agent;
- retry waits use the manager-configured delay and return to the shared pool;
- the agent callback list must not include shared delayed retries; and
- UI and reporting must never label a system retry as a customer callback.

### 6.6 Review service

Create a review service with scoped operations:

- list `review` items as masked references and aggregate attempt data;
- reschedule a shared retry with a required future time and reason;
- increase the per-item maximum and return it to `retry_wait`; or
- close the contact using its last standard retry outcome, recording a required administrative reason.

Review listing must not expose phone numbers. If an authorized reviewer needs to call the number, it must enter the normal lease path rather than reveal data in the review table.

Closing from review must:

- set the work item and campaign contact terminal fields in one transaction;
- use the last agent-recorded standard code as `final_disposition_code`;
- retain the last attempting agent as `completed_by_agent_id` when one exists, never substitute the reviewing manager as the caller;
- preserve all immutable attempts;
- record the reviewer in an audit event rather than attributing a call to that reviewer; and
- trigger normal campaign-completion eligibility.

### 6.7 Standard disposition service

Add service functions such as:

- `install_standard_dispositions(db, campaign, actor_id)`;
- `validate_standard_dispositions(db, campaign)`;
- `update_retry_policy(db, campaign, code, delay, actor_id)`; and
- `adopt_standard_disposition_policy(db, campaign, actor_id)`.

All entry points—browser, JSON API, backfill command, and campaign creation—must call these services rather than constructing standard rows independently.

Campaign launch must fail closed if policy version 1 is required and:

- any standard code is absent or inactive;
- an unexpected active disposition is present;
- a fixed label, order, or behaviour flag differs from the manifest;
- a retry delay is outside bounds; or
- DNC protection is not exactly configured.

### 6.8 API contract changes

Extend `CompleteOut` with:

- `retry_at: datetime | None`;
- `redial_lease: LeaseOut | None`; and
- `next_step: Literal['complete', 'retry_scheduled', 'callback_scheduled', 'redial_ready', 'review', 'suppressed']`.

Extend `LeaseOut` with a backward-compatible lease reason:

- `normal`;
- `scheduled_callback`;
- `delayed_retry`; or
- `immediate_redial`.

Retain `is_callback` during compatibility rollout and derive it only from `scheduled_callback`.

Add scoped policy and review routes:

- `GET /api/v1/campaigns/{campaign_id}/disposition-policy`;
- `PATCH /api/v1/campaigns/{campaign_id}/disposition-policy/retries`;
- `POST /api/v1/campaigns/{campaign_id}/disposition-policy/adopt`;
- `GET /api/v1/campaigns/{campaign_id}/review-items`;
- `POST /api/v1/campaigns/{campaign_id}/review-items/{work_item_id}/retry`; and
- `POST /api/v1/campaigns/{campaign_id}/review-items/{work_item_id}/close`.

Existing arbitrary-disposition creation routes remain available only for legacy campaigns while standard enforcement is off. Once a campaign adopts policy version 1, those routes must reject free-form additions.

### 6.9 Audit events

Add or extend audit actions without storing raw phone numbers or notes:

- `campaign.disposition_policy.install`;
- `campaign.disposition_policy.adopt`;
- `campaign.retry_policy.update`;
- `work.retry.schedule`;
- `work.redial.ready`;
- `work.review.retry`;
- `work.review.close`;
- `gamification.preference.update`; and
- `gamification.achievement.award`.

Metadata may include campaign ID, stable semantic code, delay, policy version, attempt count, or achievement code. It must not include decrypted numbers, imported names, encrypted note plaintext, or callback free text.

## 7. Gamification design

### 7.1 First-release experience

The first release contains three connected elements:

1. **Private daily progress** — visible only to the agent and based on unique contacts handled during the agent's local campaign day.
2. **Personal achievement badges** — neutral acknowledgements for using the workflow, not judgments about call quality.
3. **Shared campaign milestones** — aggregate progress toward processing the campaign, with no individual ranking.

There will be no points currency in the first release. This avoids creating a reward system before the organization has evidence that agents find it useful and fair.

### 7.2 Progress definitions

Define metrics precisely:

- `unique_contacts_handled_today`: distinct campaign-contact IDs with at least one committed attempt by the current agent during the campaign-local day;
- `attempts_recorded_today`: committed immutable attempts, shown as context but not used for competitive ranking;
- `campaign_resolved_contacts`: campaign contacts with non-null `completed_at`;
- `campaign_total_contacts`: all retained contacts in the campaign; and
- `campaign_progress_percent`: resolved divided by total, bounded from 0 to 100.

Repeated `Hung Up`, `No Answer`, or `Unavailable` attempts on the same contact must not inflate the unique-contact progress count. `Do Not Call` is a correctly recorded workflow outcome and must not reduce progress or trigger a penalty. Connected outcomes must not be weighted more heavily than truthful non-connected outcomes.

Use the campaign timezone for “today.” If no active campaign exists, use the application's configured default timezone. Do not continue using an implicit UTC boundary for the gamified daily view.

### 7.3 Achievement catalogue version 1

Use code-owned, versioned definitions:

| Code | Display name | Criterion | Repeatable |
|---|---|---|:---:|
| `first_outcome` | First Step | First committed disposition | No |
| `ten_contacts` | Building Momentum | Ten distinct contacts handled across retained attempts | No |
| `fifty_contacts` | Steady Contributor | Fifty distinct contacts handled across retained attempts | No |
| `callback_follow_through` | Follow-through | First scheduled callback subsequently handled while due | No |

Do not create badges for:

- having few DNC outcomes;
- reporting unusually short or long calls;
- recording the most connected calls;
- avoiding skips;
- achieving sales or conversion labels; or
- outperforming named colleagues.

The callback criterion must use the immutable `source_lease_reason` snapshot rather than infer callback status from a mutable work row. Badge names and descriptions should celebrate workflow participation, not imply verified call quality or compliance certification.

### 7.4 Gamification persistence

Add `AgentGamificationPreference`:

- `user_id`, primary key and foreign key;
- `enabled`, default `false` for the opt-in pilot;
- `celebrations_enabled`, default `true` only when gamification is enabled;
- `daily_goal`, nullable and agent-controlled, bounded from 1 to 500; and
- timestamps.

Add `AgentAchievement`:

- UUID primary key;
- `user_id`;
- `achievement_code`;
- `criteria_version`;
- `awarded_at`; and
- a unique constraint on `(user_id, achievement_code, criteria_version)`.

Do not store campaign-contact IDs, phone fingerprints, names, notes, or imported metadata in either table. Achievements may remain after campaign contact retention purge because they contain no customer linkage.

Progress itself should be computed from authoritative events at current scale. If query volume later requires rollups, add a separate reviewed phase with replay and reconciliation; do not introduce unverified mutable counters now.

### 7.5 Achievement processing

Achievement refresh must be idempotent and must never block a call disposition:

- enqueue `refresh_agent_achievements(agent_id)` only after the work transaction commits;
- calculate eligibility from immutable attempts and standard semantic codes;
- insert with the unique constraint so retries cannot duplicate an award;
- run a periodic reconciliation task to recover from task-enqueue or worker failures; and
- treat task failure as a gamification delay, not a work-flow failure.

Shared campaign progress is read-only and computed from campaign contacts. It does not require award events.

### 7.6 Gamification authorization

- An agent may read only their preferences, private progress, and achievements.
- An assigned agent may read only aggregate progress for their active campaign.
- Managers, Team Leaders, Team Captains, and Viewers retain only their existing scoped aggregate-report permissions.
- No route may list agents ordered by attempts, connected outcomes, badges, goals, or progress.
- Gamification records never grant capabilities, campaign assignments, exports, or contact access.

## 8. Frontend design

### 8.1 Agent workbench disposition control

Replace the free-form active outcome rendering with the validated seven-item set in the exact manifest order. Each option should include short help text after selection:

- Connected — “This number will be completed.”
- No Answer — “This number will return to the pool after X minutes.”
- Call Back Later — “Choose the agreed future date and time.”
- Hung Up — “The outcome will be saved and this contact kept ready for immediate redial.”
- Number Disconnected — “This number will be completed and will not return to the pool.”
- Do Not Call — retain the prominent global-suppression warning.
- Unavailable — “This number will return to the pool after X minutes.”

The server remains authoritative. JavaScript may reveal fields and explanatory text, but it must not decide the state transition.

### 8.2 Completion feedback

After saving:

- `No Answer` and `Unavailable` show the local retry date/time;
- `Call Back Later` confirms the callback appointment;
- `Hung Up` returns to the same contact card with the renewed countdown and a prominent `Redial now` telephone link;
- `Connected` and `Number Disconnected` confirm that the number is complete;
- `Do Not Call` confirms suppression without displaying campaign-wide contact details; and
- an attempt-ceiling result explains that the number has gone to manager review.

Never use only colour to communicate the result. Use text, status roles, keyboard focus management, and accessible button labels.

### 8.3 Campaign control room

Replace the free-form disposition builder for policy-version-1 campaigns with a “Call outcome policy” panel that shows:

- all seven locked labels and stable codes;
- a plain-language description of each result;
- editable retry delays for `No Answer` and `Unavailable`;
- the configured maximum attempts;
- validation status; and
- policy version.

The launch section must show a blocking preflight error if the standard set is not valid.

For legacy campaigns, show a clearly labelled migration panel:

- current legacy outcome count;
- conflicts by stable code;
- dry-run effects;
- requirement to pause an active campaign before adoption; and
- an explicit, audited “Adopt standard outcomes” action.

### 8.4 Attempt-limit review screen

Add a scoped campaign review panel displaying only:

- masked contact reference;
- last standard outcome;
- attempt count and maximum;
- date of the last attempt;
- next eligible action; and
- review status.

Available actions:

- “Try again later” with date/time or delay and required reason;
- “Allow more attempts” with bounded new maximum and reason; and
- “Close after repeated attempts” with confirmation and required reason.

The table must not reveal a phone number. Calling requires normal assignment and leasing.

### 8.5 Agent progress panel

When `agent_gamification_enabled` and the agent preference are both true, add:

- a private “Today” progress bar using the agent-selected daily goal;
- unique contacts handled and attempts recorded as clearly different values;
- earned badges with plain descriptions;
- an aggregate campaign progress bar with 25%, 50%, 75%, and 100% markers; and
- a link to game preferences.

When the feature or preference is off, retain a compact factual activity summary without badges or celebration language.

### 8.6 Preferences and accessibility

Add controls for:

- enable or disable gamification;
- enable or disable celebrations;
- choose or clear a private daily goal; and
- reduce motion, while also respecting the operating system's `prefers-reduced-motion` setting.

Celebrations must be brief, non-blocking, dismissible, silent, and absent when reduced motion is requested. All progress information must remain understandable without animation.

## 9. Feature flags

Add two independent, default-off flags:

### `standard_dispositions_enabled`

When off:

- legacy campaign behaviour continues;
- existing work states remain serviceable;
- admins may run dry-run migration reports; and
- policy-version-1 campaigns may be prepared but not forced globally.

When on:

- new campaigns receive the standard manifest;
- launch requires a valid standard policy;
- policy-version-1 campaigns show only the seven standard dispositions;
- already-active legacy campaigns continue using their existing outcomes until they are paused and explicitly adopted;
- legacy campaigns cannot add more free-form outcomes after enforcement is enabled; and
- delayed retry and immediate-redial semantics are enforced.

### `agent_gamification_enabled`

When off:

- preference and progress routes return a controlled disabled response;
- achievement tasks no-op safely;
- work completion remains unchanged; and
- no game UI is rendered.

When on:

- only agents who opt in see the game presentation during the pilot; and
- aggregate campaign progress still follows existing scope checks.

Disabling gamification must be an immediate, low-risk rollback and must not alter work data.

## 10. Migration and legacy campaign strategy

### 10.1 Migration files

Create separate Alembic revisions after the current head:

1. `0018_standard_dispositions_and_retry_wait`
2. `0019_agent_gamification`

Revision 0018 should:

- add campaign policy version;
- add standard/retry/redial disposition fields;
- add resulting redial lease fields to call attempts;
- widen the work-item state constraint for `retry_wait`;
- add the due-retry index;
- seed `standard_dispositions_enabled = false`; and
- preserve all existing rows as legacy.

Revision 0019 should:

- create preferences and achievement tables;
- add required indexes and unique constraints; and
- seed `agent_gamification_enabled = false`.

Do not bulk-rewrite campaign dispositions inside the schema migration. Use an audited operational command after deployment.

### 10.2 Idempotent backfill command

Add an operator command with dry-run as the default, for example:

`python -m app.ops.standardize_dispositions --dry-run`

The report must classify campaigns as:

- new/empty draft — safe to install automatically;
- draft with compatible codes — safe after preview;
- draft with conflicts — manual resolution required;
- active or paused — pause and explicit adoption required;
- completed or archived — retain legacy read-only history; and
- already policy version 1 — validate only.

The apply mode must require an explicit campaign selection or approved batch input, actor identity, and reason. It must be idempotent and audited.

### 10.3 Historical data

- Never update `CallAttempt.semantic_outcome` for old attempts.
- Never change final disposition codes on completed campaign contacts.
- Never relabel historical exports.
- Legacy definitions used by historical attempts must remain available even if inactive.
- Completed and archived campaigns remain viewable/exportable under their original labels.
- A standard-policy campaign may not delete a disposition referenced by attempts.

## 11. File-level implementation map

Expected primary changes include:

| Area | Files or modules | Planned responsibility |
|---|---|---|
| Standard catalogue | `app/campaigns/standard_dispositions.py` | Versioned seven-outcome manifest and validation |
| Campaign model/service | `app/models/campaign.py`, `app/campaigns/service.py`, `app/campaigns/schemas.py` | Policy version, fixed fields, install/adopt/update operations, launch preflight |
| Work model/service | `app/models/work.py`, `app/work/service.py`, `app/work/schemas.py` | Retry wait, due selection, atomic redial, idempotent result contract, review operations |
| APIs | `app/api/campaigns.py`, `app/api/work.py`, a scoped review/progress router if clearer | Policy, review, completion, progress and preference contracts |
| Browser routes | `app/web/campaigns.py`, `app/web/agent_work.py` | Manager policy/review forms and agent result handling |
| Reporting/game service | `app/reporting/agent_stats.py`, `app/reporting/campaign_stats.py`, new `app/gamification/` package | Local-day progress, badges, preferences, milestone aggregates |
| Feature flags | `app/flags/service.py`, flag migration and tests | Independent rollout controls |
| Templates | `app/templates/campaign_detail.html`, `app/templates/agent_work.html`, optional progress/preferences partials | Locked policy UI, retry review, agent progress and redial feedback |
| Browser behaviour | `app/static/js/agent-work.js`, scoped gamification JavaScript | Conditional fields, help text, focus, optional animation |
| Styling | `app/static/css/base.css` or a scoped stylesheet | Accessible progress, badges, milestone markers and reduced motion |
| Tasks | new `app/gamification/tasks.py`, Celery registration | Idempotent achievement refresh and reconciliation |
| Operations | new `app/ops/standardize_dispositions.py` | Dry-run and audited legacy adoption |
| Migrations | revisions 0018 and 0019 | Forward-compatible schema and flags |
| Tests | work, campaign, flag, reporting, retention, web, concurrency, migration tests | Full behavioural and regression proof |

The exact router split may be adjusted to follow repository conventions, but service ownership and shared enforcement must not be duplicated between web and API paths.

## 12. Implementation phases

### Phase A: Schema and standard catalogue

- Add migration 0018 and model fields.
- Add the code-owned version-1 manifest.
- Implement manifest validation and standard installation.
- Add default-off workflow flag.
- Add unit tests for every fixed semantic combination.

Exit criterion: a new draft campaign can receive a valid seven-outcome policy without changing active production behaviour.

### Phase B: Retry, redial, and review services

- Implement `retry_wait` eligibility and ordering.
- Implement atomic same-agent redial with idempotent replay.
- Update lease reclamation, transfer, disable, suppression, and completion handling.
- Implement attempt-limit review services.
- Extend completion and lease response schemas.
- Add service, integration, and concurrency coverage.

Exit criterion: all seven outcomes produce the specified state transitions under concurrent and repeated requests.

### Phase C: Manager and agent workflow UI

- Replace free-form policy controls for version-1 campaigns.
- Add launch preflight and legacy-adoption preview.
- Add attempt-limit review screen.
- Add exact agent outcome ordering and explanatory text.
- Add delayed-retry, callback, terminal, DNC, and redial feedback.
- Complete keyboard and accessibility verification.

Exit criterion: an authorized manager can configure and launch a standard campaign, and an agent can execute every outcome without using the JSON API.

### Phase D: Gamification backend

- Add migration 0019, models, services, and default-off flag.
- Implement campaign-local daily progress.
- Implement versioned achievements and idempotent refresh.
- Add reconciliation task and scoped read/preference routes.
- Confirm retention purge leaves no customer-linked game data.

Exit criterion: progress and badge results are reproducible from authoritative events and inaccessible across users/scopes.

### Phase E: Gamification UI and pilot controls

- Add opt-in preferences.
- Add private progress, badges, and aggregate campaign milestones.
- Add optional accessible celebrations.
- Preserve the factual non-game view when disabled.
- Add UX and browser integration tests.

Exit criterion: one pilot team can opt in without changing calling permissions or outcomes.

### Phase F: Backfill, rollout, and documentation

- Run dry-run reports for every non-completed campaign.
- Resolve stable-code conflicts.
- Migrate selected paused/draft pilot campaigns.
- Update Agent, Manager, Team Leader, Team Captain, Viewer, and Super Administrator manuals as applicable.
- Update the operations runbook and rollback procedure.
- Enable flags in the staged order in section 15.

Exit criterion: the pilot completes with no stranded retry/review work and with an approved production rollout decision.

## 13. Test plan

### 13.1 Unit tests

- The manifest contains exactly seven entries, exact labels, exact codes, and exact order.
- Every fixed flag combination matches section 4.1.
- Retry-delay bounds reject zero, negative, and over-seven-day values.
- Policy validation identifies missing, extra, inactive, or altered standard outcomes.
- Agent-day boundaries use the campaign timezone.
- Achievement criteria count distinct contacts and are versioned.

### 13.2 Disposition integration tests

- New standard campaigns contain exactly the seven active dispositions.
- Launch fails if any required outcome is absent or altered.
- Connected completes once and increments connected reporting.
- Number Disconnected completes without creating global suppression.
- Do Not Call suppresses every active copy and cannot be configured under another stable code.
- Call Back Later rejects missing, invalid, or past times and remains owned by the same agent.
- No Answer enters `retry_wait`, is not leaseable before due time, and is leaseable afterward.
- Unavailable has the same time gate with its own configured delay.
- Due callbacks outrank due retries, and due retries outrank fresh queue items.
- Retryable outcomes enter review exactly at the maximum attempt count.
- Review reschedule and closure preserve audit and campaign-completion rules.

Use an injectable clock or controlled timestamp fixture. Do not use real sleeps.

### 13.3 Immediate-redial tests

- Hung Up writes one attempt and returns a new lease ID for the same agent and contact.
- The old lease ID is rejected after commit.
- A second agent cannot acquire the contact during the renewed hold.
- Replaying the same idempotency key returns the original attempt and lease result.
- A different idempotency key records the next real attempt.
- At the attempt ceiling, no new lease is issued and the item enters review.
- Expiry, account disable, campaign transfer, pause, DNC race, and assignment end safely release or suppress the renewed hold.

### 13.4 Gamification tests

- The flag off state never blocks or changes work completion.
- A user with preference off sees the factual view without game elements.
- Agents cannot read another agent's goals, progress, or badges.
- Repeated attempts on one contact count once toward unique-contact progress.
- DNC and disconnected outcomes are neutral contributions rather than penalties.
- Achievement refresh is idempotent under duplicate tasks.
- A missed task is repaired by reconciliation.
- No achievement row stores a campaign-contact ID, phone fingerprint, contact name, note, or metadata.
- Campaign progress respects campaign and role scope.
- No endpoint exposes an individual ranking.
- Reduced-motion settings disable animations without hiding progress text.

### 13.5 Migration and rollback tests

- Upgrade from a representative 0017 database succeeds.
- Existing dispositions and attempts remain byte-for-byte semantically unchanged.
- Dry-run produces no writes.
- Repeated apply runs are idempotent.
- Conflicting stable codes stop adoption with a useful report.
- Downgrade maps any `retry_wait` rows safely only after the documented drain step.
- Completed and archived campaigns retain historical display/export behaviour.

### 13.6 Regression and static checks

Run at minimum:

- the complete work-flow integration file;
- browser agent and campaign operation tests;
- campaign reporting, export, retention, feature flag, DNC, assignment, and concurrency tests;
- migration upgrade/downgrade tests;
- the full automated test suite in the supported Compose/Linux database environment;
- `ruff check .`; and
- `mypy app`.

On Windows, do not treat tests that cannot connect to the Compose-only PostgreSQL host as passing integration coverage. Run database-backed checks in the supported container environment.

## 14. Performance and capacity checks

- Confirm the due-retry query uses the intended partial index with a production-like queue size.
- Confirm callback and fresh-queue latency does not regress materially.
- Index agent progress by agent and attempt creation time, and distinct-contact queries by the existing attempt/contact keys.
- Measure workbench render time with progress enabled and disabled.
- Keep badge refresh outside the disposition transaction.
- Set an initial target of p95 under 250 ms for progress reads at expected pilot volume; record actual measurements before rollout.
- Monitor review backlog and oldest due retry so stranded work becomes visible operationally.

## 15. Rollout

### 15.1 Pre-deployment

- Back up the production database and validate restore instructions.
- Inventory all draft, active, paused, completed, and archived campaigns.
- Export a dry-run conflict report for all disposition definitions.
- Agree on the 60-minute and 30-minute retry defaults or revise this plan before coding.
- Select one synthetic or low-risk pilot campaign and willing pilot team.

### 15.2 Deployment with flags off

1. Deploy application code that understands both legacy and version-1 policies.
2. Apply migrations 0018 and 0019.
3. Confirm legacy calling, DNC, callback, export, and retention paths.
4. Run the standardization command in dry-run mode.
5. Install the standard policy on the pilot draft/paused campaign.

### 15.3 Workflow pilot

1. Enable `standard_dispositions_enabled` only after the pilot campaign passes validation.
2. Exercise all seven outcomes with synthetic contacts.
3. Verify retry timing, same-agent redial, attempt ceiling, review resolution, DNC, export, and retention completion.
4. Observe queue and review health for at least one full operating cycle.

### 15.4 Gamification pilot

1. Enable `agent_gamification_enabled` after workflow stability is confirmed.
2. Invite the pilot agents to opt in; do not silently enroll them during the pilot.
3. Collect feedback on clarity, fairness, distraction, and pressure.
4. Compare support issues, incomplete callbacks, and outcome-entry errors before and during the pilot.
5. Do not use short-term call volume as the sole success measure.

### 15.5 General rollout

- Pause and migrate active campaigns one controlled group at a time.
- Enable standard outcomes only after every in-scope campaign validates.
- Keep gamification independently reversible.
- Publish updated role manuals before broad enablement.
- Record the final retry defaults, pilot decision, and flag state in the change record.

## 16. Rollback

### 16.1 Gamification rollback

- Disable `agent_gamification_enabled`.
- Stop or no-op achievement tasks.
- Leave preferences and awarded badges in place for possible re-enable.
- Verify the standard calling workflow continues unchanged.

### 16.2 Workflow rollback

Do not roll an old binary back while live `retry_wait` rows exist; the old application does not understand that state.

Safe sequence:

1. Disable new standard-disposition completions.
2. Wait for active leases to finish or reclaim them safely.
3. Inventory `retry_wait`, `callback_wait`, `leased`, and `review` rows.
4. Convert `retry_wait` rows to `queued` only under an audited rollback operation that preserves their due time in audit metadata.
5. Confirm no version-1 campaign is accepting work.
6. Disable `standard_dispositions_enabled`.
7. Roll back the application binary if still necessary.

Prefer leaving additive columns and tables in place during an emergency application rollback. Run schema downgrade only after a database backup and explicit verification that no incompatible states or policy-version-1 dependencies remain.

## 17. Failure points and mitigations

| Failure | Consequence | Mitigation |
|---|---|---|
| Retry delay is configured as zero | Tight repeat-call loop | Enforce 5-minute minimum in schema and service |
| Due retry query ignores `due_at` | Number is called too early | Dedicated state, service-level predicate, integration test with controlled clock |
| Hung Up clears the lease before reissue | Another agent may obtain the number | One atomic transaction under established phone/work locks |
| Idempotent Hung Up rotates twice | Duplicate attempts or broken lease | Persist resulting lease details on the attempt and test replay |
| Standard set is incomplete | Agent sees ambiguous choices | Launch preflight and policy version validation |
| Legacy code conflicts with a standard code | Incorrect semantics | Dry-run conflict report; never overwrite automatically |
| Review queue has no owner | Numbers remain stranded | Scoped manager/captain review screen plus backlog monitoring |
| Gamification task fails | Badge appears late | Keep it outside work transaction and reconcile periodically |
| Repeated retries inflate progress | Rewards unproductive loops | Base progress and milestones on distinct contacts/resolved contacts |
| DNC is treated as failure | Agents may avoid recording opt-outs | Neutral treatment and no outcome-weighted points |
| Public rankings create unfair pressure | Distorted outcome entry and poor adoption | No leaderboard endpoint or UI in this phase |
| Retention purge leaves customer linkage in game tables | Data-protection breach | Store only user, achievement code, version, and time; add purge regression tests |
| Feature flag disables only the UI | API behaviour remains active unexpectedly | Enforce each flag in shared service code and test both directions |
| Old binary is restored with `retry_wait` rows | Retry work becomes invisible | Mandatory drain/convert step before binary rollback |

## 18. Security and data-protection invariants

- Selecting a disposition never grants broader access to customer data.
- Gamification never reveals a phone number, contact name, note, callback detail, or imported metadata beyond the existing authorized workbench.
- DNC suppression remains synchronous and cannot be delayed by achievement processing.
- No reward is conditional on avoiding DNC, disconnected, unavailable, or no-answer outcomes.
- All mutation routes retain CSRF, authenticated session, capability, campaign-scope, lease-ownership, and object-level checks.
- Standard policy changes and review actions require existing campaign-management authority and audit records.
- Phone fingerprint locking order remains consistent across leasing, retry, redial, import, completion, and suppression.
- Progress and badge data cannot be exported by agents or used to retrieve contact data.
- Disabling gamification leaves the full operational workflow usable.
- No game data changes role assignments, campaign assignments, exports, or retention deadlines.

## 19. Operational metrics

Monitor the workflow, not individual employee performance:

- count and oldest age of `retry_wait` items;
- count and oldest age of `review` items;
- callback backlog and overdue callbacks;
- percentage of campaigns on policy version 1;
- standard-policy validation failures;
- redial lease issuance and expiry counts;
- achievement-task failures and reconciliation repairs; and
- agent opt-in/opt-out totals for the pilot.

Do not send phone numbers, names, notes, workforce IDs, or per-agent outcome totals to Sentry. Operational logs and errors should use non-sensitive entity IDs and stable error codes.

## 20. Resources and ownership

Implementation needs:

- backend ownership for migrations, work-state transitions, idempotency, locking, APIs, and review service;
- frontend ownership for manager policy/review screens and agent progress/redial UX;
- QA coverage in PostgreSQL/Compose, including concurrency and migration drills;
- an operations owner for backfill, flag rollout, monitoring, and rollback rehearsal; and
- a business owner to approve retry defaults, attempt limits, and pilot feedback criteria.

No email provider, public internet dependency, or external gamification platform is required.

## 21. Measurable success criteria

The phase is successful when:

- every new standard campaign exposes exactly the seven required outcomes in the required order;
- `No Answer` and `Unavailable` cannot be leased before their configured time;
- `Hung Up` reliably returns a renewed same-agent lease with no queue race;
- all terminal outcomes preserve campaign completion, export, and retention behaviour;
- DNC suppression remains immediate and cross-campaign;
- attempt-limit work is visible and resolvable through an authorized review screen;
- historical attempts and completed exports remain unchanged;
- gamification can be enabled or disabled without affecting the work queue;
- private progress cannot be read by another agent;
- no public ranking exists;
- repeated attempts do not inflate unique-contact progress;
- achievement records contain no customer linkage;
- all listed automated, concurrency, migration, static, and production-like smoke checks pass; and
- pilot agents report that the game presentation is understandable and voluntary rather than pressuring or confusing.

## 22. Definition of done

- [ ] Business owner has approved retry defaults and attempt-limit policy.
- [ ] Migrations 0018 and 0019 have upgrade and safe downgrade coverage.
- [ ] Standard manifest and launch validation are implemented.
- [ ] All seven server-enforced transitions are implemented.
- [ ] Delayed retries and atomic same-agent redial are concurrency tested.
- [ ] Manager review workflow is implemented and scoped.
- [ ] Browser and API contracts agree.
- [ ] Gamification preferences, progress, achievements, and aggregate milestones are implemented behind a separate flag.
- [ ] No leaderboard, call-quality score, recording, or customer-linked game record has been introduced.
- [ ] Legacy migration dry-run and apply modes are idempotent and audited.
- [ ] Role manuals and operations documentation are updated.
- [ ] Full supported-environment tests, `ruff check .`, and `mypy app` pass.
- [ ] Backup, rollout, smoke test, monitoring, and rollback evidence is recorded.

## 23. Explicitly deferred ideas

The following may be reconsidered only after pilot evidence and a separate approved plan:

- opt-in team challenges beyond campaign completion milestones;
- cosmetic profile themes or avatars;
- training simulations and privacy-practice badges;
- repeatable seasonal achievements;
- persistent progress rollups for very large event volumes; and
- any comparison between named agents.

Public individual leaderboards, call-quality scoring, recordings, and outcome-weighted rewards remain outside CipherContact's approved product direction.
