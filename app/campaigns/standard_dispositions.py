"""The authoritative, code-owned seven-outcome disposition manifest (phase 4D
plan section 4).

Labels, stable codes, display order, and fixed behaviour flags are frozen
here and are never editable by a campaign manager. Only the two retry delays
are manager-adjustable, and only within MIN/MAX_RETRY_DELAY_MINUTES.

`next_action` values "retry_wait" and "immediate_redial" are stored on
installed rows but are not yet understood by app.work.service.complete_work_item
- that wiring is a later phase. Until then, a work item completed against
one of these two dispositions fails closed with DispositionMismatch rather
than silently behaving like "complete" or "requeue".
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_VERSION = 1

MIN_RETRY_DELAY_MINUTES = 5
MAX_RETRY_DELAY_MINUTES = 10_080  # 7 days

DEFAULT_NO_ANSWER_RETRY_MINUTES = 60
DEFAULT_UNAVAILABLE_RETRY_MINUTES = 30


@dataclass(frozen=True)
class StandardDisposition:
    """One locked entry of the version-1 manifest (plan section 4.1 table)."""

    order: int
    label: str
    stable_semantic_code: str
    next_action: str | None
    causes_dnc: bool
    requires_callback_time: bool
    counts_as_connected: bool
    terminal: bool
    immediate_redial: bool
    retry_delay_editable: bool
    default_retry_delay_minutes: int | None


STANDARD_DISPOSITIONS: tuple[StandardDisposition, ...] = (
    StandardDisposition(
        order=10,
        label="Connected",
        stable_semantic_code="connected",
        next_action="complete",
        causes_dnc=False,
        requires_callback_time=False,
        counts_as_connected=True,
        terminal=True,
        immediate_redial=False,
        retry_delay_editable=False,
        default_retry_delay_minutes=None,
    ),
    StandardDisposition(
        order=20,
        label="No Answer",
        stable_semantic_code="no_answer",
        next_action="retry_wait",
        causes_dnc=False,
        requires_callback_time=False,
        counts_as_connected=False,
        terminal=False,
        immediate_redial=False,
        retry_delay_editable=True,
        default_retry_delay_minutes=DEFAULT_NO_ANSWER_RETRY_MINUTES,
    ),
    StandardDisposition(
        order=30,
        label="Call Back Later",
        stable_semantic_code="callback_later",
        next_action=None,
        causes_dnc=False,
        requires_callback_time=True,
        counts_as_connected=False,
        terminal=False,
        immediate_redial=False,
        retry_delay_editable=False,
        default_retry_delay_minutes=None,
    ),
    StandardDisposition(
        order=40,
        label="Hung Up",
        stable_semantic_code="hung_up",
        next_action="immediate_redial",
        causes_dnc=False,
        requires_callback_time=False,
        counts_as_connected=False,
        terminal=False,
        immediate_redial=True,
        retry_delay_editable=False,
        default_retry_delay_minutes=None,
    ),
    StandardDisposition(
        order=50,
        label="Number Disconnected",
        stable_semantic_code="number_disconnected",
        next_action="complete",
        causes_dnc=False,
        requires_callback_time=False,
        counts_as_connected=False,
        terminal=True,
        immediate_redial=False,
        retry_delay_editable=False,
        default_retry_delay_minutes=None,
    ),
    StandardDisposition(
        order=60,
        label="Do Not Call",
        stable_semantic_code="explicit_dnc",
        next_action=None,
        causes_dnc=True,
        requires_callback_time=False,
        counts_as_connected=False,
        terminal=True,
        immediate_redial=False,
        retry_delay_editable=False,
        default_retry_delay_minutes=None,
    ),
    StandardDisposition(
        order=70,
        label="Unavailable",
        stable_semantic_code="unavailable",
        next_action="retry_wait",
        causes_dnc=False,
        requires_callback_time=False,
        counts_as_connected=False,
        terminal=False,
        immediate_redial=False,
        retry_delay_editable=True,
        default_retry_delay_minutes=DEFAULT_UNAVAILABLE_RETRY_MINUTES,
    ),
)

STANDARD_DISPOSITIONS_BY_CODE: dict[str, StandardDisposition] = {
    entry.stable_semantic_code: entry for entry in STANDARD_DISPOSITIONS
}

# Plain-language result text (plan 8.1's exact wording), shared by the agent
# workbench's per-option help and the manager policy panel's description
# column. "{minutes}" is substituted with the disposition's actual configured
# retry_delay_minutes at render time - the two editable outcomes are the only
# ones where the number isn't fixed by the manifest itself.
DISPOSITION_HELP_TEXT: dict[str, str] = {
    "connected": "This number will be completed.",
    "no_answer": "This number will return to the pool after {minutes} minutes.",
    "callback_later": "Choose the agreed future date and time.",
    "hung_up": "The outcome will be saved and this contact kept ready for immediate redial.",
    "number_disconnected": "This number will be completed and will not return to the pool.",
    "explicit_dnc": (
        "This records an explicit do-not-call request and suppresses this "
        "number across active campaigns."
    ),
    "unavailable": "This number will return to the pool after {minutes} minutes.",
}
