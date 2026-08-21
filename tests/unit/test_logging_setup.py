from __future__ import annotations

import json
import logging

from app.logging_setup import JsonFormatter, RedactionFilter


def _format(record: logging.LogRecord) -> dict[str, object]:
    assert RedactionFilter().filter(record)
    return json.loads(JsonFormatter().format(record))


def test_mapping_style_logging_preserves_mapping_and_redacts_number():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Import %(job)s failed for %(phone)s",
        args={"job": "abc", "phone": "+263 77 123 4567"},
        exc_info=None,
    )

    payload = _format(record)

    assert payload["message"] == "Import abc failed for [redacted-number]"


def test_positional_and_nested_logging_arguments_are_redacted():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Import payload: %s",
        args=({"contact": {"phone": "+263771234567"}},),
        exc_info=None,
    )

    payload = _format(record)

    assert "+263771234567" not in str(payload["message"])
    assert "[redacted-number]" in str(payload["message"])


def test_exception_text_is_redacted():
    try:
        raise ValueError("contact +263 77 123 4567 failed")
    except ValueError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Import failed",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )

    payload = _format(record)

    assert "+263 77 123 4567" not in str(payload["exc"])
    assert "[redacted-number]" in str(payload["exc"])
