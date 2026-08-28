# CipherContact operational runbooks

Phase 5 (production hardening / controlled pilot) deliverable: master plan Phase 5
lists "completed runbooks" and requires that "support and incident owners can
execute runbooks." These six cover deploy/release, rollback, backup and restore,
service restart, cold boot, and disk space - the procedures the master plan names
directly (cold-boot, service-restart, disk-alert, backup, and restore drills) plus
the deploy/rollback pair that ties them together.

**Contacts are role placeholders, not real names or phone numbers.** Fill in
`## Contacts` before this build goes anywhere near a real pilot - an
un-staffed escalation table is the same as no escalation table.

Every command below assumes: working directory is the repo root, the stack is
already defined by `compose.yaml`, and `deploy/secrets/*` exist. Where a runbook
says "verify," do not skip it - most of the incidents this project has actually
had this session were caught by verification steps, not by assuming a command
that returned exit 0 did what it was supposed to.

## Contacts

| Role | Name | How to reach |
|---|---|---|
| Technical lead | _fill in_ | _fill in_ |
| IT / network engineer | _fill in_ | _fill in_ |
| Incident owner (on-call) | _fill in_ | _fill in_ |
| Product owner | _fill in_ | _fill in_ |
| Privacy / legal reviewer | _fill in_ | _fill in_ |

## Reference: services, health, and where things live

| Service | Restart policy | Health check | Notes |
|---|---|---|---|
| `postgres` | `unless-stopped` | `pg_isready` | Named volume `pgdata` |
| `redis` | `unless-stopped` | `redis-cli ping` | Named volume `redisdata` |
| `web` | `unless-stopped` | `GET /healthz` inside the container | Serves the app; depends on postgres+redis being healthy to start |
| `worker` | `unless-stopped` | `celery inspect ping` | Celery worker; shares the `quarantine` volume with web |
| `beat` | `unless-stopped` | **none defined** - verify by log inspection (see Service Restart) | Celery scheduler |
| `caddy` | `unless-stopped` | none | Reverse proxy / TLS termination; only service with ports published to the host (80/443) |

- No database or app port is published to the host - only Caddy's 80/443. Anything
  above needs `docker compose exec`/`run`, not a direct host connection.
- `GET /healthz` is liveness only (process is up) - it does **not** prove the app
  can reach Postgres or Redis. Use `GET /readyz` for that (see below).
- `GET /readyz` runs a real `SELECT 1` against Postgres and a real Redis `PING`.
  It requires an `X-Health-Token` header matching the `health_token` secret once
  one is configured - without the right token it returns 404, not 401, so its
  existence doesn't leak to an unauthenticated caller. Example:
  `curl -H "X-Health-Token: $(cat deploy/secrets/health_token)" https://<host>/readyz`
- Secrets live in `deploy/secrets/` as individual files (`db_password`,
  `app_secret_key`, `field_encryption_key`, `phone_fingerprint_hmac_key`,
  `health_token`, `backup_passphrase`). Never print their contents to a shared
  terminal, log, or chat channel - reference the file path, not the value.
- `deploy/monitoring/` is an empty placeholder as of this writing - there is no
  automated alerting yet. The disk-space runbook below is a manual check, not a
  response to a real alert, until that gets built.

---

## Runbook: Deploy / release

**Owner:** Technical lead | **Frequency:** As needed (every release)
**Last updated:** 2026-08-28 | **Last run:** N/A - written from the deploy pattern
practiced repeatedly this session (see BUILD-LOG.md), not yet run as a standalone
script-by-script drill.

### Purpose
Ship a reviewed, CI-green commit to the running stack with a pre-deploy backup,
a real readiness check afterward, and a traceable record of what shipped.

### Prerequisites
- [ ] The target commit's CI run is green (`gh run list --branch main --limit 1`
      or check the GitHub Actions tab) - build, security, quality, and
      integration all passed.
- [ ] You are on the deploy host, repo checked out at the target commit.
- [ ] `deploy/secrets/backup_passphrase` exists (needed for the pre-deploy backup).
- [ ] No other deploy or rollback is in progress.

### Procedure

#### Step 1: Record what's about to ship
```
bash scripts/release-manifest.sh
```
**Expected result:** A JSON object with the commit hash, the list of migrations
present, and a build timestamp. Save this output somewhere durable (the incident
channel, a pinned message, a local `deploy-log.txt`) - it's the answer to "what
exactly did we deploy" during any later investigation.
**If it fails:** `git rev-parse HEAD` failing means you're not in the repo root
or not in a git checkout at all - fix your working directory before continuing.

#### Step 2: Pre-deploy backup
```
bash scripts/backup.sh
```
**Expected result:** Ends with `Backup complete and decrypt-verified: <path>
(<n> bytes)`. The script already re-decrypts the archive it just wrote as an
integrity check - if it prints that line, the backup is real, not just written.
**If it fails:** `ERROR: backup passphrase file not found` means
`deploy/secrets/backup_passphrase` is missing - stop, do not deploy without a
pre-deploy backup. Any other failure (disk full, postgres unreachable) - fix the
underlying cause before proceeding; do not skip this step "just this once."

#### Step 3: Build the new image
```
docker compose build web
```
**Expected result:** Build completes with no errors. `worker` and `beat` share
this same image (`build: .` in `compose.yaml`), so one build covers all three -
you do not need to build them separately.
**If it fails:** Read the actual build error - a dependency resolution failure
or a syntax error should have already been caught by CI's `build` job, so a
local build failure at this stage usually means your checkout doesn't actually
match the commit CI passed. Re-verify `git status` and `git log -1`.

#### Step 4: Run migrations against the live database
```
docker compose run --rm web alembic upgrade head
```
**Expected result:** Alembic prints each migration it applies (or nothing, if
already at head) and exits 0. This runs in a throwaway container from the image
you just built, against the real running `postgres` service - the live schema
is now at the new head.
**If it fails:** Stop here. Do not proceed to Step 5. A failed migration against
a live database needs investigation before anything else changes - check the
error, check whether it partially applied (`docker compose exec postgres psql -U
ciphercontact -d ciphercontact -c "SELECT version_num FROM alembic_version"`),
and consider whether `alembic downgrade -1` is safe before trying again. This is
an escalate-if-unsure situation (see Escalation).

#### Step 5: Recreate the application services
```
docker compose up -d web worker beat caddy
```
**Expected result:** Docker reports the four services recreated. Deliberately
not `postgres`/`redis` here - they don't need touching for an application-only
release and restarting them is unnecessary risk to already-healthy stateful
services.
**If it fails:** Check `docker compose logs web --tail 50` (or whichever service
failed to come up) for the actual error before retrying.

#### Step 6: Verify
```
docker compose ps
curl -sS https://<host>/healthz
curl -sS -H "X-Health-Token: $(cat deploy/secrets/health_token)" https://<host>/readyz
curl -sSI https://<host>/login
```
**Expected result:** `docker compose ps` shows all six services `Up` (web/worker/
postgres/redis show `(healthy)`; beat and caddy have no healthcheck, so just
`Up` is normal for them - see the Service Restart runbook for how to actually
verify beat). `/healthz` returns `{"status":"ok"}`. `/readyz` returns
`{"status":"ok", ...}` with `"database":"ok"` and `"redis":"ok"` - if either
says `"error"`, the deploy is not actually healthy even though `/healthz` looked
fine. `/login`'s headers include `content-security-policy`,
`strict-transport-security`, and `cache-control: no-store`.
**If it fails:** A missing security header or a non-200 on `/login` after a
clean-looking deploy has been a real bug in this build before (an unrelated
tool's redesign broke `/login` and sat unnoticed on `main` for six days - see
BUILD-LOG.md, 2026-08-28). Do not treat "the containers are up" as proof the
release is good; run this whole verify block every time.

#### Step 7: Confirm no errors in fresh logs
```
docker compose logs web worker beat --since 5m
```
**Expected result:** No tracebacks, no repeated connection errors. Routine
`app.access` request logs are normal.

### Verification
- [ ] `docker compose ps` shows all six services healthy or (for beat/caddy) Up.
- [ ] `/healthz` returns ok.
- [ ] `/readyz` shows `database: ok` and `redis: ok`.
- [ ] `/login` returns 200 with CSP/HSTS/no-store headers present.
- [ ] A protected route (e.g. `/campaigns`) redirects an unauthenticated request
      to `/login`.
- [ ] No errors in the last 5 minutes of web/worker/beat logs.
- [ ] The release manifest from Step 1 is saved somewhere durable.

### Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose build web` fails on a dependency | Checkout doesn't match the CI-passed commit | `git status`, `git log -1`, re-sync |
| Migration fails partway | Bad migration, or live data violates a new constraint | Stop, do not proceed to Step 5, see Step 4's failure note, escalate if unsure |
| `web` container restarts in a loop after Step 5 | App can't reach postgres/redis, or a config/secret is missing | `docker compose logs web`, confirm postgres/redis are healthy first |
| `/readyz` says `database: error` but `/healthz` is fine | `/healthz` never touches the database - this is exactly the gap it exists to catch | Check postgres health, check `DB_*` env/secrets weren't touched |
| `/login` missing security headers | A middleware or route regression | Do not conclude the deploy is fine from `/healthz` alone - see Step 6 |

### Rollback
See the **Rollback** runbook below. Do not attempt an ad-hoc rollback outside
that procedure - it exists specifically because of the master plan's own
non-breaking rollback requirements (preserve attempts/audit events, never
delete volumes).

### Escalation
Migration failures, repeated container crash-loops after a clean build, or any
uncertainty about whether it's safe to proceed - stop and reach the Technical
lead before continuing. See `## Contacts`.

---

## Runbook: Rollback

**Owner:** Technical lead + Incident owner | **Frequency:** As needed (incident only)
**Last updated:** 2026-08-28 | **Last run:** N/A

### Purpose
Reverse a bad release with minimum data risk, following the master plan's own
Phase 5 rollback approach: application image rolls back first; the database is
only touched if the incident lead specifically approves it; nothing that holds
attempts or audit events is ever deleted as a routine step.

### Prerequisites
- [ ] You know the last known-good commit (check `BUILD-LOG.md` for the most
      recent entry confirming a green, deployed release, or `git log`).
- [ ] The incident owner is aware a rollback is happening.
- [ ] You have NOT yet decided to touch the database - that's a separate,
      later decision gated on its own approval (Step 5).

### Procedure

#### Step 1: Halt new work intake
```
docker compose stop web worker
```
**Expected result:** `web` and `worker` stop; `postgres`, `redis`, `beat`, and
`caddy` keep running. This is the closest equivalent to the master plan's "pause
new leases before rollback" that exists today - there is no finer-grained
feature flag yet to pause leasing without stopping the service entirely. Note
this limitation; a real "pause leasing" flag independent of taking the app down
is a reasonable Phase 5+ follow-up.
**If it fails:** If `docker compose stop` itself errors, something is already
wrong with the Docker daemon - see the Cold Boot runbook's troubleshooting
section for Docker Desktop-specific failure modes encountered this session.

#### Step 2: Check out the known-good commit
```
git status
git checkout <last-known-good-commit>
```
**Expected result:** Clean checkout at the prior commit. Run `git status` first
per this project's own standing safety practice - never `checkout` over
uncommitted changes without knowing what they are.

#### Step 3: Rebuild the prior image
```
docker compose build web
```
**Expected result:** Same as the deploy runbook's Step 3, now building the
OLDER code.

#### Step 4: Recreate the application services from the prior image
```
docker compose up -d web worker beat caddy
```
**Expected result:** Same verification as the deploy runbook's Step 6 - run
that full verify block now. If the rollback fixes the issue and the app is
healthy on the prior version with the CURRENT (unmodified) database, **stop
here.** Most rollbacks resolve at this point without ever touching data.

#### Step 5: Database restore - approval-gated, not automatic
Only proceed past this point if:
- The prior application version alone does not resolve the incident, AND
- The problem is genuinely data-related (a bad migration, corrupted rows,
  incorrect writes from the bad release), AND
- **The incident owner has explicitly approved a database restore.**

If all three are true:
```
bash scripts/restore.sh backups/<the-pre-deploy-backup-from-the-bad-release>.dump.gpg
```
**Expected result:** By default this restores into `ciphercontact_restore`, a
**separate database, not the live one** - this is a deliberate safety rail in
the script, not an oversight. Inspect that restored copy first
(`docker compose exec postgres psql -U ciphercontact -d ciphercontact_restore`)
to confirm it actually contains what you expect before going anywhere near the
live database.

Only after that inspection, and only with the incident owner's approval to
proceed, restore over the live database by passing its real name explicitly:
```
bash scripts/restore.sh backups/<file>.dump.gpg ciphercontact
```
**This overwrites the live database with the backup's contents
(`pg_restore --clean --if-exists`).** Everything written since that backup was
taken - every attempt, every audit event, every campaign action - is gone
unless you have captured it separately. This is exactly the scenario the master
plan means by "preserve attempts and audit events" - a full restore is the last
resort, not the default response to a bad deploy.
**If it fails:** Do not retry blindly. A failed restore over the live database
is itself an incident - stop and escalate immediately.

#### Step 6: Verify
Run the full verification block from the Deploy runbook's Step 6 and Verification
checklist again.

### Verification
- [ ] Application is running the confirmed-good commit.
- [ ] `/healthz`, `/readyz`, `/login` all check out per the Deploy runbook.
- [ ] If a database restore happened: the incident owner has confirmed the data
      loss window (time between the backup and the restore) is understood and
      accepted, and it's recorded in the incident log.
- [ ] No volumes were deleted at any point (`pgdata`, `redisdata`, `quarantine`,
      `caddydata`, `caddyconfig` all still exist: `docker volume ls`).

### Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| Rolling back the app image doesn't fix it | The problem is data, not code | Move to Step 5, with approval |
| `restore.sh` can't find the backup file | Wrong path, or the backup predates what you need | Check `backups/manifest.jsonl` for the exact filename and timestamp |
| Live restore needed but no recent backup exists | A release shipped without Step 2 of the Deploy runbook | This is a process gap, not a script bug - escalate; there is no good technical fix for a backup that was never taken |

### Escalation
Any database restore decision - Incident owner approval required before Step 5.
Any uncertainty at all - Technical lead. See `## Contacts`.

---

## Runbook: Backup and restore

**Owner:** Technical lead | **Frequency:** Before every release (backup), monthly
(restore test), plus incident-driven restores as needed
**Last updated:** 2026-08-28 | **Last run:** Practiced repeatedly this session
during real deploys (see BUILD-LOG.md's several `backups/ciphercontact-*.dump.gpg`
references) - not yet run as a standalone drill outside a real release.

### Purpose
Create a verifiable encrypted backup, prove backups are actually restorable
(a backup nobody has restored is not a backup), and restore one when needed.

### Prerequisites
- [ ] `deploy/secrets/backup_passphrase` exists.
- [ ] The stack is up (`docker compose ps` shows `postgres` healthy).

### Procedure

#### Step 1: Create a backup
```
bash scripts/backup.sh
```
**Expected result:** `backups/ciphercontact-<UTC timestamp>.dump.gpg` is
created, an entry is appended to `backups/manifest.jsonl` with its SHA-256 and
size, and the script re-decrypts the file itself to prove it isn't corrupt.
Ends with `Backup complete and decrypt-verified`.
**If it fails:** See the Deploy runbook's Step 2 failure notes.

#### Step 2: Move the backup off-device
The script's own output says this explicitly: **"Copy the backup and its
passphrase to separate, off-device custody."** A backup that lives on the same
host as the database it backs up does not protect against host loss. This step
has no scripted command here because the actual off-device target (S3, another
host, an encrypted external drive) isn't decided yet - that decision itself is
a Phase 5 gap worth closing before pilot, not something to leave implicit.

#### Step 3: Prove it restores (routine - monthly, and before any high-risk release)
```
bash scripts/restore-test.sh
```
**Expected result:** Restores the most recent backup into a throwaway,
timestamped database, checks it has at least one table in the `public` schema,
prints `Restore test PASSED for <file>`, then drops the test database
automatically (via its own cleanup trap) whether it passed or failed.
**If it fails:** `ERROR: restore produced no tables` or a `pg_restore` error
means the backup itself is bad - re-run Step 1 and investigate why immediately.
Do not treat an untested backup as a real one.

#### Step 4: Restore for real (incident use - see the Rollback runbook for the
approval gate around this)
```
bash scripts/restore.sh backups/<file>.dump.gpg [target_db]
```
**Expected result:** Without a second argument, restores into
`ciphercontact_restore` - a separate database, safe to inspect without touching
production. Only pass the live database name (`ciphercontact`) as the second
argument when you specifically intend to overwrite production, per the
Rollback runbook's approval gate.

### Verification
- [ ] `backups/manifest.jsonl` has a new entry after every backup.
- [ ] `scripts/restore-test.sh` has passed within the last month.
- [ ] The backup passphrase and at least the most recent backup file exist in
      off-device custody, not only on the production host.

### Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| `pg_dump` hangs or times out | Postgres under heavy load, or a long-running transaction holding locks | Check `docker compose exec postgres psql -U ciphercontact -c "SELECT * FROM pg_stat_activity"` for blocking queries |
| Backup file is suspiciously small | Something failed silently before this session's data existed, or the wrong database was targeted | Check `DB_NAME`/`DB_USER` env vars match, compare size against the manifest's history |
| `restore-test.sh` says no backups found | `BACKUP_DIR` doesn't match where `backup.sh` actually wrote to | Both scripts default to `./backups` - confirm you're running from the repo root |

### Escalation
A failed restore test on a backup you're about to rely on for a release -
Technical lead, before proceeding with that release. See `## Contacts`.

---

## Runbook: Service restart

**Owner:** IT / network engineer or Technical lead | **Frequency:** As needed
**Last updated:** 2026-08-28 | **Last run:** N/A

### Purpose
Restart one or more services safely, respecting the dependency order
(`web`/`worker` need `postgres` and `redis` healthy to start cleanly), and
actually confirm each came back up rather than assuming it did.

### Prerequisites
- [ ] You know which specific service is misbehaving, or that a full-stack
      restart is genuinely what's needed (prefer restarting the single affected
      service first).

### Procedure

#### Step 1: Restart a single service
```
docker compose restart <service>
```
**Expected result:** Docker reports the service restarted.
**If it fails:** `docker compose logs <service> --tail 50` for the actual error.

#### Step 2: Verify that service specifically
```
docker compose ps <service>
```
For services with a defined healthcheck (`postgres`, `redis`, `web`, `worker`),
wait for `(healthy)`, not just `Up` - a container can be `Up` and still failing
its healthcheck during startup.

**`beat` has no healthcheck defined.** Verify it manually:
```
docker compose logs beat --tail 20
```
Look for normal scheduler activity in the log (Celery Beat logging its periodic
wake-ups), not a crash traceback repeating. There is no automated way to prove
beat is healthy right now - this is worth a real healthcheck definition as a
follow-up, not something to keep working around manually forever.

`caddy` also has no healthcheck; verify it by actually reaching the app through
it: `curl -sSI https://<host>/healthz`.

#### Step 3: If restarting multiple services, order matters
```
docker compose restart postgres redis
# wait for both healthy (Step 2), then:
docker compose restart web worker
# wait for both healthy, then:
docker compose restart beat caddy
```
**Expected result:** `web`/`worker` starting before `postgres`/`redis` are
healthy will crash-loop on connection errors - restarting them again after
their dependencies are actually up resolves it. `compose.yaml`'s
`depends_on: ... condition: service_healthy` handles this automatically for a
fresh `up`, but a plain `restart` on an already-running stack does not
re-evaluate that condition - hence doing it manually, in order, here.

### Verification
- [ ] Every service you restarted shows `(healthy)` where a healthcheck exists,
      or passes its manual check (beat, caddy) otherwise.
- [ ] `curl /healthz` and, if you touched web, `curl /readyz` both succeed.

### Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| `web`/`worker` crash-loop after restart | Restarted before postgres/redis were healthy | Restart postgres/redis first, confirm healthy, then retry web/worker |
| `beat` log shows a repeating traceback | Usually a code-level bug, not an infra issue | Check whether it started after the same code change on web/worker - if so, this is a rollback situation, not a restart-and-hope one |
| Restarting doesn't clear the problem at all | The issue may not be at the service level | Check `docker compose logs <service>` for the real error before restarting again - repeated blind restarts rarely fix a real bug |

### Escalation
A service that won't come back healthy after two restart attempts and a log
review - Technical lead. See `## Contacts`.

---

## Runbook: Cold boot (full host restart)

**Owner:** IT / network engineer | **Frequency:** As needed (planned maintenance
or unplanned host restart)
**Last updated:** 2026-08-28 | **Last run:** N/A on the actual production host.
The Docker Desktop-specific failure mode documented below under Troubleshooting
was encountered firsthand on the development machine this session, not the
pilot host - included because it's real, specific, and worth knowing about
before it happens somewhere that matters more.

### Purpose
After a full host reboot, confirm the stack actually came back up correctly
rather than assuming `restart: unless-stopped` handled everything silently.

### Prerequisites
- [ ] The host has finished booting and you can reach it (SSH, console, or
      remote desktop as appropriate for this host).

### Procedure

#### Step 1: Confirm Docker itself is running
```
docker version
```
**Expected result:** Both a `Client` and `Server` section print with version
info.
**If it fails:** On Windows/Docker Desktop hosts specifically, see
Troubleshooting below for a real failure mode this session hit: Docker
Desktop's Windows-side processes can be running while its actual VM/engine
never started, especially right after a Docker Desktop self-update. `docker
version` failing (or returning only a `Client` section) while `docker`-named
processes appear to be running in Task Manager is exactly that symptom, not a
sign the whole machine is broken.

#### Step 2: Confirm all six services are up
```
docker compose ps
```
**Expected result:** All six services listed, `postgres`/`redis`/`web`/`worker`
showing `(healthy)`. `restart: unless-stopped` on every service means Docker
should have started them automatically - this step is confirming that actually
happened, not assuming it from the restart policy alone.
**If it fails:** `docker compose up -d` to bring up anything that didn't
start on its own, then re-run this check.

#### Step 3: Confirm the schema is at head
```
docker compose exec postgres psql -U ciphercontact -d ciphercontact -c "SELECT version_num FROM alembic_version"
```
Compare against `ls migrations/versions/ | tail -1` (the newest migration file
in the checked-out code). They should match - a cold boot doesn't run
migrations, so this just confirms nothing is silently out of sync with the code
that's actually running.

#### Step 4: Run the full verification block
Same as the Deploy runbook's Step 6: `/healthz`, `/readyz` (with the health
token), `/login` headers, and a protected-route redirect check.

### Verification
- [ ] `docker version` shows both client and server.
- [ ] All six services `Up`, four with `(healthy)`.
- [ ] Schema version matches the newest migration in the checked-out code.
- [ ] Full deploy-style verification block passes.

### Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| `docker` CLI not found on PATH right after a reboot (Windows) | Docker Desktop relocated its install directory during a self-update (observed this session: moved from `C:\Program Files\Docker\Docker` to `%LOCALAPPDATA%\Programs\DockerDesktop`) and the system PATH wasn't refreshed | Find the new `resources\bin\docker.exe` under the current install path and invoke it directly, or fix PATH once the correct location is confirmed |
| `docker version` errors with `failed to connect to the docker API at npipe://...dockerDesktopLinuxEngine` | Docker Desktop's Windows-side processes are running but its WSL2 backend VM never started - can follow an interrupted or in-progress self-update | Check `wsl --list --verbose` for the `docker-desktop` distro's state; try restarting Docker Desktop fully (quit from the tray, relaunch) before anything more invasive |
| WSL reports `Logon failure: the user has not been granted the requested logon type` / error code ending `0x80070569` when trying to start the `docker-desktop` distro directly | A Windows logon-type/security-policy right (e.g. "Log on as a service") needed by Hyper-V's Host Compute Service is missing for the account - can follow a Group Policy refresh on a domain-joined machine | **Do not attempt to change local security policy without IT.** Try a full host reboot first (often resolves a stale logon session from an in-progress Docker Desktop update); if it persists, this is IT/network engineer territory, not something to self-service |
| All containers show `Up` but `docker volume ls` is missing volumes you expect | A Docker Desktop update reset its data disk (observed this session, on a dev machine, after the above logon-type issue was eventually resolved) | This is why off-device backup custody (Backup & Restore runbook, Step 2) matters - restore from the most recent backup rather than assuming local state survived any update |

### Escalation
`docker version` failing at all after a reboot, or any WSL2/Hyper-V permission
error - IT / network engineer. A missing/reset data volume - Technical lead,
immediately (this is a data-loss situation; see the Backup & Restore runbook).
See `## Contacts`.

---

## Runbook: Disk space

**Owner:** IT / network engineer | **Frequency:** Manual periodic check - **no
automated alert exists yet** (`deploy/monitoring/` is an empty placeholder as of
this writing). Treat this as "how to check when someone asks," not "what to do
when an alert fires," until real monitoring is built.
**Last updated:** 2026-08-28 | **Last run:** N/A

### Purpose
Check what's consuming disk space across the named volumes and the host, and
know what's expected to grow versus what growing would indicate a real problem.

### Prerequisites
- [ ] Access to the Docker host directly (this needs host-level disk info, not
      just container-level).

### Procedure

#### Step 1: Check Docker's own view of volume sizes
```
docker system df -v
```
**Expected result:** A table including each named volume
(`pgdata`, `redisdata`, `caddydata`, `caddyconfig`, `quarantine`) and its size.

#### Step 2: Check host-level disk usage
```
df -h
```
**Expected result:** Free space on whatever filesystem Docker's data root lives
on. Docker Desktop on Windows keeps its data inside a WSL2 virtual disk
(`%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx`) rather than directly on the
host filesystem - on Windows, also check that file's size specifically, since
`df -h` inside a Linux container/WSL shell won't show the Windows-side picture.

#### Step 3: Understand what's actually growing
- **`pgdata`** grows steadily and is expected to - `audit_events` is explicitly
  append-only (no updates, no deletes, by design), and every campaign attempt is
  a permanent row. This is normal growth, not a leak. If it grows unexpectedly
  fast, check for a runaway import or a bug creating duplicate rows rather than
  assuming it's fine.
- **`quarantine`** holds import source files. The happy path cleans up after
  itself (`import_service.cleanup_committed_source` runs after a successful
  commit), but a failed, abandoned, or never-decided import can leave its
  source file behind. If this volume is larger than the last few days of import
  activity would explain, look for orphaned files from incomplete imports.
- **`redisdata`**/**`caddydata`**/**`caddyconfig`** should stay small and
  roughly constant - TLS certificate/session state, not accumulating business
  data. Unexpected growth here is worth investigating on its own, not explained
  by normal usage.

### Verification
- [ ] You know which volume(s) are actually large, not just that disk is low
      overall.
- [ ] You've matched the growth against Step 3's expectations for that volume.

### Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| `pgdata` growing faster than expected | Runaway import, duplicate-row bug, or genuinely higher pilot volume than planned | Check recent `campaign.create`/`import.commit` audit events for volume; if it's a bug, this is a code investigation, not a disk-space fix |
| `quarantine` larger than recent import activity explains | Orphaned files from abandoned/failed imports | Identify and remove only files confirmed to belong to committed or explicitly cancelled imports - do not bulk-delete without checking against `import_jobs` state first |
| Host disk critically low with no time to investigate | - | Do not delete anything from `pgdata`, `redisdata`, or `caddydata` under time pressure - a wrong deletion here is a data-loss incident, not a disk-space fix. Escalate immediately; buying time (attaching more disk, moving Docker's data root) beats improvising a deletion |

### Escalation
Any disk situation urgent enough to consider deleting volume data - stop, do
not delete, escalate to IT/network engineer and Technical lead immediately.
See `## Contacts`.
