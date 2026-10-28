"""Тесты проверки реквизитов (#20). Запуск: python -m pytest tests"""
import importlib.util
from pathlib import Path

import pytest

# Модуль грузится по пути: импорт пакета utils тянет настройки бота (BOT_TOKEN и т.д.)
_PATH = Path(__file__).resolve().parent.parent / "utils" / "payment_details.py"


def _load():
    spec = importlib.util.spec_from_file_location("payment_details", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pd = _load()


@pytest.mark.parametrize("number", ["4111111111111111", "4111 1111 1111 1111", "5555-5555-5555-4444"])
def test_valid_cards(number):
    assert pd.normalize_card(number) == number.replace(" ", "").replace("-", "")


@pytest.mark.parametrize("number", ["4111111111111112", "411111111111111", "abcd111111111111", ""])
def test_invalid_cards(number):
    assert pd.normalize_card(number) is None


@pytest.mark.parametrize("raw", ["+998901234567", "998901234567", "+998 (93) 123-45-67"])
def test_valid_phones(raw):
    assert pd.normalize_uz_phone(raw) is not None


@pytest.mark.parametrize("raw", ["+998121234567", "+79001234567", "99890123456", "+9989012345678"])
def test_invalid_phones(raw):
    assert pd.normalize_uz_phone(raw) is None


def test_mask():
    assert pd.mask("4111111111111111") == "****1111"
    assert pd.mask(None) == "—"


def test_encrypt_roundtrip(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv("PAYOUT_ENCRYPTION_KEY", fernet.Fernet.generate_key().decode())
    module = _load()
    token = module.encrypt("4111111111111111")
    assert token.startswith("enc:") and "4111111111111111" not in token
    assert module.decrypt(token) == "4111111111111111"
    assert module.decrypt("8600123412341234") == "8600123412341234"  # старая незашифрованная заявка


def test_no_key_keeps_plain_text(monkeypatch):
    monkeypatch.delenv("PAYOUT_ENCRYPTION_KEY", raising=False)
    assert _load().encrypt("4111111111111111") == "4111111111111111"
