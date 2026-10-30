"""Имя и username пользователя обновляются, когда он их меняет в Telegram."""
from _load import load

ps = load("utils/profile_sync.py", "profile_sync_under_test")


def test_normalize_strips_and_drops_empty_username():
    assert ps.normalize("  D S ", "@affairme") == ("D S", "affairme")
    assert ps.normalize("D S", None) == ("D S", None)
    assert ps.normalize("D S", "") == ("D S", None)


def test_normalize_cuts_to_column_length():
    name, nick = ps.normalize("a" * 300, "b" * 300)
    assert len(name) == ps.MAX_LEN
    assert len(nick) == ps.MAX_LEN


def test_new_user_is_checked_once():
    cache = ps.ProfileCache()
    key = ps.normalize("D S", "affairme")
    assert cache.changed(1, key)
    cache.remember(1, key)
    assert not cache.changed(1, key)


def test_changed_username_is_detected():
    cache = ps.ProfileCache()
    cache.remember(1, ps.normalize("D S", "affairme"))
    assert cache.changed(1, ps.normalize("D S", "new_name"))
    assert cache.changed(1, ps.normalize("D S", None))
    assert cache.changed(1, ps.normalize("Dilshod", "affairme"))


def test_cache_size_is_bounded():
    cache = ps.ProfileCache(max_size=2)
    for uid in range(5):
        cache.remember(uid, ("n", None))
    assert len(cache._data) <= 2
