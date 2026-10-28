_ESCAPE_CHARS = frozenset("_*[]()~`>#+-=|{}.!")


def make_title(title: str) -> str:
    return "".join(f"\\{ch}" if ch in _ESCAPE_CHARS else ch for ch in title)
