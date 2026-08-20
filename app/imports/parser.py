"""Bounded CSV/XLSX parsing.

Enforces row, column, and cell-length limits while streaming, and never evaluates
spreadsheet formulas: XLSX cells are read with cached values only (data_only=True), so a
formula's text is never seen or executed by this application.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from app.config import get_settings

_FORMULA_LIKE_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class ParseLimitExceeded(ValueError):
    pass


@dataclass(frozen=True)
class ParsedRow:
    row_number: int  # 1-based, header excluded
    values: dict[str, str]


def sanitize_text(value: str) -> str:
    """Neutralize spreadsheet-formula injection in a text value bound for storage."""
    if value and value[0] in _FORMULA_LIKE_PREFIXES:
        return "'" + value
    return value


def _truncate_cell(value: str, max_len: int) -> str:
    return value if len(value) <= max_len else value[:max_len]


def _validate_header(header: list[str]) -> list[str]:
    settings = get_settings()
    if len(header) > settings.upload_max_columns:
        raise ParseLimitExceeded(f"too many columns: {len(header)} > {settings.upload_max_columns}")
    return [h.strip() for h in header]


def parse_csv(path: Path) -> Iterator[ParsedRow]:
    settings = get_settings()
    with open(path, newline="", encoding="utf-8-sig", errors="strict") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return
        header = _validate_header(header)

        for row_number, raw_row in enumerate(reader, start=1):
            if row_number > settings.upload_max_rows:
                raise ParseLimitExceeded(f"too many rows: exceeds {settings.upload_max_rows}")
            if len(raw_row) > settings.upload_max_columns:
                raise ParseLimitExceeded(f"row {row_number} has too many columns")
            values = {
                header[i]: _truncate_cell(cell, settings.upload_max_cell_length)
                for i, cell in enumerate(raw_row)
                if i < len(header)
            }
            yield ParsedRow(row_number=row_number, values=values)


def parse_xlsx(path: Path) -> Iterator[ParsedRow]:
    settings = get_settings()
    # data_only=True: read cached values only. Formula text is never retrieved or evaluated.
    workbook = openpyxl.load_workbook(
        path, read_only=True, data_only=True, keep_links=False
    )
    try:
        sheet = workbook.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            return
        header = _validate_header([str(c) if c is not None else "" for c in header_row])

        for row_number, raw_row in enumerate(rows_iter, start=1):
            if row_number > settings.upload_max_rows:
                raise ParseLimitExceeded(f"too many rows: exceeds {settings.upload_max_rows}")
            if len(raw_row) > settings.upload_max_columns:
                raise ParseLimitExceeded(f"row {row_number} has too many columns")
            values: dict[str, str] = {}
            for i, cell in enumerate(raw_row):
                if i >= len(header):
                    break
                text = "" if cell is None else str(cell)
                values[header[i]] = _truncate_cell(text, settings.upload_max_cell_length)
            yield ParsedRow(row_number=row_number, values=values)
    finally:
        workbook.close()


def parse_file(path: Path, extension: str) -> Iterator[ParsedRow]:
    if extension == ".csv":
        yield from parse_csv(path)
    elif extension == ".xlsx":
        yield from parse_xlsx(path)
    else:
        raise ParseLimitExceeded(f"unsupported extension: {extension}")
