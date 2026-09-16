# Activation code handoff runbook

Implements the admin user management plan's offline activation procedure
(`docs/architecture/PHASE-4C-ADMIN-USER-MANAGEMENT-PLAN.md`, sections 4.2-4.3, 9, 15).
CipherContact sends no email or SMS (ADR-017), so every activation code reaches its
person through a human-verified, one-time handoff - never a bulk file.

## Who this is for

Anyone holding `RESET_USER_AUTH` (today, Super Administrator). Issuing an activation
code, resetting a password, or resetting MFA are separate capabilities from workforce
management - a Manager, Team Leader, or Team Captain does not get them merely by
holding workforce-appointment authority.

## Issue one person's activation code

1. Confirm the person's identity through the organization's approved support identity
   check. Never accept a password, activation code, or authenticator code as proof of
   identity - those are exactly what a lockout or impersonation attempt would lack.
2. Open **User administration** (`/admin/users`), search for the person by name,
   email, or workforce ID, and open their row.
3. Confirm the state shown is `Activation not issued`, `Activation code active`, or
   `Activation code expired`. If it is `Ready`, the person already has a password -
   use **Reset password** instead, not activation issuance.
4. Select **Issue activation code**. If more than a few minutes have passed since your
   last identity confirmation at Account security, you will be sent there first.
5. The result page shows the code once, with its exact expiry. It is never shown
   again and never appears in a URL, log, or audit record.
6. Hand the code to the person directly through the organization's approved private
   channel (in person, a controlled call back to a known number, or an equivalent
   verified channel) - never email, chat, a shared document, or a ticket.
7. The person opens `/activate`, enters the code, sets their own password, signs in,
   and enrolls MFA. Confirm their state changes to `Ready` and their last login is
   populated before closing out the request.

## Expired or lost code

A code is single-use and expires 24 hours after issue. If it expired, was never
delivered, or may have been seen by the wrong person:

1. Return to the person's row in User administration and select **Issue activation
   code** again. Issuing a replacement immediately invalidates every prior unused
   code for that person - there is no separate "revoke" step.
2. If the code was delivered to the wrong person or exposed, treat it as a
   confidentiality incident: record what happened, who saw it, and confirm the
   replacement reached the correct person before considering it closed.
3. Do not try to work around the 24-hour expiry or recreate the person's identity to
   get a fresh window - reissuing is the entire procedure.

## Never do this

- Never export, print, or copy multiple people's activation codes into one file,
  spreadsheet, ticket, or chat thread. CipherContact deliberately does not provide a
  bulk code export (plan 4.3) - a hand-built one recreates the exact secrets database
  the design avoids.
- Never place a code in a URL, bookmark, saved search, or browser autofill.
- Never accept a code, password, or authenticator code as proof of someone's
  identity - verify identity independently first, every time.
- Never reset your own password or MFA through the administrative route; it is
  blocked server-side. Escalate to a second authorized administrator instead.

## Bulk-created users awaiting activation

When a bulk workforce import runs with `deferred_bulk_activation_enabled` on, created
identities commit with **no** activation code - each shows `Activation not issued`
until an administrator issues one individually, when that person is actually ready to
onboard. This is deliberate: committing hundreds of short-lived codes nobody may be
ready to use is the bulk-secrets-file problem in a different shape.

To find them: open **User administration**, filter **Activation state** to
`Activation not issued`, and work the list - confirm identity, issue, hand off - one
person at a time, following the steps above.

## Account-recovery escalation

- A locked-out Team Leader, Team Captain, Manager, Agent, or Viewer: any holder of
  `RESET_USER_AUTH` follows **Issue one person's activation code** (for a pending
  account) or the password/MFA reset actions on the same page (for an already-active
  account).
- A locked-out Super Administrator, or suspected compromise of a `RESET_USER_AUTH`
  holder: use the organization's separate emergency-access procedure with a second
  authorized person. The in-application administrative route cannot be used to
  recover the only account that can grant it.
- A password or MFA reset always revokes the target's active sessions immediately and
  is recorded as an audit event naming the actor, target, and outcome - never the
  secret itself. Confirm the audit event before closing an escalation.

## Feature flag

`deferred_bulk_activation_enabled` (`/flags`) controls whether bulk `users` imports
issue codes immediately at commit (off, the default) or defer to individual issuance
(on). Toggling it requires `MANAGE_ROLES` and a recorded reason, and is itself an
audited event. Disabling it after enabling does not affect users already created
either way - it only changes what the next commit does.
