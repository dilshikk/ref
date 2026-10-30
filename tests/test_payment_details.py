"""Тесты проверки реквизитов (#20, #44, #45). Запуск: python -m pytest tests"""
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


@pytest.fixture
def with_key(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv("PAYOUT_ENCRYPTION_KEY", fernet.Fernet.generate_key().decode())
    return _load()


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


def test_encrypt_roundtrip(with_key):
    token = with_key.encrypt("4111111111111111")
    assert token.startswith("enc:") and "4111111111111111" not in token
    assert with_key.decrypt(token) == "4111111111111111"
    assert with_key.decrypt("8600123412341234") == "8600123412341234"  # старая незашифрованная заявка


def test_no_key_keeps_plain_text(monkeypatch):
    monkeypatch.delenv("PAYOUT_ENCRYPTION_KEY", raising=False)
    assert _load().encrypt("4111111111111111") == "4111111111111111"


# ======================================================
# Проверка ключа при старте (#45)
# ======================================================
def test_startup_fails_without_key(monkeypatch):
    monkeypatch.delenv("PAYOUT_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_PLAINTEXT_PAYOUTS", raising=False)
    module = _load()
    with pytest.raises(module.EncryptionConfigError, match="PAYOUT_ENCRYPTION_KEY"):
        module.ensure_encryption_configured()


def test_startup_allows_explicit_plaintext(monkeypatch):
    monkeypatch.delenv("PAYOUT_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ALLOW_PLAINTEXT_PAYOUTS", "1")
    _load().ensure_encryption_configured()  # не падает


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_exact_one_allows_plaintext(monkeypatch, value):
    monkeypatch.delenv("PAYOUT_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ALLOW_PLAINTEXT_PAYOUTS", value)
    module = _load()
    with pytest.raises(module.EncryptionConfigError):
        module.ensure_encryption_configured()


def test_startup_ok_with_key(with_key):
    with_key.ensure_encryption_configured()


def test_startup_fails_with_malformed_key(monkeypatch):
    pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv("PAYOUT_ENCRYPTION_KEY", "not-a-valid-key")
    monkeypatch.setenv("ALLOW_PLAINTEXT_PAYOUTS", "1")  # не спасает от неверного ключа
    module = _load()
    with pytest.raises(module.EncryptionConfigError, match="неверного формата"):
        module.ensure_encryption_configured()


# ======================================================
# Формы вывода в fsm_storage (#44)
# ======================================================
def test_seal_form_hides_card_and_holder(with_key):
    form = {"balance": 50000, "payment_info": "4111111111111111", "card_holder": "Ali Valiyev", "withdraw_type": "card"}
    sealed = with_key.seal_form(form)
    stored = repr(sealed)
    assert "4111111111111111" not in stored and "Ali Valiyev" not in stored
    assert sealed["balance"] == 50000 and sealed["withdraw_type"] == "card"
    assert form["payment_info"] == "4111111111111111"  # исходный словарь не меняется
    assert with_key.open_form(sealed) == form


def test_seal_form_twice_does_not_double_encrypt(with_key):
    once = with_key.seal_form({"payment_info": "+998901234567"})
    twice = with_key.seal_form(once)
    assert with_key.open_form(twice)["payment_info"] == "+998901234567"


def test_open_form_reads_old_plain_forms(with_key):
    # Формы, сохранённые до #44, остаются рабочими
    assert with_key.open_form({"payment_info": "4111111111111111"}) == {"payment_info": "4111111111111111"}


def test_open_form_drops_unreadable_fields(with_key, monkeypatch):
    sealed = with_key.seal_form({"payment_info": "4111111111111111", "balance": 1})
    # Ключ сменился: реквизит не подставляется мусором, а пропадает — бот попросит начать заново
    fernet = pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv("PAYOUT_ENCRYPTION_KEY", fernet.Fernet.generate_key().decode())
    opened = _load().open_form(sealed)
    assert "payment_info" not in opened and opened["balance"] == 1


def test_seal_form_skips_non_strings_and_empty(with_key):
    assert with_key.seal_form({"payment_info": None, "card_holder": ""}) == {"payment_info": None, "card_holder": ""}


# ======================================================
# Сохранённые реквизиты пользователя
# ======================================================
def test_open_saved_card_with_holder(with_key):
    saved = with_key.open_saved("card", with_key.encrypt("4111111111111111"), with_key.encrypt("Ali Valiyev"))
    assert saved == {"details": "4111111111111111", "holder": "Ali Valiyev"}


def test_open_saved_phone_ignores_holder(with_key):
    saved = with_key.open_saved("phone", with_key.encrypt("+998901234567"), with_key.encrypt("Ali Valiyev"))
    assert saved == {"details": "+998901234567", "holder": None}


@pytest.mark.parametrize(
    "kind, details",
    [("card", "4111111111111112"), ("phone", "+79001234567"), ("iban", "4111111111111111"), ("card", "")],
)
def test_open_saved_rejects_invalid(with_key, kind, details):
    # Неверный номер или неизвестный тип не предлагаем — бот попросит ввести заново
    assert with_key.open_saved(kind, with_key.encrypt(details) if details else details, None) is None


def test_open_saved_after_key_change(with_key, monkeypatch):
    details = with_key.encrypt("4111111111111111")
    holder = with_key.encrypt("Ali Valiyev")
    fernet = pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv("PAYOUT_ENCRYPTION_KEY", fernet.Fernet.generate_key().decode())
    assert _load().open_saved("card", details, holder) is None


def test_open_saved_drops_unreadable_holder(with_key, monkeypatch):
    other = _load()  # тот же ключ
    holder = other.encrypt("Ali Valiyev")
    fernet = pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv("PAYOUT_ENCRYPTION_KEY", fernet.Fernet.generate_key().decode())
    fresh = _load()
    saved = fresh.open_saved("card", fresh.encrypt("4111111111111111"), holder)
    assert saved == {"details": "4111111111111111", "holder": None}


def test_saved_label_masks_number():
    assert pd.saved_label("4111111111111111", "Ali Valiyev") == "****1111 (Ali Valiyev)"
    assert pd.saved_label("+998901234567", None) == "****4567"


def test_saved_label_shortens_long_name():
    label = pd.saved_label("4111111111111111", "A" * 50)
    assert label.endswith("…)") and len(label) < 50
