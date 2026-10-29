"""#108: у каждой кнопки reply-клавиатур, которые показывает бот, есть обработчик.
Только разбор исходников (ast), без импорта aiogram и настроек бота.
Запуск: python -m pytest tests"""
import ast
import re

from _load import ROOT

KEYBOARDS_DIR = ROOT / "keyboards" / "reply_keyboards"
ADMIN_KEYBOARDS = KEYBOARDS_DIR / "admin_menu.py"
HANDLERS_DIR = ROOT / "handlers"


def _strings(node: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _keyboards(paths) -> dict[str, set[str]]:
    """{имя функции-клавиатуры: тексты её кнопок}."""
    out: dict[str, set[str]] = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            texts: set[str] = set()
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = _call_name(call)
                if name == "_kb":
                    for arg in call.args:
                        texts |= _strings(arg)
                elif name == "KeyboardButton":
                    for kw in call.keywords:
                        if kw.arg == "text":
                            texts |= _strings(kw.value)
            if texts:
                out[node.name] = texts
    return out


def _handler_sources() -> dict[str, str]:
    return {str(p): p.read_text(encoding="utf-8") for p in sorted(HANDLERS_DIR.rglob("*.py"))}


def _module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    consts: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            consts[node.target.id] = node.value
    return consts


def _handled_texts(sources: dict[str, str]) -> set[str]:
    """Тексты из фильтров обработчиков сообщений: F.text == "…", F.text.in_({…}), константы модуля."""
    texts: set[str] = set()
    for src in sources.values():
        tree = ast.parse(src)
        consts = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "message"):
                    continue
                for arg in dec.args:
                    texts |= _strings(arg)
                    for name in ast.walk(arg):
                        if isinstance(name, ast.Name) and name.id in consts:
                            texts |= _strings(consts[name.id])
    return texts


def _is_used(func_name: str, all_sources: str) -> bool:
    return re.search(rf"\b{re.escape(func_name)}\(", all_sources) is not None


def test_keyboards_are_parsed():
    # Защита от «пустой» проверки: если разбор сломается, тест ниже прошёл бы впустую
    kbs = _keyboards(sorted(KEYBOARDS_DIR.glob("*.py")))
    assert "👤 Меню пользователя" in kbs["reply_keyboard_for_admin"]
    assert "💰 Баланс" in kbs["menu_kb"]
    assert "⏸ На удержании" in _handled_texts(_handler_sources())


def test_every_shown_button_has_handler():
    sources = _handler_sources()
    all_sources = "\n".join(sources.values())
    handled = _handled_texts(sources)
    missing = [
        f"{kb}: {text}"
        for kb, texts in _keyboards(sorted(KEYBOARDS_DIR.glob("*.py"))).items()
        if _is_used(kb, all_sources)
        for text in sorted(texts - handled)
    ]
    assert missing == []


def test_every_admin_keyboard_is_shown():
    # #99: tariffs_kb лежала без дела, а её кнопки вели к дублю инлайн-панели тарифов
    all_sources = "\n".join(_handler_sources().values())
    unused = [kb for kb in _keyboards([ADMIN_KEYBOARDS]) if not _is_used(kb, all_sources)]
    assert unused == []
