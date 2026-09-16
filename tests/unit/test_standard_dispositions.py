from __future__ import annotations

from app.campaigns import standard_dispositions as sd


def test_manifest_has_exactly_seven_entries_in_order():
    assert len(sd.STANDARD_DISPOSITIONS) == 7
    assert [entry.order for entry in sd.STANDARD_DISPOSITIONS] == [10, 20, 30, 40, 50, 60, 70]


def test_manifest_labels_and_codes_are_exact_and_in_plan_order():
    assert [entry.label for entry in sd.STANDARD_DISPOSITIONS] == [
        "Connected",
        "No Answer",
        "Call Back Later",
        "Hung Up",
        "Number Disconnected",
        "Do Not Call",
        "Unavailable",
    ]
    assert [entry.stable_semantic_code for entry in sd.STANDARD_DISPOSITIONS] == [
        "connected",
        "no_answer",
        "callback_later",
        "hung_up",
        "number_disconnected",
        "explicit_dnc",
        "unavailable",
    ]


def test_by_code_lookup_covers_every_entry_with_no_duplicates():
    assert len(sd.STANDARD_DISPOSITIONS_BY_CODE) == 7
    assert set(sd.STANDARD_DISPOSITIONS_BY_CODE) == {
        entry.stable_semantic_code for entry in sd.STANDARD_DISPOSITIONS
    }


def test_only_explicit_dnc_causes_dnc():
    causes_dnc = {e.stable_semantic_code for e in sd.STANDARD_DISPOSITIONS if e.causes_dnc}
    assert causes_dnc == {"explicit_dnc"}


def test_only_callback_later_requires_callback_time():
    requires_callback = {
        e.stable_semantic_code for e in sd.STANDARD_DISPOSITIONS if e.requires_callback_time
    }
    assert requires_callback == {"callback_later"}


def test_only_hung_up_is_immediate_redial():
    immediate_redial = {
        e.stable_semantic_code for e in sd.STANDARD_DISPOSITIONS if e.immediate_redial
    }
    assert immediate_redial == {"hung_up"}


def test_only_connected_counts_as_connected():
    connected = {
        e.stable_semantic_code for e in sd.STANDARD_DISPOSITIONS if e.counts_as_connected
    }
    assert connected == {"connected"}


def test_terminal_outcomes_are_exactly_connected_disconnected_and_dnc():
    terminal = {e.stable_semantic_code for e in sd.STANDARD_DISPOSITIONS if e.terminal}
    assert terminal == {"connected", "number_disconnected", "explicit_dnc"}


def test_only_no_answer_and_unavailable_have_editable_retry_delays():
    editable = {
        e.stable_semantic_code for e in sd.STANDARD_DISPOSITIONS if e.retry_delay_editable
    }
    assert editable == {"no_answer", "unavailable"}
    non_editable_defaults = {
        e.stable_semantic_code: e.default_retry_delay_minutes
        for e in sd.STANDARD_DISPOSITIONS
        if not e.retry_delay_editable
    }
    assert all(value is None for value in non_editable_defaults.values())


def test_default_retry_delays_match_plan_deployment_defaults():
    assert sd.STANDARD_DISPOSITIONS_BY_CODE["no_answer"].default_retry_delay_minutes == 60
    assert sd.STANDARD_DISPOSITIONS_BY_CODE["unavailable"].default_retry_delay_minutes == 30
    assert sd.DEFAULT_NO_ANSWER_RETRY_MINUTES == 60
    assert sd.DEFAULT_UNAVAILABLE_RETRY_MINUTES == 30


def test_retry_delay_bounds_are_five_minutes_to_seven_days():
    assert sd.MIN_RETRY_DELAY_MINUTES == 5
    assert sd.MAX_RETRY_DELAY_MINUTES == 7 * 24 * 60


def test_next_action_pending_codes_are_isolated_to_no_answer_unavailable_and_hung_up():
    # "retry_wait" and "immediate_redial" are not yet understood by
    # app.work.service.complete_work_item - confirming exactly which three
    # codes carry them pins down the fail-closed boundary for a later phase.
    pending = {
        e.stable_semantic_code: e.next_action
        for e in sd.STANDARD_DISPOSITIONS
        if e.next_action in ("retry_wait", "immediate_redial")
    }
    assert pending == {
        "no_answer": "retry_wait",
        "unavailable": "retry_wait",
        "hung_up": "immediate_redial",
    }


def test_connected_and_disconnected_use_the_existing_complete_action():
    assert sd.STANDARD_DISPOSITIONS_BY_CODE["connected"].next_action == "complete"
    assert sd.STANDARD_DISPOSITIONS_BY_CODE["number_disconnected"].next_action == "complete"


def test_callback_later_and_dnc_have_no_next_action():
    # Their existing requires_callback_time/causes_dnc branches in
    # complete_work_item() run before next_action is ever consulted.
    assert sd.STANDARD_DISPOSITIONS_BY_CODE["callback_later"].next_action is None
    assert sd.STANDARD_DISPOSITIONS_BY_CODE["explicit_dnc"].next_action is None
