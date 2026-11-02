"""
Terminal I/O layer: colours, prompts (ask), file dialog, path resolving, language settings, menu pieces.
Слой терминала: цвета, вопросы (ask), диалог выбора файла, пути, язык, части меню.
The only layer (together with flows) that prints and reads input.
"""

import builtins as _bi
import importlib.util
import os
import subprocess
import sys

from .sc_format import SEARCH_DIRS, UserError, detect_sc_package, fmt_size, set_notifier, unquote
from .i18n import current_language, set_language, tr


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_DIR = os.path.join(APP_DIR, "input")
OUT_DIR = os.path.join(APP_DIR, "output")

REQUIRED = [("PIL", "pillow"), ("flatbuffers", "flatbuffers"), ("zstandard", "zstandard")]


# ================================================================ оформление

USE_COLOR = True


def enable_ansi():
    global USE_COLOR
    if os.name == "nt":
        os.system("")  # включает поддержку ANSI-цветов в консоли Windows 10+
    USE_COLOR = not os.environ.get("NO_COLOR")


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def cyan(t): return paint(t, "36;1")


def green(t): return paint(t, "32;1")


def yellow(t): return paint(t, "33")


def red(t): return paint(t, "31;1")


def gray(t): return paint(t, "90")


def bold(t): return paint(t, "1")


def _settings_path():
    return os.path.join(APP_DIR, "settings.json")


def lang_detect():
    for i, a in enumerate(sys.argv):
        if a.startswith("--lang="):
            return a.split("=", 1)[1].strip().lower()[:2]
        if a == "--lang" and i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip().lower()[:2]
    env = os.environ.get("SCHUB_LANG", "").strip().lower()[:2]
    if env:
        return env
    try:
        import json as _json
        with open(_settings_path(), encoding="utf-8") as fh:
            v = str(_json.load(fh).get("lang", "")).lower()[:2]
        if v:
            return v
    except (OSError, ValueError):
        pass
    try:
        import locale
        loc = (locale.getdefaultlocale()[0] or "").lower()
    except Exception:
        loc = ""
    return "ru" if loc.startswith("ru") or loc.startswith("be") or loc.startswith("uk") else "en"


def lang_set(code, save=True):
    set_language(code)
    if save:
        try:
            import json as _json
            with open(_settings_path(), "w", encoding="utf-8") as fh:
                _json.dump({"lang": current_language()}, fh)
        except OSError:
            pass


def print(*args, **kwargs):          # noqa: A001 - перевод вывода
    _bi.print(*[tr(a) if isinstance(a, str) else a for a in args], **kwargs)


def input(prompt=""):                # noqa: A001 - перевод приглашений
    return _bi.input(tr(prompt))


WIDTH = 60


def banner():
    print(cyan("╔" + "═" * (WIDTH - 2) + "╗"))
    title = tr("SC HUB  ·  инструменты для .sc")
    pad = WIDTH - 2 - len(title)
    print(cyan("║") + " " * (pad // 2) + bold(title) + " " * (pad - pad // 2) + cyan("║"))
    print(cyan("╚" + "═" * (WIDTH - 2) + "╝"))


def line(char="─"):
    print(gray(char * WIDTH))


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def ask(prompt, default=""):
    shown = f" {yellow('[' + str(default) + ']')}" if default != "" else ""
    try:
        raw = input(f"{prompt}{shown}: ").strip()
    except EOFError:
        raw = ""
    return raw if raw else str(default)


def ask_yes_no(prompt, default=False):
    raw = ask(prompt + " (y/n)", "y" if default else "n").lower()
    if raw in ("y", "yes", "д", "да", "1"):
        return True
    if raw in ("n", "no", "н", "нет", "0"):
        return False
    return default


def pause():
    try:
        input(gray("\nEnter - вернуться в меню..."))
    except EOFError:
        pass


def ensure_dirs():
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    SEARCH_DIRS[:] = [IN_DIR, OUT_DIR]
    set_notifier(ui_notify)


def show_line(text):
    """Строка из логики: предупреждения («⚠») жёлтым."""
    print(yellow(text) if text.lstrip().startswith("⚠") else text)


def ui_notify(msg):
    """Сообщения о ходе работы, которые присылает логика (notify)."""
    head = msg.lstrip()
    print(yellow(msg) if head.startswith("⚠") else green(msg) if head.startswith("✔") else gray(msg))


def saved(path):
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    print(green("  ✔ Сохранено: ") + os.path.abspath(path) + gray(f"  ({fmt_size(size)})"))


# ================================================================ пути

def file_dialog(title, patterns):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title=title, filetypes=patterns)
        root.destroy()
        return path
    except Exception:
        return ""


LAST_INPUTS = []


def resolve_input(prompt, default_name, dialog_patterns, allow_dir=False):
    raw = unquote(ask(f"{prompt} {gray('(? - выбрать в окне)')}", default_name))
    if raw == "?":
        raw = file_dialog(prompt, dialog_patterns)
        if not raw:
            raise UserError("Файл не выбран")
    candidates = [raw]
    if not os.path.isabs(raw):
        candidates = [os.path.join(IN_DIR, raw), os.path.join(OUT_DIR, raw),
                      os.path.join(APP_DIR, raw), os.path.abspath(raw)]
    for cand in candidates:
        if os.path.isfile(cand) or (allow_dir and os.path.isdir(cand)):
            LAST_INPUTS.append(cand)
            return cand
    raise UserError(f"Не нашёл «{raw}». Положи файл в папку input:\n  {IN_DIR}")


def resolve_output(raw, ext):
    raw = unquote(raw)
    if not raw.lower().endswith(ext):
        raw += ext
    if not os.path.isabs(raw) and not os.path.dirname(raw):
        raw = os.path.join(OUT_DIR, raw)
    parent = os.path.dirname(os.path.abspath(raw))
    os.makedirs(parent, exist_ok=True)
    return raw


def resolve_out_dir(raw):
    raw = unquote(raw)
    if not os.path.isabs(raw):
        first = raw.replace("\\", "/").split("/")[0].lower()
        # "output" и "output/что-то" - от папки хаба, любое другое имя - внутри output/
        raw = os.path.join(APP_DIR if first == "output" else OUT_DIR, raw)
    os.makedirs(raw, exist_ok=True)
    return raw


def print_sc_package(path):
    pkg = detect_sc_package(path)
    print("  Связанный SC-пакет:")
    print(gray("    основной: " + os.path.basename(pkg['main'])))
    if pkg['tex']:
        print(gray("    внешний _tex.sc: " + os.path.basename(pkg['tex'])))
    if pkg['variants']:
        for fp, kind in pkg['variants']:
            print(gray(f"    {kind}: {os.path.basename(fp)}"))
    if len(pkg['files']) == 1:
        print(gray("    дополнительных файлов рядом не найдено"))
    return pkg


def ensure_deps():
    missing = []
    for mod, pkg in REQUIRED:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if not missing:
        return
    print(yellow("Ставлю недостающие библиотеки: " + ", ".join(missing)))
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                           "--disable-pip-version-check", *missing])
    for mod, _pkg in REQUIRED:
        importlib.import_module(mod)
