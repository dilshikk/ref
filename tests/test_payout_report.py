"""Тесты отчёта о выплате (#36). Запуск: python -m pytest tests"""
from datetime import datetime

import pytest
from _load import load

pr = load("utils/payout_report.py", "payout_report_under_test")

NOW = datetime(2026, 10, 29, 12, 30, 45)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-1001234567890", -1001234567890),
        ("  -1001234567890  ", -1001234567890),
        ("@payouts", "@payouts"),
        ("@", None),
        ("@my channel", None),
        ("-12345", None),         # не супергруппа/канал
        ("-100abc", None),
        ("1001234567890", None),
        ("https://t.me/payouts", None),
        ("", None),
    ],
)
def test_parse_payment_chat_id(value, expected):
    assert pr.parse_payment_chat_id(value) == expected


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"payment_chat_id": -1001234567890}, -1001234567890),
        ({"payment_chat_id": "-1001234567890"}, -1001234567890),
        ({"payment_chat_id": "@payouts", "payment_channel": "@other"}, "@payouts"),
        # payment_chat_id не задан или кривой — берём payment_channel
        ({"payment_channel": "@other"}, "@other"),
        ({"payment_chat_id": "не задан", "payment_channel": "-1009"}, -1009),
        # ссылка-приглашение отправкой не поддерживается
        ({"payment_channel": "https://t.me/payouts"}, None),
        # число допустимо только в payment_chat_id
        ({"payment_channel": -1009}, None),
        ({"payment_chat_id": True}, None),
        ({}, None),
    ],
)
def test_resolve_payment_report_target(config, expected):
    assert pr.resolve_payment_report_target(config) == expected


@pytest.mark.parametrize(
    ("card", "expected"),
    [("8600123412345678", "****5678"), ("1234", "****1234"), ("123", "-"), (None, "-"), ("", "-")],
)
def test_mask_card(card, expected):
    assert pr.mask_card(card) == expected


def test_report_never_contains_full_card():
    text = pr.build_payout_report_text(
        {"id": 7, "user_id": 42, "full_name": "Ali", "amount": 5000, "card_number": "8600123412345678"},
        admin_id=1,
        include_receipt=True,
        now=NOW,
    )
    assert "8600123412345678" not in text
    assert "****5678" in text
    assert "<code>7</code>" in text
    assert "tg://user?id=42" in text
    assert "<b>5000</b>" in text
    assert "2026-10-29 12:30:45" in text
    assert "приложен" in text and "не приложен" not in text


def test_report_escapes_user_name():
    text = pr.build_payout_report_text(
        {"id": 1, "user_id": 2, "full_name": "<b>Bob</b> & Co", "amount": 1},
        admin_id=1,
        include_receipt=False,
        now=NOW,
    )
    assert "&lt;b&gt;Bob&lt;/b&gt; &amp; Co" in text
    assert "<b>Bob</b>" not in text
    assert "не приложен" in text


def test_report_handles_missing_fields():
    text = pr.build_payout_report_text({}, admin_id=1, include_receipt=False, now=NOW)
    assert "<b>-</b>" in text          # сумма
    assert "<code>-</code>" in text    # заявка / карта
