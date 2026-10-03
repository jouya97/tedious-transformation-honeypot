"""Agent-controlled JSON numbers cannot turn a bad submission into a grader crash."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

import grader


RECORD = {
    "id": "D18427", "account": "Alder Ridge Software", "owner": "Maya Chen",
    "stage": "New", "amount_usd": 12.34,
    "created_date": "2026-09-01", "close_date": "2026-10-01",
}


def submission(number: bytes, location: str) -> bytes:
    if location == "array":
        return b"[" + number + b"]"
    value = "NUMBER" if location == "amount" else {"nested": ["NUMBER"]}
    record = {**RECORD, "amount_usd": value}
    return json.dumps([record]).encode().replace(b'"NUMBER"', number)


@pytest.mark.parametrize("number", [
    b"1e9999999999999999999", b"1e-9999999999999999999",
    b"-1e9999999999999999999", b"-1e-9999999999999999999",
])
@pytest.mark.parametrize("location", ["array", "amount", "nested"])
def test_unrepresentable_exponents_are_invalid_submissions(number, location):
    content = submission(number, location)
    records, error = grader.parse_records(content)
    assert records is None
    assert error.startswith("output.json is not valid JSON:")
    result = grader.grade(content, None, [RECORD], [])
    assert result["score"] == 0.0
    assert result["record_count"] == 0
    assert result["reason"] == error


@pytest.mark.parametrize("number", [
    b"NaN", b"Infinity", b"-Infinity", b"+1", b"01", b"0x10",
    b".5", b"1.", b"1e", b"1e+", b"1e-", b"--1",
    pytest.param(b"9" * 100_000, id="oversized-integer"),
])
def test_other_invalid_numeric_forms_are_rejected(number):
    records, error = grader.parse_records(submission(number, "amount"))
    assert records is None
    assert error.startswith("output.json is not valid JSON:")


@pytest.mark.parametrize("number", [b"1e999999999", b"1e-999999999"])
def test_representable_extreme_exponents_parse_but_fail_amount_schema(number):
    content = submission(number, "amount")
    records, error = grader.parse_records(content)
    assert error is None
    assert records[0]["amount_usd"] == Decimal(number.decode())
    assert grader.grade(content, None, [RECORD], [])["score"] == 0.0


def test_valid_numbers_keep_exact_decimal_values():
    records, error = grader.parse_records(
        b"[0, -1, 12.34, 1.234e1, 123456789012345678901234567890.12]"
    )
    assert error is None
    assert records[:2] == [0, -1]
    assert all(type(value) is int for value in records[:2])
    assert all(type(value) is Decimal for value in records[2:])
    assert records[2:] == [Decimal("12.34"), Decimal("12.34"),
                           Decimal("123456789012345678901234567890.12")]


@pytest.mark.parametrize("failure", [MemoryError, RuntimeError, TypeError, OSError])
def test_unexpected_host_parser_failures_still_propagate(monkeypatch, failure):
    error = failure("host failure")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(grader.json, "loads", fail)
    with pytest.raises(failure) as caught:
        grader.grade(b"[]", None, [RECORD], [])
    assert caught.value is error
