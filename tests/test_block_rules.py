"""Админа нельзя заблокировать из профиля."""
import ast

from _load import ROOT, load

rules = load("utils/block_rules.py", "block_rules")

ADMINS = [111, 222]


def test_admin_cannot_be_blocked():
    assert rules.can_block(111, ADMINS) is False
    assert rules.can_block(333, ADMINS) is True


def test_block_button_hidden_for_admin():
    assert rules.show_block_button(111, False, ADMINS) is False
    assert rules.show_block_button(333, False, ADMINS) is True


def test_unblock_button_shown_for_already_blocked_admin():
    assert rules.show_block_button(111, True, ADMINS) is True


def test_block_handler_checks_admins():
    # Кнопку можно нажать в старом сообщении, поэтому проверка нужна и в самом обработчике
    tree = ast.parse((ROOT / "handlers" / "private" / "user_management.py").read_text(encoding="utf-8"))
    block_user = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "block_user"
    )
    assert "can_block" in ast.unparse(block_user)
