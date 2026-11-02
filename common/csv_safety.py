"""Escape spreadsheet-sensitive text at the CSV boundary, never in stored data.

The leading apostrophe is an intentional export-only text marker. CSV quoting
alone does not prevent formula evaluation by spreadsheet applications. Re-saving
or transforming a file can remove the marker; transformed files are not covered.
"""
from datetime import date, datetime, time
from decimal import Decimal
from unicodedata import category


_FORMULA_STARTS = frozenset("=+-@＝＋－＠")


def safe_csv_cell(value):
    """Keep typed numbers intact; mark dangerous text, including numeric strings.

    A string such as '+98912...' or '-12.5' is text, not a typed number. It is
    deliberately escaped, rather than guessed or converted. None remains empty.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal)):
        return value
    if isinstance(value, (datetime, date, time)):
        value = value.isoformat()
    elif not isinstance(value, str):
        value = str(value)
    if not value:
        return value
    index = 0
    leading_control = False
    while index < len(value):
        character = value[index]
        is_control = ord(character) < 32 or ord(character) == 127
        if not (character.isspace() or is_control or category(character) == "Cf"):
            break
        leading_control = leading_control or is_control
        index += 1
    if leading_control or (index < len(value) and value[index] in _FORMULA_STARTS):
        return "'" + value
    return value


def safe_csv_row(row):
    """Make a new row; never modify the input row or an underlying model."""
    return [safe_csv_cell(value) for value in row]
