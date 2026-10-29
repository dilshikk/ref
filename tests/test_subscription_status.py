"""Тесты итога проверки подписки (#24). Запуск: python -m pytest tests"""
from _load import load

ss = load("utils/subscription_status.py", "subscription_status_under_test")
S = ss.SubscriptionStatus


def test_all_members():
    assert ss.combine(["member", "member"]) is S.SUBSCRIBED


def test_no_channels_means_subscribed():
    assert ss.combine([]) is S.SUBSCRIBED


def test_not_member_wins_over_error():
    assert ss.combine(["error", "not_member"]) is S.NOT_SUBSCRIBED


def test_network_error_is_not_counted_as_subscription():
    # Раньше любая ошибка = подписан. Теперь — «не удалось проверить»
    assert ss.combine(["member", "error"]) is S.UNKNOWN


def test_misconfigured_channel_skipped_for_access():
    assert ss.combine(["member", "config"]) is S.SUBSCRIBED


def test_misconfigured_channel_blocks_money_in_strict_mode():
    assert ss.combine(["member", "config"], strict=True) is S.NOT_SUBSCRIBED
    assert ss.combine(["member", "error"], strict=True) is S.UNKNOWN
