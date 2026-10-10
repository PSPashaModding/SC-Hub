#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SC HUB - console menu for working with Supercell .sc files (SC1 and SC2).
SC HUB - консольное меню для работы с .sc

Run / Запуск: start.bat (Windows), ./start.sh (Linux/macOS) or `python sc_hub.py [--lang en|ru]`.
Folders next to the script are created automatically / Папки рядом со скриптом создаются сами:
  input/   - your source files / сюда кладёшь исходники
  output/  - results / сюда сохраняется результат
Press Enter on any question to accept the value in [brackets] / На любой вопрос можно просто нажать Enter.
Interface language / Язык интерфейса: ru, en (menu item [L], --lang, SCHUB_LANG).

Menu items / Пункты (see README.md / README.ru.md for details):
  1  PNG -> SC   (масштаб клипа, точка привязки, пакетная конвертация папки)
  2  GIF -> SC   (любой размер как у PNG, прозрачность, масштаб, привязка)
  3  Разобрать .sc (сводка + подробный дамп в .txt; SC1 и SC2)
  4  Переименовать ExportName'ы (одно имя, замена части имени, список из файла; SC1 и SC2)
  5  Размер (коэффициент) и положение (px по X и Y) MovieClip'ов и Shape'ов; видны исходные fps и координаты
  6  FPS мувиклипов (одному, всем экспортным или списком)
  7  Разбор .sc (SC1 и SC2) в JSON (или msgpack, если JSON больше 20 МБ) + PNG-атласы и обратная сборка .sc
  8  Правка TextField'ов: поиск по тексту или сразу по ID, правка текста, размера шрифта, рамки (SC2)
  9  Перестроить атласы с нуля: каждый объект (фрагмент текстуры шейпа) упаковывается заново, атласы до 4096 по
     стороне (вторая сторона по заполнению), объекты, недостижимые от ExportName'ов, выбрасываются
  10 Удалить ExportName'ы вместе с клипами и шейпами, которые нужны только им (SC1 и SC2)
  11 Открыть папку output
  12 Информация об объекте по ID
  13 Найти связанный SC-пакет
  ...
  19 Создать фон меню (градиент + символы + верхняя часть / gif)
  20 Загрузочный экран: разобрать loading.sc на части (PNG + шаблон), собрать со своим фоном, лого игры и
     лого SUPERCELL (в том числе плавный переход SUPERCELL -> своё лого)
  21 Части .sc: разобрать любой .sc на PNG-части и собрать обратно после правок
"""
import os
import re
import sys
import math
import time
import shutil
import struct
import base64
import importlib
import importlib.util
import subprocess
from types import SimpleNamespace

APP_DIR = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(APP_DIR, "input")
OUT_DIR = os.path.join(APP_DIR, "output")

REQUIRED = [("PIL", "pillow"), ("flatbuffers", "flatbuffers"), ("zstandard", "zstandard")]

PAGE_MAX_DIM = 4092
HARD_RAW_LIMIT = 1_900_000_000
COMPRESS_LEVEL = 10
REPACK_COMPRESS_LEVEL = 19   # при пересборке готового файла: 17 МБ жмутся ~4 с, размер меньше оригинала


def zstd_pack(raw):
    """Сжатие при пересборке. Маленькие файлы пробуем несколькими уровнями и берём самый короткий
    результат (размер zstd на уровне 22 заметно скачет от одного байта данных), большие жмём уровнем 19."""
    import zstandard
    levels = (22, 19, 15) if len(raw) < 4 * 1024 * 1024 else (REPACK_COMPRESS_LEVEL,)
    best = None
    for lv in levels:
        out = zstandard.ZstdCompressor(level=lv, write_content_size=True).compress(raw)
        if best is None or len(out) < len(best):
            best = out
    return best
FRAMERATE = 30
DEFAULT_EXPORT_NAME = "img"

PIVOTS = {"center": (0.5, 0.5), "top": (0.5, 0.0), "bottom": (0.5, 1.0), "left": (0.0, 0.5),
          "right": (1.0, 0.5), "tl": (0.0, 0.0), "tr": (1.0, 0.0), "bl": (0.0, 1.0), "br": (1.0, 1.0)}
PIVOT_ORDER = ["center", "bottom", "top", "left", "right", "tl", "tr", "bl", "br"]

_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z",
       "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
       "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh",
       "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}


class UserError(Exception):
    pass


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


# ================================================================ язык интерфейса (ru / en)
#
# Тексты в коде написаны по-русски. Английский интерфейс - таблица I18N_EN (русский фрагмент -> английский),
# она подставляется в каждую выводимую строку и в приглашения ввода. Язык: --lang ru|en, переменная SCHUB_LANG,
# пункт меню [L] (запоминается в settings.json рядом со скриптом), иначе по языку системы.
# Interface texts are written in Russian in the code; English UI is the I18N_EN table (Russian fragment -> English)
# applied to every printed line and every prompt. Language: --lang ru|en, SCHUB_LANG, menu item [L]
# (saved to settings.json next to the script), otherwise the system language.

import builtins as _bi

LANG = "ru"
_TR_PAIRS = None


def _settings_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


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
    global LANG
    LANG = "en" if code == "en" else "ru"
    if save:
        try:
            import json as _json
            with open(_settings_path(), "w", encoding="utf-8") as fh:
                _json.dump({"lang": LANG}, fh)
        except OSError:
            pass


def tr(text):
    """Строка интерфейса на выбранном языке (для ru возвращается как есть)."""
    global _TR_PAIRS
    if LANG != "en" or not isinstance(text, str) or not text:
        return text
    if _TR_PAIRS is None:
        _TR_PAIRS = sorted(I18N_EN.items(), key=lambda kv: -len(kv[0]))
    for ru, en in _TR_PAIRS:
        if ru in text:
            text = text.replace(ru, en)
    return text


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


def unquote(text):
    return text.strip().strip('"').strip("'")


def pause():
    try:
        input(gray("\nEnter - вернуться в меню..."))
    except EOFError:
        pass


def ensure_dirs():
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)


def fmt_size(n):
    return f"{n / 1024:.1f} КБ" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} МБ"


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


# ================================================================ связанные SC-файлы / пакет
#
# Некоторые игровые ресурсы состоят не из одного .sc: рядом могут лежать
# <base>_tex.sc и <base>_<int>.sctx/.zktx. Пока это только обнаружение и
# диагностика: существующие операции намеренно не пытаются менять такие
# пакеты, пока формат внешних текстур не изучен полностью.

def detect_sc_package(path):
    """Возвращает описание связанных файлов без изменения содержимого."""
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    name = os.path.basename(path)
    base, ext = os.path.splitext(name)
    if ext.lower() != '.sc':
        return {"main": path, "files": [path], "variants": [], "tex": None}
    candidates = []
    # Для main.sc ищем main_tex.sc и main_<int>.*. Для skin.sc аналогично.
    for fn in os.listdir(folder):
        full = os.path.join(folder, fn)
        if not os.path.isfile(full):
            continue
        if fn == name:
            continue
        if fn == base + '_tex.sc':
            candidates.append((fn, 'external_tex_sc'))
            continue
        m = re.fullmatch(re.escape(base) + r'_(\d+)\.(sctx|zktx)', fn, re.I)
        if m:
            candidates.append((fn, 'resolution_' + m.group(1)))
            continue
        # Иногда основной файл называется main, а текстуры имеют main_<int>.*;
        # здесь намеренно не захватываем произвольные соседние файлы.
    candidates.sort(key=lambda x: (0 if x[1] == 'external_tex_sc' else 1, x[0].lower()))
    return {"main": path, "files": [path] + [os.path.join(folder, x[0]) for x in candidates],
            "variants": [(os.path.join(folder, x[0]), x[1]) for x in candidates if x[1] != 'external_tex_sc'],
            "tex": next((os.path.join(folder, x[0]) for x in candidates if x[1] == 'external_tex_sc'), None)}


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


# ================================================================ зависимости и утилиты

def ensure_numpy_optional():
    """numpy ускоряет упаковку атласов в разы. Не обязателен: без него всё работает, просто медленнее."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                                   "--disable-pip-version-check", "numpy"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


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


def clean_export_name(raw):
    t = "".join((_TR[c.lower()].capitalize() if c.isupper() and _TR[c.lower()] else _TR[c.lower()])
                if c.lower() in _TR else c for c in raw)
    t = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]+", "_", t).strip("_"))
    if not t:
        return DEFAULT_EXPORT_NAME
    return "img_" + t if t[0].isdigit() else t


def parse_size(text):
    parts = [p for p in re.split(r"\s*[x×*]\s*", text.strip().lower()) if p]
    try:
        vals = [float(p.replace(",", ".")) for p in parts]
    except ValueError:
        raise UserError(f"Не понял размер «{text}» (нужно 135x100 или 135)")
    if not vals or len(vals) > 2 or any(v <= 0 for v in vals):
        raise UserError(f"Не понял размер «{text}» (нужно 135x100 или 135)")
    return vals[0], (vals[1] if len(vals) == 2 else None)


# ================================================================ билдеры .sc (как в gifconv.py / png2sc.py)

TEXTFIELDS_CHUNK = base64.b64decode("DAAAAAgAAAAEAAQABAAAAA==")
MODIFIERS_CHUNK = base64.b64decode("DAAAAAgAAAAEAAQABAAAAA==")


def build_export_names_chunk(fb, movieclip_id, name_ref_id=1):
    b = fb.Builder(128)
    b.StartVector(2, 1, 2); b.PrependUint16(movieclip_id); oid = b.EndVector()
    b.StartVector(4, 1, 4); b.PrependUint32(name_ref_id); nrf = b.EndVector()
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(1, nrf, 0)
    b.PrependUOffsetTRelativeSlot(0, oid, 0)
    b.Finish(b.EndObject())
    body = bytes(b.Output())
    return struct.pack("<I", len(body)) + body


def build_data_storage(fb, layouts, timeline, export_name, matrices, matrix_index):
    n_slots = len(timeline)
    b = fb.Builder(1024 + len(layouts) * 64 + n_slots * 8)
    s_empty = b.CreateString(""); s_name = b.CreateString(export_name)
    b.StartVector(4, 2, 4); b.PrependUOffsetTRelative(s_name); b.PrependUOffsetTRelative(s_empty)
    strings_vec = b.EndVector()

    vb = b""
    for L in layouts:
        vb += struct.pack("<ffHH", L["x0"], L["y0"], L["u0"], L["v0"])
        vb += struct.pack("<ffHH", L["x0"], L["y1"], L["u0"], L["v1"])
        vb += struct.pack("<ffHH", L["x1"], L["y0"], L["u1"], L["v0"])
        vb += struct.pack("<ffHH", L["x1"], L["y1"], L["u1"], L["v1"])
    points_vec = b.CreateByteVector(vb)

    # элемент кадра в памяти: (child, matrix, color), 0xFFFF = нет
    b.StartVector(2, n_slots * 3, 2)
    for k in reversed(range(n_slots)):
        b.PrependUint16(0xFFFF); b.PrependUint16(matrix_index); b.PrependUint16(timeline[k])
    elems_vec = b.EndVector()

    # матрица: 6 float (a, b, c, d, tx, ty)
    b.StartVector(24, len(matrices), 4)
    for (a, bb, c, d, tx, ty) in reversed(matrices):
        b.Prep(4, 24)
        for v in (ty, tx, d, c, bb, a):
            b.PrependFloat32(v)
    matrices_vec = b.EndVector()

    b.StartVector(7, 0, 1); colors_vec = b.EndVector()
    b.StartObject(3)
    b.PrependUOffsetTRelativeSlot(0, matrices_vec, 0)
    b.PrependUOffsetTRelativeSlot(1, colors_vec, 0)
    bank = b.EndObject()
    b.StartVector(4, 1, 4); b.PrependUOffsetTRelative(bank); banks_vec = b.EndVector()

    b.StartObject(7)
    b.PrependUOffsetTRelativeSlot(0, strings_vec, 0)
    b.PrependUOffsetTRelativeSlot(4, elems_vec, 0)
    b.PrependUOffsetTRelativeSlot(5, points_vec, 0)
    b.PrependUOffsetTRelativeSlot(6, banks_vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def build_shapes(fb, page_indices):
    n = len(page_indices)
    b = fb.Builder(1024 + n * 32)
    offs = []
    for i, page_idx in enumerate(page_indices):
        b.StartVector(16, 1, 4); b.Prep(4, 16)
        b.PrependUint32(i * 4); b.PrependUint32(4); b.PrependUint32(page_idx); b.PrependUint32(0)
        cmds = b.EndVector()
        b.StartObject(2)
        b.PrependUint16Slot(0, i, 0)
        b.PrependUOffsetTRelativeSlot(1, cmds, 0)
        offs.append(b.EndObject())
    b.StartVector(4, n, 4)
    for o in reversed(offs):
        b.PrependUOffsetTRelative(o)
    vec = b.EndVector()
    b.StartObject(1); b.PrependUOffsetTRelativeSlot(0, vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def build_movieclips(fb, n_shapes, n_slots, framerate, movieclip_id):
    b = fb.Builder(1024 + n_shapes * 8 + n_slots * 16)
    b.StartVector(2, n_shapes, 2)
    for i in reversed(range(n_shapes)):
        b.PrependUint16(i)
    ids = b.EndVector()
    b.StartVector(1, n_shapes, 1)
    for _ in range(n_shapes):
        b.PrependUint8(0)
    blend = b.EndVector()
    b.StartVector(8, n_slots, 4)
    for _ in range(n_slots):
        b.Prep(4, 8); b.PrependUint32(0); b.PrependUint32(1)
    frames = b.EndVector()
    b.StartObject(13)
    b.PrependUint16Slot(0, movieclip_id, 0)
    b.PrependUint32Slot(1, 1, None)
    b.PrependUint8Slot(2, framerate, 0)
    b.PrependUint16Slot(3, n_slots, 0)
    b.PrependUint8Slot(4, 0, 0)
    b.PrependUOffsetTRelativeSlot(5, ids, 0)
    b.PrependUOffsetTRelativeSlot(7, blend, 0)
    b.PrependUOffsetTRelativeSlot(8, frames, 0)
    b.PrependUint32Slot(9, 0, 4294967295)
    b.PrependUint32Slot(10, 0, 0)
    clip = b.EndObject()
    b.StartVector(4, 1, 4); b.PrependUOffsetTRelative(clip); clips = b.EndVector()
    b.StartObject(1); b.PrependUOffsetTRelativeSlot(0, clips, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def build_textures(fb, pages):
    total = sum(len(p[0]) for p in pages)
    if total > HARD_RAW_LIMIT:
        raise UserError("Текстура больше 2 ГБ - не влезает в flatbuffers")
    b = fb.Builder(total + 4096 + len(pages) * 256)
    sets = []
    for page in pages:
        payload, fmt, w, h = page[:4]
        tex_flags = page[4] if len(page) > 4 else 0
        data = b.CreateByteVector(payload)
        page[0] = None
        del payload
        b.StartObject(6)
        b.PrependUint8Slot(0, fmt, 0); b.PrependUint8Slot(1, tex_flags, 0)
        b.PrependUint16Slot(2, w, 0); b.PrependUint16Slot(3, h, 0)
        b.PrependUOffsetTRelativeSlot(4, data, 0)
        td = b.EndObject()
        b.StartObject(2); b.PrependUOffsetTRelativeSlot(1, td, 0)
        sets.append(b.EndObject())
    b.StartVector(4, len(pages), 4)
    for o in reversed(sets):
        b.PrependUOffsetTRelative(o)
    vec = b.EndVector()
    b.StartObject(1); b.PrependUOffsetTRelativeSlot(0, vec, 0)
    b.Finish(b.EndObject())
    try:
        return bytes(memoryview(b.Bytes)[b.Head():])
    except Exception:
        return bytes(b.Output())


def build_file_descriptor(fb, export_name, shapes, clips, textures, res_off, tex_len, comp_size):
    b = fb.Builder(512)
    name = b.CreateString(export_name)
    b.StartObject(2); b.PrependUOffsetTRelativeSlot(0, name, 0); exp = b.EndObject()
    b.StartVector(4, 1, 4); b.PrependUOffsetTRelative(exp); exports = b.EndVector()
    b.StartObject(13)
    b.PrependUint32Slot(0, 1, 0); b.PrependUint32Slot(1, 1, 0)
    b.PrependUint32Slot(2, shapes, 0); b.PrependUint32Slot(3, clips, 0)
    b.PrependUint32Slot(4, textures, 0)
    b.PrependUint32Slot(5, 0, 0); b.PrependUint32Slot(6, 0, 0); b.PrependUint32Slot(7, 0, 0)
    b.PrependUint32Slot(8, res_off, 0); b.PrependUint32Slot(9, tex_len, 0)
    b.PrependUOffsetTRelativeSlot(10, exports, 0)
    b.PrependUint32Slot(11, comp_size, 0); b.PrependUint32Slot(12, 0, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def build_sc_bytes(fb, zstd, export_name, layouts, page_indices, pages, timeline, fps, matrices, matrix_index):
    ds = build_data_storage(fb, layouts, timeline, export_name, matrices, matrix_index)
    shapes = build_shapes(fb, page_indices)
    mc_id = len(layouts)
    mc = build_movieclips(fb, len(layouts), len(timeline), fps, mc_id)
    n_pages = len(pages)
    tex = build_textures(fb, pages)
    inner = b"".join([
        struct.pack("<I", len(ds)), ds,
        build_export_names_chunk(fb, mc_id),
        TEXTFIELDS_CHUNK,
        struct.pack("<I", len(shapes)), shapes,
        struct.pack("<I", len(mc)), mc,
        MODIFIERS_CHUNK,
        struct.pack("<I", len(tex)), tex,
    ])
    comp = zstd.ZstdCompressor(level=COMPRESS_LEVEL, write_content_size=True).compress(inner)
    fd = build_file_descriptor(fb, export_name, len(layouts), 1, n_pages, 4 + len(ds), len(tex) + 4, len(comp))
    return b"".join([b"SC", struct.pack("<I", 5), struct.pack("<I", len(fd)), fd, comp])


# ================================================================ PNG -> SC

def build_layout(w, h, pivot, tw, th, inset):
    px, py = PIVOTS[pivot]
    ins = max(0.0, inset)
    return {"x0": -px * w, "x1": (1 - px) * w, "y0": -py * h, "y1": (1 - py) * h,
            "u0": round(ins / tw * 65535), "v0": round(ins / th * 65535),
            "u1": round((tw - ins) / tw * 65535), "v1": round((th - ins) / th * 65535)}


def premultiply(Image, rgba):
    from PIL import ImageChops
    r, g, b, a = rgba.split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))


def resolve_scale(o, w, h):
    sx = sy = 1.0
    if o.scale is not None:
        sx = sy = o.scale
    if o.target:
        tw, th = parse_size(o.target)
        sx = tw / w
        sy = th / h if th is not None else sx
    if sx <= 0 or sy <= 0:
        raise UserError("Масштаб должен быть больше нуля")
    return sx, sy


def convert_png(Image, fb, zstd, path, export_name, o):
    with Image.open(path) as im:
        im.load()
        rgba = im.convert("RGBA")
    src_w, src_h = rgba.size
    shape_w, shape_h = float(src_w), float(src_h)      # 1 пиксель PNG = 1 единица клипа
    if o.size:
        w, h = parse_size(o.size)
        shape_w, shape_h = w, (h if h is not None else w * src_h / src_w)

    tex = rgba
    if max(src_w, src_h) > PAGE_MAX_DIM:
        k = PAGE_MAX_DIM / max(src_w, src_h)
        res = getattr(Image, "Resampling", Image)
        tex = rgba.resize((max(1, int(src_w * k)), max(1, int(src_h * k))), res.LANCZOS)
        print(yellow(f"  PNG больше {PAGE_MAX_DIM}px, текстура ужата до {tex.size[0]}x{tex.size[1]} "
                     f"(размер в игре не меняется)"))
    tw, th = tex.size
    if o.premultiply:
        tex = premultiply(Image, tex)

    sx, sy = resolve_scale(o, shape_w, shape_h)
    identity = abs(sx - 1) < 1e-9 and abs(sy - 1) < 1e-9
    if identity:
        matrices, midx, lw, lh = [], 0xFFFF, shape_w, shape_h
    elif o.mode == "matrix":
        matrices, midx, lw, lh = [(sx, 0.0, 0.0, sy, 0.0, 0.0)], 0, shape_w, shape_h
    else:
        matrices, midx, lw, lh = [], 0xFFFF, shape_w * sx, shape_h * sy

    layout = build_layout(lw, lh, o.pivot, tw, th, o.uv_inset)
    pages = [[tex.tobytes(), 0, tw, th]]
    data = build_sc_bytes(fb, zstd, export_name, [layout], [0], pages, [0], FRAMERATE, matrices, midx)
    return data, (src_w, src_h), (shape_w, shape_h), (sx, sy)


def flow_png():
    ensure_deps()
    from PIL import Image
    import flatbuffers
    import zstandard

    src = resolve_input("Входной PNG или папка с PNG", "input.png",
                        [("PNG", "*.png"), ("Все файлы", "*.*")], allow_dir=True)
    batch = os.path.isdir(src)
    if batch:
        files = sorted(os.path.join(src, f) for f in os.listdir(src) if f.lower().endswith(".png"))
        if not files:
            raise UserError(f"В папке нет PNG: {src}")
        out_dir = resolve_out_dir(ask("Папка для результата", "output"))
        print(gray(f"  найдено PNG: {len(files)}"))
    else:
        files = [src]
        out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
        default_name = clean_export_name(os.path.splitext(os.path.basename(out_path))[0])
        export_name = clean_export_name(ask("ExportName", default_name))

    raw = ask("Размер клипа: число (0.5 = вдвое меньше, 2 = вдвое больше) или итог 135x100", "1")
    opts = SimpleNamespace(scale=None, target=None, size=None, pivot="center", mode="shape",
                           premultiply=False, uv_inset=0.0)
    if re.search(r"[x×*]", raw.lower()):
        opts.target = raw
    else:
        try:
            opts.scale = float(raw.replace(",", "."))
        except ValueError:
            raise UserError(f"Не понял масштаб «{raw}»")
    if opts.scale is not None and opts.scale <= 0:
        raise UserError("Масштаб должен быть больше нуля")

    print("  Точка привязки (масштаб идёт от неё; для пинов, стоящих на земле - bottom):")
    print("  " + "   ".join(f"{i + 1} {name}" for i, name in enumerate(PIVOT_ORDER)))
    raw = ask("Точка привязки", "1").lower()
    if raw.isdigit() and 1 <= int(raw) <= len(PIVOT_ORDER):
        opts.pivot = PIVOT_ORDER[int(raw) - 1]
    elif raw in PIVOTS:
        opts.pivot = raw
    else:
        print(yellow(f"  Нет такой точки «{raw}», беру center"))

    raw = ask("Режим масштаба: 1 - в размере шейпа, 2 - матрицей клипа", "1")
    opts.mode = "matrix" if raw == "2" else "shape"

    if ask_yes_no("Расширенные настройки (premultiply, размер шейпа)", False):
        opts.premultiply = ask_yes_no("  Premultiplied alpha", False)
        raw = ask("  Размер шейпа до масштаба (по умолчанию размер PNG), например 270x200", "")
        opts.size = raw or None

    line()
    results = []
    used = set()
    for path in files:
        if batch:
            stem = os.path.splitext(os.path.basename(path))[0]
            export_name = clean_export_name(stem)
            base, n = export_name, 2
            while export_name in used:
                export_name, n = f"{base}_{n}", n + 1
            used.add(export_name)
            out_path = os.path.join(out_dir, export_name + ".sc")
        print(f"{cyan('▶')} {os.path.basename(path)}")
        data, src_sz, shape, (sx, sy) = convert_png(Image, flatbuffers, zstandard, path, export_name, opts)
        with open(out_path, "wb") as fh:
            fh.write(data)
        saved(out_path)
        print(gray(f"    PNG {src_sz[0]}x{src_sz[1]}, шейп {shape[0]:g}x{shape[1]:g}, масштаб {sx:g}x{sy:g} "
                   f"→ в игре {shape[0] * sx:g}x{shape[1] * sy:g}, режим {opts.mode}, привязка {opts.pivot}"))
        print(gray(f'    в моде: "FileName": "sc/{os.path.basename(out_path)}", "ExportName": "{export_name}"'))
        results.append(out_path)
    return results


# ================================================================ GIF -> SC (логика бывшего gifconv.py встроена в хаб)

GIF_FRAMES_PER_PAGE = 12
GIF_QUAD_WIDTH = 640.0                 # режим «фон под экран»: ширина клипа в единицах игры
GIF_AUTO_MARGIN = 0.03
GIF_STRETCH_TOL = 0.02
GIF_UV_INSET_PX = 2
GIF_MAX_SHAPES = 3000
GIF_MAX_PAGES = 255
GIF_MAX_TIMELINE = 30000
GIF_RAW_BUDGET = 1_050_000_000
GIF_TILES = [2048, 1848, 1536, 1280, 1024, 896, 768, 640, 512, 384, 256, 192, 128]
GIF_USER_MAX_FPS = 60
GIF_DEFAULT_MAX_FPS = 30
GIF_MIN_DELAY_MS = 10
GIF_FALLBACK_DELAY_MS = 100


def gif_resample(Image):
    res = getattr(Image, "Resampling", Image)
    return res.LANCZOS, res.BICUBIC


def gif_analyze(Image, path):
    import hashlib
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        if n < 2:
            raise UserError("В файле один кадр - это не анимация. Для одной картинки используй пункт 1 (PNG → SC)")
        size = im.size
        delays, digests = [], []
        has_alpha = False
        for i in range(n):
            im.seek(i)
            delays.append(int(im.info.get("duration", 0) or 0))
            rgba = im.convert("RGBA")
            if not has_alpha and rgba.getchannel("A").getextrema()[0] < 255:
                has_alpha = True
            digests.append(hashlib.blake2b(rgba.tobytes(), digest_size=16).digest())
            if (i + 1) % 25 == 0 or i + 1 == n:
                print(f"\r  анализ кадров: {i + 1}/{n}", end="", flush=True)
        print()
    return {"n_frames": n, "size": size, "delays": delays, "digests": digests, "has_alpha": has_alpha}


def gif_effective_delays(raw):
    return [GIF_FALLBACK_DELAY_MS if d <= GIF_MIN_DELAY_MS else d for d in raw]


def gif_iter_frames(Image, path, wanted, bg_rgb):
    """Кадры по списку номеров. bg_rgb=None - с прозрачностью (RGBA), иначе прозрачное заливается этим цветом."""
    with Image.open(path) as im:
        cur, cached = -1, None
        for want in wanted:
            if want != cur:
                im.seek(want)
                rgba = im.convert("RGBA")
                if bg_rgb is not None:
                    base = Image.new("RGBA", rgba.size, tuple(bg_rgb) + (255,))
                    base.alpha_composite(rgba)
                    rgba = base.convert("RGB")
                cached, cur = rgba, want
            yield cached


def gif_suggest_fps(delays_ms):
    bounds, acc = [], 0.0
    for d in delays_ms:
        acc += d
        bounds.append(acc)
    for fps in range(1, GIF_USER_MAX_FPS + 1):
        worst = 0.0
        for b in bounds:
            x = b * fps / 1000.0
            worst = max(worst, abs(x - round(x)))
        if worst / fps * 1000.0 <= 1.5:
            return fps
    return GIF_DEFAULT_MAX_FPS


def gif_plan_timeline(delays_ms, fps, pingpong):
    from bisect import bisect_right
    starts, acc = [], 0.0
    for d in delays_ms:
        starts.append(acc)
        acc += d
    n_slots = max(1, int(round(acc * fps / 1000.0)))
    slots = []
    for k in range(n_slots):
        t = (k + 0.5) * 1000.0 / fps
        i = bisect_right(starts, t) - 1
        slots.append(min(max(i, 0), len(delays_ms) - 1))
    if pingpong and len(slots) > 2:
        slots = slots + slots[-2:0:-1]
    return slots


def gif_build_plan(slots, digests, reuse):
    if not reuse:
        return list(slots), list(range(len(slots)))
    shape_of, stored, timeline = {}, [], []
    for src in slots:
        key = digests[src]
        if key not in shape_of:
            shape_of[key] = len(stored)
            stored.append(src)
        timeline.append(shape_of[key])
    return stored, timeline


def gif_best_grid(n, tw, th):
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        if cols * tw > PAGE_MAX_DIM or rows * th > PAGE_MAX_DIM:
            continue
        key = (cols * rows - n, max(cols * tw, rows * th))
        if best is None or key < best[0]:
            best = (key, cols, rows)
    return (best[1], best[2]) if best else None


def gif_frames_per_page(tw, th):
    for n in range(GIF_FRAMES_PER_PAGE, 0, -1):
        if gif_best_grid(n, tw, th):
            return n
    return 0


def gif_pages_needed(n, tw, th):
    fpp = gif_frames_per_page(tw, th)
    return 1 << 30 if fpp == 0 else math.ceil(n / fpp)


def gif_estimate_raw(n_frames, tw, th):
    fpp = gif_frames_per_page(tw, th)
    if fpp == 0:
        return 1 << 62
    total, left = 0, n_frames
    while left > 0:
        k = min(fpp, left)
        cols, rows = gif_best_grid(k, tw, th)
        total += cols * tw * rows * th * 4
        left -= k
    return total


def gif_pick_tile(n_frames, src_w, src_h, free_size):
    """Ширина и высота кадра в текстуре. free_size=True: по возможности исходный размер гифки (любое разрешение);
    иначе ширина из списка стандартных, высота по пропорции экрана (передаётся через src_h/src_w)."""
    if free_size:
        cands = [src_w] + [t for t in GIF_TILES if t < src_w]
        if src_w < GIF_TILES[-1]:
            cands = [src_w]
    else:
        side = max(src_w, src_h)
        cands = [t for t in GIF_TILES if t <= max(side, 256)] or [GIF_TILES[-1]]
    for t in cands:
        th = src_h if t == src_w else max(2, int(round(t * src_h / src_w)))
        if gif_estimate_raw(n_frames, t, th) <= GIF_RAW_BUDGET and gif_pages_needed(n_frames, t, th) <= GIF_MAX_PAGES:
            return t, th
    t = cands[-1]
    return t, max(2, int(round(t * src_h / src_w)))


def gif_cover(Image, img, w, h):
    lanczos, _ = gif_resample(Image)
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(w, math.ceil(iw * scale)), max(h, math.ceil(ih * scale))
    big = img.resize((nw, nh), lanczos)
    left, top = (nw - w) // 2, (nh - h) // 2
    return big.crop((left, top, left + w, top + h))


def gif_screen_tile(Image, ImageFilter, img, tw, th, stretch, margin):
    """Режим «под экран»: кадр вписывается в пропорции экрана, пустые края - размытая копия кадра."""
    lanczos, bicubic = gif_resample(Image)
    box_w = max(2, round(tw * (1.0 - 2.0 * margin)))
    box_h = max(2, round(th * (1.0 - 2.0 * margin)))
    iw, ih = img.size
    if stretch:
        fg = img.resize((box_w, box_h), lanczos)
    else:
        k = min(box_w / iw, box_h / ih)
        fg = img.resize((max(1, round(iw * k)), max(1, round(ih * k))), lanczos)
    if fg.width >= tw - 1 and fg.height >= th - 1:
        if fg.size != (tw, th):
            fg = fg.resize((tw, th), lanczos)
        return fg.convert("RGBA")
    sw, sh = max(32, tw // 4), max(32, th // 4)
    bg = gif_cover(Image, img, sw, sh).filter(ImageFilter.GaussianBlur(max(2.0, max(8, max(tw, th) // 24) / 4.0)))
    canvas = bg.resize((tw, th), bicubic).point(lambda p: int(p * 0.55)).convert("RGBA")
    canvas.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
    return canvas


def gif_build_page(Image, frames, tw, th, tile_fn, shape_w, shape_h, pivot, premult):
    cols, rows = gif_best_grid(len(frames), tw, th)
    page_w, page_h = cols * tw, rows * th
    page = Image.new("RGBA", (page_w, page_h), (0, 0, 0, 0))
    inset = GIF_UV_INSET_PX if min(tw, th) >= 128 else 1
    px, py = PIVOTS[pivot]
    layouts = []
    for i, fr in enumerate(frames):
        tile = tile_fn(fr)
        if premult:
            tile = premultiply(Image, tile)
        left, top = (i % cols) * tw, (i // cols) * th
        page.paste(tile, (left, top))
        layouts.append({"x0": -px * shape_w, "x1": (1 - px) * shape_w, "y0": -py * shape_h, "y1": (1 - py) * shape_h,
                        "u0": round((left + inset) / page_w * 65535), "v0": round((top + inset) / page_h * 65535),
                        "u1": round((left + tw - inset) / page_w * 65535), "v1": round((top + th - inset) / page_h * 65535)})
    raw = page.tobytes()
    del page
    return raw, page_w, page_h, layouts


def gif_ask_fps(delays, pingpong, digests, keep_all_ask):
    suggested = gif_suggest_fps(delays)
    while True:
        raw = ask(f"Кадров в секунду в игре (целое, до {GIF_USER_MAX_FPS}; Enter - {suggested})", str(suggested))
        try:
            fps = int(raw.strip())
        except ValueError:
            raise UserError(f"Не понял число «{raw}»")
        fps = max(1, min(GIF_USER_MAX_FPS, fps))
        slots = gif_plan_timeline(delays, fps, pingpong)
        stored, timeline = gif_build_plan(slots, digests, True)
        if len(slots) > GIF_MAX_TIMELINE:
            if fps <= 1:
                raise UserError(f"Анимация слишком длинная: {len(slots)} слотов при 1 кадр/с (максимум {GIF_MAX_TIMELINE})")
            print(yellow(f"  Получилось {len(slots)} слотов таймлайна, максимум {GIF_MAX_TIMELINE}. Выбери FPS поменьше."))
            suggested = max(1, int(fps * GIF_MAX_TIMELINE / len(slots) * 0.9))
            continue
        if len(stored) > GIF_MAX_SHAPES:
            if fps <= 1:
                raise UserError(f"В GIF слишком много разных кадров: {len(stored)} (максимум {GIF_MAX_SHAPES})")
            print(yellow(f"  При {fps} кадр/с выходит {len(stored)} разных кадров, максимум {GIF_MAX_SHAPES}. "
                         f"Выбери FPS поменьше."))
            suggested = max(1, int(fps * GIF_MAX_SHAPES / len(stored) * 0.9))
            continue
        break
    if len(stored) < len(slots) and not pingpong and keep_all_ask:
        if not ask_yes_no(f"Слотов {len(slots)}, уникальных кадров {len(stored)}. Хранить каждый кадр один раз (экономит память)", True):
            if len(slots) <= GIF_MAX_SHAPES:
                stored, timeline = gif_build_plan(slots, digests, False)
            else:
                print(gray("  Раздельных кадров вышло бы больше максимума - оставляю экономный режим"))
    return fps, stored, timeline


def flow_gif():
    ensure_deps()
    from PIL import Image
    import flatbuffers
    import zstandard

    src = resolve_input("Входной GIF", "input.gif", [("GIF", "*.gif"), ("Все файлы", "*.*")])
    print(gray("  Читаю GIF..."))
    try:
        meta = gif_analyze(Image, src)
    except (OSError, ValueError) as e:
        raise UserError(f"Не получилось открыть файл как анимацию ({e}). Проверь, что это GIF")
    src_w, src_h = meta["size"]
    total_s = sum(gif_effective_delays(meta["delays"])) / 1000.0
    print(gray(f"  GIF: {src_w}x{src_h}, {meta['n_frames']} кадров (разных: {len(set(meta['digests']))}), {total_s:.2f} с"
               + (", есть прозрачность" if meta["has_alpha"] else "")))
    print(gray("  Анимированный фон для меню собирается в пункте 19 (там можно использовать top.gif)."))

    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    default_name = clean_export_name(os.path.splitext(os.path.basename(out_path))[0])
    export_name = clean_export_name(ask("ExportName", default_name))

    opts = SimpleNamespace(scale=None, target=None, size=None, pivot="center", mode="shape", premultiply=False)
    raw = ask("Размер клипа: число (0.5 = вдвое меньше, 2 = вдвое больше) или итог 135x100", "1")
    if re.search(r"[x×*]", raw.lower()):
        opts.target = raw
    else:
        try:
            opts.scale = float(raw.replace(",", "."))
        except ValueError:
            raise UserError(f"Не понял масштаб «{raw}»")
    if opts.scale is not None and opts.scale <= 0:
        raise UserError("Масштаб должен быть больше нуля")
    print("  Точка привязки (масштаб идёт от неё):")
    print("  " + "   ".join(f"{i + 1} {name}" for i, name in enumerate(PIVOT_ORDER)))
    raw = ask("Точка привязки", "1").lower()
    if raw.isdigit() and 1 <= int(raw) <= len(PIVOT_ORDER):
        opts.pivot = PIVOT_ORDER[int(raw) - 1]
    elif raw in PIVOTS:
        opts.pivot = raw
    else:
        print(yellow(f"  Нет такой точки «{raw}», беру center"))
    opts.mode = "matrix" if ask("Режим масштаба: 1 - в размере шейпа, 2 - матрицей клипа", "1") == "2" else "shape"
    if ask_yes_no("Расширенные настройки (premultiply, размер клипа до масштаба)", False):
        opts.premultiply = ask_yes_no("  Premultiplied alpha", False)
        raw = ask("  Размер шейпа до масштаба (по умолчанию размер GIF), например 270x200", "")
        opts.size = raw or None
    pingpong = ask_yes_no("Проигрывать туда-обратно (пинг-понг)", False)
    shape_w, shape_h = float(src_w), float(src_h)
    if opts.size:
        w, h = parse_size(opts.size)
        shape_w, shape_h = w, (h if h is not None else w * src_h / src_w)
    sx, sy = resolve_scale(opts, shape_w, shape_h)

    delays = gif_effective_delays(meta["delays"])
    fps, stored, timeline = gif_ask_fps(delays, pingpong, meta["digests"], True)
    n_shapes, n_slots = len(stored), len(timeline)

    tw, th = gif_pick_tile(n_shapes, src_w, src_h, True)
    ask_t = ask("Ширина кадра в файле, px (Enter - авто; меньше - легче файл, хуже чёткость)", "").strip()
    if ask_t:
        try:
            want = int(float(ask_t.replace(",", ".")))
        except ValueError:
            raise UserError(f"Не понял число «{ask_t}»")
        wh = max(2, int(round(want * src_h / src_w)))
        if want < 8 or gif_frames_per_page(want, wh) == 0 or gif_estimate_raw(n_shapes, want, wh) > HARD_RAW_LIMIT \
                or gif_pages_needed(n_shapes, want, wh) > GIF_MAX_PAGES:
            print(yellow(f"  Ширина {want}px не влезает в лимиты формата, оставляю {tw}px"))
        else:
            tw, th = want, wh
    est = gif_estimate_raw(n_shapes, tw, th)
    fpp = gif_frames_per_page(tw, th)
    print(gray(f"  Слотов: {n_slots} ({n_slots / fps:.2f} с при {fps} кадр/с), кадров в текстурах: {n_shapes}, "
               f"размер кадра {tw}x{th}, текстуры ~{est / 1e6:.0f} МБ"))

    lanczos = gif_resample(Image)[0]

    def tile_fn(fr):
        return fr if fr.size == (tw, th) else fr.resize((tw, th), lanczos)
    if opts.mode == "matrix":
        matrices, midx, lw, lh = [(sx, 0.0, 0.0, sy, 0.0, 0.0)], 0, shape_w, shape_h
    elif abs(sx - 1) < 1e-9 and abs(sy - 1) < 1e-9:
        matrices, midx, lw, lh = [], 0xFFFF, shape_w, shape_h
    else:
        matrices, midx, lw, lh = [], 0xFFFF, shape_w * sx, shape_h * sy

    print(gray(f"  Собираю текстуры: {math.ceil(n_shapes / fpp)} шт. по {fpp} кадров"))
    frames_iter = gif_iter_frames(Image, src, stored, None)
    pages, layouts, page_idx = [], [], []
    while True:
        chunk = []
        for fr in frames_iter:
            chunk.append(fr)
            if len(chunk) == fpp:
                break
        if not chunk:
            break
        raw, pw, ph, lay = gif_build_page(Image, chunk, tw, th, tile_fn, lw, lh, opts.pivot, opts.premultiply)
        del chunk
        pages.append([raw, 0, pw, ph])
        del raw
        layouts.extend(lay)
        page_idx.extend([len(pages) - 1] * len(lay))
        print(gray(f"    страница {len(pages)}: {len(layouts)}/{n_shapes} кадров"), flush=True)
    if len(layouts) != n_shapes:
        raise UserError(f"Не удалось прочитать все кадры GIF: прочитано {len(layouts)} из {n_shapes}")

    raw_bytes = sum(len(p[0]) for p in pages)
    print(gray(f"  Сжимаю {raw_bytes / 1e6:.0f} МБ, это может занять пару минут..."))
    data = build_sc_bytes(flatbuffers, zstandard, export_name, layouts, page_idx, pages, timeline, fps, matrices, midx)
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    results = [out_path]
    print(gray(f"    {len(data) / 1048576:.1f} МБ, {n_slots / fps:.2f} с, {n_slots} слотов, {fps} кадр/с, "
               f"кадров {n_shapes}, текстур {len(pages)}, в памяти игры ~{raw_bytes / 1e6:.0f} МБ"))
    print(gray(f"    GIF {src_w}x{src_h}, шейп {shape_w:g}x{shape_h:g}, масштаб {sx:g}x{sy:g} → в игре "
               f"{shape_w * sx:g}x{shape_h * sy:g}, режим {opts.mode}, привязка {opts.pivot}"))
    print(gray(f'    в моде: "FileName": "sc/{os.path.basename(out_path)}", "ExportName": "{export_name}"'))
    if ask_yes_no("Сохранить превью (PNG, первый кадр)", True):
        try:
            first = next(gif_iter_frames(Image, src, [stored[0]], None)).convert("RGBA")
            pw = 1280
            ph = max(2, round(pw * first.size[1] / first.size[0]))
            small = first.resize((pw, ph), lanczos)
            img = Image.new("RGBA", (pw, ph), (60, 60, 60, 255))
            for yy in range(0, ph, 32):
                for xx in range(0, pw, 32):
                    if (xx // 32 + yy // 32) % 2:
                        img.paste((90, 90, 90, 255), (xx, yy, xx + 32, yy + 32))
            img.alpha_composite(small)
            prev = os.path.splitext(out_path)[0] + "_preview.png"
            img.convert("RGB").save(prev)
            saved(prev)
            results.append(prev)
        except Exception as e:
            print(yellow(f"  Превью не получилось: {e}"))
    return results


# ================================================================ чтение .sc

def u16(b, p): return struct.unpack_from("<H", b, p)[0]
def u32(b, p): return struct.unpack_from("<I", b, p)[0]
def i32(b, p): return struct.unpack_from("<i", b, p)[0]
def f32(b, p): return struct.unpack_from("<f", b, p)[0]


CHUNK_NAMES = ["DataStorage", "ExportNames", "TextFields", "Shapes", "MovieClips", "Modifiers", "Textures"]


def read_sc(path):
    with open(path, "rb") as fh:
        data = fh.read()
    return parse_sc(data)


def parse_sc(data):
    import zstandard
    if data[:2] != b"SC" or len(data) < 10:
        raise UserError("Это не .sc (нет заголовка SC)")
    version = u32(data, 2)
    fd_len = u32(data, 6)
    fd = data[10:10 + fd_len]
    comp = data[10 + fd_len:]
    try:
        inner = zstandard.ZstdDecompressor().decompressobj().decompress(comp)
    except Exception as e:
        raise UserError(f"Не смог распаковать данные (не zstd или другой контейнер): {e}")
    chunks, pos = [], 0
    while pos + 4 <= len(inner):
        n = u32(inner, pos)
        if pos + 4 + n > len(inner):
            break
        chunks.append(inner[pos + 4:pos + 4 + n])
        pos += 4 + n
    return {"kind": "sc2", "version": version, "fd": fd, "inner_len": len(inner), "chunks": chunks,
            "leftover": len(inner) - pos, "file_size": len(data)}


def fb_slots(b, t):
    if t < 4 or t + 4 > len(b):
        raise ValueError("bad table")
    vt = t - i32(b, t)
    if vt < 0 or vt + 4 > len(b):
        raise ValueError("bad vtable")
    vlen, olen = u16(b, vt), u16(b, vt + 2)
    if vlen < 4 or vlen % 2 or vt + vlen > len(b) or olen < 4 or t + olen > len(b):
        raise ValueError("bad vtable sizes")
    out = []
    for i in range((vlen - 4) // 2):
        off = u16(b, vt + 4 + 2 * i)
        if off:
            out.append((i, t + off))
    return out


def fb_slot(b, t, slot):
    for s, p in fb_slots(b, t):
        if s == slot:
            return p
    return None


def fb_root(b):
    return u32(b, 0)


def fb_vec(b, p):
    tgt = p + u32(b, p)
    return tgt + 4, u32(b, tgt)


def fb_str(b, p):
    tgt = p + u32(b, p)
    n = u32(b, tgt)
    return b[tgt + 4:tgt + 4 + n].decode("utf-8", "replace")


def fb_table_vec(b, p):
    start, n = fb_vec(b, p)
    return [start + 4 * i + u32(b, start + 4 * i) for i in range(n)]


def summarize(sc):
    if sc.get("kind") == "sc1":
        return sc1_summary(sc)
    """Сводка по структуре, как её пишет gifconv/png2sc. Для чужих файлов может не совпасть."""
    lines = []
    lines.append(f"Файл: {fmt_size(sc['file_size'])}, версия {sc['version']}, "
                 f"распаковано {fmt_size(sc['inner_len'])}, блоков: {len(sc['chunks'])}"
                 + (f", лишних байт в конце: {sc['leftover']}" if sc["leftover"] else ""))
    chunks = sc["chunks"]
    if len(chunks) != 7:
        lines.append(yellow("  Блоков не 7, как в файлах gifconv/png2sc - структура другая, "
                            "смотри подробный дамп."))
    strings = []
    try:
        ds = chunks[0]
        t = fb_root(ds)
        p = fb_slot(ds, t, 0)
        if p is not None:
            start, n = fb_vec(ds, p)
            strings = [fb_str(ds, start + 4 * i) for i in range(n)]
        pv = fb_slot(ds, t, 5)
        verts = fb_vec(ds, pv)[1] // 12 if pv is not None else 0
        pe = fb_slot(ds, t, 4)
        elems = fb_vec(ds, pe)[1] // 3 if pe is not None else 0
        mats = cols = 0
        pb = fb_slot(ds, t, 6)
        if pb is not None:
            for bank in fb_table_vec(ds, pb):
                pm, pc = fb_slot(ds, bank, 0), fb_slot(ds, bank, 1)
                mats += fb_vec(ds, pm)[1] if pm is not None else 0
                cols += fb_vec(ds, pc)[1] if pc is not None else 0
        lines.append(f"DataStorage: строк {len(strings)}, вершин {verts}, элементов кадров {elems}, "
                     f"матриц {mats}, цветов {cols}")
    except Exception as e:
        lines.append(yellow(f"DataStorage: не разобрать по схеме gifconv ({e})"))
    try:
        en = chunks[1]
        t = fb_root(en)
        pi, pn = fb_slot(en, t, 0), fb_slot(en, t, 1)
        ids_start, n_ids = fb_vec(en, pi)
        nm_start, n_nm = fb_vec(en, pn)
        lines.append("ExportName'ы:")
        for i in range(min(n_ids, n_nm)):
            cid = u16(en, ids_start + 2 * i)
            ref = u32(en, nm_start + 4 * i)
            name = strings[ref] if ref < len(strings) else f"<строка #{ref}>"
            lines.append(f"  клип id {cid}  →  {name}")
    except Exception as e:
        lines.append(yellow(f"ExportNames: не разобрать по схеме gifconv ({e})"))
    try:
        sh = chunks[3]
        t = fb_root(sh)
        p = fb_slot(sh, t, 0)
        n = fb_vec(sh, p)[1] if p is not None else 0
        lines.append(f"Shapes: {n}")
    except Exception as e:
        lines.append(yellow(f"Shapes: не разобрать по схеме gifconv ({e})"))
    try:
        mc = chunks[4]
        t = fb_root(mc)
        p = fb_slot(mc, t, 0)
        clips = fb_table_vec(mc, p) if p is not None else []
        lines.append(f"MovieClips: {len(clips)}")
        for c in clips[:20]:
            ps = {s: q for s, q in fb_slots(mc, c)}
            cid = u16(mc, ps[0]) if 0 in ps else "?"
            fps = mc[ps[2]] if 2 in ps else "?"
            frames = u16(mc, ps[3]) if 3 in ps else "?"
            kids = fb_vec(mc, ps[5])[1] if 5 in ps else 0
            lines.append(f"  клип {cid}: {frames} кадр., {fps} fps, детей {kids}")
    except Exception as e:
        lines.append(yellow(f"MovieClips: не разобрать по схеме gifconv ({e})"))
    try:
        tx = chunks[6]
        t = fb_root(tx)
        p = fb_slot(tx, t, 0)
        sets = fb_table_vec(tx, p) if p is not None else []
        lines.append(f"Текстуры: {len(sets)}")
        for i, s in enumerate(sets[:30]):
            pd = fb_slot(tx, s, 1)
            td = pd + u32(tx, pd)
            ps = {k: q for k, q in fb_slots(tx, td)}
            fmt = tx[ps[0]] if 0 in ps else 0
            w = u16(tx, ps[2]) if 2 in ps else 0
            h = u16(tx, ps[3]) if 3 in ps else 0
            size = fb_vec(tx, ps[4])[1] if 4 in ps else 0
            kind = ""
            if fmt != 0 and 4 in ps:
                kind = "  (" + describe_texture_payload(tx, ps[4]) + ")"
            lines.append(f"  #{i}: {w}x{h}, формат {fmt}, {fmt_size(size)}{kind}")
        if len(sets) > 30:
            lines.append(f"  ... ещё {len(sets) - 30}")
    except Exception as e:
        lines.append(yellow(f"Textures: не разобрать по схеме gifconv ({e})"))
    try:
        tf = chunks[2]
        if len(tf) >= 16:
            t = fb_root(tf)
            p = fb_slot(tf, t, 0)
            if p is not None:
                vs, vn = fb_vec(tf, p)
                fonts = {}
                for i in range(vn):
                    font = u32(tf, vs + 40 * i + 4)
                    name = strings[font] if font < len(strings) else f"<строка #{font}>"
                    fonts[name] = fonts.get(name, 0) + 1
                lines.append(f"TextFields: {vn} (запись 40 байт: id, шрифт, рамка int16 x4, ...)")
                for name, cnt in fonts.items():
                    lines.append(f"  шрифт {name!r}: {cnt}")
    except Exception as e:
        lines.append(yellow(f"TextFields: не разобрать ({e})"))
    return lines


ASTC_GL = {0x93B0: "4x4", 0x93B1: "5x4", 0x93B2: "5x5", 0x93B3: "6x5", 0x93B4: "6x6", 0x93B5: "8x5",
           0x93B6: "8x6", 0x93B7: "8x8", 0x93B8: "10x5", 0x93B9: "10x6", 0x93BA: "10x8",
           0x93BB: "10x10", 0x93BC: "12x10", 0x93BD: "12x12"}


def describe_texture_payload(tx, data_field_pos):
    """Формат 8 в реальных файлах - это KTX1-контейнер; показываем, что внутри."""
    start, n = fb_vec(tx, data_field_pos)
    head = bytes(tx[start:start + 64])
    if n >= 64 and head[1:6] == b"KTX 1":
        gl_internal = struct.unpack_from("<I", head, 12 + 4 * 4)[0]
        if gl_internal in ASTC_GL:
            return f"KTX, ASTC {ASTC_GL[gl_internal]}, извлечь пока нельзя"
        return f"KTX, glInternalFormat 0x{gl_internal:X}, извлечь пока нельзя"
    return "не сырой RGBA, извлечь нельзя"


# ---- эвристический дамп: для файлов, схему которых я пока не знаю

MAX_DEPTH = 4
MAX_LINES = 4000


def looks_like_table(b, t):
    try:
        fb_slots(b, t)
        return True
    except (ValueError, struct.error):
        return False


def as_string(b, tgt):
    try:
        if tgt + 4 > len(b):
            return None
        n = u32(b, tgt)
        if n == 0 or n > 512 or tgt + 4 + n + 1 > len(b) or b[tgt + 4 + n] != 0:
            return None
        s = b[tgt + 4:tgt + 4 + n].decode("utf-8")
        return s if s.isprintable() else None
    except (UnicodeDecodeError, struct.error):
        return None


def dump_table(b, t, depth, out, ind):
    try:
        slots = fb_slots(b, t)
    except (ValueError, struct.error):
        out.append(f"{ind}(не таблица)")
        return
    for s, p in slots:
        if len(out) > MAX_LINES:
            out.append(ind + "... (обрезано)")
            return
        avail = len(b) - p
        parts = [f"u8={b[p]}"]
        if avail >= 2:
            parts.append(f"u16={u16(b, p)}")
        if avail >= 4:
            parts.append(f"u32={u32(b, p)}")
            try:
                parts.append(f"f32={f32(b, p):.5g}")
            except struct.error:
                pass
        text = f"{ind}slot {s} @+{p - t}: " + " ".join(parts)
        follow = None
        if avail >= 4:
            tgt = p + u32(b, p)
            if 4 < tgt < len(b) - 3:
                st = as_string(b, tgt)
                if st is not None:
                    text += f"   -> строка {st!r}"
                else:
                    try:
                        n = u32(b, tgt)
                    except struct.error:
                        n = None
                    if n is not None and 0 < n < 10_000_000:
                        refs = []
                        check = min(n, 64)
                        ok = tgt + 4 + 4 * check <= len(b)
                        if ok:
                            for i in range(check):
                                e = tgt + 4 + 4 * i
                                r = e + u32(b, e)
                                if 4 < r < len(b) and (looks_like_table(b, r) or as_string(b, r) is not None):
                                    refs.append(r)
                                else:
                                    ok = False
                                    break
                        if ok and refs:
                            text += f"   -> вектор ссылок? n={n}"
                            follow = ("vec", tgt, n)
                        elif tgt + 4 + n <= len(b):
                            head = b[tgt + 4:tgt + 4 + 24].hex(" ")
                            text += f"   -> вектор? n={n} начало: {head}"
                            if looks_like_table(b, tgt) and depth < MAX_DEPTH and n < 64:
                                follow = ("tbl", tgt, None)
                    elif n == 0:
                        pass
        out.append(text)
        if follow and depth < MAX_DEPTH:
            kind, tgt, n = follow
            if kind == "vec":
                for i in range(min(n, 2)):
                    e = tgt + 4 + 4 * i
                    r = e + u32(b, e)
                    st = as_string(b, r)
                    if st is not None:
                        out.append(f"{ind}    [{i}] строка {st!r}")
                    else:
                        out.append(f"{ind}    [{i}] таблица:")
                        dump_table(b, r, depth + 1, out, ind + "        ")
                if n > 2:
                    out.append(f"{ind}    ... ещё {n - 2}")
            else:
                dump_table(b, tgt, depth + 1, out, ind + "    ")


def build_dump(path, sc):
    if sc.get("kind") == "sc1":
        return build_dump_sc1(path, sc)
    out = [f"Дамп: {os.path.basename(path)}", "=" * 60]
    for l in summarize(sc):
        out.append(re.sub(r"\033\[[0-9;]*m", "", l))
    out.append("")
    out.append("Файловый дескриптор (FileDescriptor):")
    fd = sc["fd"]
    try:
        ft = fb_root(fd)
        names = {2: "шейпов", 3: "клипов", 4: "текстур", 5: "TextField'ов",
                 8: "смещение ресурсов", 9: "длина текстур"}
        vals = []
        for slot, label in names.items():
            p = fb_slot(fd, ft, slot)
            vals.append(f"{label}: {u32(fd, p)}" if p is not None else f"{label}: нет")
        out.append("  по схеме gifconv (сверено на реальном файле): " + ", ".join(vals))
        out.append("  ВНИМАНИЕ: эвристика ниже иногда принимает такой счётчик за ссылку на вектор "
                   "(\"вектор ссылок?\" на слотах 3-4 - ложное срабатывание). Опирайся на строку выше.")
    except Exception:
        pass
    try:
        dump_table(fd, fb_root(fd), 0, out, "  ")
    except Exception as e:
        out.append(f"  ошибка: {e}")
    for i, chunk in enumerate(sc["chunks"]):
        name = CHUNK_NAMES[i] if len(sc["chunks"]) == 7 else f"chunk {i}"
        out.append("")
        out.append(f"--- блок {i}: {name}, {len(chunk)} байт ---")
        if len(chunk) < 8:
            out.append("  (пустой)")
            continue
        try:
            dump_table(chunk, fb_root(chunk), 0, out, "  ")
        except Exception as e:
            out.append(f"  ошибка: {e}")
    return "\n".join(out) + "\n"


def flow_inspect():
    ensure_deps()
    src = resolve_input("Какой .sc разобрать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    line()
    print(f"  Формат: {'SC2 (flatbuffers)' if sc.get('kind') != 'sc1' else 'SC1 (теги)'}")
    print_sc_package(src)
    for l in summarize(sc):
        print("  " + l)
    stem = os.path.splitext(os.path.basename(src))[0]
    dump_path = resolve_output(ask("Куда сохранить подробный дамп", f"{stem}_dump.txt"), ".txt")
    with open(dump_path, "w", encoding="utf-8") as fh:
        fh.write(build_dump(src, sc))
    saved(dump_path)
    print(gray("  Дамп можно отправить мне вместо самого .sc - по нему видна вся структура."))
    return [dump_path]


# ================================================================ переименование ExportName
#
# ExportName лежит в трёх местах (сверено на реальном файле, порядок везде одинаковый):
#   1) DataStorage: таблица строк (вектор строк, слот 0);
#   2) блок ExportNames: id клипа + ИНДЕКС строки из п.1 (число, не сама строка);
#   3) FileDescriptor: список экспортов с именами (слот 10).
# Старые данные не трогаем. В конец DataStorage дописывается новая таблица строк
# (копии старых + новые имена), корневое поле переставляется на неё, индексы старых строк
# не меняются. В ExportNames меняются только индексы у переименованных экспортов.
# В дескриптор дописываются новые строки, а смещение ресурсов (слот 8) увеличивается
# на прирост DataStorage. Хеша данных в SC2-заголовке нет (проверено), пересчитывать нечего.

def _align4(buf):
    while len(buf) % 4:
        buf.append(0)


def _append_string(buf, raw):
    """Дописывает flatbuffers-строку в конец буфера, возвращает позицию её поля длины."""
    _align4(buf)
    pos = len(buf)
    buf += struct.pack("<I", len(raw)) + raw + b"\0"
    _align4(buf)
    return pos


def finalize_sc(sc, chunks, fdb):
    """Собирает готовый .sc из новых блоков. Смещение ресурсов в дескрипторе (слот 8) обновляется
    под новую длину DataStorage; если оно не равно 4+длина DataStorage, файл устроен иначе - отказ."""
    import zstandard
    fd = sc["fd"]
    ft = fb_root(fd)
    p8 = fb_slot(fd, ft, 8)
    if p8 is None or u32(fd, p8) != 4 + len(sc["chunks"][0]):
        raise UserError("Смещение ресурсов в дескрипторе не равно 4+длина DataStorage - "
                        "файл устроен иначе, чем ожидалось, не рискую его менять")
    fdb = bytearray(fdb)
    p8 = fb_slot(fdb, fb_root(fdb), 8)
    if p8 is None:
        raise UserError("В новом дескрипторе нет поля смещения ресурсов")
    struct.pack_into("<I", fdb, p8, 4 + len(chunks[0]))
    inner = b"".join(struct.pack("<I", len(c)) + c for c in chunks)
    comp = zstd_pack(inner)
    p11 = fb_slot(fdb, fb_root(fdb), 11)
    if p11 is not None:                      # у части файлов в дескрипторе лежит размер сжатых данных
        struct.pack_into("<I", fdb, p11, len(comp))
    return b"".join([b"SC", struct.pack("<I", sc["version"]), struct.pack("<I", len(fdb)), bytes(fdb), comp])


def get_exports(sc):
    if sc.get("kind") == "sc1":
        return sc1_exports(sc)
    ds, en = sc["chunks"][0], sc["chunks"][1]
    t = fb_root(ds)
    ps = fb_slot(ds, t, 0)
    if ps is None:
        raise UserError("В DataStorage нет таблицы строк")
    s0, n = fb_vec(ds, ps)
    strings = [fb_str(ds, s0 + 4 * i) for i in range(n)]
    et = fb_root(en)
    pi, pn = fb_slot(en, et, 0), fb_slot(en, et, 1)
    if pi is None or pn is None:
        raise UserError("В файле нет ExportName'ов")
    ids_s, n_ids = fb_vec(en, pi)
    nm_s, n_nm = fb_vec(en, pn)
    if n_ids != n_nm:
        raise UserError("Число id и имён в блоке ExportNames не совпадает")
    out = []
    for i in range(n_ids):
        ref = u32(en, nm_s + 4 * i)
        if ref >= len(strings):
            raise UserError(f"Экспорт #{i} ссылается на несуществующую строку {ref}")
        out.append({"id": u16(en, ids_s + 2 * i), "ref": ref, "name": strings[ref]})
    return out, strings


def fd_export_names(fd):
    ft = fb_root(fd)
    p = fb_slot(fd, ft, 10)
    if p is None:
        return []
    return [fb_str(fd, fb_slot(fd, t, 0)) for t in fb_table_vec(fd, p)]


def plan_rename(names, mapping):
    """Проверяет карту переименований. Возвращает только реальные изменения {старое: новое}."""
    unknown = [o for o in mapping if o not in names]
    if unknown:
        shown = ", ".join(repr(x) for x in unknown[:8])
        raise UserError(f"Нет таких ExportName'ов в файле: {shown}" + (" ..." if len(unknown) > 8 else ""))
    changes = {o: n for o, n in mapping.items() if o != n}
    for o, n in changes.items():
        if not n:
            raise UserError(f"Пустое новое имя для «{o}»")
        if "\0" in n or any(ord(c) < 32 for c in n):
            raise UserError(f"В новом имени «{n}» есть управляющие символы")
    final = [changes.get(x, x) for x in names]
    seen, dups = set(), []
    for x in final:
        if x in seen and x not in dups:
            dups.append(x)
        seen.add(x)
    if dups:
        raise UserError("Получились одинаковые ExportName'ы: " + ", ".join(repr(x) for x in dups[:8]))
    return changes


def rename_exports(sc, mapping):
    """Возвращает (байты нового .sc, {старое: новое}). Исходные данные не меняются."""
    if sc.get("kind") == "sc1":
        return sc1_rename_exports(sc, mapping)
    """Возвращает (байты нового .sc, {старое: новое}). Исходные данные не меняются."""
    import zstandard
    if sc["version"] != 5 or len(sc["chunks"]) != 7 or sc["leftover"]:
        raise UserError("Переименование умеет только SC2 версии 5 с 7 блоками (как у твоего файла)")
    exports, strings = get_exports(sc)
    names = [e["name"] for e in exports]
    changes = plan_rename(names, mapping)
    if not changes:
        raise UserError("Все новые имена совпадают со старыми - менять нечего")

    ds = sc["chunks"][0]
    en = bytearray(sc["chunks"][1])
    fd = sc["fd"]

    # 1) DataStorage: новая таблица строк в конце буфера (вектор идёт раньше строк, оффсеты только вперёд)
    t = fb_root(ds)
    p0 = fb_slot(ds, t, 0)
    s0, n = fb_vec(ds, p0)
    old_raw = []
    for i in range(n):
        tp = s0 + 4 * i + u32(ds, s0 + 4 * i)
        old_raw.append(bytes(ds[tp + 4:tp + 4 + u32(ds, tp)]))
    index_of = {}
    for i, s in enumerate(strings):
        index_of.setdefault(s, i)
    all_raw = list(old_raw)
    for new in changes.values():
        if new not in index_of:
            index_of[new] = len(all_raw)
            all_raw.append(new.encode("utf-8"))
    b = bytearray(ds)
    _align4(b)
    vec_pos = len(b)
    m = len(all_raw)
    b += struct.pack("<I", m) + bytes(4 * m)
    str_pos = []
    for raw in all_raw:
        str_pos.append(_append_string(b, raw))
    for i in range(m):
        e = vec_pos + 4 + 4 * i
        struct.pack_into("<I", b, e, str_pos[i] - e)
    struct.pack_into("<I", b, p0, vec_pos - p0)
    new_ds = bytes(b)

    # 2) ExportNames: у переименованных меняем индекс строки
    et = fb_root(en)
    nm_s, _ = fb_vec(en, fb_slot(en, et, 1))
    for i, e in enumerate(exports):
        if e["name"] in changes:
            struct.pack_into("<I", en, nm_s + 4 * i, index_of[changes[e["name"]]])

    # 3) FileDescriptor: новые строки в конец + смещение ресурсов
    fdb = bytearray(fd)
    ft = fb_root(fd)
    p10 = fb_slot(fd, ft, 10)
    if p10 is None:
        raise UserError("В дескрипторе нет списка экспортов")
    for tpos in fb_table_vec(fd, p10):
        pn = fb_slot(fd, tpos, 0)
        old_name = fb_str(fd, pn)
        if old_name in changes:
            spos = _append_string(fdb, changes[old_name].encode("utf-8"))
            struct.pack_into("<I", fdb, pn, spos - pn)
    chunks = list(sc["chunks"])
    chunks[0], chunks[1] = new_ds, bytes(en)
    data = finalize_sc(sc, chunks, fdb)

    verify_rename(sc, data, changes)
    return data, changes


def verify_rename(old, new_data, changes):
    """Перечитывает готовый файл и сверяет с оригиналом. При расхождении бросает UserError."""
    new = parse_sc(new_data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if len(new["chunks"]) != 7 or new["leftover"]:
        fail("структура блоков нарушена")
    for i in (2, 3, 4, 5, 6):
        if new["chunks"][i] != old["chunks"][i]:
            fail(f"блок {CHUNK_NAMES[i]} изменился")
    old_exp, old_str = get_exports(old)
    new_exp, new_str = get_exports(new)
    expected = [(e["id"], changes.get(e["name"], e["name"])) for e in old_exp]
    if [(e["id"], e["name"]) for e in new_exp] != expected:
        fail("список ExportName'ов не совпал с ожидаемым")
    if new_str[:len(old_str)] != old_str:
        fail("старые строки в таблице изменились")
    o_ds, n_ds = old["chunks"][0], new["chunks"][0]
    p0 = fb_slot(o_ds, fb_root(o_ds), 0)
    if n_ds[:p0] != o_ds[:p0] or n_ds[p0 + 4:len(o_ds)] != o_ds[p0 + 4:]:
        fail("DataStorage изменился не только в одном поле")
    if len(n_ds) % 4:
        fail("длина DataStorage не кратна 4")
    if fd_export_names(new["fd"]) != [x[1] for x in expected]:
        fail("имена в дескрипторе не совпали")
    o_fd, n_fd = old["fd"], new["fd"]
    for slot in (0, 1, 2, 3, 4, 5, 9):
        po, pn = fb_slot(o_fd, fb_root(o_fd), slot), fb_slot(n_fd, fb_root(n_fd), slot)
        if (po is None) != (pn is None) or (po is not None and u32(o_fd, po) != u32(n_fd, pn)):
            fail(f"поле дескриптора {slot} изменилось")
    if u32(n_fd, fb_slot(n_fd, fb_root(n_fd), 8)) != 4 + len(n_ds):
        fail("смещение ресурсов в дескрипторе неверное")
    if len(n_fd) % 4:
        fail("длина дескриптора не кратна 4")


def parse_rename_file(path):
    mapping = {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        for num, raw in enumerate(fh, 1):
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            for sep in ("->", "=", "\t"):
                if sep in text:
                    old, new = (x.strip() for x in text.split(sep, 1))
                    break
            else:
                raise UserError(f"Строка {num} в {os.path.basename(path)}: нужен формат старое=новое")
            if not old or not new:
                raise UserError(f"Строка {num}: пустое имя")
            mapping[old] = new
    return mapping


def flow_add_names(sc, src, m, exports, id_to_name):
    have = {e["id"] for e in exports}
    parents = {}
    for c in m["clips"]:
        for ch in set(c["children"]):
            parents.setdefault(ch, set()).add(c["id"])
    unnamed_clips = [c for c in m["clips"] if c["id"] not in have]
    top = [c for c in unnamed_clips if not parents.get(c["id"])]
    shapes_un = [s_ for s_ in m["shapes"] if s_["id"] not in have]
    tf_un = [i for i in sorted(m["tf_bounds"]) if i not in have]
    clip_by = {c["id"]: c for c in unnamed_clips}
    shape_by = {s_["id"]: s_ for s_ in shapes_un}
    kinds = {}
    for c in unnamed_clips:
        kinds[c["id"]] = "clip"
    for s_ in shapes_un:
        kinds[s_["id"]] = "shape"
    for i in tf_un:
        kinds[i] = "tf"
    if not kinds:
        raise UserError("У всех объектов в файле уже есть ExportName")
    print(f"  Без ExportName: клипов {len(unnamed_clips)} (верхнеуровневых, ни в одном клипе не лежат: {len(top)}), "
          f"шейпов {len(shapes_un)}, текстовых полей {len(tf_un)}")

    def object_info(oid):
        k = kinds[oid]
        if k == "clip":
            return clip_info_line(m, clip_by[oid]["index"], id_to_name, with_pos=True)
        if k == "shape":
            sh = shape_by[oid]
            pts = shape_points(m, sh["index"])
            bb = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)) if pts else None
            users = len(parents.get(oid, ()))
            return f"шейп id {oid}: {len(pts)} вершин, " + (fmt_bbox(bb) if bb else "вершин нет") + f"; лежит в клипах: {users}"
        return f"текстовое поле id {oid}; лежит в клипах: {len(parents.get(oid, ()))}"

    shown = top if top else unnamed_clips
    print(gray("  Верхнеуровневые клипы без имени (положение считается по кадру 1, от точки (0,0) клипа):"))
    for c in shown[:12]:
        print(gray("    " + clip_info_line(m, c["index"], id_to_name, with_pos=True)))
    if len(shown) > 12:
        print(gray(f"    ... ещё {len(shown) - 12}"))
    print("  Вводи id объекта (клип, шейп или текстовое поле), потом имя. Пустой ввод - закончить.")
    print(gray("  «shapes» - показать шейпы без имени, «@файл» - строки «id имя»."))
    additions = {}
    while True:
        raw = ask("id объекта без имени", "").strip()
        if not raw:
            break
        pairs = []
        if raw.lower() == "shapes":
            for s_ in shapes_un[:20]:
                print(gray("    " + object_info(s_["id"])))
            if len(shapes_un) > 20:
                print(gray(f"    ... ещё {len(shapes_un) - 20}"))
            continue
        if raw.startswith("@"):
            fpath = unquote(raw[1:])
            if not os.path.isabs(fpath) and not os.path.dirname(fpath):
                fpath = os.path.join(IN_DIR, fpath)
            if not os.path.isfile(fpath):
                print(red(f"  Нет файла {fpath}"))
                continue
            with open(fpath, "r", encoding="utf-8-sig") as fh:
                for num, ln in enumerate(fh, 1):
                    ln = ln.strip()
                    if not ln or ln.startswith("#"):
                        continue
                    parts = re.split(r"[\s=:;]+", ln, maxsplit=1)
                    if len(parts) != 2 or not parts[0].isdigit():
                        raise UserError(f"Строка {num}: нужен формат «id имя»")
                    pairs.append((int(parts[0]), parts[1].strip()))
        else:
            if not raw.isdigit():
                print(red(f"  «{raw}» - не id"))
                continue
            oid = int(raw)
            if oid in have:
                print(red(f"  У объекта {oid} уже есть ExportName ({id_to_name.get(oid)}) - для смены имени есть способ 1"))
                continue
            if oid not in kinds:
                print(red(f"  Нет клипа, шейпа или текстового поля с id {oid}"))
                continue
            print(gray("    " + object_info(oid)))
            name = ask("Имя для этого объекта").strip()
            if not name:
                print(gray("  Имя не задано, объект пропущен"))
                continue
            pairs.append((oid, name))
        for oid, name in pairs:
            if oid in additions:
                print(yellow(f"  Объекту {oid} имя уже задано ({additions[oid]}), заменяю на {name}"))
            additions[oid] = name
        print(green(f"  ✔ Имён в списке: {len(additions)}"))
    if not additions:
        raise UserError("Ни одного имени не задано")
    check_new_export_names([e["name"] for e in exports], list(additions.values()))
    for oid in additions:
        if oid in have or oid not in kinds:
            raise UserError(f"Объект {oid} нельзя назвать: у него уже есть ExportName или его нет в файле")
    if sc.get("kind") == "sc1" and any(kinds[o] == "tf" for o in additions):
        raise UserError("В SC1 хаб не знает текстовых полей")
    line()
    labels = {"clip": "клип", "shape": "шейп", "tf": "текстовое поле"}
    for oid, name in additions.items():
        print(f"    {labels[kinds[oid]]} {oid}  {cyan('→')}  {name}")
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")
    print(gray("  Пересобираю и сжимаю (несколько секунд)..."))
    data = add_exports(sc, additions)
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    print(green("  ✔ Проверка пройдена: ") + gray(f"добавлено имён: {len(additions)}, остальное содержимое без изменений"))
    if sc.get("kind") == "sc1":
        print(yellow("  ⚠ SC1: в хвосте файла (START…) для новых имён записей нет: что означают их 16-байтные значения, "
                     "я не выяснил."))
    return [out_path]


def flow_rename():
    ensure_deps()
    src = resolve_input("Какой .sc переименовывать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    exports, _strings = get_exports(sc)
    names = [e["name"] for e in exports]
    id_to_name = {e["id"]: e["name"] for e in exports}
    m = load_model(sc)
    print(f"  Формат: {'SC2' if sc.get('kind') != 'sc1' else 'SC1'}, ExportName'ов в файле: {len(names)}")
    for x in names[:8]:
        print(gray("    " + x))
    if len(names) > 8:
        print(gray(f"    ... ещё {len(names) - 8}"))
    print("  Как переименовать:")
    print("    1 - одно имя")
    print("    2 - заменить часть имени сразу во всех (например glowbert_ на bea_)")
    print("    3 - списком из файла, по строке на имя: старое=новое")
    print("    4 - дать ExportName объектам, у которых его ещё нет: клипам, шейпам, текстовым полям (по id)")
    mode = ask("Способ", "1")

    if mode == "4":
        return flow_add_names(sc, src, m, exports, id_to_name)

    mapping = {}
    if mode == "1":
        old = ask("Какое имя переименовать")
        if old not in names:
            hits = [x for x in names if old and old.lower() in x.lower()]
            hint = ("\n  Похожие:\n    " + "\n    ".join(hits[:15])) if hits else ""
            raise UserError(f"Нет ExportName «{old}».{hint}")
        found = find_clips_by_pattern(m, exports, old)
        if found:
            print(gray("    " + clip_info_line(m, found[0], id_to_name)))
        new = ask("Новое имя")
        if not new:
            raise UserError("Новое имя не задано")
        mapping[old] = new
    elif mode == "2":
        find = ask("Какой кусок имени заменить")
        if not find:
            raise UserError("Кусок для замены не задан")
        repl = ask("На что заменить (Enter - просто убрать)", "")
        mapping = {x: x.replace(find, repl) for x in names if find in x}
        if not mapping:
            raise UserError(f"Ни одно имя не содержит «{find}»")
    elif mode == "3":
        rpath = ask("Файл со списком", "renames.txt")
        rpath = unquote(rpath)
        if not os.path.isabs(rpath) and not os.path.dirname(rpath):
            rpath = os.path.join(IN_DIR, rpath)
        if not os.path.isfile(rpath):
            with open(rpath, "w", encoding="utf-8") as fh:
                fh.write("# Правь только то, что надо поменять: старое=новое. Остальные строки можно удалить.\n")
                for x in names:
                    fh.write(f"{x}={x}\n")
            saved(rpath)
            raise UserError("Файла не было, я создал шаблон со всеми именами. Отредактируй и запусти пункт снова.")
        mapping = parse_rename_file(rpath)
    else:
        raise UserError("Нет такого способа")

    changes = plan_rename(names, mapping)
    if not changes:
        raise UserError("Все новые имена совпадают со старыми - менять нечего")
    line()
    print(f"  Будет переименовано: {len(changes)}")
    for o, n in list(changes.items())[:20]:
        print(f"    {o}  {cyan('→')}  {n}")
    if len(changes) > 20:
        print(gray(f"    ... ещё {len(changes) - 20}"))

    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")

    print(gray("  Пересобираю и сжимаю (несколько секунд)..."))
    data, changes = rename_exports(sc, mapping)
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    print(green("  ✔ Проверка пройдена: ") + gray(f"переименовано {len(changes)}, остальное содержимое файла "
                                                   f"без изменений"))
    return [out_path]


# ================================================================ размер и положение (MovieClip и Shape)
#
# Всё сверено на реальном файле по твоему data.msgpack (все 95 596 матриц, все 252 422 элемента
# кадров, все 2 957 команд шейпов совпали):
#   * Вершины шейпа: DataStorage, байтовый вектор, слот 5, по 12 байт (float x, float y, u16 u, u16 v).
#     Команда шейпа - struct из 4 uint32: (тип, номер текстуры, число вершин, номер первой вершины).
#   * Элемент кадра: 3 uint16 (номер ребёнка, номер матрицы, номер цвета), 0xFFFF = "нет".
#     Клип берёт свои элементы из общего вектора со смещением (слот 9 клипа, в uint16), банк - слот 10.
#   * Матрица: 6 float (a, b, c, d, tx, ty), лежит в банке матриц (DataStorage, слот 6).
# Матрицы часто общие для многих клипов, поэтому:
#   * матрица, которой пользуется только этот клип, правится на месте;
#   * общая матрица не трогается, вместо неё в банк дописывается новая (вектор банка пересобирается
#     в конце буфера), и только у элементов этого клипа меняется номер матрицы.
# Шейп правится на месте (вершины у шейпов не пересекаются, проверено), но он общий для всех клипов,
# которые его используют - хаб показывает, кто ещё его использует.

NO_INDEX = 0xFFFF
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def f32r(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def xf_matrix(m, kx, ky, dx, dy):
    """Клип масштабируется от своей точки (0,0), затем сдвигается: x' = kx*x + dx, y' = ky*y + dy."""
    a, b, c, d, tx, ty = m
    return tuple(f32r(v) for v in (a * kx, b * ky, c * kx, d * ky, tx * kx + dx, ty * ky + dy))


def load_model(sc):
    if sc.get("kind") == "sc1":
        return sc1_load_model(sc)
    chunks = sc["chunks"]
    if sc["version"] != 5 or len(chunks) != 7 or sc["leftover"]:
        raise UserError("Умею работать только с SC2 версии 5 и 7 блоками (как у твоего файла)")
    ds, shb, mcb = chunks[0], chunks[3], chunks[4]
    t = fb_root(ds)
    pv, pe, pb = fb_slot(ds, t, 5), fb_slot(ds, t, 4), fb_slot(ds, t, 6)
    v_start, v_bytes = fb_vec(ds, pv) if pv is not None else (0, 0)
    e_start, e_n = fb_vec(ds, pe) if pe is not None else (0, 0)
    banks = []
    if pb is not None:
        for bt in fb_table_vec(ds, pb):
            pm, pc, ph = fb_slot(ds, bt, 0), fb_slot(ds, bt, 1), fb_slot(ds, bt, 2)
            ms, mn = fb_vec(ds, pm) if pm is not None else (0, 0)
            cs, cn = fb_vec(ds, pc) if pc is not None else (0, 0)
            hs, hn = fb_vec(ds, ph) if ph is not None else (0, 0)       # матрицы половинной точности: 6 x int16
            banks.append({"field": pm, "cfield": pc, "start": ms, "count": mn, "cstart": cs, "ccount": cn,
                          "hstart": hs, "hcount": hn})
    n_verts = v_bytes // 12

    shapes = []
    sp = fb_slot(shb, fb_root(shb), 0)
    for si, s in enumerate(fb_table_vec(shb, sp) if sp is not None else []):
        ps = dict(fb_slots(shb, s))
        sid = u16(shb, ps[0]) if 0 in ps else 0
        verts = []
        if 1 in ps:
            cs, cn = fb_vec(shb, ps[1])
            for i in range(cn):
                _typ, _tex, cnt, first = struct.unpack_from("<4I", shb, cs + 16 * i)
                if first + cnt > n_verts:
                    raise UserError(f"Шейп {sid} ссылается на вершины за пределами таблицы")
                verts.extend(range(first, first + cnt))
        shapes.append({"index": si, "id": sid, "verts": verts})

    clips = []
    cp = fb_slot(mcb, fb_root(mcb), 0)
    for ci, c in enumerate(fb_table_vec(mcb, cp) if cp is not None else []):
        ps = dict(fb_slots(mcb, c))
        cid = u16(mcb, ps[0]) if 0 in ps else 0
        children = []
        if 5 in ps:
            cs, cn = fb_vec(mcb, ps[5])
            children = list(struct.unpack_from(f"<{cn}H", mcb, cs)) if cn else []
        n_el, frame0, nframes = 0, 0, 0
        if 8 in ps:
            fs, fn = fb_vec(mcb, ps[8])
            n_el = sum(u32(mcb, fs + 8 * i) for i in range(fn))
            nframes = fn
            frame0 = u32(mcb, fs) if fn else 0
        off = u32(mcb, ps[9]) if 9 in ps else None
        bank = u32(mcb, ps[10]) if 10 in ps else 0
        if n_el and (off is None or off + 3 * n_el > e_n):
            raise UserError(f"У клипа {cid} элементы кадров лежат за пределами вектора")
        clips.append({"index": ci, "id": cid, "children": children, "n_el": n_el,
                      "offset": off, "bank": bank, "bank_field": ps.get(10),
                      "grid": u32(mcb, ps[11]) if 11 in ps else None,
                      "fps": mcb[ps[2]] if 2 in ps else 0, "fps_field": ps.get(2),
                      "frame0": frame0, "nframes": nframes})
    tf_bounds = {}
    tfc = chunks[2]
    if len(tfc) >= 16:
        tp = fb_slot(tfc, fb_root(tfc), 0)
        if tp is not None:
            vs, vn = fb_vec(tfc, tp)
            for i in range(vn):
                at = vs + 40 * i
                tf_bounds[u32(tfc, at)] = struct.unpack_from("<4h", tfc, at + 8)
    grids = []
    pg = fb_slot(ds, t, 3)
    if pg is not None:
        gs, gn = fb_vec(ds, pg)
        grids = [struct.unpack_from("<4f", ds, gs + 16 * i) for i in range(gn)]
    return {"kind": "sc2", "ds": ds, "v_start": v_start, "n_verts": n_verts, "e_start": e_start, "e_n": e_n,
            "banks": banks, "shapes": shapes, "clips": clips, "tf_bounds": tf_bounds, "grids": grids}


def bank_matrices(m, bank):
    if bank >= len(m["banks"]):
        raise UserError(f"Клип ссылается на несуществующий банк матриц {bank}")
    bk = m["banks"][bank]
    return list(struct.iter_unpack("<6f", m["ds"][bk["start"]:bk["start"] + 24 * bk["count"]]))


def clip_flat(m, clip):
    if m.get("kind") == "sc1":
        return clip["flat"]
    """Плоский список uint16: (ребёнок, матрица, цвет) * число элементов."""
    if not clip["n_el"]:
        return []
    return list(struct.unpack_from(f"<{3 * clip['n_el']}H", m["ds"], m["e_start"] + 2 * clip["offset"]))


def clip_effective(m, clip, mats_cache):
    if m.get("kind") == "sc1":
        return [IDENTITY if i == NO_INDEX else sc1_mat_float(m["mats"][i]) for i in clip["flat"][1::3]]
    flat = clip_flat(m, clip)
    if not flat:
        return []
    bank = clip["bank"]
    if bank not in mats_cache:
        mats_cache[bank] = bank_matrices(m, bank)
    mats = mats_cache[bank]
    out = []
    for k in range(clip["n_el"]):
        mi = flat[3 * k + 1]
        if mi == NO_INDEX:
            out.append(IDENTITY)
        elif mi < len(mats):
            out.append(mats[mi])
        else:
            raise UserError(f"Клип {clip['id']}: номер матрицы {mi} вне банка {bank}")
    return out


def apply_transform(sc, clip_targets=None, shape_targets=None, pivot="origin"):
    """clip_targets: {индекс клипа: (kx, ky, dx, dy)}, shape_targets: {индекс шейпа: (kx, ky, dx, dy)}.
    Возвращает байты нового .sc. Исходные данные не меняются."""
    clip_targets = clip_targets or {}
    shape_targets = shape_targets or {}
    if sc.get("kind") == "sc1":
        return sc1_apply_transform(sc, clip_targets, shape_targets, pivot)
    m = load_model(sc)
    ds = m["ds"]
    b = bytearray(ds)
    moved = {}

    if clip_targets:
        # кто какими матрицами пользуется (по всем клипам, не только по выбранным)
        usage, flats = {}, {}
        for clip in m["clips"]:
            if not clip["n_el"]:
                continue
            flat = clip_flat(m, clip)
            if clip["index"] in clip_targets:
                flats[clip["index"]] = flat
            for idx in set(flat[1::3]):
                if idx != NO_INDEX:
                    usage.setdefault((clip["bank"], idx), set()).add(clip["index"])
        mats_cache = {}
        inplace = {}            # (банк, номер) -> новая матрица
        appended_m = {}         # банк -> {матрица: будущий номер}
        appended_c = {}         # банк -> {7 байт цвета: будущий номер}
        plans = {}

        def bank_mats(bank):
            if bank not in mats_cache:
                mats_cache[bank] = bank_matrices(m, bank)
            return mats_cache[bank]

        def n_m(bank):
            return m["banks"][bank]["count"] + len(appended_m.get(bank, {}))

        def n_c(bank):
            return m["banks"][bank]["ccount"] + len(appended_c.get(bank, {}))

        for ci, (kx, ky, dx, dy) in clip_targets.items():
            clip = m["clips"][ci]
            if not clip["n_el"]:
                raise UserError(f"У клипа {clip['id']} нет элементов кадров - менять нечего")
            flat = flats[ci]
            bank = clip["bank"]
            bk = m["banks"][bank] if bank < len(m["banks"]) else None
            if bk is None:
                raise UserError(f"Клип {clip['id']} ссылается на несуществующий банк матриц {bank}")
            mats = bank_mats(bank)
            used = sorted(set(flat[1::3]))
            new_of = {}
            for old in used:
                if old != NO_INDEX and old >= len(mats):
                    raise UserError(f"Клип {clip['id']}: номер матрицы {old} вне банка {bank}")
                new_of[old] = xf_matrix(IDENTITY if old == NO_INDEX else mats[old], kx, ky, dx, dy)
            stay = {old: (old != NO_INDEX and usage[(bank, old)] == {ci}) for old in used}
            need = {new_of[o] for o in used if not stay[o]}
            fresh = [k for k in need if k not in appended_m.get(bank, {})]
            fits = not fresh or (bk["field"] is not None and n_m(bank) + len(fresh) <= NO_INDEX)
            plan = {"map_m": {}, "map_c": None}
            if fits:
                # остаёмся в своём банке: свои матрицы правим на месте, общие - копируем в конец банка
                for old in used:
                    if stay[old]:
                        inplace[(bank, old)] = new_of[old]
                        plan["map_m"][old] = ("keep", old)
                    else:
                        appended_m.setdefault(bank, {}).setdefault(new_of[old], None)
                        plan["map_m"][old] = ("new", bank, new_of[old])
            else:
                # банк заполнен (как банк 32 в твоём файле): клип переезжает в другой банк,
                # у клипа меняется только номер банка, его матрицы и цвета копируются туда
                if clip["bank_field"] is None:
                    raise UserError(f"Банк {bank} полон, а у клипа {clip['id']} нет поля банка - "
                                    f"перенести его нельзя")
                cols_used = sorted(set(flat[2::3]) - {NO_INDEX})
                col_bytes = {}
                for cidx in cols_used:
                    if cidx >= bk["ccount"]:
                        raise UserError(f"Клип {clip['id']}: номер цвета {cidx} вне банка {bank}")
                    at = bk["cstart"] + 7 * cidx
                    col_bytes[cidx] = bytes(ds[at:at + 7])
                need_m, need_c = set(new_of.values()), set(col_bytes.values())
                best = None
                for cand, cb in enumerate(m["banks"]):
                    if cand == bank:
                        continue
                    fm = [k for k in need_m if k not in appended_m.get(cand, {})]
                    fc = [k for k in need_c if k not in appended_c.get(cand, {})]
                    if fm and cb["field"] is None:
                        continue
                    if fc and cb["cfield"] is None:
                        continue
                    if n_m(cand) + len(fm) > NO_INDEX or n_c(cand) + len(fc) > NO_INDEX:
                        continue
                    rebuilding = cand in appended_m or cand in appended_c
                    cost = 0 if rebuilding else cb["count"] * 24 + cb["ccount"] * 7
                    if best is None or cost < best[0]:
                        best = (cost, cand)
                if best is None:
                    raise UserError(f"Банк матриц {bank} полон, и другого банка с достаточным местом "
                                    f"для клипа {clip['id']} нет")
                dest = best[1]
                for old in used:
                    appended_m.setdefault(dest, {}).setdefault(new_of[old], None)
                    plan["map_m"][old] = ("new", dest, new_of[old])
                plan["map_c"] = {}
                for cidx, raw in col_bytes.items():
                    appended_c.setdefault(dest, {}).setdefault(raw, None)
                    plan["map_c"][cidx] = (dest, raw)
                moved[ci] = dest
            plans[ci] = plan

        idx_m = {bank: {k: m["banks"][bank]["count"] + i for i, k in enumerate(d)}
                 for bank, d in appended_m.items()}
        idx_c = {bank: {k: m["banks"][bank]["ccount"] + i for i, k in enumerate(d)}
                 for bank, d in appended_c.items()}
        for (bank, idx), new in inplace.items():
            struct.pack_into("<6f", b, m["banks"][bank]["start"] + 24 * idx, *new)
        for bank, d in appended_m.items():
            bk = m["banks"][bank]
            _align4(b)
            vec_pos = len(b)
            b += struct.pack("<I", bk["count"] + len(d))
            b += bytes(b[bk["start"]:bk["start"] + 24 * bk["count"]])
            for key in d:
                b += struct.pack("<6f", *key)
            struct.pack_into("<I", b, bk["field"], vec_pos - bk["field"])
        for bank, d in appended_c.items():
            bk = m["banks"][bank]
            _align4(b)
            vec_pos = len(b)
            b += struct.pack("<I", bk["ccount"] + len(d))
            b += bytes(b[bk["cstart"]:bk["cstart"] + 7 * bk["ccount"]])
            for raw in d:
                b += raw
            struct.pack_into("<I", b, bk["cfield"], vec_pos - bk["cfield"])
            _align4(b)      # 7-байтовые цвета: держим длину буфера кратной 4
        for ci, plan in plans.items():
            clip = m["clips"][ci]
            flat = flats[ci]
            base = m["e_start"] + 2 * clip["offset"]
            for k in range(clip["n_el"]):
                old_m, old_c = flat[3 * k + 1], flat[3 * k + 2]
                mp = plan["map_m"][old_m]
                new_m = mp[1] if mp[0] == "keep" else idx_m[mp[1]][mp[2]]
                if new_m != old_m:
                    struct.pack_into("<H", b, base + 2 * (3 * k + 1), new_m)
                if plan["map_c"] is not None and old_c != NO_INDEX:
                    dest, raw = plan["map_c"][old_c]
                    new_c = idx_c[dest][raw]
                    if new_c != old_c:
                        struct.pack_into("<H", b, base + 2 * (3 * k + 2), new_c)

    if shape_targets:
        owner = {}
        for sh in m["shapes"]:
            for v in sh["verts"]:
                owner.setdefault(v, set()).add(sh["index"])
        for si, (kx, ky, dx, dy) in shape_targets.items():
            sh = m["shapes"][si]
            verts = sorted(set(sh["verts"]))
            if not verts:
                raise UserError(f"У шейпа {sh['id']} нет вершин")
            for v in verts:
                if owner[v] != {si}:
                    others = sorted(m["shapes"][o]["id"] for o in owner[v] if o != si)
                    raise UserError(f"Вершины шейпа {sh['id']} общие с шейпами {others} - "
                                    f"править на месте нельзя")
            pts = [struct.unpack_from("<ff", ds, m["v_start"] + 12 * v) for v in verts]
            if pivot == "center":
                px = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2.0
                py = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2.0
            else:
                px = py = 0.0
            for v, (x, y) in zip(verts, pts):
                struct.pack_into("<ff", b, m["v_start"] + 12 * v,
                                 px + kx * (x - px) + dx, py + ky * (y - py) + dy)

    chunks = list(sc["chunks"])
    chunks[0] = bytes(b)
    if moved:
        mcb2 = bytearray(chunks[4])
        for ci, dest in moved.items():
            struct.pack_into("<I", mcb2, m["clips"][ci]["bank_field"], dest)
        chunks[4] = bytes(mcb2)
    data = finalize_sc(sc, chunks, sc["fd"])
    verify_transform(sc, data, clip_targets, shape_targets, pivot, moved)
    return data


def clip_colors(m, clip):
    """Итоговые цвета элементов клипа: 7 байт или None."""
    flat = clip_flat(m, clip)
    if not flat:
        return []
    bk = m["banks"][clip["bank"]]
    out = []
    for c in flat[2::3]:
        if c == NO_INDEX:
            out.append(None)
        elif c < bk["ccount"]:
            at = bk["cstart"] + 7 * c
            out.append(bytes(m["ds"][at:at + 7]))
        else:
            raise UserError(f"Клип {clip['id']}: номер цвета {c} вне банка {clip['bank']}")
    return out


def _close(a, b):
    return abs(a - b) <= 1e-5 * max(1.0, abs(b))


def verify_transform(old, new_data, clip_targets, shape_targets, pivot, moved=None):
    """Перечитывает готовый файл и сверяет с оригиналом на уровне смысла:
    у всех невыбранных клипов итоговые матрицы и у всех невыбранных шейпов вершины - как были;
    у выбранных - ровно как посчитано. Цвета элементов у всех клипов должны остаться теми же."""
    moved = moved or {}
    new = parse_sc(new_data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if len(new["chunks"]) != 7 or new["leftover"]:
        fail("структура блоков нарушена")
    for i in (1, 2, 3, 5, 6):
        if new["chunks"][i] != old["chunks"][i]:
            fail(f"блок {CHUNK_NAMES[i]} изменился")
    expected_mc = bytearray(old["chunks"][4])
    mo_pre = load_model(old)
    for ci, dest in moved.items():
        struct.pack_into("<I", expected_mc, mo_pre["clips"][ci]["bank_field"], dest)
    if bytes(expected_mc) != new["chunks"][4]:
        fail("блок MovieClips изменился не только номерами банков у перенесённых клипов")
    if len(new["chunks"][0]) % 4 or len(new["fd"]) % 4:
        fail("длина блока не кратна 4")
    oe, os_ = get_exports(old)
    ne, ns = get_exports(new)
    if oe != ne or os_ != ns:
        fail("ExportName'ы или таблица строк изменились")
    o_fd, n_fd = old["fd"], new["fd"]
    for slot in (0, 1, 2, 3, 4, 5, 9):
        po, pn = fb_slot(o_fd, fb_root(o_fd), slot), fb_slot(n_fd, fb_root(n_fd), slot)
        if (po is None) != (pn is None) or (po is not None and u32(o_fd, po) != u32(n_fd, pn)):
            fail(f"поле дескриптора {slot} изменилось")
    if u32(n_fd, fb_slot(n_fd, fb_root(n_fd), 8)) != 4 + len(new["chunks"][0]):
        fail("смещение ресурсов в дескрипторе неверное")
    if fd_export_names(o_fd) != fd_export_names(n_fd):
        fail("имена в дескрипторе изменились")

    mo, mn = load_model(old), load_model(new)
    if len(mo["clips"]) != len(mn["clips"]) or len(mo["shapes"]) != len(mn["shapes"]) \
            or len(mo["banks"]) != len(mn["banks"]):
        fail("число клипов, шейпов или банков изменилось")
    for bo, bn in zip(mo["banks"], mn["banks"]):
        if mo["ds"][bo["cstart"]:bo["cstart"] + 7 * bo["ccount"]] != \
                mn["ds"][bn["cstart"]:bn["cstart"] + 7 * bo["ccount"]]:
            fail("старые цветовые преобразования в банке изменились")

    cache_o, cache_n = {}, {}
    for co, cn in zip(mo["clips"], mn["clips"]):
        if (co["id"], moved.get(co["index"], co["bank"]), co["offset"], co["n_el"], co["children"]) != \
                (cn["id"], cn["bank"], cn["offset"], cn["n_el"], cn["children"]):
            fail(f"описание клипа {co['id']} изменилось")
        fo, fn = clip_flat(mo, co), clip_flat(mn, cn)
        if fo[0::3] != fn[0::3]:
            fail(f"у клипа {co['id']} изменились дети")
        if clip_colors(mo, co) != clip_colors(mn, cn):
            fail(f"у клипа {co['id']} изменились цвета")
        eo = clip_effective(mo, co, cache_o)
        en_ = clip_effective(mn, cn, cache_n)
        if co["index"] in clip_targets:
            kx, ky, dx, dy = clip_targets[co["index"]]
            for x, y in zip(eo, en_):
                if not all(_close(p, q) for p, q in zip(y, xf_matrix(x, kx, ky, dx, dy))):
                    fail(f"матрицы клипа {co['id']} посчитаны не так, как ожидалось")
        elif eo != en_:
            fail(f"у невыбранного клипа {co['id']} изменились итоговые матрицы")

    vo = mo["ds"][mo["v_start"]:mo["v_start"] + 12 * mo["n_verts"]]
    vn = mn["ds"][mn["v_start"]:mn["v_start"] + 12 * mn["n_verts"]]
    if mo["n_verts"] != mn["n_verts"]:
        fail("число вершин изменилось")
    target_verts = {}
    for si, params in shape_targets.items():
        for v in set(mo["shapes"][si]["verts"]):
            target_verts[v] = (si, params)
    centers = {}
    for si in shape_targets:
        verts = sorted(set(mo["shapes"][si]["verts"]))
        pts = [struct.unpack_from("<ff", vo, 12 * v) for v in verts]
        if pivot == "center":
            centers[si] = ((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2.0,
                           (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2.0)
        else:
            centers[si] = (0.0, 0.0)
    for v in range(mo["n_verts"]):
        ro, rn = vo[12 * v:12 * v + 12], vn[12 * v:12 * v + 12]
        if v not in target_verts:
            if ro != rn:
                fail(f"изменилась вершина {v}, не относящаяся к выбранным шейпам")
            continue
        si, (kx, ky, dx, dy) = target_verts[v]
        px, py = centers[si]
        x, y, u, w = struct.unpack("<ffHH", ro)
        nx, ny, nu, nw = struct.unpack("<ffHH", rn)
        if (u, w) != (nu, nw) or not _close(nx, px + kx * (x - px) + dx) \
                or not _close(ny, py + ky * (y - py) + dy):
            fail(f"вершина {v} посчитана не так, как ожидалось")


# ---- выбор целей и разбор ввода

def find_clips_by_pattern(m, exports, text):
    """ExportName, шаблон со звёздочкой (emoji_*) или числовой id клипа -> список индексов клипов."""
    import fnmatch
    text = text.strip()
    by_id = {c["id"]: c["index"] for c in m["clips"]}
    id_of = {}
    for e in exports:
        id_of.setdefault(e["name"], e["id"])
    if text in id_of:
        return [by_id[id_of[text]]] if id_of[text] in by_id else []
    if any(ch in text for ch in "*?["):
        return [by_id[id_of[n]] for n in id_of if fnmatch.fnmatchcase(n, text) and id_of[n] in by_id]
    if text.lower().startswith("id:"):
        text = text[3:].strip()
    if text.isdigit():
        return [by_id[int(text)]] if int(text) in by_id else []
    return []


def parse_id_list(text):
    ids = []
    for part in re.split(r"[,\s;]+", text.strip()):
        if not part:
            continue
        mo = re.fullmatch(r"(\d+)-(\d+)", part)
        if mo:
            a, b = int(mo.group(1)), int(mo.group(2))
            if b < a or b - a > 100000:
                raise UserError(f"Не понял диапазон «{part}»")
            ids.extend(range(a, b + 1))
        elif part.isdigit():
            ids.append(int(part))
        else:
            raise UserError(f"Не понял «{part}» в списке id")
    return ids


def parse_scale(text):
    t = text.strip().lower().replace("х", "x")
    parts = [p for p in re.split(r"[;x\s]+", t) if p]
    if len(parts) == 1 and "," in t and "." in t:
        parts = t.split(",")
    try:
        vals = [float(p.replace(",", ".")) for p in parts]
    except ValueError:
        raise UserError(f"Не понял коэффициент «{text}» (нужно 0.5 или 0.5,0.8 для X и Y)")
    if len(vals) == 1:
        vals = [vals[0], vals[0]]
    if len(vals) != 2 or any(v <= 0 for v in vals):
        raise UserError("Коэффициент должен быть числом больше нуля (или двумя: для X и для Y)")
    return vals[0], vals[1]


def parse_px(text, what):
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        raise UserError(f"Не понял сдвиг по {what}: «{text}»")


def parse_transform_file(path):
    """Строки вида: имя  коэффициент  dx  dy (пробелы, табы или ;). Пропущенное = 1 0 0."""
    out = {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        for num, raw in enumerate(fh, 1):
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            parts = [p for p in re.split(r"[;\t ]+", text) if p]
            name = parts[0]
            try:
                k = parse_scale(parts[1]) if len(parts) > 1 else (1.0, 1.0)
                dx = parse_px(parts[2], "X") if len(parts) > 2 else 0.0
                dy = parse_px(parts[3], "Y") if len(parts) > 3 else 0.0
            except UserError as e:
                raise UserError(f"Строка {num} в {os.path.basename(path)}: {e}")
            out[name] = (k[0], k[1], dx, dy)
    return out


def ask_transform_params(default_scale="1"):
    k = parse_scale(ask("Коэффициент размера (1 - не менять, 0.5 - вдвое меньше, 2 - вдвое больше)", default_scale))
    dx = parse_px(ask("Сдвиг по X в пикселях (вправо +, влево -)", "0"), "X")
    dy = parse_px(ask("Сдвиг по Y в пикселях (вниз +, вверх -)", "0"), "Y")
    if abs(k[0] - 1) < 1e-12 and abs(k[1] - 1) < 1e-12 and dx == 0 and dy == 0:
        raise UserError("Коэффициент 1 и сдвиг 0 - менять нечего")
    return k[0], k[1], dx, dy


def describe_params(kx, ky, dx, dy):
    scale = f"{kx:g}" if abs(kx - ky) < 1e-12 else f"{kx:g} по X, {ky:g} по Y"
    return f"размер ×{scale}, сдвиг {dx:+g} по X и {dy:+g} по Y"


def flow_transform():
    ensure_deps()
    src = resolve_input("Какой .sc менять", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    m = load_model(sc)
    exports, _strings = get_exports(sc)
    names = [e["name"] for e in exports]
    id_to_name = {e["id"]: e["name"] for e in exports}
    print(f"  Формат: {'SC2' if sc.get('kind') != 'sc1' else 'SC1'}, клипов: {len(m['clips'])}, "
          f"шейпов: {len(m['shapes'])}, ExportName'ов: {len(names)}")
    print("  Что менять:")
    print("    1 - MovieClip целиком (по ExportName, шаблону emoji_* или id)")
    print("    2 - Shape (по id или все шейпы клипа)")
    print("    3 - клипы списком из файла: имя  коэффициент  dx  dy")
    mode = ask("Что менять", "1")

    clip_targets, shape_targets, pivot = {}, {}, "origin"
    clip_by_index = {c["index"]: c for c in m["clips"]}
    parents = {}
    for c in m["clips"]:
        for ch in set(c["children"]):
            parents.setdefault(ch, set()).add(c["index"])
    single_clip = None

    def clip_label(ci):
        cid = clip_by_index[ci]["id"]
        return id_to_name.get(cid, f"id {cid}")

    def nested_warning(indices):
        chosen = set(indices)
        shared = {}
        for ci in indices:
            outside = parents.get(clip_by_index[ci]["id"], set()) - chosen
            if outside:
                shared[ci] = len(outside)
        if shared:
            print(yellow(f"  ⚠ Клипов, которые используются внутри других клипов: {len(shared)} "
                         f"(например {clip_label(next(iter(shared)))} - в {next(iter(shared.values()))}). "
                         f"Внутри тех клипов они изменятся тоже."))

    if mode == "1":
        text = ask("ExportName, шаблон со * или id клипа")
        hits = find_clips_by_pattern(m, exports, text)
        if not hits:
            near = [x for x in names if text and text.lower() in x.lower()]
            hint = ("\n  Похожие:\n    " + "\n    ".join(near[:15])) if near else ""
            raise UserError(f"Не нашёл клип «{text}».{hint}")
        print(f"  Найдено клипов: {len(hits)}")
        print("  Исходные данные (положение считается по кадру 1, от точки (0,0) клипа; "
              "Y растёт вниз):")
        for ci in hits[:8]:
            print(gray("    " + clip_info_line(m, ci, id_to_name, with_pos=True)))
        if len(hits) > 8:
            print(gray(f"    ... ещё {len(hits) - 8}"))
        nested_warning(hits)
        if len(hits) == 1:
            single_clip = hits[0]
        params = ask_transform_params()
        clip_targets = {ci: params for ci in hits}
        summary = describe_params(*params)
    elif mode == "2":
        text = ask("id шейпов (5, 7-9) или ExportName/шаблон клипа, чьи шейпы менять")
        shape_by_id = {}
        for sh in m["shapes"]:
            shape_by_id.setdefault(sh["id"], sh)
        picked = []
        if re.fullmatch(r"[\d,\s;\-]+", text.strip()) and text.strip():
            for sid in parse_id_list(text):
                if sid not in shape_by_id:
                    raise UserError(f"Нет шейпа с id {sid}")
                picked.append(shape_by_id[sid]["index"])
        else:
            hits = find_clips_by_pattern(m, exports, text)
            if not hits:
                raise UserError(f"Не нашёл клип «{text}»")
            for ci in hits:
                for ch in clip_by_index[ci]["children"]:
                    if ch in shape_by_id and shape_by_id[ch]["index"] not in picked:
                        picked.append(shape_by_id[ch]["index"])
            if not picked:
                raise UserError("Среди детей этих клипов нет шейпов (там только вложенные клипы)")
            print(f"  Клипов: {len(hits)}, шейпов у них напрямую: {len(picked)}")
        print("  Исходные координаты шейпов (в координатах шейпа, Y растёт вниз):")
        for si in picked[:10]:
            sh = m["shapes"][si]
            pts = shape_points(m, si)
            users = parents.get(sh["id"], set())
            bb = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)) if pts else None
            print(gray(f"    шейп {sh['id']}: {len(pts)} вершин, " + (fmt_bbox(bb) if bb else "вершин нет")
                       + f"; используется в клипах: {len(users)}"))
        if len(picked) > 10:
            print(gray(f"    ... ещё {len(picked) - 10}"))
        multi = [m["shapes"][si]["id"] for si in picked if len(parents.get(m["shapes"][si]["id"], set())) > 1]
        if multi:
            print(yellow(f"  ⚠ Шейпов, которыми пользуются несколько клипов: {len(multi)}. "
                         f"Изменение затронет все эти клипы сразу."))
        print("  Масштабировать от:")
        print("    1 - точки (0,0) шейпа")
        print("    2 - центра шейпа")
        pivot = "center" if ask("Точка", "1") == "2" else "origin"
        params = ask_transform_params()
        shape_targets = {si: params for si in picked}
        summary = describe_params(*params) + (", от центра шейпа" if pivot == "center" else ", от (0,0)")
    elif mode == "3":
        rpath = unquote(ask("Файл со списком", "transforms.txt"))
        if not os.path.isabs(rpath) and not os.path.dirname(rpath):
            rpath = os.path.join(IN_DIR, rpath)
        if not os.path.isfile(rpath):
            with open(rpath, "w", encoding="utf-8") as fh:
                fh.write("# имя  коэффициент  dx  dy   (пропущенное = 1 0 0). "
                         "Строки, где менять нечего, можно удалить.\n")
                for x in names:
                    fh.write(f"{x}  1  0  0\n")
            saved(rpath)
            raise UserError("Файла не было, я создал шаблон со всеми экспортами. "
                            "Отредактируй и запусти пункт снова.")
        entries = parse_transform_file(rpath)
        for name, params in entries.items():
            hits = find_clips_by_pattern(m, exports, name)
            if not hits:
                raise UserError(f"В списке «{name}»: такого клипа нет в файле")
            for ci in hits:
                clip_targets[ci] = params
        clip_targets = {ci: p for ci, p in clip_targets.items()
                        if not (abs(p[0] - 1) < 1e-12 and abs(p[1] - 1) < 1e-12 and p[2] == 0 and p[3] == 0)}
        if not clip_targets:
            raise UserError("В списке нет ни одной строки, где что-то меняется")
        nested_warning(list(clip_targets))
        summary = f"клипов по списку: {len(clip_targets)}"
    else:
        raise UserError("Нет такого варианта")

    line()
    print(f"  {summary}")
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")
    print(gray("  Пересобираю и сжимаю (несколько секунд)..."))
    data = apply_transform(sc, clip_targets, shape_targets, pivot)
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    print(green("  ✔ Проверка пройдена: ") + gray("у всех остальных клипов и шейпов итоговые матрицы и "
                                                   "вершины такие же, как в оригинале"))
    if single_clip is not None:
        try:
            mn = load_model(open_sc_bytes(data))
            before, after = clip_bbox(m, single_clip), clip_bbox(mn, single_clip)
            if before and after:
                print(gray("  Положение клипа (кадр 1) было:   " + fmt_bbox(before)))
                print(gray("                           стало:  " + fmt_bbox(after)))
        except Exception:
            pass
    return [out_path]


# ================================================================ разбор в JSON/msgpack + PNG
#
# Формат результата - тот же, что в твоём data.msgpack (slop_guide.md): is_sc2, version, exports,
# matrix_banks, textures, shapes, movieclips, textfields, movieclip_modifiers. Для SC2 результат
# сверен с твоим data.msgpack (см. тесты ниже в истории чата), для SC1 - с примером SonicFaces.
# Лишние ключи, которых нет в твоём формате: у текстур width/height, у SC1 корневой блок "sc1".

JSON_LIMIT = 20 * 1024 * 1024
KTX_MAGIC = b"\xabKTX 11\xbb\r\n\x1a\n"
ASTC_BLOCKS = {}
for _i, _bd in enumerate([(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8),
                          (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12)]):
    ASTC_BLOCKS[0x93B0 + _i] = _bd          # COMPRESSED_RGBA_ASTC_*
    ASTC_BLOCKS[0x93D0 + _i] = _bd          # COMPRESSED_SRGB8_ALPHA8_ASTC_*
SC1_TEXTURE_TAGS = (1, 16, 19, 24, 27, 28, 29, 34)


def f32s(x):
    """float32 (пришедший как double) -> самое короткое десятичное число, которое читается обратно
    в то же float32. Так JSON читается глазами (1.15, а не 1.149999976158142) и ничего не теряет."""
    if x != x or x in (float("inf"), float("-inf")) or x == 0:
        return 0.0 if x == 0 else x
    for p in range(1, 10):
        y = float(f"{x:.{p}g}")
        if struct.unpack("<f", struct.pack("<f", y))[0] == x:
            return y
    return x


def ktx_info(payload):
    """(glInternalFormat, ширина, высота, смещение данных первого уровня, размер) или None."""
    if len(payload) < 68 or payload[:12] != KTX_MAGIC:
        return None
    (_end, _gt, _gts, _gf, gl_internal, _gb, pw, ph, _pd, _arr, _faces, _mips, kv) = \
        struct.unpack_from("<13I", payload, 12)
    at = 64 + kv
    size = struct.unpack_from("<I", payload, at)[0]
    return gl_internal, pw, ph, at + 4, size


ROOT_ORDER = ["is_sc2", "version", "use_external_textures", "use_external_texture", "use_multi_resolution",
              "use_low_resolution", "use_precision_matrix", "use_half_precision_matrices", "use_short_frames",
              "exports", "matrix_banks", "textures", "shapes", "movieclips", "textfields",
              "movieclip_modifiers", "sc1"]


def order_doc(doc):
    return {k: doc[k] for k in ROOT_ORDER if k in doc}


def sc2_to_doc(sc):
    chunks = sc["chunks"]
    if sc["version"] != 5 or len(chunks) != 7 or sc["leftover"]:
        raise UserError("Разбор SC2 умеет только версию 5 с 7 блоками (как у твоего файла)")
    ds, _en, tfc, shb, mcb, mdc, txb = chunks
    exports, strings = get_exports(sc)
    m = load_model(sc)
    doc = {"is_sc2": True, "version": sc["version"], "use_external_textures": False,
           "use_external_texture": False, "use_multi_resolution": False, "use_low_resolution": False,
           "use_precision_matrix": False, "use_half_precision_matrices": False, "use_short_frames": False,
           "exports": [{"id": e["id"], "name": e["name"]} for e in exports]}

    banks = []
    for bk in m["banks"]:
        mats = [{"a": f32s(a), "b": f32s(b), "c": f32s(c), "d": f32s(d), "tx": f32s(tx), "ty": f32s(ty)}
                for (a, b, c, d, tx, ty) in struct.iter_unpack("<6f", ds[bk["start"]:bk["start"] + 24 * bk["count"]])]
        cols = []
        for i in range(bk["ccount"]):
            raw = ds[bk["cstart"] + 7 * i:bk["cstart"] + 7 * i + 7]
            cols.append({"alpha": raw[3], "multiply": {"r": raw[0], "g": raw[1], "b": raw[2]},
                         "add": {"r": raw[4], "g": raw[5], "b": raw[6]}})
        if bk.get("hcount"):
            # "короткие" матрицы (так хранит часть игровых файлов, например loading.sc): масштаб/поворот в 1/1024,
            # сдвиг в двадцатых долях пикселя. В документе они становятся обычными матрицами - при сборке
            # пишутся как float32 (значения те же, кроме округления до float32).
            if mats:
                raise UserError("В одном банке матриц есть и обычные, и короткие (int16) матрицы - порядок индексов "
                                "для такого сочетания не известен, не рискую разбирать этот файл")
            for (a, b, c, d, tx, ty) in struct.iter_unpack("<6h", ds[bk["hstart"]:bk["hstart"] + 12 * bk["hcount"]]):
                mats.append({"a": f32s(a / 1024.0), "b": f32s(b / 1024.0), "c": f32s(c / 1024.0),
                             "d": f32s(d / 1024.0), "tx": f32s(tx / 20.0), "ty": f32s(ty / 20.0)})
        banks.append({"matrices": mats, "color_transforms": cols})
    doc["matrix_banks"] = banks

    shapes = []
    sp = fb_slot(shb, fb_root(shb), 0)
    for s in (fb_table_vec(shb, sp) if sp is not None else []):
        ps = dict(fb_slots(shb, s))
        cmds = []
        if 1 in ps:
            cs, cn = fb_vec(shb, ps[1])
            for i in range(cn):
                typ, tex, cnt, first = struct.unpack_from("<4I", shb, cs + 16 * i)
                verts = []
                for k in range(first, first + cnt):
                    x, y, u, v = struct.unpack_from("<ffHH", ds, m["v_start"] + 12 * k)
                    verts.append({"x": f32s(x), "y": f32s(y), "u": f32s(u / 65535), "v": f32s(v / 65535)})
                # тип: в файле 0 у всех команд, у тебя в msgpack это 1 (полоса); других значений не видел
                cmds.append({"type": 1 if typ == 0 else 0, "texture_index": tex, "vertices": verts})
        shapes.append({"id": u16(shb, ps[0]) if 0 in ps else 0, "commands": cmds})
    doc["shapes"] = shapes

    movieclips = []
    cp = fb_slot(mcb, fb_root(mcb), 0)
    tabs = fb_table_vec(mcb, cp) if cp is not None else []
    for clip, tab in zip(m["clips"], tabs):
        ps = dict(fb_slots(mcb, tab))
        names = []
        if 6 in ps:
            vs, vn = fb_vec(mcb, ps[6])
            names = [strings[u32(mcb, vs + 4 * i)] for i in range(vn)]
        blends = []
        if 7 in ps:
            vs, vn = fb_vec(mcb, ps[7])
            blends = list(mcb[vs:vs + vn])
        childrens = []
        for i, cid in enumerate(clip["children"]):
            childrens.append({"id": cid, "blend_mode": blends[i] if i < len(blends) else 0,
                              "name": names[i] if i < len(names) else ""})
        frames = []
        if 8 in ps:
            fs, fn = fb_vec(mcb, ps[8])
            for i in range(fn):
                cnt, lab = struct.unpack_from("<2I", mcb, fs + 8 * i)
                frames.append({"label": strings[lab] if lab < len(strings) else "", "elements_count": cnt})
        flat = clip_flat(m, clip)
        elems = [{"instance_index": flat[3 * k], "matrix_index": flat[3 * k + 1],
                  "colorTransform_index": flat[3 * k + 2]} for k in range(clip["n_el"])]
        entry = {"id": clip["id"], "frame_rate": mcb[ps[2]] if 2 in ps else 0,
                 "bank_index": clip["bank"], "unknown_flag": bool(mcb[ps[4]]) if 4 in ps else False}
        if clip["grid"] is not None:
            if clip["grid"] >= len(m["grids"]):
                raise UserError(f"Клип {clip['id']}: индекс сетки масштабирования {clip['grid']} вне списка")
            gl, gt, gr, gb = m["grids"][clip["grid"]]
            entry["scaling_grid"] = {"left": f32s(gl), "top": f32s(gt), "right": f32s(gr), "bottom": f32s(gb)}
        entry.update({"childrens": childrens, "frames": frames, "frame_elements": elems})
        movieclips.append(entry)
    doc["movieclips"] = movieclips

    textfields = []
    if len(tfc) >= 16:
        tf_recs, _tf_strings = read_textfields(sc)
        for r in tf_recs:
            at = r["pos"]
            tid, font = r["id"], u32(tfc, at + 4)
            text_i = r["text_ref"]
            typo_i = r["typography_ref"]
            left, top, right, bottom = struct.unpack_from("<4h", tfc, at + 8)
            f_color, o_color = struct.unpack_from("<II", tfc, at + 16)
            flags = tfc[at + 32] | (tfc[at + 33] << 8)
            # Раскладка флагов подобрана по 19 текстовым полям твоего файла. В нём outlined и
            # use_device_font всегда совпадают, поэтому оба берутся из одного бита (бит 1),
            # bold/italic нигде не были включены, h_align/v_align встречались только как 1 и 3.
            textfields.append({
                "id": tid, "font_name": strings[font] if font < len(strings) else "",
                "text": strings[text_i] if text_i < len(strings) else "",
                "text_kind": r.get("text_kind", "fallback"),
                "localization_key": r.get("localization_key"),
                "text_slot": r.get("text_slot", 24),
                "font_color": f_color, "font_size": tfc[at + 34],
                "font_horizontal_align": 1 if flags & 0x0200 else 3,
                "font_vertical_align": 1 if flags & 0x1000 else 3,
                "bold": False, "italic": False, "multiline": bool(flags & 0x10),
                "outlined": bool(flags & 0x02), "use_device_font": bool(flags & 0x02),
                "auto_kern": bool(flags & 0x40), "unknown_flag": bool(flags & 0x20),
                "bend_angle": 0.0, "outline_angle": struct.unpack_from("<i", tfc, at + 36)[0],
                "typography_file": strings[typo_i] if typo_i < len(strings) else "",
                "outline_color": o_color,
                "bounds": {"left": left, "top": top, "right": right, "bottom": bottom},
                "flags_raw": flags})
    doc["textfields"] = textfields

    mods = []
    if len(mdc) >= 16:
        mp_ = fb_slot(mdc, fb_root(mdc), 0)
        if mp_ is not None:
            vs, vn = fb_vec(mdc, mp_)
            mods = [{"id": u16(mdc, vs + 4 * i), "type": u16(mdc, vs + 4 * i + 2)} for i in range(vn)]
    doc["movieclip_modifiers"] = mods

    tex_infos = []
    tp = fb_slot(txb, fb_root(txb), 0)
    for s in (fb_table_vec(txb, tp) if tp is not None else []):
        pd = fb_slot(txb, s, 1)
        td = pd + u32(txb, pd)
        ps = dict(fb_slots(txb, td))
        start, n = fb_vec(txb, ps[4]) if 4 in ps else (0, 0)
        tex_infos.append({"fmt": txb[ps[0]] if 0 in ps else 0, "flags": txb[ps[1]] if 1 in ps else 0,
                          "w": u16(txb, ps[2]) if 2 in ps else 0,
                          "h": u16(txb, ps[3]) if 3 in ps else 0, "payload": bytes(txb[start:start + n])})
    doc["textures"] = []
    for i, ti in enumerate(tex_infos):
        ki = ktx_info(ti["payload"])
        doc["textures"].append({"index": i, "file": f"texture_{i}.png", "pixel_format": 0 if ki else ti["fmt"],
                                "encoding": 1 if ki else 2, "khronos_format": ki[0] if ki else 0,
                                "linear": True, "filtering": 0, "downscaling": True,
                                "width": ti["w"], "height": ti["h"]})
        if ti["flags"]:
            # у этой текстуры в файле есть байт настроек (в маленьком файле он = 6 у одной из двух текстур);
            # что он означает, я не выяснил, поэтому он сохраняется как есть и пишется обратно при сборке
            doc["textures"][-1]["tex_flags_raw"] = ti["flags"]
    return order_doc(doc), tex_infos


# ---- SC1: контейнер и поток тегов

def sc1_container(data):
    """Разбирает контейнер SC1 (версии 1, 3, 4). Возвращает dict с распакованным потоком."""
    import hashlib
    if data[:2] != b"SC" or len(data) < 14:
        raise UserError("Это не .sc (нет заголовка SC)")
    ver = struct.unpack_from(">I", data, 2)[0]
    o, real = 6, ver
    if ver == 4:
        real = struct.unpack_from(">I", data, 6)[0]
        o = 10
    hl = struct.unpack_from(">I", data, o)[0]
    o += 4
    md5 = data[o:o + hl]
    o += hl
    payload = data[o:]
    tail = b""
    if payload[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard
        obj = zstandard.ZstdDecompressor().decompressobj()
        raw = obj.decompress(payload)
        tail = getattr(obj, "unused_data", None) or getattr(obj, "unused", b"") or b""
        comp = "zstd"
    elif payload[:4] == b"SCLZ":
        raise UserError("Файл сжат LZHAM (SCLZ) - этот формат сжатия хаб не умеет")
    else:
        import lzma
        if len(payload) < 9:
            raise UserError("Повреждён заголовок сжатия")
        usize = struct.unpack_from("<I", payload, 5)[0]
        dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
        try:
            raw = dec.decompress(payload[:5] + struct.pack("<Q", usize) + payload[9:])
        except lzma.LZMAError as e:
            raise UserError(f"Не смог распаковать данные (ни zstd, ни LZMA): {e}")
        tail = dec.unused_data or b""
        comp = "lzma"
    return {"ver": ver, "real": real, "md5": bytes(md5), "raw": raw, "tail": bytes(tail), "comp": comp,
            "md5_ok": (hashlib.md5(raw).digest() == md5) if hl == 16 else None}


def sc1_read_string(raw, pos):
    n = raw[pos]
    if n == 0xFF:
        return None, pos + 1
    return bytes(raw[pos + 1:pos + 1 + n]), pos + 1 + n


def sc1_parse_stream(raw):
    if len(raw) < 19:
        raise UserError("Слишком короткий поток SC1")
    counts = list(struct.unpack_from("<6H", raw, 0))
    reserved = bytes(raw[12:17])
    n = struct.unpack_from("<H", raw, 17)[0]
    pos = 19
    ids = list(struct.unpack_from(f"<{n}H", raw, pos))
    pos += 2 * n
    names = []
    for _ in range(n):
        s, pos = sc1_read_string(raw, pos)
        names.append(s)
    tags = []
    while pos + 5 <= len(raw):
        tag = raw[pos]
        ln = struct.unpack_from("<i", raw, pos + 1)[0]
        if ln < 0 or pos + 5 + ln > len(raw):
            raise UserError(f"Тег {tag} выходит за пределы потока (позиция {pos})")
        tags.append([tag, bytes(raw[pos + 5:pos + 5 + ln])])
        pos += 5 + ln
        if tag == 0:
            break
    return {"counts": counts, "reserved": reserved, "ids": ids, "names": names, "tags": tags,
            "rest": bytes(raw[pos:])}


def sc1_build_stream(model):
    tags = model["tags"]
    counts = list(model["counts"])
    # В шапке - число матриц и цветов только ПЕРВОГО банка; следующие банки открывает тег 42 (u16, u16)
    first42 = next((i for i, t in enumerate(tags) if t[0] == 42), len(tags))
    counts[4] = sum(1 for t in tags[:first42] if t[0] == 8)
    counts[5] = sum(1 for t in tags[:first42] if t[0] == 9)
    out = bytearray(struct.pack("<6H", *counts))
    out += model["reserved"]
    out += struct.pack("<H", len(model["ids"]))
    out += struct.pack(f"<{len(model['ids'])}H", *model["ids"])
    for nm in model["names"]:
        if nm is None:
            out.append(0xFF)
        else:
            out.append(len(nm))
            out += nm
    for tag, body in tags:
        out.append(tag)
        out += struct.pack("<i", len(body))
        out += body
    out += model["rest"]
    return bytes(out)


def sc1_subtags(body, pos):
    """Разбирает вложенные теги (кадры мувиклипа, команды шейпа) с позиции pos до тега 0."""
    out = []
    while pos + 5 <= len(body):
        tag = body[pos]
        ln = struct.unpack_from("<i", body, pos + 1)[0]
        out.append((tag, body[pos + 5:pos + 5 + ln]))
        pos += 5 + ln
        if tag == 0:
            break
    return out


def sc1_parse_shape(body):
    sid, _ncmd, _nv = struct.unpack_from("<3H", body, 0)
    cmds = []
    for tag, b in sc1_subtags(body, 6):
        if tag != 22:
            continue
        tex, n = b[0], b[1]
        xy = struct.unpack_from(f"<{2 * n}i", b, 2)
        uv = struct.unpack_from(f"<{2 * n}H", b, 2 + 8 * n)
        cmds.append({"tex": tex, "xy": list(xy), "uv": list(uv)})
    return sid, cmds


def sc1_build_shape(sid, cmds):
    body = bytearray(struct.pack("<3H", sid, len(cmds), sum(len(c["xy"]) // 2 for c in cmds)))
    for c in cmds:
        n = len(c["xy"]) // 2
        sub = bytes([c["tex"], n]) + struct.pack(f"<{2 * n}i", *c["xy"]) + struct.pack(f"<{2 * n}H", *c["uv"])
        body += bytes([22]) + struct.pack("<i", len(sub)) + sub
    body += bytes([0]) + struct.pack("<i", 0)
    return bytes(body)


def sc1_parse_clip(body):
    cid, fps, nfr = struct.unpack_from("<HBH", body, 0)
    pos = 5
    flag = body[pos]
    pos += 1
    nel = struct.unpack_from("<I", body, pos)[0]
    pos += 4
    flat = list(struct.unpack_from(f"<{3 * nel}H", body, pos))
    pos += 6 * nel
    ninst = struct.unpack_from("<H", body, pos)[0]
    pos += 2
    # Таблица экземпляров устроена по столбцам: сначала все id (u16), потом все режимы наложения (u8),
    # потом все имена (строки). При одном экземпляре это совпадает с записью "по строкам".
    ids = list(struct.unpack_from(f"<{ninst}H", body, pos))
    pos += 2 * ninst
    blends = list(body[pos:pos + ninst])
    pos += ninst
    inst = []
    for k in range(ninst):
        nm, pos = sc1_read_string(body, pos)
        inst.append({"id": ids[k], "blend": blends[k], "name": nm})
    frames, pre, post, bank = [], [], [], None
    for tag, b in sc1_subtags(body, pos):
        if tag == 11:
            cnt = struct.unpack_from("<H", b, 0)[0]
            lab, _ = sc1_read_string(b, 2)
            frames.append({"count": cnt, "label": lab})
        elif tag != 0 and not frames:
            pre.append((tag, bytes(b)))          # например тег 41: номер банка матриц (1 байт)
            if tag == 41 and len(b) == 1:
                bank = b[0]
        elif tag != 0:
            post.append((tag, bytes(b)))         # например тег 31: сетка масштабирования (после кадров)
    return {"id": cid, "fps": fps, "nframes": nfr, "flag": flag, "flat": flat, "inst": inst, "frames": frames,
            "pre": pre, "post": post, "bank": bank or 0}


def sc1_build_clip(c):
    body = bytearray(struct.pack("<HBH", c["id"], c["fps"], c["nframes"]))
    body.append(c["flag"])
    body += struct.pack("<I", len(c["flat"]) // 3)
    body += struct.pack(f"<{len(c['flat'])}H", *c["flat"])
    body += struct.pack("<H", len(c["inst"]))
    body += struct.pack(f"<{len(c['inst'])}H", *[i["id"] for i in c["inst"]])
    body += bytes(i["blend"] for i in c["inst"])
    for i in c["inst"]:
        nm = i["name"]
        body += b"\xff" if nm is None else bytes([len(nm)]) + nm
    for tag, b in c.get("pre", ()):
        body += bytes([tag]) + struct.pack("<i", len(b)) + b
    for f in c["frames"]:
        lab = f["label"]
        sub = struct.pack("<H", f["count"]) + (b"\xff" if lab is None else bytes([len(lab)]) + lab)
        body += bytes([11]) + struct.pack("<i", len(sub)) + sub
    for tag, b in c.get("post", ()):
        body += bytes([tag]) + struct.pack("<i", len(b)) + b
    body += bytes([0]) + struct.pack("<i", 0)
    return bytes(body)


def sc1_text(b):
    return "" if b is None else b.rstrip(b"\0").decode("utf-8", "replace")


def sc1_footer(tail):
    """Хвост после сжатых данных: b"START" + (имя\\0, u8 длина хеша, хеш)*N + остаток. Возвращает
    (записи [(имя, хеш)], остаток) или None, если формат хвоста другой."""
    if tail[:5] != b"START":
        return None
    p, ents = 5, []
    while True:
        try:
            e = tail.index(b"\0", p)
        except ValueError:
            break
        name, hl = tail[p:e], (tail[e + 1] if e + 1 < len(tail) else 0)
        if not name or hl != 16 or e + 2 + 16 > len(tail) or not all(32 <= c < 127 for c in name):
            break
        ents.append((name.decode(), bytes(tail[e + 2:e + 18])))
        p = e + 18
    return ents, bytes(tail[p:])


# ---- текстовые поля SC1 -> запись в виде, как в SC2 (нужно для слияния и перевода SC1 в SC2)
#
# Раскладка тегов 7 / 15 / 33 сверена побайтно со всеми 15 текстовыми полями из portraits_frank и events
# (одиночные .sc в SC2 служили эталоном). Прочие теги текстовых полей (20, 21, 25, 43, 44) не проверены,
# поэтому для них хаб отказывается, а не угадывает.

SC1_TF_VERIFIED = (7, 15, 33)


def sc1_textfields(cont):
    """(список текстовых полей в виде записей SC2, предупреждения, {тег: сколько}) из потока SC1."""
    out, warns, unknown = [], [], {}
    for tag, body in sc1_parse_stream(cont["raw"])["tags"]:
        if tag not in SC1_TEXT_TAGS:
            continue
        if tag not in SC1_TF_VERIFIED:
            unknown[tag] = unknown.get(tag, 0) + 1
            continue
        tid = struct.unpack_from("<H", body, 0)[0]
        font, pos = sc1_read_string(body, 2)
        color = struct.unpack_from("<I", body, pos)[0]
        bold, italic, multiline, unk, align, size = body[pos + 4:pos + 10]
        left, top, right, bottom = struct.unpack_from("<4h", body, pos + 10)
        pos += 18
        pos += 1                                    # байт после рамки (в образцах всегда 0)
        text, pos = sc1_read_string(body, pos)
        dev, o_color, o_angle = 0, 0, -65536
        if tag in (15, 33):
            dev = body[pos]
            pos += 1
        if tag == 33:
            o_color, o_angle = struct.unpack_from("<Ii", body, pos)
            pos += 8
        if pos != len(body):
            raise UserError(f"Текстовое поле {tid} (тег {tag}): после разбора остались лишние байты - формат "
                            f"отличается от известного")
        if bold or italic or align not in (0, 2):
            warns.append(f"поле {tid}: bold/italic/выравнивание ({bold}/{italic}/{align}) переведены приблизительно")
        flags = (0x200 if align == 2 else 0) | (0x10 if multiline else 0) | (0x02 if dev else 0) \
            | (0x20 if tag == 33 else 0)
        out.append({
            "id": tid, "font_name": sc1_text(font) if font is not None else "",
            "text": sc1_text(text) if text is not None else "", "text_kind": "fallback", "localization_key": None,
            "text_slot": 24, "font_color": color, "font_size": size,
            "font_horizontal_align": 1 if flags & 0x0200 else 3, "font_vertical_align": 3,
            "bold": False, "italic": False, "multiline": bool(multiline), "outlined": bool(flags & 0x02),
            "use_device_font": bool(flags & 0x02), "auto_kern": False, "unknown_flag": bool(flags & 0x20),
            "bend_angle": 0.0, "outline_angle": o_angle, "typography_file": "", "outline_color": o_color,
            "bounds": {"left": left, "top": top, "right": right, "bottom": bottom}, "flags_raw": flags})
    return out, warns, unknown



SC2_DOC_FLAGS = ("use_external_textures", "use_external_texture", "use_multi_resolution", "use_low_resolution",
                 "use_precision_matrix", "use_half_precision_matrices", "use_short_frames")


def doc_sc1_to_sc2(doc, textfields):
    """Документ, разобранный из SC1, -> документ SC2 версии 5 (экспорты, шейпы, клипы, банки у них общие).
    Текстуры SC2 получатся сырыми RGBA8 из PNG, текстовые поля берутся из sc1_textfields."""
    import copy
    d = copy.deepcopy(doc)
    d["is_sc2"], d["version"] = True, 5
    for k in SC2_DOC_FLAGS:
        d[k] = False
    d.pop("sc1", None)
    d["textfields"] = copy.deepcopy(textfields)
    for c in d["movieclips"]:
        for ch in c["childrens"]:
            ch.pop("name_null", None)
            ch["name"] = ch.get("name") or ""
        for f in c["frames"]:
            f.pop("label_null", None)
            f["label"] = f.get("label") or ""
    for i, t in enumerate(d["textures"]):
        t.update({"index": i, "pixel_format": 0, "encoding": 2, "khronos_format": 0, "linear": True,
                  "filtering": 0, "downscaling": True})
        t.pop("sc1_tag", None)
    return d



SC1_TAGS_FOR_SC2 = {7, 15, 33, 30, 37, 38, 39, 40, 42}


def sc1_to_sc2_doc(cont, doc):
    """Документ, разобранный из SC1 (sc1_to_doc), -> документ SC2. Возвращает (документ, предупреждения).
    Модификаторы маски (теги 38/39/40) и текстовые поля (7/15/33) берутся из потока; если в файле есть
    теги, которых хаб не умеет переносить, бросает UserError, а не теряет данные молча."""
    tfs, warns, unknown = sc1_textfields(cont)
    bad = {int(t): n for t, n in (doc.get("sc1") or {}).get("unparsed_tags", {}).items()
           if int(t) not in SC1_TAGS_FOR_SC2}
    bad.update(unknown)
    if bad:
        raise UserError("В этом SC1 есть теги, которые хаб не умеет переносить в SC2 (тег: сколько): "
                        + ", ".join(f"{t}: {n}" for t, n in sorted(bad.items())) +
                        ". Такой файл можно исключить из слияния; поддержку тега можно добавить по образцу.")
    out = doc_sc1_to_sc2(doc, tfs)
    # банки: в документе SC1 все матрицы и цвета лежат одним списком, границы банков задают теги 42
    mats, cols = doc["matrix_banks"][0]["matrices"], doc["matrix_banks"][0]["color_transforms"]
    sizes, cur = [], [0, 0]
    for t, b in sc1_parse_stream(cont["raw"])["tags"]:
        if t == 8:
            cur[0] += 1
        elif t == 9:
            cur[1] += 1
        elif t == 42:
            sizes.append(cur)
            cur = [0, 0]
    sizes.append(cur)
    banks, mi, ci = [], 0, 0
    for nm, nc in sizes:
        banks.append({"matrices": mats[mi:mi + nm], "color_transforms": cols[ci:ci + nc]})
        mi, ci = mi + nm, ci + nc
    out["matrix_banks"] = banks
    for sh in out["shapes"]:
        for cmd in sh["commands"]:
            if cmd["type"] != 0:
                raise UserError(f"Шейп {sh['id']}: тип команды {cmd['type']} в SC1 хаб не умеет переводить в SC2")
            vs = cmd["vertices"]
            if len(vs) > 3:                                 # веер SC1 -> полоса треугольников SC2
                order, lo, hi, take_hi = [0, 1], 2, len(vs) - 1, True
                while lo <= hi:
                    if take_hi:
                        order.append(hi)
                        hi -= 1
                    else:
                        order.append(lo)
                        lo += 1
                    take_hi = not take_hi
                cmd["vertices"] = [vs[k] for k in order]
            cmd["type"] = 1
    out["movieclip_modifiers"] = [{"id": struct.unpack_from("<H", b, 0)[0], "type": t}
                                  for t, b in sc1_parse_stream(cont["raw"])["tags"] if t in (38, 39, 40)]
    return out, warns



def sc1_to_doc(cont, tex_cont=None, ext=None):
    """cont - результат sc1_container основного файла, tex_cont - то же для _tex.sc (если есть),
    ext - уже прочитанные .sctx/.zktx (список из read_ext_texture) для текстур без пикселей в теге."""
    mdl = sc1_parse_stream(cont["raw"])
    doc = {"is_sc2": False, "version": cont["real"], "use_external_textures": False,
           "use_external_texture": False, "use_multi_resolution": None, "use_low_resolution": None,
           "use_precision_matrix": None, "use_half_precision_matrices": False, "use_short_frames": False}
    doc["exports"] = [{"id": i, "name": sc1_text(n)} for i, n in zip(mdl["ids"], mdl["names"])]

    mats, cols, shapes, clips, heads, unparsed, flag_tags = [], [], [], [], [], {}, []
    for tag, body in mdl["tags"]:
        if tag == 8:
            v = struct.unpack("<6i", body[:24])
            mats.append({"a": v[0] / 1024, "b": v[1] / 1024, "c": v[2] / 1024, "d": v[3] / 1024,
                         "tx": v[4] / 20, "ty": v[5] / 20})
        elif tag == 9:
            cols.append({"alpha": body[3], "multiply": {"r": body[4], "g": body[5], "b": body[6]},
                         "add": {"r": body[0], "g": body[1], "b": body[2]}})
        elif tag == 18:
            sid, cmds = sc1_parse_shape(body)
            shapes.append({"id": sid, "commands": [
                {"type": 0, "texture_index": c["tex"],
                 "vertices": [{"x": c["xy"][2 * k] / 20, "y": c["xy"][2 * k + 1] / 20,
                               "u": c["uv"][2 * k] / 65535, "v": c["uv"][2 * k + 1] / 65535}
                              for k in range(len(c["xy"]) // 2)]} for c in cmds]})
        elif tag == 49:
            c = sc1_parse_clip(body)
            childrens = []
            for i in c["inst"]:
                ch = {"id": i["id"], "blend_mode": i["blend"] & 0x3F, "name": sc1_text(i["name"])}
                if i["blend"] & 0x40:
                    ch["hidden"] = True
                if i["name"] is None:
                    ch["name_null"] = True
                if i["blend"] & 0x80:
                    ch["blend_raw"] = i["blend"]      # старший бит неизвестного смысла: храним байт целиком
                childrens.append(ch)
            clips.append({"id": c["id"], "frame_rate": c["fps"], "bank_index": c["bank"], "unknown_flag": bool(c["flag"]),
                          "childrens": childrens,
                          "frames": [dict({"label": sc1_text(f["label"]), "elements_count": f["count"]},
                                          **({"label_null": True} if f["label"] is None else {}))
                                     for f in c["frames"]],
                          "frame_elements": [{"instance_index": c["flat"][3 * k], "matrix_index": c["flat"][3 * k + 1],
                                              "colorTransform_index": c["flat"][3 * k + 2]}
                                             for k in range(len(c["flat"]) // 3)]})
        elif tag in SC1_TEXTURE_TAGS:
            heads.append((tag, body))
        elif tag in (23, 26):
            flag_tags.append(tag)
        elif tag != 0:
            unparsed[str(tag)] = unparsed.get(str(tag), 0) + 1
    doc["matrix_banks"] = [{"matrices": mats, "color_transforms": cols}]
    doc["shapes"], doc["movieclips"] = shapes, clips
    doc["textfields"], doc["movieclip_modifiers"] = [], []

    tex_infos = []
    pix_tags = []
    ext_used = 0
    if tex_cont is not None:
        pix_tags = [(t, b) for t, b in sc1_parse_stream_tags(tex_cont["raw"]) if t in SC1_TEXTURE_TAGS]
    for i, (tag, body) in enumerate(heads):
        pf, w, h = body[0], struct.unpack_from("<H", body, 1)[0], struct.unpack_from("<H", body, 3)[0]
        data = body[5:] if len(body) > 5 else b""
        if not data and i < len(pix_tags) and len(pix_tags[i][1]) > 5:
            data = pix_tags[i][1][5:]
        ext_one = None
        if not data and ext and ext_used < len(ext):
            ext_one = ext[ext_used]
            ext_used += 1
            pf, w, h, data = 0, ext_one["w"], ext_one["h"], ext_one["rgba"]
        tex_infos.append({"fmt": pf, "w": w, "h": h, "payload": bytes(data), "tag": tag,
                          **({"ext": ext_one["name"]} if ext_one else {})})
    doc["use_external_textures"] = any(len(b) <= 5 for _t, b in heads)
    doc["textures"] = [{"index": i, "file": f"texture_{i}.png", "pixel_format": t["fmt"], "encoding": 2,
                        "khronos_format": 0, "linear": None, "filtering": None, "downscaling": None,
                        "width": t["w"], "height": t["h"], "sc1_tag": t["tag"]} for i, t in enumerate(tex_infos)]
    foot = sc1_footer(cont["tail"])
    groups = []
    for tag, _b in mdl["tags"]:
        if groups and groups[-1][0] == tag:
            groups[-1][1] += 1
        else:
            groups.append([tag, 1])
    doc["sc1"] = {"container_version": cont["ver"], "compression": cont["comp"], "md5": cont["md5"].hex(),
                  "md5_ok": cont["md5_ok"], "flag_tags": flag_tags, "unparsed_tags": unparsed,
                  "reserved_hex": mdl["reserved"].hex(), "tag_order": groups,
                  "names_nul": any(n is not None and n.endswith(b"\0") for n in mdl["names"]),
                  "tex_container": ({"ver": tex_cont["ver"], "real": tex_cont["real"]} if tex_cont else None),
                  "ext_textures": [{"name": t["ext"], "w": t["w"], "h": t["h"]} for t in tex_infos if t.get("ext")],
                  "footer_exports": [n for n, _h in foot[0]] if foot else None,
                  "footer_hex": cont["tail"].hex() if cont["tail"] else ""}
    return order_doc(doc), tex_infos


def sc1_parse_stream_tags(raw):
    """Теги файла _tex.sc: там нет шапки со счётчиками, поток сразу начинается с тегов."""
    tags, pos = [], 0
    while pos + 5 <= len(raw):
        tag = raw[pos]
        ln = struct.unpack_from("<i", raw, pos + 1)[0]
        if ln < 0 or pos + 5 + ln > len(raw):
            raise UserError(f"В файле текстур тег {tag} выходит за пределы потока (позиция {pos})")
        tags.append((tag, bytes(raw[pos + 5:pos + 5 + ln])))
        pos += 5 + ln
        if tag == 0:
            break
    return tags


# ---- текстуры в PNG

def raw_to_rgba(fmt, w, h, data):
    """Сырые пиксели SC в RGBA8. Проверено только формат 0 (RGBA8), остальные - по соглашениям
    OpenGL, на файлах не сверялись."""
    px = w * h
    if fmt in (0, 1):
        if len(data) < px * 4:
            raise UserError("Пикселей меньше, чем w*h*4")
        return bytes(data[:px * 4])
    out = bytearray(px * 4)
    if fmt in (2, 3, 4):
        if len(data) < px * 2:
            raise UserError("Пикселей меньше, чем w*h*2")
        for i, v in enumerate(struct.unpack_from(f"<{px}H", data)):
            if fmt == 2:
                r, g, b, a = (v >> 12) & 15, (v >> 8) & 15, (v >> 4) & 15, v & 15
                r, g, b, a = r * 17, g * 17, b * 17, a * 17
            elif fmt == 3:
                r, g, b, a = (v >> 11) & 31, (v >> 6) & 31, (v >> 1) & 31, (v & 1) * 255
                r, g, b = r * 255 // 31, g * 255 // 31, b * 255 // 31
            else:
                r, g, b, a = (v >> 11) & 31, (v >> 5) & 63, v & 31, 255
                r, g, b = r * 255 // 31, g * 255 // 63, b * 255 // 31
            out[4 * i:4 * i + 4] = bytes((r, g, b, a))
        return bytes(out)
    if fmt == 6:
        for i in range(px):
            l, a = data[2 * i], data[2 * i + 1]
            out[4 * i:4 * i + 4] = bytes((l, l, l, a))
        return bytes(out)
    if fmt == 10:
        for i in range(px):
            l = data[i]
            out[4 * i:4 * i + 4] = bytes((l, l, l, 255))
        return bytes(out)
    raise UserError(f"Формат пикселей {fmt} не поддерживается")


_ASTC = {"tried": False, "mod": None}


def astc_decoder():
    """Модуль texture2ddecoder (декодер ASTC на C). Один раз пробует поставить его через pip."""
    if _ASTC["mod"] is not None or _ASTC["tried"]:
        return _ASTC["mod"]
    _ASTC["tried"] = True
    try:
        import texture2ddecoder as mod
    except ImportError:
        print(yellow("  Ставлю texture2ddecoder для распаковки ASTC-текстур..."))
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                                   "--disable-pip-version-check", "texture2ddecoder"])
            import texture2ddecoder as mod
        except Exception:
            return None
    _ASTC["mod"] = mod
    return mod


def texture_image(Image, info, enc_doc):
    """PIL-картинка текстуры или (None, причина)."""
    payload = info["payload"]
    if not payload:
        return None, "в файле нет пикселей (внешняя текстура: положи рядом <имя>_tex.sc)"
    ki = ktx_info(payload)
    if ki:
        gl, w, h, at, size = ki
        blk = ASTC_BLOCKS.get(gl)
        if not blk:
            return None, f"KTX с форматом 0x{gl:X}, декодера нет"
        return Image.frombytes("RGBA", (w, h), astc_to_rgba(payload[at:at + size], w, h, blk[0], blk[1])), None
    try:
        return Image.frombytes("RGBA", (info["w"], info["h"]),
                               raw_to_rgba(info["fmt"], info["w"], info["h"], payload)), None
    except UserError as e:
        return None, str(e)


def save_textures(Image, doc, tex_infos, out_dir):
    notes = []
    for i, info in enumerate(tex_infos):
        entry = doc["textures"][i]
        img, why = texture_image(Image, info, entry)
        if img is not None:
            path = os.path.join(out_dir, f"texture_{i}.png")
            img.save(path)
            entry["file"] = f"texture_{i}.png"
        elif info["payload"] and ktx_info(info["payload"]):
            path = os.path.join(out_dir, f"texture_{i}.ktx")
            with open(path, "wb") as fh:
                fh.write(info["payload"])
            entry["file"] = f"texture_{i}.ktx"
            notes.append(f"текстура {i}: {why}")
        else:
            entry["file"] = ""
            notes.append(f"текстура {i}: {why}")
    return notes


# ---- запись JSON / msgpack

def mp_pack(o, out):
    if o is None:
        out.append(0xC0)
    elif o is True:
        out.append(0xC3)
    elif o is False:
        out.append(0xC2)
    elif isinstance(o, int):
        if 0 <= o < 128:
            out.append(o)
        elif -32 <= o < 0:
            out.append(o & 0xFF)
        elif 0 <= o < 256:
            out += b"\xcc" + bytes([o])
        elif 0 <= o < 65536:
            out += b"\xcd" + struct.pack(">H", o)
        elif 0 <= o < 2 ** 32:
            out += b"\xce" + struct.pack(">I", o)
        elif 0 <= o < 2 ** 64:
            out += b"\xcf" + struct.pack(">Q", o)
        elif -128 <= o < 0:
            out += b"\xd0" + struct.pack(">b", o)
        elif -32768 <= o < 0:
            out += b"\xd1" + struct.pack(">h", o)
        elif -2 ** 31 <= o < 0:
            out += b"\xd2" + struct.pack(">i", o)
        else:
            out += b"\xd3" + struct.pack(">q", o)
    elif isinstance(o, float):
        out += b"\xcb" + struct.pack(">d", o)
    elif isinstance(o, str):
        b = o.encode("utf-8")
        n = len(b)
        if n < 32:
            out.append(0xA0 | n)
        elif n < 256:
            out += b"\xd9" + bytes([n])
        elif n < 65536:
            out += b"\xda" + struct.pack(">H", n)
        else:
            out += b"\xdb" + struct.pack(">I", n)
        out += b
    elif isinstance(o, (list, tuple)):
        n = len(o)
        if n < 16:
            out.append(0x90 | n)
        elif n < 65536:
            out += b"\xdc" + struct.pack(">H", n)
        else:
            out += b"\xdd" + struct.pack(">I", n)
        for x in o:
            mp_pack(x, out)
    elif isinstance(o, dict):
        n = len(o)
        if n < 16:
            out.append(0x80 | n)
        elif n < 65536:
            out += b"\xde" + struct.pack(">H", n)
        else:
            out += b"\xdf" + struct.pack(">I", n)
        for k, v in o.items():
            mp_pack(k, out)
            mp_pack(v, out)
    else:
        raise TypeError(f"msgpack: тип {type(o)} не поддерживается")


def write_doc(doc, out_dir, limit=JSON_LIMIT):
    """JSON (с отступами, читаемый), а если он получился бы больше limit - msgpack. Возвращает (путь, вид)."""
    import json
    enc = json.JSONEncoder(indent=2, ensure_ascii=False)
    total, too_big = 0, False
    for chunk in enc.iterencode(doc):
        total += len(chunk.encode("utf-8"))
        if total > limit:
            too_big = True
            break
    if not too_big:
        path = os.path.join(out_dir, "data.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
        return path, "json"
    buf = bytearray()
    mp_pack(doc, buf)
    path = os.path.join(out_dir, "data.msgpack")
    with open(path, "wb") as fh:
        fh.write(buf)
    return path, "msgpack"


# ================================================================ ASTC на чистом Python (запасной декодер)
# Чистый Python-декодер ASTC LDR (запасной вариант, если нет texture2ddecoder)

_astc_ISE = {2: (1, 0, 0), 3: (0, 1, 0), 4: (2, 0, 0), 5: (0, 0, 1), 6: (1, 1, 0), 8: (3, 0, 0), 10: (1, 0, 1),
        12: (2, 1, 0), 16: (4, 0, 0), 20: (2, 0, 1), 24: (3, 1, 0), 32: (5, 0, 0), 40: (3, 0, 1),
        48: (4, 1, 0), 64: (6, 0, 0), 80: (4, 0, 1), 96: (5, 1, 0), 128: (7, 0, 0), 160: (5, 0, 1),
        192: (6, 1, 0), 256: (8, 0, 0)}
_astc_COLOR_LEVELS = sorted(_astc_ISE)                      # по возрастанию точности
_astc_WEIGHT_LEVELS = [2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32]


def _astc_ise_bits(count, levels):
    nb, nt, nq = _astc_ISE[levels]
    n = nb * count
    if nt:
        n += (8 * count + 4) // 5
    if nq:
        n += (7 * count + 2) // 3
    return n


def _astc_build_trit_table():
    tab = []
    for T in range(256):
        if (T >> 2) & 7 == 7:
            C = ((T >> 5) << 2) | (T & 3)
            t4, t3 = 2, 2
        else:
            C = T & 31
            if (T >> 5) & 3 == 3:
                t4, t3 = 2, (T >> 7) & 1
            else:
                t4, t3 = (T >> 7) & 1, (T >> 5) & 3
        if C & 3 == 3:
            t2, t1, t0 = 2, (C >> 4) & 1, (((C >> 3) & 1) << 1) | (((C >> 2) & 1) & (~(C >> 3) & 1))
        elif (C >> 2) & 3 == 3:
            t2, t1, t0 = 2, 2, C & 3
        else:
            t2, t1, t0 = (C >> 4) & 1, (C >> 2) & 3, C & 3
        tab.append((t0, t1, t2, t3, t4))
    return tab


def _astc_build_quint_table():
    tab = []
    for Q in range(128):
        if (Q >> 1) & 3 == 3 and (Q >> 5) & 3 == 0:
            q2 = ((Q & 1) << 2) | ((((Q >> 4) & 1) & ~(Q & 1) & 1) << 1) | (((Q >> 3) & 1) & ~(Q & 1) & 1)
            q1, q0 = 4, 4
        else:
            if (Q >> 1) & 3 == 3:
                q2 = 4
                C = (((Q >> 3) & 3) << 3) | ((~(Q >> 5) & 3) << 1) | (Q & 1)
            else:
                q2 = (Q >> 5) & 3
                C = Q & 31
            if C & 7 == 5:
                q1, q0 = 4, (C >> 3) & 3
            else:
                q1, q0 = (C >> 3) & 3, C & 7
        tab.append((q0, q1, q2))
    return tab


_astc_TRITS = _astc_build_trit_table()
_astc_QUINTS = _astc_build_quint_table()


def _astc_ise_decode(stream, count, levels):
    """stream - целое, биты читаются с младшего. Возвращает список (D << nb) | младшие биты."""
    nb, nt, nq = _astc_ISE[levels]
    mask = (1 << nb) - 1
    out = []
    pos = 0
    if nt:
        i = 0
        while i < count:
            lows = [0] * 5
            T = 0
            lows[0] = (stream >> pos) & mask; pos += nb
            T |= (stream >> pos) & 3; pos += 2
            lows[1] = (stream >> pos) & mask; pos += nb
            T |= ((stream >> pos) & 3) << 2; pos += 2
            lows[2] = (stream >> pos) & mask; pos += nb
            T |= ((stream >> pos) & 1) << 4; pos += 1
            lows[3] = (stream >> pos) & mask; pos += nb
            T |= ((stream >> pos) & 3) << 5; pos += 2
            lows[4] = (stream >> pos) & mask; pos += nb
            T |= ((stream >> pos) & 1) << 7; pos += 1
            tr = _astc_TRITS[T]
            for k in range(min(5, count - i)):
                out.append((tr[k] << nb) | lows[k])
            i += 5
    elif nq:
        i = 0
        while i < count:
            lows = [0] * 3
            Q = 0
            lows[0] = (stream >> pos) & mask; pos += nb
            Q |= (stream >> pos) & 7; pos += 3
            lows[1] = (stream >> pos) & mask; pos += nb
            Q |= ((stream >> pos) & 3) << 3; pos += 2
            lows[2] = (stream >> pos) & mask; pos += nb
            Q |= ((stream >> pos) & 3) << 5; pos += 2
            qs = _astc_QUINTS[Q]
            for k in range(min(3, count - i)):
                out.append((qs[k] << nb) | lows[k])
            i += 3
    else:
        for i in range(count):
            out.append((stream >> pos) & mask)
            pos += nb
    return out


def _astc_replicate(v, n, total):
    r, filled = 0, 0
    while filled < total:
        r = (r << n) | v
        filled += n
    return r >> (filled - total)


_astc_COLOR_BC = {  # (трит/квинт, число бит) -> (C, шаблон B из 9 бит, старший первым)
    (1, 1): (204, "000000000"), (1, 2): (93, "b000b0bb0"), (1, 3): (44, "cb000cbcb"),
    (1, 4): (22, "dcb000dcb"), (1, 5): (11, "edcb000ed"), (1, 6): (5, "fedcb000f"),
    (2, 1): (113, "000000000"), (2, 2): (54, "b0000bb00"), (2, 3): (26, "cb0000cbc"),
    (2, 4): (13, "dcb0000dc"), (2, 5): (6, "edcb0000e"),
}
_astc_WEIGHT_BC = {
    (1, 1): (50, "0000000"), (1, 2): (23, "b000b0b"), (1, 3): (11, "cb000cb"),
    (2, 1): (28, "0000000"), (2, 2): (13, "b0000b0"),
}


def _astc_pattern(pat, low):
    r = 0
    for ch in pat:
        r <<= 1
        if ch.isalpha():
            r |= (low >> (ord(ch) - 97)) & 1
    return r


def _astc_build_unquant(levels, weights):
    nb, nt, nq = _astc_ISE[levels]
    out = []
    for v in range(levels):
        if not nt and not nq:
            if weights:
                r = _astc_replicate(v, nb, 6) if nb <= 6 else v
                if nb == 1:
                    r = 63 if v else 0
                out.append(r + 1 if r > 32 else r)
            else:
                out.append(_astc_replicate(v, nb, 8) if nb < 8 else v)
            continue
        D, low = v >> nb, v & ((1 << nb) - 1)
        kind = 1 if nt else 2
        if nb == 0:
            if weights:
                r = ([0, 32, 63] if nt else [0, 16, 32, 47, 63])[D]
                out.append(r + 1 if r > 32 else r)
            else:
                raise ValueError("нет такого диапазона цветов")
            continue
        if weights:
            C, pat = _astc_WEIGHT_BC[(kind, nb)]
            A = 0x7F if low & 1 else 0
            T = D * C + _astc_pattern(pat, low)
            T ^= A
            T = (A & 0x20) | (T >> 2)
            out.append(T + 1 if T > 32 else T)
        else:
            C, pat = _astc_COLOR_BC[(kind, nb)]
            A = 0x1FF if low & 1 else 0
            T = D * C + _astc_pattern(pat, low)
            T ^= A
            T = (A & 0x80) | (T >> 2)
            out.append(T)
    return out


_astc_COLOR_UNQ = {}
for _lv in _astc_COLOR_LEVELS:
    try:
        _astc_COLOR_UNQ[_lv] = _astc_build_unquant(_lv, False)
    except (ValueError, KeyError):
        pass
_astc_WEIGHT_UNQ = {_lv: _astc_build_unquant(_lv, True) for _lv in _astc_WEIGHT_LEVELS}


# ---------------------------------------------------------------- блок ASTC
_astc_COLOR_VALID = [lv for lv in (6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96, 128, 160, 192, 256) if lv in _astc_COLOR_UNQ]
_astc_MAGENTA = bytes((255, 0, 255, 255))


def _astc_hash52(p):
    M = 0xFFFFFFFF
    p ^= p >> 15
    p = (p - (p << 17)) & M
    p = (p + (p << 7)) & M
    p = (p + (p << 4)) & M
    p ^= p >> 5
    p = (p + (p << 16)) & M
    p ^= p >> 7
    p ^= p >> 3
    p = (p ^ (p << 6)) & M
    p ^= p >> 17
    return p


_astc_PART_CACHE = {}


def _astc_partition_map(seed, nparts, bw, bh):
    key = (seed, nparts, bw, bh)
    r = _astc_PART_CACHE.get(key)
    if r is not None:
        return r
    small = bw * bh < 31
    s = seed + (nparts - 1) * 1024
    rnum = _astc_hash52(s)
    sd = [(rnum >> sh) & 0xF for sh in (0, 4, 8, 12, 16, 20, 24, 28, 18, 22, 26)]
    sd.append(((rnum >> 30) | (rnum << 2)) & 0xF)
    sd = [v * v for v in sd]
    if seed & 1:
        sh1 = 4 if seed & 2 else 5
        sh2 = 6 if nparts == 3 else 5
    else:
        sh1 = 6 if nparts == 3 else 5
        sh2 = 4 if seed & 2 else 5
    sh3 = sh1 if seed & 0x10 else sh2
    shifts = (sh1, sh2, sh1, sh2, sh1, sh2, sh1, sh2, sh3, sh3, sh3, sh3)
    sd = [v >> k for v, k in zip(sd, shifts)]
    out = []
    for y in range(bh):
        for x in range(bw):
            xx, yy = (x << 1, y << 1) if small else (x, y)
            a = (sd[0] * xx + sd[1] * yy + (rnum >> 14)) & 0x3F
            b = (sd[2] * xx + sd[3] * yy + (rnum >> 10)) & 0x3F
            c = (sd[4] * xx + sd[5] * yy + (rnum >> 6)) & 0x3F
            d = (sd[6] * xx + sd[7] * yy + (rnum >> 2)) & 0x3F
            if nparts < 4:
                d = 0
            if nparts < 3:
                c = 0
            if a >= b and a >= c and a >= d:
                out.append(0)
            elif b >= c and b >= d:
                out.append(1)
            elif c >= d:
                out.append(2)
            else:
                out.append(3)
    _astc_PART_CACHE[key] = out
    return out


_astc_INFILL_CACHE = {}


def _astc_infill(bw, bh, gx, gy):
    key = (bw, bh, gx, gy)
    r = _astc_INFILL_CACHE.get(key)
    if r is not None:
        return r
    ds = (1024 + bw // 2) // (bw - 1) if bw > 1 else 0
    dt = (1024 + bh // 2) // (bh - 1) if bh > 1 else 0
    last = gx * gy - 1
    out = []
    for y in range(bh):
        for x in range(bw):
            gs = (ds * x * (gx - 1) + 32) >> 6
            gt = (dt * y * (gy - 1) + 32) >> 6
            js, fs, jt, ft = gs >> 4, gs & 15, gt >> 4, gt & 15
            v0 = js + gx * jt
            w11 = (fs * ft + 8) >> 4
            out.append((min(v0, last), min(v0 + 1, last), min(v0 + gx, last), min(v0 + gx + 1, last),
                        16 - fs - ft + w11, fs - w11, ft - w11, w11))
    _astc_INFILL_CACHE[key] = out
    return out


def _astc_block_mode(m):
    R = (m >> 4) & 1
    H = (m >> 9) & 1
    D = (m >> 10) & 1
    A = (m >> 5) & 3
    if m & 3:
        R |= (m & 3) << 1
        B = (m >> 7) & 3
        k = (m >> 2) & 3
        if k == 0:
            gx, gy = B + 4, A + 2
        elif k == 1:
            gx, gy = B + 8, A + 2
        elif k == 2:
            gx, gy = A + 2, B + 8
        else:
            B &= 1
            if m & 0x100:
                gx, gy = B + 2, A + 2
            else:
                gx, gy = A + 2, B + 6
    else:
        R |= ((m >> 2) & 3) << 1
        if (m >> 2) & 3 == 0:
            return None
        B = (m >> 9) & 3
        k = (m >> 7) & 3
        if k == 0:
            gx, gy = 12, A + 2
        elif k == 1:
            gx, gy = A + 2, 12
        elif k == 2:
            gx, gy = A + 6, B + 6
            D = H = 0
        else:
            a2 = (m >> 5) & 3
            if a2 == 0:
                gx, gy = 6, 10
            elif a2 == 1:
                gx, gy = 10, 6
            else:
                return None
    idx = (R - 2) + 6 * H
    if not 0 <= idx < 12:
        return None
    return gx, gy, bool(D), _astc_WEIGHT_LEVELS[idx]


def _astc_clamp(v):
    return 0 if v < 0 else 255 if v > 255 else v


def _astc_bts(a, b):
    """bit_transfer_signed: a - смещение, b - база. Возвращает (a, b)."""
    b = (b >> 1) | (a & 0x80)
    a = (a >> 1) & 0x3F
    if a & 0x20:
        a -= 0x40
    return a, b


def _astc_blue_contract(r, g, b, a):
    return ((r + b) >> 1, (g + b) >> 1, b, a)


def _astc_endpoints(cem, v):
    """Возвращает ((r,g,b,a), (r,g,b,a)) или None для HDR-режимов."""
    if cem == 0:
        return (v[0], v[0], v[0], 255), (v[1], v[1], v[1], 255)
    if cem == 1:
        l0 = (v[0] >> 2) | (v[1] & 0xC0)
        l1 = min(255, l0 + (v[1] & 0x3F))
        return (l0, l0, l0, 255), (l1, l1, l1, 255)
    if cem == 4:
        return (v[0], v[0], v[0], v[2]), (v[1], v[1], v[1], v[3])
    if cem == 5:
        d0, b0 = _astc_bts(v[1], v[0])
        d1, b1 = _astc_bts(v[3], v[2])
        l1, a1 = _astc_clamp(b0 + d0), _astc_clamp(b1 + d1)
        return (b0, b0, b0, b1), (l1, l1, l1, a1)
    if cem == 6:
        s = v[3]
        return ((v[0] * s) >> 8, (v[1] * s) >> 8, (v[2] * s) >> 8, 255), (v[0], v[1], v[2], 255)
    if cem == 8:
        if v[1] + v[3] + v[5] >= v[0] + v[2] + v[4]:
            return (v[0], v[2], v[4], 255), (v[1], v[3], v[5], 255)
        return _astc_blue_contract(v[1], v[3], v[5], 255), _astc_blue_contract(v[0], v[2], v[4], 255)
    if cem == 9:
        d0, b0 = _astc_bts(v[1], v[0])
        d1, b1 = _astc_bts(v[3], v[2])
        d2, b2 = _astc_bts(v[5], v[4])
        hi = (_astc_clamp(b0 + d0), _astc_clamp(b1 + d1), _astc_clamp(b2 + d2), 255)
        lo = (b0, b1, b2, 255)
        if d0 + d1 + d2 < 0:
            return _astc_blue_contract(*hi), _astc_blue_contract(*lo)
        return lo, hi
    if cem == 10:
        s = v[3]
        return ((v[0] * s) >> 8, (v[1] * s) >> 8, (v[2] * s) >> 8, v[4]), (v[0], v[1], v[2], v[5])
    if cem == 12:
        if v[1] + v[3] + v[5] >= v[0] + v[2] + v[4]:
            return (v[0], v[2], v[4], v[6]), (v[1], v[3], v[5], v[7])
        return _astc_blue_contract(v[1], v[3], v[5], v[7]), _astc_blue_contract(v[0], v[2], v[4], v[6])
    if cem == 13:
        d0, b0 = _astc_bts(v[1], v[0])
        d1, b1 = _astc_bts(v[3], v[2])
        d2, b2 = _astc_bts(v[5], v[4])
        d3, b3 = _astc_bts(v[7], v[6])
        hi = (_astc_clamp(b0 + d0), _astc_clamp(b1 + d1), _astc_clamp(b2 + d2), _astc_clamp(b3 + d3))
        lo = (b0, b1, b2, b3)
        if d0 + d1 + d2 < 0:
            return _astc_blue_contract(*hi), _astc_blue_contract(*lo)
        return lo, hi
    return None


def astc_block(blk, bw, bh):
    """Один блок ASTC (16 байт) -> bw*bh*4 байт RGBA."""
    val = int.from_bytes(blk, "little")
    mode = val & 0x7FF
    n = bw * bh
    if (mode & 0x1FF) == 0x1FC:
        if mode & 0x200:
            return _astc_MAGENTA * n
        px = bytes(((val >> sh) & 0xFFFF) >> 8 for sh in (64, 80, 96, 112))
        return px * n
    bm = _astc_block_mode(mode)
    if bm is None:
        return _astc_MAGENTA * n
    gx, gy, dual, wlv = bm
    wcount = gx * gy * (2 if dual else 1)
    wbits = _astc_ise_bits(wcount, wlv)
    if wcount > 64 or wbits < 24 or wbits > 96 or gx > bw or gy > bh:
        return _astc_MAGENTA * n
    nparts = ((val >> 11) & 3) + 1
    if dual and nparts == 4:
        return _astc_MAGENTA * n
    s = format(val, "0128b")
    wun = _astc_WEIGHT_UNQ[wlv]
    W = [wun[x] for x in _astc_ise_decode(int(s[:wbits][::-1], 2), wcount, wlv)]
    extra = 0
    if nparts == 1:
        cems = [(val >> 13) & 15]
        seed = 0
        cstart = 17
    else:
        seed = (val >> 13) & 0x3FF
        sel = (val >> 23) & 3
        cstart = 29
        if sel == 0:
            cems = [(val >> 25) & 15] * nparts
        else:
            extra = 3 * nparts - 4
            below = 128 - wbits - (2 if dual else 0)
            B = ((val >> 25) & 15) | (((val >> (below - extra)) & ((1 << extra) - 1)) << 4)
            cems = [((sel - 1 + ((B >> i) & 1)) << 2) | ((B >> (nparts + 2 * i)) & 3) for i in range(nparts)]
    ccs = (val >> (128 - wbits - 2)) & 3 if dual else 0
    nvals = sum(((c >> 2) + 1) * 2 for c in cems)
    avail = 128 - wbits - (2 if dual else 0) - extra - cstart
    level = 0
    for lv in reversed(_astc_COLOR_VALID):
        if _astc_ise_bits(nvals, lv) <= avail:
            level = lv
            break
    if not level:
        return _astc_MAGENTA * n
    cun = _astc_COLOR_UNQ[level]
    raw = _astc_ise_decode((val >> cstart) & ((1 << _astc_ise_bits(nvals, level)) - 1), nvals, level)
    cv = [cun[x] for x in raw]
    eps = []
    at = 0
    for c in cems:
        k = ((c >> 2) + 1) * 2
        ep = _astc_endpoints(c, cv[at:at + k])
        at += k
        if ep is None:
            return _astc_MAGENTA * n
        eps.append(((tuple(x * 257 for x in ep[0])), tuple(x * 257 for x in ep[1])))
    inf = _astc_infill(bw, bh, gx, gy)
    pmap = _astc_partition_map(seed, nparts, bw, bh) if nparts > 1 else None
    out = bytearray(n * 4)
    o = 0
    for t in range(n):
        i0, i1, i2, i3, f0, f1, f2, f3 = inf[t]
        if dual:
            wa = (W[2 * i0] * f0 + W[2 * i1] * f1 + W[2 * i2] * f2 + W[2 * i3] * f3 + 8) >> 4
            wb = (W[2 * i0 + 1] * f0 + W[2 * i1 + 1] * f1 + W[2 * i2 + 1] * f2 + W[2 * i3 + 1] * f3 + 8) >> 4
        else:
            wa = wb = (W[i0] * f0 + W[i1] * f1 + W[i2] * f2 + W[i3] * f3 + 8) >> 4
        e0, e1 = eps[pmap[t]] if pmap else eps[0]
        for c in range(4):
            wt = wb if (dual and c == ccs) else wa
            out[o] = ((e0[c] * (64 - wt) + e1[c] * wt + 32) >> 6) >> 8
            o += 1
    return bytes(out)


def astc_decode(data, w, h, bw, bh, progress=None):
    """Картинка целиком: RGBA, w*h*4 байт."""
    bx, by = (w + bw - 1) // bw, (h + bh - 1) // bh
    if len(data) < bx * by * 16:
        raise ValueError("ASTC: данных меньше, чем нужно для картинки")
    img = bytearray(w * h * 4)
    stride = w * 4
    cache = {}
    for j in range(by):
        y0 = j * bh
        rows = min(bh, h - y0)
        for i in range(bx):
            x0 = i * bw
            cols = min(bw, w - x0)
            blk = data[16 * (j * bx + i):16 * (j * bx + i) + 16]
            px = cache.get(blk)
            if px is None:
                px = astc_block(blk, bw, bh)
                if len(cache) < 20000:
                    cache[blk] = px
            for r in range(rows):
                dst = (y0 + r) * stride + x0 * 4
                img[dst:dst + cols * 4] = px[r * bw * 4:r * bw * 4 + cols * 4]
        if progress and j % 8 == 0:
            progress(j + 1, by)
    return bytes(img)


# ================================================================ кодировщик ASTC (LDR, numpy)
#
# Нужен, чтобы записывать .sctx / .zktx и встроенные KTX. Блоки: одно разбиение, один слой весов, режимы RGB (CEM 8)
# и RGBA (CEM 12), у каждого блока выбирается лучший из нескольких размеров сетки весов. Однотонные блоки идут как
# void-extent. Для блоков, не менявшихся относительно исходной текстуры, берутся исходные 16 байт (base) - поэтому
# неизменённая картинка кодируется побайтно так же, а правка одной иконки не портит остальной атлас.
# Все блоки проверяются тем же декодером, который сверен побайтно с эталонами.

_astc_TRIT_REV = {}
for _t, _tr in enumerate(_astc_TRITS):
    _astc_TRIT_REV.setdefault(_tr, _t)
_astc_QUINT_REV = {}
for _q, _qs in enumerate(_astc_QUINTS):
    _astc_QUINT_REV.setdefault(_qs, _q)
_astc_MODE_OF = {}


def _astc_modes():
    if not _astc_MODE_OF:
        for m in range(2048):
            if (m & 0x1FF) == 0x1FC:
                continue
            bm = _astc_block_mode(m)
            if bm and not bm[2]:
                _astc_MODE_OF.setdefault((bm[0], bm[1], bm[3]), m)
    return _astc_MODE_OF


def _astc_ise_encode(vals, count, levels):
    nb, nt, nq = _astc_ISE[levels]
    mask = (1 << nb) - 1
    out, pos = 0, 0
    if nt:
        for i in range(0, count, 5):
            g = list(vals[i:i + 5]) + [0] * (5 - len(vals[i:i + 5]))
            lo = [v & mask for v in g]
            T = _astc_TRIT_REV[tuple(v >> nb for v in g)]
            for k, (tb, tn) in enumerate(((T & 3, 2), ((T >> 2) & 3, 2), ((T >> 4) & 1, 1), ((T >> 5) & 3, 2), ((T >> 7) & 1, 1))):
                out |= lo[k] << pos
                pos += nb
                out |= tb << pos
                pos += tn
    elif nq:
        for i in range(0, count, 3):
            g = list(vals[i:i + 3]) + [0] * (3 - len(vals[i:i + 3]))
            lo = [v & mask for v in g]
            Q = _astc_QUINT_REV[tuple(v >> nb for v in g)]
            for k, (qb, qn) in enumerate(((Q & 7, 3), ((Q >> 3) & 3, 2), ((Q >> 5) & 3, 2))):
                out |= lo[k] << pos
                pos += nb
                out |= qb << pos
                pos += qn
    else:
        for v in vals[:count]:
            out |= v << pos
            pos += nb
    return out & ((1 << _astc_ise_bits(count, levels)) - 1)


def _astc_candidates(bw, bh, nvals, cstart=17):
    """Допустимые (gx, gy, wlevels, color_level, mode) без второго слоя весов; cstart - с какого бита идут цвета."""
    modes = _astc_modes()
    out = []
    for (gx, gy, wl), mode in modes.items():
        if gx > bw or gy > bh:
            continue
        wb = _astc_ise_bits(gx * gy, wl)
        if wb < 24 or wb > 96:
            continue
        avail = 128 - cstart - wb
        lev = 0
        for lv in reversed(_astc_COLOR_VALID):
            if _astc_ise_bits(nvals, lv) <= avail:
                lev = lv
                break
        if lev >= 6:
            out.append((gx, gy, wl, lev, mode))
    return out


def _astc_pick_candidates(bw, bh, nvals, limit=14, cstart=17, min_level=8):
    cands = [c for c in _astc_candidates(bw, bh, nvals, cstart) if c[3] >= min_level]
    best = {}
    for c in cands:
        key = (c[0], c[1])
        score = (c[3] >= 2 * min_level, c[2] if c[3] >= 2 * min_level else -c[2])
        if key not in best or score > best[key][0]:
            best[key] = (score, c)
    pool = sorted((v[1] for v in best.values()), key=lambda c: (-(c[0] * c[1]), -c[2]))
    if len(pool) > limit:
        step = (len(pool) - 1) / (limit - 1)
        pool = [pool[round(i * step)] for i in range(limit)]
    return pool


_ENC_CACHE = {}


def _astc_enc_tables(bw, bh, gx, gy):
    """Матрицы интерполяции сетки весов: индексы и доли (в 16-х), псевдообратная для подбора весов сетки."""
    import numpy as np
    key = (bw, bh, gx, gy)
    r = _ENC_CACHE.get(key)
    if r is None:
        inf = _astc_infill(bw, bh, gx, gy)
        I = np.array([[t[0], t[1], t[2], t[3]] for t in inf], dtype=np.int64)
        F = np.array([[t[4], t[5], t[6], t[7]] for t in inf], dtype=np.int64)
        B = np.zeros((len(inf), gx * gy))
        for t in range(len(inf)):
            for k in range(4):
                B[t, I[t, k]] += F[t, k] / 16.0
        r = (I, F, np.linalg.pinv(B))
        _ENC_CACHE[key] = r
    return r


def _astc_quant_tables(level, weights=False):
    import numpy as np
    unq = (_astc_WEIGHT_UNQ if weights else _astc_COLOR_UNQ)[level]
    vals = np.array(unq, dtype=np.int64)
    if weights:
        return vals, None
    idx = np.abs(np.arange(256)[:, None] - vals[None, :]).argmin(axis=1)
    return vals, idx


def _astc_pattern_table(bw, bh, parts):
    import numpy as np
    key = ("pat", bw, bh, parts)
    r = _ENC_CACHE.get(key)
    if r is None:
        pm = np.array([_astc_partition_map(s, parts, bw, bh) for s in range(1024)], dtype=np.int64)   # (1024, T)
        oh = np.zeros((1024, bw * bh, parts), np.float32)
        for k in range(parts):
            oh[:, :, k] = pm == k
        r = (pm, oh.reshape(1024, -1))
        _ENC_CACHE[key] = r
    return r


def _astc_kmeans(data, parts):
    """Грубая кластеризация текселей блока на parts групп: (n, T) метки."""
    import numpy as np
    n, T, C = data.shape
    mean = data.mean(axis=1, keepdims=True)
    cen = np.zeros((n, parts, C), np.float32)
    ar = np.arange(n)
    cen[:, 0] = data[ar, ((data - mean) ** 2).sum(2).argmax(1)]
    for k in range(1, parts):
        dist = np.min(((data[:, :, None, :] - cen[:, None, :k, :]) ** 2).sum(3), axis=2)
        cen[:, k] = data[ar, dist.argmax(1)]
    for _ in range(3):
        lab = ((data[:, :, None, :] - cen[:, None, :, :]) ** 2).sum(3).argmin(2)
        for k in range(parts):
            m = (lab == k)[:, :, None]
            cnt = m.sum(1)
            upd = (data * m).sum(1) / np.maximum(cnt, 1)
            cen[:, k] = np.where(cnt > 0, upd, cen[:, k])
    return ((data[:, :, None, :] - cen[:, None, :, :]) ** 2).sum(3).argmin(2)


def _astc_enc_endpoints(data, wgt, M):
    """Начальные концы отрезков по главной оси для каждой части. M: (n, T, P) 0/1."""
    import numpy as np
    n, T, C = data.shape
    P = M.shape[2]
    E0 = np.zeros((n, P, C))
    E1 = np.zeros((n, P, C))
    for p in range(P):
        m = M[:, :, p:p + 1]
        wp = wgt * m
        ws = wp.sum(axis=1)
        okp = ws[:, 0] > 0
        mean = (data * wp).sum(axis=1) / np.maximum(ws, 1e-6)
        cen = (data - mean[:, None, :]) * np.sqrt(wp)
        cov = np.einsum("ntc,ntd->ncd", cen, cen)
        _v, vec = np.linalg.eigh(cov)
        axis = vec[:, :, -1]
        proj = ((data - mean[:, None, :]) * axis[:, None, :]).sum(axis=2)
        lo = np.where(m[:, :, 0] > 0.5, proj, np.inf).min(axis=1)
        hi = np.where(m[:, :, 0] > 0.5, proj, -np.inf).max(axis=1)
        lo, hi = np.where(okp, lo, 0.0), np.where(okp, hi, 0.0)
        e0 = np.clip(mean + axis * lo[:, None], 0, 255)
        e1 = np.clip(mean + axis * hi[:, None], 0, 255)
        sw = e1[:, :3].sum(axis=1) < e0[:, :3].sum(axis=1)
        e0[sw], e1[sw] = e1[sw].copy(), e0[sw].copy()
        E0[:, p], E1[:, p] = e0, e1
    return E0, E1


def _astc_enc_eval(data, wgt, M, E0, E1, bw, bh, modes, cstart, best_err, best, parts, seeds):
    """Перебирает режимы сетки весов для заданного разбиения, обновляет best (по блокам, где ошибка меньше)."""
    import numpy as np
    n, T, C = data.shape
    P = parts
    for (gx, gy, wl, lev, mode) in modes:
        I, F, pinv = _astc_enc_tables(bw, bh, gx, gy)
        cvals, cidx = _astc_quant_tables(lev)
        wv, _o = _astc_quant_tables(wl, True)
        wvf = wv.astype(np.float64)

        def quant(e):
            qi = cidx[np.clip(np.rint(e), 0, 255).astype(np.int64)]
            u = cvals[qi]
            bad = u[:, :, 1 - 1:3].sum(axis=2)       # заглушка для ясности ниже
            return qi, u

        def fix(q0, u0, q1, u1):
            bad = u1[:, :, :3].sum(axis=2) < u0[:, :, :3].sum(axis=2)
            q0b, q1b, u0b, u1b = q0.copy(), q1.copy(), u0.copy(), u1.copy()
            q0[bad], q1[bad] = q1b[bad], q0b[bad]
            u0[bad], u1[bad] = u1b[bad], u0b[bad]
        q0, u0 = quant(E0)
        q1, u1 = quant(E1)
        fix(q0, u0, q1, u1)
        for it in range(2):
            u0t = np.einsum("ntp,npc->ntc", M, u0.astype(np.float64))
            u1t = np.einsum("ntp,npc->ntc", M, u1.astype(np.float64))
            dt = u1t - u0t
            ddt = (dt * dt).sum(axis=2)
            t = np.where(ddt > 1e-6, ((data - u0t) * dt).sum(axis=2) / np.maximum(ddt, 1e-6), 0.0)
            g = np.clip(t @ pinv.T, 0.0, 1.0) * 64.0
            wi = np.abs(g[:, :, None] - wvf[None, None, :]).argmin(axis=2)
            Wq = wv[wi]
            wt = (Wq[:, I[:, 0]] * F[:, 0] + Wq[:, I[:, 1]] * F[:, 1] + Wq[:, I[:, 2]] * F[:, 2]
                  + Wq[:, I[:, 3]] * F[:, 3] + 8) >> 4
            w = wt / 64.0
            if it == 0:
                ne0, ne1 = E0.copy(), E1.copy()
                for p in range(P):
                    m = M[:, :, p]
                    a_ = (m * (1 - w) ** 2).sum(axis=1)
                    b_ = (m * w * (1 - w)).sum(axis=1)
                    c_ = (m * w ** 2).sum(axis=1)
                    det = a_ * c_ - b_ * b_
                    ok = det > 1e-3
                    sp0 = (data * (m * (1 - w))[:, :, None]).sum(axis=1)
                    sp1 = (data * (m * w)[:, :, None]).sum(axis=1)
                    dets = np.where(ok, det, 1.0)[:, None]
                    n0 = (c_[:, None] * sp0 - b_[:, None] * sp1) / dets
                    n1 = (a_[:, None] * sp1 - b_[:, None] * sp0) / dets
                    ne0[:, p] = np.where(ok[:, None], np.clip(n0, 0, 255), E0[:, p])
                    ne1[:, p] = np.where(ok[:, None], np.clip(n1, 0, 255), E1[:, p])
                q0, u0 = quant(ne0)
                q1, u1 = quant(ne1)
                fix(q0, u0, q1, u1)
        u0t = np.einsum("ntp,npc->ntc", M.astype(np.int64), u0)
        u1t = np.einsum("ntp,npc->ntc", M.astype(np.int64), u1)
        rec = (u0t * 257 * (64 - wt)[:, :, None] + u1t * 257 * wt[:, :, None] + 32) >> 6 >> 8
        err = (((rec - data) ** 2) * wgt).sum(axis=(1, 2))
        better = err < best_err
        for i in np.nonzero(better)[0]:
            best[i] = (parts, int(seeds[i]) if seeds is not None else 0, mode, gx, gy, wl, lev,
                       q0[i].copy(), q1[i].copy(), wi[i].copy())
        best_err[better] = err[better]


def _astc_encode_group(px, bw, bh, alpha, hard_thr=5.0):
    """px: (n, T, 4) float32 неоднотонные блоки одной группы (alpha - есть ли непрозрачность не везде).
    Возвращает для каждого блока 128-битное слово (python int)."""
    import numpy as np
    n, T, _ = px.shape
    C = 4 if alpha else 3
    cem = 12 if alpha else 8
    wgt = (0.25 + 0.75 * px[:, :, 3:4] / 255.0) if alpha else np.ones((n, T, 1), np.float32)
    wgt = wgt.astype(np.float64)
    data = px[:, :, :C].astype(np.float64)
    best_err = np.full(n, np.inf)
    best = [None] * n
    M1 = np.ones((n, T, 1))
    E0, E1 = _astc_enc_endpoints(data, wgt, M1)
    _astc_enc_eval(data, wgt, M1, E0, E1, bw, bh, _astc_pick_candidates(bw, bh, 2 * C), 17, best_err, best, 1, None)
    hard = np.nonzero(best_err / (T * C) > hard_thr)[0]
    if len(hard):
        import itertools
        d, wg = data[hard], wgt[hard]
        for parts in (2, 3):
            pm, poh = _astc_pattern_table(bw, bh, parts)
            lab = _astc_kmeans(d.astype(np.float32), parts)
            score = None
            for perm in itertools.permutations(range(parts)):
                lp = np.array(perm)[lab]
                Y = np.zeros((len(hard), T, parts), np.float32)
                for k in range(parts):
                    Y[:, :, k] = lp == k
                sc = Y.reshape(len(hard), -1) @ poh.T
                score = sc if score is None else np.maximum(score, sc)
            K = 2
            top = np.argsort(-score, axis=1)[:, :K]
            modes = _astc_pick_candidates(bw, bh, 2 * C * parts, limit=10, cstart=29)
            if not modes:
                continue
            sub_err = best_err[hard].copy()
            sub_best = [best[i] for i in hard]
            for r in range(K):
                seeds = top[:, r]
                M = np.zeros((len(hard), T, parts))
                lab_s = pm[seeds]
                for k in range(parts):
                    M[:, :, k] = lab_s == k
                E0, E1 = _astc_enc_endpoints(d, wg, M)
                _astc_enc_eval(d, wg, M, E0, E1, bw, bh, modes, 29, sub_err, sub_best, parts, seeds)
            for j, i in enumerate(hard):
                best[i] = sub_best[j]
            best_err[hard] = sub_err
    out = []
    for i in range(n):
        parts, seed, mode, gx, gy, wl, lev, a, b, wi = best[i]
        vals = []
        for p in range(parts):
            for c in range(C):
                vals += [int(a[p, c]), int(b[p, c])]
        cs = _astc_ise_encode(vals, len(vals), lev)
        ws = _astc_ise_encode([int(x) for x in wi], gx * gy, wl)
        wbits = _astc_ise_bits(gx * gy, wl)
        rev = int(format(ws, f"0{wbits}b")[::-1], 2) if wbits else 0
        if parts == 1:
            head = mode | (cem << 13) | (cs << 17)
        else:
            head = mode | ((parts - 1) << 11) | (seed << 13) | (cem << 25) | (cs << 29)
        out.append(head | (rev << (128 - wbits)))
    return out


def astc_encode(rgba, w, h, bw, bh, base=None, progress=None):
    """RGBA8 (w*h*4 байт) -> блоки ASTC. base=(блоки ASTC исходной текстуры, её RGBA) - для повторного использования
    неизменённых блоков. Возвращает bytes длиной блоков*16."""
    ensure_numpy_optional()
    try:
        import numpy as np
    except ImportError:
        raise UserError("Для кодирования ASTC нужен numpy: pip install numpy")
    bx, by = (w + bw - 1) // bw, (h + bh - 1) // bh

    def to_blocks(buf):
        im = np.frombuffer(buf, np.uint8).reshape(h, w, 4)
        im = np.pad(im, ((0, by * bh - h), (0, bx * bw - w), (0, 0)), mode="edge")
        return im.reshape(by, bh, bx, bw, 4).transpose(0, 2, 1, 3, 4).reshape(by * bx, bh * bw, 4)
    blocks = to_blocks(rgba)
    N = blocks.shape[0]
    out = np.zeros((N, 16), np.uint8)
    todo = np.ones(N, bool)
    if base is not None and len(base[0]) == N * 16:
        same = (blocks == to_blocks(base[1])).all(axis=(1, 2))
        out[same] = np.frombuffer(base[0], np.uint8).reshape(N, 16)[same]
        todo &= ~same
    const = (blocks == blocks[:, :1, :]).all(axis=(1, 2)) & todo
    for i in np.nonzero(const)[0]:
        c = blocks[i, 0]
        out[i] = np.frombuffer(b"\xfc\xfd" + b"\xff" * 6 + b"".join((int(v) * 257).to_bytes(2, "little") for v in c), np.uint8)
    todo &= ~const
    idx = np.nonzero(todo)[0]
    opaque = (blocks[idx, :, 3] == 255).all(axis=1)
    done = 0
    for alpha, sel in ((False, idx[opaque]), (True, idx[~opaque])):
        for s in range(0, len(sel), 4096):
            part = sel[s:s + 4096]
            words = _astc_encode_group(blocks[part].astype(np.float32), bw, bh, alpha)
            for i, wd in zip(part, words):
                out[i] = np.frombuffer(wd.to_bytes(16, "little"), np.uint8)
            done += len(part)
            if progress:
                progress(done, len(idx))
    return out.tobytes()



# ---- внешние текстуры: .sctx и .zktx (SC1-пакет: <имя>.sc + <имя>_tex.sc + <имя>_N.sctx|zktx)
#
# _tex.sc такого пакета - это поток тегов, где вместо пикселей лежат теги 47 (ссылка на файл):
#   u8 длина имени, имя файла, u8 (формат, 0), u16 ширина, u16 высота.
# .zktx - это обычный KTX1 (ASTC), сжатый zstd целиком.
# .sctx - flatbuffer 'SCTX' с шапкой (ширина, высота, тип пикселей, размер данных, превью-текстуры),
#         за ним ещё один маленький блок, дальше zstd-кадр с блоками ASTC. Оба блока идут с префиксом
#         длины (u32). Всё это сверено побайтно с готовыми одиночными .sc (см. тесты в истории чата).

SC1_EXT_TAG = 47
ASTC_FOOTPRINTS = sorted(set(ASTC_BLOCKS.values()))


def astc_to_rgba(data, w, h, bw, bh):
    """ASTC -> RGBA8 (w*h*4 байт). Берёт быстрый texture2ddecoder, если он есть, иначе чистый Python."""
    from PIL import Image
    dec = astc_decoder()
    if dec is not None:
        raw = dec.decode_astc(bytes(data), w, h, bw, bh)
        return Image.frombytes("RGBA", (w, h), raw, "raw", "BGRA").tobytes()
    return astc_decode(data, w, h, bw, bh)       # медленнее (секунды), зато без сторонних пакетов


def _zstd_all(buf, what):
    import zstandard
    try:
        return zstandard.ZstdDecompressor().decompressobj().decompress(buf)
    except Exception as e:
        raise UserError(f"{what}: не удалось распаковать zstd ({e})")


def _guess_astc_footprint(w, h, size):
    for bw, bh in ASTC_FOOTPRINTS:
        if ((w + bw - 1) // bw) * ((h + bh - 1) // bh) * 16 == size:
            return bw, bh
    return None


def read_sctx(path):
    """Читает .sctx. Возвращает {'w','h','rgba','kind','block','ptype'}."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 16:
        raise UserError(f"{os.path.basename(path)}: файл слишком короткий для .sctx")
    n = u32(data, 0)
    fb = data[4:4 + n]
    if len(fb) != n or fb[4:8] != b"SCTX":
        raise UserError(f"{os.path.basename(path)}: нет сигнатуры SCTX - это не .sctx")
    slots = dict(fb_slots(fb, fb_root(fb)))
    if 2 not in slots or 3 not in slots or 7 not in slots:
        raise UserError(f"{os.path.basename(path)}: в шапке нет ширины, высоты или размера данных")
    w, h, usize = u16(fb, slots[2]), u16(fb, slots[3]), u32(fb, slots[7])
    ptype = u32(fb, slots[6]) if 6 in slots else None
    pos = 4 + n
    pos += 4 + u32(data, pos)                       # второй блок шапки
    raw = _zstd_all(data[pos:], os.path.basename(path))
    if len(raw) != usize:
        raise UserError(f"{os.path.basename(path)}: размер данных {len(raw)} не равен заявленному {usize}")
    blk = _guess_astc_footprint(w, h, len(raw))
    if blk:
        rgba = astc_to_rgba(raw, w, h, blk[0], blk[1])
    elif len(raw) == w * h * 4:
        rgba, blk = bytes(raw), None
    else:
        raise UserError(f"{os.path.basename(path)}: тип пикселей {ptype} ({w}x{h}, {len(raw)} байт) хаб пока "
                        f"не умеет разбирать - поддерживается ASTC и RGBA8")
    return {"w": w, "h": h, "rgba": rgba, "kind": "sctx", "block": blk, "ptype": ptype}


# ---- запись .sctx / .zktx

SCTX_ASTC_W = {186: (4, 4), 187: (5, 4), 188: (5, 5), 189: (6, 5), 190: (6, 6), 192: (8, 5), 193: (8, 6), 194: (8, 8),
               195: (10, 5), 196: (10, 6), 197: (10, 8), 198: (10, 10), 199: (12, 10), 200: (12, 12), 204: (4, 4),
               205: (5, 4), 206: (5, 5), 207: (6, 5), 208: (6, 6), 210: (8, 5), 211: (8, 6), 212: (8, 8), 213: (10, 5),
               214: (10, 6), 215: (10, 8), 216: (10, 10), 217: (12, 10), 218: (12, 12)}
SCTX_RAW_W = {70: "RGBA", 71: "RGBA", 80: "BGRA", 81: "BGRA"}


class _FB:
    """Минимальный построитель flatbuffers (хватает для шапки .sctx). Буфер строится с конца файла к началу."""

    def __init__(self):
        self.b = bytearray()                    # байты в обратном порядке

    def size(self):
        return len(self.b)

    def prep(self, align, extra=0):
        self.b.extend(b"\0" * ((-(len(self.b) + extra)) % align))

    def put(self, v, n, signed=False):
        self.b.extend(int(v).to_bytes(n, "little", signed=signed)[::-1])

    def byte_vector(self, data, align=4):
        self.prep(align, len(data))
        self.b.extend(bytes(data)[::-1])
        self.prep(4)
        self.put(len(data), 4)
        return self.size()

    def u32_vector(self, vals):
        self.prep(4, 4 * len(vals))
        for v in reversed(vals):
            self.put(v, 4)
        self.put(len(vals), 4)
        return self.size()

    def offset_vector(self, offs):
        self.prep(4, 4 * len(offs))
        for o in reversed(offs):
            self.put(len(self.b) + 4 - o, 4)
        self.put(len(offs), 4)
        return self.size()

    def table(self, fields):
        """fields: [(слот, 'u8'|'u16'|'u32'|'i32'|'off', значение, по_умолчанию)] в порядке записи."""
        start = len(self.b)
        slots = {}
        for slot, kind, val, default in fields:
            if kind != "off" and val == default:
                continue
            if kind == "off":
                self.prep(4)
                self.put(len(self.b) + 4 - val, 4)
            else:
                n = {"u8": 1, "u16": 2, "u32": 4, "i32": 4}[kind]
                self.prep(n)
                self.put(val, n, signed=(kind == "i32"))
            slots[slot] = len(self.b)
        self.prep(4)
        self.put(0, 4)
        obj = len(self.b)
        nslots = (max(slots) + 1) if slots else 0
        for i in reversed(range(nslots)):
            self.prep(2)
            self.put((obj - slots[i]) if i in slots else 0, 2)
        self.prep(2)
        self.put(obj - start, 2)
        self.prep(2)
        self.put(4 + 2 * nslots, 2)
        self.b[obj - 4:obj] = (len(self.b) - obj).to_bytes(4, "little")[::-1]
        return obj

    def finish(self, root, ident=None, align=4):
        self.prep(align, 8 + (4 if ident else 0))
        if ident:
            self.b.extend(ident[::-1])
        self.put(len(self.b) + 4 - root, 4)
        self.put(len(self.b), 4)
        return bytes(self.b[::-1])


def _fnv1a64(data):
    v = 0xCBF29CE484222325
    for byte in data:
        v = ((v ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return v.to_bytes(8, "little")


def sctx_root_chunk(info, w, h, payload_len, variant, dal=16, fal=16):
    fb = _FB()
    var_off = 0
    if variant:
        vt, vw, vh, vd = variant
        ids_v = fb.u32_vector(info["ids"]) if info.get("ids") else 0
        data = fb.byte_vector(vd, dal)
        desc = fb.table([(3, "off", data, 0), (0, "u32", vt, 0), (2, "u16", vh, 0), (1, "u16", vw, 0)])
        tex_v = fb.offset_vector([desc])
        var_off = fb.table([(1, "off", tex_v, 0)] + ([(0, "off", ids_v, 0)] if ids_v else []))
    fields = [(10, "off", var_off, 0)] if var_off else []
    fields += [(9, "i32", info.get("unk6", 0), 0), (8, "i32", info.get("unk5", 0), 0), (7, "i32", payload_len, 0),
               (6, "u32", info["flags"], 0), (5, "i32", info.get("unk3", 0), 0), (4, "u8", info.get("levels", 1), 1),
               (3, "u16", h, 0), (2, "u16", w, 0), (1, "u32", info["ptype"], 0), (0, "i32", info.get("unk1", 0), 0)]
    return fb.finish(fb.table(fields), b"SCTX", fal)


def sctx_mip_entry(w, h, offset, hash8):
    fb = _FB()
    hv = fb.byte_vector(hash8)
    return fb.finish(fb.table([(3, "off", hv, 0), (2, "u32", offset, 0), (1, "u16", h, 0), (0, "u16", w, 0)]))


def sctx_template(path):
    """Параметры существующего .sctx: тип пикселей, флаги, неизвестные поля, число уровней, превью-текстура."""
    with open(path, "rb") as fh:
        data = fh.read()
    n = u32(data, 0)
    root = data[4:4 + n]
    if root[4:8] != b"SCTX":
        raise UserError(f"{os.path.basename(path)}: это не .sctx")
    sl = dict(fb_slots(root, fb_root(root)))
    g = lambda k, f, d=0: f(root, sl[k]) if k in sl else d
    gi = lambda k: struct.unpack_from("<i", root, sl[k])[0] if k in sl else 0
    info = {"ptype": g(1, u32), "w": g(2, u16), "h": g(3, u16), "levels": root[sl[4]] if 4 in sl else 1,
            "flags": g(6, u32), "unk1": gi(0), "unk3": gi(5), "unk5": gi(8), "unk6": gi(9), "ids": [], "variant": None,
            "tlen": gi(7)}
    if 10 in sl:
        vt = sl[10] + u32(root, sl[10])
        vs = dict(fb_slots(root, vt))
        if 0 in vs:
            ip = vs[0] + u32(root, vs[0])
            info["ids"] = [u32(root, ip + 4 + 4 * k) for k in range(u32(root, ip))]
        if 1 in vs:
            tp = vs[1] + u32(root, vs[1])
            if u32(root, tp):
                dt = tp + 4 + u32(root, tp + 4)
                ds = dict(fb_slots(root, dt))
                info["variant"] = (u32(root, ds[0]) if 0 in ds else 0, u16(root, ds[1]) if 1 in ds else 0,
                                   u16(root, ds[2]) if 2 in ds else 0)
    return info


def sctx_blocks(path):
    """(info, блоки ASTC основного уровня, ширина блока, высота блока) - для повторного использования блоков."""
    info = sctx_template(path)
    with open(path, "rb") as fh:
        data = fh.read()
    n = u32(data, 0)
    pos = 4 + n
    mlen = u32(data, pos)
    start = pos + 4 + mlen
    if info["flags"] & 8:
        start = (start + 15) & ~15
    blob = data[start:]
    raw = _zstd_all(blob, os.path.basename(path)) if (info["flags"] & 1 or blob[:4] == b"\x28\xb5\x2f\xfd") else blob
    return info, raw, SCTX_ASTC_W.get(info["ptype"])


def _mip_sizes(w, h, levels):
    import math
    out, cw, ch = [(w, h)], float(w), float(h)
    for _ in range(1, levels):
        cw, ch = cw / 2.0, ch / 2.0
        out.append((1 if cw < 1 else math.ceil(cw), 1 if ch < 1 else math.ceil(ch)))
    return out


def _encode_level(rgba, w, h, ptype, base=None):
    if ptype in SCTX_ASTC_W:
        bw, bh = SCTX_ASTC_W[ptype]
        return astc_encode(rgba, w, h, bw, bh, base)
    if ptype in SCTX_RAW_W:
        from PIL import Image
        mode = SCTX_RAW_W[ptype]
        return Image.frombytes("RGBA", (w, h), rgba).tobytes("raw", mode)
    raise UserError(f"Тип пикселей {ptype} для записи не поддержан (умею ASTC и RGBA/BGRA)")


def _resize_rgba(rgba, w, h, nw, nh):
    from PIL import Image
    return Image.frombytes("RGBA", (w, h), rgba).resize((nw, nh), Image.LANCZOS).tobytes()


def sctx_build(rgba, w, h, template=None, progress=None):
    """Собирает .sctx. template - путь к исходному .sctx этой текстуры: берутся тип пикселей, флаги, превью,
    а неизменившиеся блоки копируются из него. Без шаблона: ASTC 8x8, сжатие zstd, превью в 8 раз меньше."""
    import zstandard
    info = {"ptype": 212, "flags": 5, "levels": 1, "ids": [3], "variant": (212, 8, 8)}
    base = None
    if template:
        info = sctx_template(template)
        try:
            tinfo, raw, blk = sctx_blocks(template)
            if blk and (tinfo["w"], tinfo["h"]) == (w, h) and tinfo["levels"] == 1 and len(raw) == len(
                    astc_encode_size(w, h, blk[0], blk[1])):
                base = (raw, astc_to_rgba(raw, w, h, blk[0], blk[1]))
        except (UserError, KeyError, struct.error):
            base = None
    if base and base[1] == bytes(rgba) and template:
        with open(template, "rb") as fh:                   # картинка не менялась - файл остаётся как был
            return fh.read(), True
    ptype, levels = info["ptype"], info["levels"]
    sizes = _mip_sizes(w, h, levels)
    parts, mips, off, cur = [], [], 0, bytes(rgba)
    for k, (lw, lh) in enumerate(sizes):
        if k:
            cur = _resize_rgba(bytes(rgba), w, h, lw, lh)
        enc = _encode_level(cur, lw, lh, ptype, base if k == 0 else None)
        parts.append(enc)
        mips.append((lw, lh, off, _fnv1a64(enc)))
        off += len(enc)
    payload = b"".join(parts)
    variant = None
    if info.get("variant"):
        vt, tw, th = info["variant"]
        rw = max(1, round(info["w"] / tw)) if (template and tw) else (tw or 8)
        rh = max(1, round(info["h"] / th)) if (template and th) else (th or 8)
        vw, vh = max(1, w // rw), max(1, h // rh)
        variant = (vt, vw, vh, _encode_level(_resize_rgba(bytes(rgba), w, h, vw, vh), vw, vh, vt))
    root = sctx_root_chunk(info, w, h, len(payload), variant)
    entries = [sctx_mip_entry(*m) for m in mips]
    chunk = struct.pack("<I", sum(len(e) for e in entries)) + b"".join(entries)
    out = bytearray(root + chunk)
    if info["flags"] & 8:
        out.extend(b"\0" * (((len(out) + 15) & ~15) - len(out)))
    if info["flags"] & 1:
        out.extend(zstandard.ZstdCompressor(level=16, write_checksum=False, write_content_size=True).compress(payload))
    else:
        out.extend(payload)
    return bytes(out), False


def astc_encode_size(w, h, bw, bh):
    return b"\0" * (((w + bw - 1) // bw) * ((h + bh - 1) // bh) * 16)


KTX_HEAD = struct.Struct("<12s13I")
KTX_ID = b"\xabKTX 11\xbb\r\n\x1a\n"


def zktx_build(rgba, w, h, template=None):
    """Собирает .zktx (KTX1 + zstd). template - путь к исходному .zktx: берутся формат ASTC и ключи KTX, а
    неизменившиеся блоки копируются. Возвращает (байты, изменился ли файл)."""
    import zstandard
    gl, kv, levels, base = None, b"", 1, None
    if template:
        with open(template, "rb") as fh:
            ktx = _zstd_all(fh.read(), os.path.basename(template))
        head = KTX_HEAD.unpack_from(ktx, 0)
        gl, levels, kvb = head[5], max(1, head[12]), head[13]
        kv = ktx[64:64 + kvb]
        ki = ktx_info(ktx)
        if ki and ki[1:3] == (w, h) and levels == 1 and gl in ASTC_BLOCKS:
            blk = ASTC_BLOCKS[gl]
            raw = ktx[ki[3]:ki[3] + ki[4]]
            base = (raw, astc_to_rgba(raw, w, h, blk[0], blk[1]))
        if base and base[1] == bytes(rgba):
            with open(template, "rb") as fh:
                return fh.read(), True
    if gl is None:
        gl = next(k for k, v in ASTC_BLOCKS.items() if v == (8, 8))
    if gl not in ASTC_BLOCKS:
        raise UserError(f"Формат KTX 0x{gl:X} для записи не поддержан (умею ASTC)")
    bw, bh = ASTC_BLOCKS[gl]
    sizes = [(max(1, w >> i), max(1, h >> i)) for i in range(levels)]
    body = bytearray()
    for k, (lw, lh) in enumerate(sizes):
        cur = bytes(rgba) if k == 0 else _resize_rgba(bytes(rgba), w, h, lw, lh)
        enc = astc_encode(cur, lw, lh, bw, bh, base if k == 0 else None)
        body += struct.pack("<I", len(enc)) + enc + b"\0" * ((-len(enc)) % 4)
    head = KTX_HEAD.pack(KTX_ID, 0x04030201, 0, 1, 0, gl, 0x1908, w, h, 0, 0, 1, levels, len(kv))
    ktx = head + kv + bytes(body)
    return zstandard.ZstdCompressor(level=16).compress(ktx), False


def write_ext_texture(path, rgba, w, h, template=None):
    """Записывает .sctx или .zktx по расширению. True, если файл не менялся (скопирован шаблон)."""
    low = path.lower()
    if low.endswith(".sctx"):
        data, same = sctx_build(rgba, w, h, template)
    elif low.endswith(".zktx"):
        data, same = zktx_build(rgba, w, h, template)
    else:
        raise UserError("Жду .sctx или .zktx")
    with open(path, "wb") as fh:
        fh.write(data)
    return same



def read_zktx(path):
    """Читает .zktx (zstd поверх KTX1). Возвращает {'w','h','rgba','kind','block','gl'}."""
    with open(path, "rb") as fh:
        ktx = _zstd_all(fh.read(), os.path.basename(path))
    ki = ktx_info(ktx)
    if not ki:
        raise UserError(f"{os.path.basename(path)}: внутри нет KTX1")
    gl, w, h, at, size = ki
    blk = ASTC_BLOCKS.get(gl)
    if blk:
        rgba = astc_to_rgba(ktx[at:at + size], w, h, blk[0], blk[1])
    elif gl == 0x8058 and size >= w * h * 4:              # GL_RGBA8
        rgba = bytes(ktx[at:at + w * h * 4])
    else:
        raise UserError(f"{os.path.basename(path)}: формат KTX 0x{gl:X} хаб не умеет разбирать "
                        f"(поддерживается ASTC и RGBA8)")
    return {"w": w, "h": h, "rgba": rgba, "kind": "zktx", "block": blk, "gl": gl}


def read_ext_texture(path):
    low = path.lower()
    if low.endswith(".sctx"):
        return read_sctx(path)
    if low.endswith(".zktx"):
        return read_zktx(path)
    raise UserError(f"{os.path.basename(path)}: неизвестный тип внешней текстуры (жду .sctx или .zktx)")


def sc1_ext_refs(tex_raw):
    """Теги 47 из _tex.sc: [{'name','pf','w','h'}] в порядке следования."""
    out = []
    for tag, body in sc1_parse_stream_tags(tex_raw):
        if tag != SC1_EXT_TAG:
            continue
        n = body[0]
        name = body[1:1 + n].decode("utf-8", "replace")
        pf = body[1 + n]
        w, h = struct.unpack_from("<2H", body, 2 + n)
        out.append({"name": name, "pf": pf, "w": w, "h": h})
    return out


def sc1_build_ext_tex_raw(refs):
    """Поток тегов _tex.sc из списка ссылок (обратное к sc1_ext_refs)."""
    out = bytearray()
    for r in refs:
        nm = r["name"].encode("utf-8")
        body = bytes([len(nm)]) + nm + bytes([r.get("pf", 0)]) + struct.pack("<2H", r["w"], r["h"])
        out += bytes([SC1_EXT_TAG]) + struct.pack("<i", len(body)) + body
    out += bytes([0]) + struct.pack("<i", 0)
    return bytes(out)


def tex_sc_candidates(path):
    """Файлы _tex.sc рядом с основным: <имя>_tex.sc, а у мультиразрешения ещё _highres_tex.sc и _lowres_tex.sc."""
    base = os.path.splitext(path)[0]
    names = [base + "_tex.sc", base + "_highres_tex.sc", base + "_lowres_tex.sc"]
    out = [c for c in names if os.path.isfile(c)]
    if not out:
        short = os.path.basename(base)
        for d in (IN_DIR, OUT_DIR):
            for suf in ("_tex.sc", "_highres_tex.sc", "_lowres_tex.sc"):
                c = os.path.join(d, short + suf)
                if os.path.isfile(c):
                    out.append(c)
    return out


def pick_tex_file(path, sizes=None):
    """Какой из _tex.sc брать. Если их несколько (highres/lowres) - тот, чьи размеры совпадают с заголовками
    текстур основного файла (sizes - список (w, h)); иначе highres, потом обычный."""
    cands = tex_sc_candidates(path)
    if not cands:
        return None
    if len(cands) > 1 and sizes:
        for c in cands:
            try:
                with open(c, "rb") as fh:
                    refs = sc1_ext_refs(sc1_container(fh.read())["raw"])
            except (UserError, struct.error, OSError):
                continue
            if [(r["w"], r["h"]) for r in refs] == list(sizes):
                return c
    for key in ("_highres_tex.sc", "_tex.sc", "_lowres_tex.sc"):
        for c in cands:
            if c.endswith(key):
                return c
    return cands[0]


def find_tex_file(path, sizes=None):
    return pick_tex_file(path, sizes)


def load_tex_cont(src):
    """Для SC1: (путь к _tex.sc, его контейнер с прикреплёнными внешними текстурами или None). Нужен атласам."""
    with open(src, "rb") as fh:
        cont = sc1_container(fh.read())
    tex_path, tex_cont, ext = load_sc1_package(src, cont)
    if tex_cont is not None:
        refs = sc1_ext_refs(tex_cont["raw"])
        if refs and len(tex_sc_candidates(src)) > 1:
            raise UserError("У пакета несколько _tex.sc (highres/lowres): атласы для такого пакета пока не "
                            "перестраиваются, чтобы не рассинхронизировать разрешения")
        tex_cont["ext"] = ext
    return tex_path, tex_cont



def sc1_head_sizes(cont):
    """Размеры текстур без пикселей в теге (они лежат во внешних файлах)."""
    mdl = sc1_parse_stream(cont["raw"])
    return [struct.unpack_from("<2H", b, 1) for t, b in mdl["tags"] if t in SC1_TEXTURE_TAGS and len(b) <= 5]


def find_ext_file(name, tex_path, main_path):
    dirs = [os.path.dirname(os.path.abspath(p)) for p in (tex_path, main_path) if p] + [IN_DIR, OUT_DIR]
    for d in dirs:
        c = os.path.join(d, name)
        if os.path.isfile(c):
            return c
    return None


def load_sc1_package(path, cont):
    """Для SC1: (tex_path, tex_cont, ext). ext - прочитанные .sctx/.zktx из тегов 47 файла _tex.sc."""
    tex_path = find_tex_file(path, [tuple(x) for x in sc1_head_sizes(cont)])
    if not tex_path:
        return None, None, []
    with open(tex_path, "rb") as fh:
        tex_cont = sc1_container(fh.read())
    ext = []
    for r in sc1_ext_refs(tex_cont["raw"]):
        p = find_ext_file(r["name"], tex_path, path)
        if not p:
            raise UserError(f"{os.path.basename(tex_path)} ссылается на «{r['name']}», но такого файла нет "
                            f"рядом с .sc (положи его туда же или в папку input)")
        t = read_ext_texture(p)
        t["name"], t["path"] = r["name"], p
        ext.append(t)
    return tex_path, tex_cont, ext


def sniff_format(data):
    if data[:2] != b"SC":
        raise UserError("Это не .sc (нет заголовка SC)")
    return "sc2" if data[2:6] == b"\x05\x00\x00\x00" else "sc1"


def decode_file(path, out_dir):
    """Разбор .sc (SC1 или SC2) в out_dir: data.json|data.msgpack + texture_N.png. Возвращает отчёт."""
    from PIL import Image
    with open(path, "rb") as fh:
        data = fh.read()
    kind = sniff_format(data)
    tex_path = None
    if kind == "sc2":
        doc, tex_infos = sc2_to_doc(parse_sc(data))
    else:
        cont = sc1_container(data)
        tex_path, tex_cont, ext = load_sc1_package(path, cont)
        doc, tex_infos = sc1_to_doc(cont, tex_cont, ext)
    os.makedirs(out_dir, exist_ok=True)
    notes = save_textures(Image, doc, tex_infos, out_dir)
    data_path, how = write_doc(doc, out_dir)
    return {"kind": kind, "doc": doc, "data_path": data_path, "how": how, "notes": notes,
            "tex_path": tex_path, "out_dir": out_dir}


def flow_decode():
    print("  Что сделать:")
    print("    1 - разобрать .sc в JSON (или msgpack, если JSON больше 20 МБ) + PNG")
    print("    2 - собрать .sc обратно из папки с data.json/data.msgpack + PNG")
    if ask("Что сделать", "1") == "2":
        return flow_build()
    ensure_deps()
    src = resolve_input("Какой .sc разобрать в JSON/msgpack + PNG", "input.sc",
                        [("SC", "*.sc"), ("Все файлы", "*.*")])
    stem = os.path.splitext(os.path.basename(src))[0]
    out_dir = resolve_out_dir(ask("Папка для результата", f"output/{stem}_decoded"))
    print(gray("  Разбираю (для больших файлов это занимает до минуты)..."))
    rep = decode_file(src, out_dir)
    doc = rep["doc"]
    line()
    print(f"  Формат: {'SC2 (flatbuffers)' if rep['kind'] == 'sc2' else 'SC1 (теги)'}, "
          f"экспортов {len(doc['exports'])}, шейпов {len(doc['shapes'])}, клипов {len(doc['movieclips'])}, "
          f"текстовых полей {len(doc['textfields'])}, текстур {len(doc['textures'])}")
    if rep["tex_path"]:
        print(gray(f"  Текстуры взяты из {os.path.basename(rep['tex_path'])}"))
    if rep["kind"] == "sc1" and doc["sc1"].get("ext_textures"):
        print(gray("  Внешние файлы: " + ", ".join(t["name"] for t in doc["sc1"]["ext_textures"])))
        print(yellow("  ⚠ Сборка обратно из JSON положит текстуры сырым RGBA в _tex.sc, а не в .sctx/.zktx: "
                     "кодировать ASTC хаб пока не умеет. Для правок без текстур используй пункты "
                     "переименования, FPS, текстов - они сохраняют .sctx/.zktx как есть."))
    saved(rep["data_path"])
    if rep["how"] == "msgpack":
        print(gray("  JSON вышел бы больше 20 МБ, поэтому записан msgpack."))
    for f in sorted(os.listdir(out_dir)):
        if f.startswith("texture_"):
            saved(os.path.join(out_dir, f))
    for n in rep["notes"]:
        print(yellow("  ⚠ " + n))
    if rep["kind"] == "sc1":
        un = doc["sc1"]["unparsed_tags"]
        if un:
            print(yellow(f"  ⚠ Теги, которые хаб не разбирает (пропущены в JSON): {un}"))
    return [rep["data_path"]]


# ================================================================ открытие SC1/SC2, правки SC1, FPS, положение клипа
#
# SC1 - это поток тегов (см. разбор выше). Правки делаются без пересборки с нуля: теги хранятся как есть,
# меняются только нужные тела тегов, затем поток собирается заново, считается MD5 и сжимается zstd.
# Перед записью проверяется, что неизменённый поток собирается байт-в-байт (иначе отказ).
# Хвост файла после сжатых данных (b"START" + имена экспортов с 16-байтными значениями) сохраняется;
# при переименовании имена в нём обновляются, сами 16-байтные значения остаются прежними (что они
# значат, я не выяснил: это не MD5 имени и не MD5 тела клипа, проверено).
# Запись SC1 умеет только zstd (LZMA/LZHAM-файлы можно читать и разбирать, но не перезаписывать).

def open_sc_bytes(data, path=""):
    kind = sniff_format(data)
    if kind == "sc2":
        sc = parse_sc(data)
    else:
        cont = sc1_container(data)
        sc = {"kind": "sc1", "cont": cont, "mdl": sc1_parse_stream(cont["raw"]), "version": cont["real"]}
    sc["path"] = path
    return sc


def open_sc(path):
    with open(path, "rb") as fh:
        return open_sc_bytes(fh.read(), path)


def sc1_require_writable(sc):
    cont = sc["cont"]
    if cont["comp"] != "zstd":
        raise UserError("У этого SC1-файла сжатие LZMA: хаб умеет его читать и разбирать, но "
                        "перезаписывать только файлы со сжатием zstd")
    if len(cont["md5"]) != 16:
        raise UserError("Хеш в заголовке не 16 байт - не рискую пересобирать этот файл")
    if "_rt" not in sc:
        sc["_rt"] = (sc1_build_stream(sc["mdl"]) == cont["raw"])
    if not sc["_rt"]:
        raise UserError("Поток этого файла не собирается обратно байт-в-байт (в нём есть нестандартные "
                        "данные), не рискую его менять")


def sc1_write(sc, new_raw, new_tail=None):
    cont = sc["cont"]
    return sc1_pack(cont["ver"], cont["real"], new_raw, cont["tail"] if new_tail is None else new_tail)


def sc1_exports(sc):
    mdl = sc["mdl"]
    return [{"id": i, "ref": None, "name": sc1_text(n)} for i, n in zip(mdl["ids"], mdl["names"])], []


def sc1_mat_float(row):
    return (row[0] / 1024, row[1] / 1024, row[2] / 1024, row[3] / 1024, row[4] / 20, row[5] / 20)


def sc1_mat_int(m):
    def q(v, k):
        return max(-2 ** 31, min(2 ** 31 - 1, int(round(v * k))))
    return (q(m[0], 1024), q(m[1], 1024), q(m[2], 1024), q(m[3], 1024), q(m[4], 20), q(m[5], 20))


def sc1_load_model(sc):
    tags = sc["mdl"]["tags"]
    mats, mat_pos, shapes, clips = [], [], [], []
    for pos, (tag, body) in enumerate(tags):
        if tag == 8:
            if len(body) != 24:
                raise UserError("Матрица нестандартной длины - файл устроен иначе, чем ожидалось")
            mats.append(struct.unpack("<6i", body))
            mat_pos.append(pos)
        elif tag == 18:
            sid, cmds = sc1_parse_shape(body)
            shapes.append({"index": len(shapes), "id": sid, "cmds": cmds, "pos": pos, "verts": []})
        elif tag == 49:
            c = sc1_parse_clip(body)
            clips.append({"index": len(clips), "id": c["id"], "children": [i["id"] for i in c["inst"]],
                          "n_el": len(c["flat"]) // 3, "offset": None, "bank": 0, "fps": c["fps"],
                          "nframes": c["nframes"], "frame0": c["frames"][0]["count"] if c["frames"] else 0,
                          "flat": c["flat"], "pos": pos, "parsed": c, "bank_field": None})
    return {"kind": "sc1", "mats": mats, "mat_pos": mat_pos, "shapes": shapes, "clips": clips,
            "banks": [], "tf_bounds": {}}


def sc1_apply_transform(sc, clip_targets, shape_targets, pivot):
    if any(t[0] == 42 for t in sc["mdl"]["tags"]):
        raise UserError("В этом SC1 несколько банков матриц (тег 42). Как клип выбирает свой банк, хаб ещё "
                        "не знает, поэтому менять матрицы (трансформ) не рискую. Переименование, FPS и "
                        "правка имён текстовых полей работают.")
    sc1_require_writable(sc)
    m = sc1_load_model(sc)
    tags = [list(t) for t in sc["mdl"]["tags"]]
    mats = m["mats"]
    appended = []                      # новые матрицы (кортежи int)
    if clip_targets:
        usage = {}
        for clip in m["clips"]:
            for idx in set(clip["flat"][1::3]):
                if idx != NO_INDEX:
                    usage.setdefault(idx, set()).add(clip["index"])
        patches, new_idx_of, plans = {}, {}, {}
        for ci, (kx, ky, dx, dy) in clip_targets.items():
            clip = m["clips"][ci]
            if not clip["n_el"]:
                raise UserError(f"У клипа {clip['id']} нет элементов кадров - менять нечего")
            plan = {}
            for old in sorted(set(clip["flat"][1::3])):
                if old != NO_INDEX and old >= len(mats):
                    raise UserError(f"Клип {clip['id']}: номер матрицы {old} вне списка")
                base = IDENTITY if old == NO_INDEX else sc1_mat_float(mats[old])
                row = sc1_mat_int(xf_matrix(base, kx, ky, dx, dy))
                if old != NO_INDEX and usage[old] == {ci}:
                    patches[old] = row
                    plan[old] = ("keep", old)
                else:
                    if row not in new_idx_of:
                        new_idx_of[row] = len(mats) + len(appended)
                        appended.append(row)
                    plan[old] = ("new", new_idx_of[row])
            plans[ci] = plan
        if len(mats) + len(appended) > NO_INDEX:
            raise UserError("Матриц получилось бы больше 65535 - в SC1 это упирается в лимит списка "
                            "(банки SC1 хаб не поддерживает). Выбери меньше клипов за раз.")
        for idx, row in patches.items():
            pos = m["mat_pos"][idx]
            tags[pos][1] = struct.pack("<6i", *row)
        for ci, plan in plans.items():
            clip = m["clips"][ci]
            parsed = dict(clip["parsed"])
            if sc1_build_clip(parsed) != tags[clip["pos"]][1]:
                raise UserError(f"Клип {clip['id']} собирается обратно не байт-в-байт - не рискую его менять")
            flat = list(clip["flat"])
            for k in range(clip["n_el"]):
                kind, val = plan[flat[3 * k + 1]]
                flat[3 * k + 1] = val
            parsed["flat"] = flat
            tags[clip["pos"]][1] = sc1_build_clip(parsed)
    if shape_targets:
        for si, (kx, ky, dx, dy) in shape_targets.items():
            sh = m["shapes"][si]
            if sc1_build_shape(sh["id"], sh["cmds"]) != tags[sh["pos"]][1]:
                raise UserError(f"Шейп {sh['id']} собирается обратно не байт-в-байт - не рискую его менять")
            pts = [(c["xy"][2 * k] / 20, c["xy"][2 * k + 1] / 20) for c in sh["cmds"] for k in range(len(c["xy"]) // 2)]
            if not pts:
                raise UserError(f"У шейпа {sh['id']} нет вершин")
            if pivot == "center":
                px = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2.0
                py = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2.0
            else:
                px = py = 0.0
            cmds = []
            for c in sh["cmds"]:
                xy = list(c["xy"])
                for k in range(len(xy) // 2):
                    x, y = xy[2 * k] / 20, xy[2 * k + 1] / 20
                    xy[2 * k] = int(round((px + kx * (x - px) + dx) * 20))
                    xy[2 * k + 1] = int(round((py + ky * (y - py) + dy) * 20))
                cmds.append({"tex": c["tex"], "xy": xy, "uv": c["uv"]})
            tags[sh["pos"]][1] = sc1_build_shape(sh["id"], cmds)
    if appended:
        at = (m["mat_pos"][-1] + 1) if m["mat_pos"] else min(c["pos"] for c in m["clips"])
        tags[at:at] = [[8, struct.pack("<6i", *row)] for row in appended]
    mdl = dict(sc["mdl"])
    mdl["tags"] = tags
    data = sc1_write(sc, sc1_build_stream(mdl))
    verify_transform_sc1(sc, data, clip_targets, shape_targets, pivot)
    return data


def verify_transform_sc1(old, data, clip_targets, shape_targets, pivot):
    new = open_sc_bytes(data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if new["kind"] != "sc1":
        fail("результат не читается как SC1")
    if new["cont"]["md5_ok"] is not True:
        fail("MD5 в заголовке не совпал с данными")
    if new["cont"]["tail"] != old["cont"]["tail"]:
        fail("хвост файла изменился")
    if new["mdl"]["ids"] != old["mdl"]["ids"] or new["mdl"]["names"] != old["mdl"]["names"]:
        fail("таблица экспортов изменилась")
    keep = lambda t: t[0] not in (8, 18, 49)
    if [t for t in old["mdl"]["tags"] if keep(t)] != [t for t in new["mdl"]["tags"] if keep(t)]:
        fail("изменились теги, которые не должны были меняться (текстуры, цвета и т.д.)")
    mo, mn = sc1_load_model(old), sc1_load_model(new)
    if len(mo["clips"]) != len(mn["clips"]) or len(mo["shapes"]) != len(mn["shapes"]):
        fail("число клипов или шейпов изменилось")
    for so, sn in zip(mo["shapes"], mn["shapes"]):
        if so["index"] not in shape_targets:
            if old["mdl"]["tags"][so["pos"]][1] != new["mdl"]["tags"][sn["pos"]][1]:
                fail(f"изменился невыбранный шейп {so['id']}")
            continue
        kx, ky, dx, dy = shape_targets[so["index"]]
        pts = [(c["xy"][2 * k] / 20, c["xy"][2 * k + 1] / 20) for c in so["cmds"] for k in range(len(c["xy"]) // 2)]
        if pivot == "center":
            px = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2.0
            py = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2.0
        else:
            px = py = 0.0
        got = [(c["xy"][2 * k] / 20, c["xy"][2 * k + 1] / 20) for c in sn["cmds"] for k in range(len(c["xy"]) // 2)]
        for (x, y), (gx, gy) in zip(pts, got):
            if abs(gx - (px + kx * (x - px) + dx)) > 0.026 or abs(gy - (py + ky * (y - py) + dy)) > 0.026:
                fail(f"вершины шейпа {so['id']} посчитаны не так, как ожидалось")
        if [c["uv"] for c in so["cmds"]] != [c["uv"] for c in sn["cmds"]]:
            fail(f"у шейпа {so['id']} изменились текстурные координаты")
    cache_o, cache_n = {}, {}
    tol = (0.0006, 0.0006, 0.0006, 0.0006, 0.026, 0.026)
    for co, cn in zip(mo["clips"], mn["clips"]):
        po, pn = dict(co["parsed"]), dict(cn["parsed"])
        fo, fn = po.pop("flat"), pn.pop("flat")
        if po != pn:
            fail(f"описание клипа {co['id']} изменилось")
        if fo[0::3] != fn[0::3] or fo[2::3] != fn[2::3]:
            fail(f"у клипа {co['id']} изменились дети или цвета")
        eo, en_ = clip_effective(mo, co, cache_o), clip_effective(mn, cn, cache_n)
        if co["index"] in clip_targets:
            kx, ky, dx, dy = clip_targets[co["index"]]
            for x, y in zip(eo, en_):
                want = xf_matrix(x, kx, ky, dx, dy)
                if any(abs(a - b) > t for a, b, t in zip(y, want, tol)):
                    fail(f"матрицы клипа {co['id']} посчитаны не так, как ожидалось")
        elif eo != en_:
            fail(f"у невыбранного клипа {co['id']} изменились итоговые матрицы")


def sc1_rename_exports(sc, mapping):
    sc1_require_writable(sc)
    exports, _ = sc1_exports(sc)
    names = [e["name"] for e in exports]
    changes = plan_rename(names, mapping)
    if not changes:
        raise UserError("Все новые имена совпадают со старыми - менять нечего")
    mdl = dict(sc["mdl"])
    new_names = []
    for raw, nm in zip(mdl["names"], names):
        if nm in changes:
            b = changes[nm].encode("utf-8")
            if raw is not None and raw.endswith(b"\0"):
                b += b"\0"
            if len(b) > 254:
                raise UserError(f"Имя «{changes[nm]}» длиннее 254 байт - в SC1 длина строки один байт")
            new_names.append(b)
        else:
            new_names.append(raw)
    mdl["names"] = new_names
    tail = sc["cont"]["tail"]
    foot = sc1_footer(tail)
    new_tail = None
    if foot:
        ents, rest = foot
        new_tail = b"START" + b"".join(changes.get(n, n).encode("utf-8") + b"\0\x10" + hsh for n, hsh in ents) + rest
    data = sc1_write(sc, sc1_build_stream(mdl), new_tail)
    new = open_sc_bytes(data)
    expected = [changes.get(n, n) for n in names]
    if [e["name"] for e in sc1_exports(new)[0]] != expected or new["mdl"]["ids"] != sc["mdl"]["ids"]:
        raise UserError("Проверка результата не прошла, файл не записан: имена экспортов не совпали")
    if new["mdl"]["tags"] != sc["mdl"]["tags"]:
        raise UserError("Проверка результата не прошла, файл не записан: теги изменились")
    if new["cont"]["md5_ok"] is not True:
        raise UserError("Проверка результата не прошла, файл не записан: MD5 в заголовке не совпал")
    if foot:
        nf = sc1_footer(new["cont"]["tail"])
        if not nf or [n for n, _h in nf[0]] != [changes.get(n, n) for n, _h in foot[0]]:
            raise UserError("Проверка результата не прошла, файл не записан: имена в хвосте не совпали")
    return data, changes


def sc1_set_fps(sc, targets):
    sc1_require_writable(sc)
    m = sc1_load_model(sc)
    tags = [list(t) for t in sc["mdl"]["tags"]]
    for ci, fps in targets.items():
        pos = m["clips"][ci]["pos"]
        body = bytearray(tags[pos][1])
        body[2] = fps
        tags[pos][1] = bytes(body)
    mdl = dict(sc["mdl"])
    mdl["tags"] = tags
    data = sc1_write(sc, sc1_build_stream(mdl))
    new = open_sc_bytes(data)
    mn = sc1_load_model(new)
    if new["cont"]["md5_ok"] is not True:
        raise UserError("Проверка результата не прошла, файл не записан: MD5 в заголовке не совпал")
    for co, cn in zip(m["clips"], mn["clips"]):
        want = targets.get(co["index"], co["fps"])
        po, pn = dict(co["parsed"]), dict(cn["parsed"])
        po["fps"] = want
        if cn["fps"] != want or po != pn:
            raise UserError(f"Проверка результата не прошла, файл не записан: клип {co['id']} изменился не так")
    for i, (a, b) in enumerate(zip(sc["mdl"]["tags"], new["mdl"]["tags"])):
        if a[0] != 49 and a != b:
            raise UserError("Проверка результата не прошла, файл не записан: изменился тег, не относящийся к клипам")
    return data


def sc1_summary(sc):
    cont, mdl = sc["cont"], sc["mdl"]
    lines = [f"SC1: контейнер версии {cont['ver']}" + (f" (реальная {cont['real']})" if cont["ver"] == 4 else "")
             + f", сжатие {cont['comp']}, MD5 в заголовке {'совпал' if cont['md5_ok'] else 'НЕ совпал'}, "
             f"распаковано {fmt_size(len(cont['raw']))}"]
    hist = {}
    for tag, _b in mdl["tags"]:
        hist[tag] = hist.get(tag, 0) + 1
    lines.append("Теги (номер: сколько): " + ", ".join(f"{t}: {n}" for t, n in sorted(hist.items())))
    known = {0, 1, 8, 9, 11, 16, 18, 19, 22, 23, 24, 26, 27, 28, 29, 32, 34, 47, 49}
    odd = {t: n for t, n in hist.items() if t not in known}
    if odd:
        lines.append(yellow(f"Теги, которых я не знаю (хаб сохраняет их как есть, но не разбирает): {odd}"))
    c = mdl["counts"]
    lines.append(f"Шапка: шейпов {c[0]}, клипов {c[1]}, текстур {c[2]}, текстовых полей {c[3]}, "
                 f"матриц {c[4]}, цветов {c[5]}")
    exports, _ = sc1_exports(sc)
    lines.append("ExportName'ы:")
    for e in exports[:30]:
        lines.append(f"  клип id {e['id']}  →  {e['name']}")
    if len(exports) > 30:
        lines.append(f"  ... ещё {len(exports) - 30}")
    m = sc1_load_model(sc)
    lines.append(f"MovieClips: {len(m['clips'])}")
    for clip in m["clips"][:20]:
        lines.append(f"  клип {clip['id']}: {clip['nframes']} кадр., {clip['fps']} fps, детей {len(clip['children'])}")
    for tag, body in mdl["tags"]:
        if tag in SC1_TEXTURE_TAGS:
            w, h = struct.unpack_from("<2H", body, 1)
            lines.append(f"Текстура (тег {tag}): {w}x{h}, формат пикселей {body[0]}, "
                         + (f"пиксели внутри ({fmt_size(len(body) - 5)})" if len(body) > 5
                            else "внешняя (пиксели в _tex.sc)"))
    foot = sc1_footer(cont["tail"])
    if foot:
        lines.append(f"Хвост файла: START + {len(foot[0])} записей экспортов + {len(foot[1])} байт")
    return lines


def build_dump_sc1(path, sc):
    out = [f"Дамп: {os.path.basename(path)}", "=" * 60]
    for l in sc1_summary(sc):
        out.append(re.sub(r"\033\[[0-9;]*m", "", l))
    out += ["", "Список тегов потока (номер, длина тела):"]
    for i, (tag, body) in enumerate(sc["mdl"]["tags"][:3000]):
        out.append(f"  #{i}: тег {tag}, {len(body)} байт")
    if len(sc["mdl"]["tags"]) > 3000:
        out.append(f"  ... ещё {len(sc['mdl']['tags']) - 3000}")
    out += ["", f"Хвост (hex): {sc['cont']['tail'].hex()}"]
    return "\n".join(out) + "\n"


# ---- FPS мувиклипов (SC2 и SC1)

def set_fps(sc, targets):
    """targets: {индекс клипа: fps (1..255)}. Возвращает байты нового .sc."""
    for ci, fps in targets.items():
        if not 1 <= fps <= 255:
            raise UserError("Частота кадров должна быть целым числом от 1 до 255")
    if sc.get("kind") == "sc1":
        return sc1_set_fps(sc, targets)
    m = load_model(sc)
    mcb = bytearray(sc["chunks"][4])
    tabs = fb_table_vec(sc["chunks"][4], fb_slot(sc["chunks"][4], fb_root(sc["chunks"][4]), 0))
    for ci, fps in targets.items():
        clip = m["clips"][ci]
        pos = clip["fps_field"]
        if pos is None:
            raise UserError(f"У клипа {clip['id']} в файле нет поля частоты кадров (значение 0) - "
                            f"поменять на месте нельзя")
        mcb[pos] = fps
    chunks = list(sc["chunks"])
    chunks[4] = bytes(mcb)
    data = finalize_sc(sc, chunks, sc["fd"])
    new = parse_sc(data)
    mn = load_model(new)
    for i in (0, 1, 2, 3, 5, 6):
        if new["chunks"][i] != sc["chunks"][i]:
            raise UserError(f"Проверка результата не прошла, файл не записан: блок {CHUNK_NAMES[i]} изменился")
    for co, cn in zip(m["clips"], mn["clips"]):
        want = targets.get(co["index"], co["fps"])
        if cn["fps"] != want or (co["id"], co["bank"], co["offset"], co["n_el"], co["children"]) != \
                (cn["id"], cn["bank"], cn["offset"], cn["n_el"], cn["children"]):
            raise UserError(f"Проверка результата не прошла, файл не записан: клип {co['id']} изменился не так")
    expected = bytearray(sc["chunks"][4])
    for ci, fps in targets.items():
        expected[m["clips"][ci]["fps_field"]] = fps
    if bytes(expected) != new["chunks"][4]:
        raise UserError("Проверка результата не прошла, файл не записан: блок MovieClips изменился не только частотой")
    return data


# ---- положение и размер клипа (кадр 1, от точки (0,0) клипа)

def model_index(m):
    if "_idx" not in m:
        m["_idx"] = {"shape": {s["id"]: s["index"] for s in m["shapes"]},
                     "clip": {c["id"]: c["index"] for c in m["clips"]}, "pts": {}}
    return m["_idx"]


def shape_points(m, si):
    idx = model_index(m)
    if si in idx["pts"]:
        return idx["pts"][si]
    sh = m["shapes"][si]
    if m.get("kind") == "sc1":
        pts = [(c["xy"][2 * k] / 20, c["xy"][2 * k + 1] / 20) for c in sh["cmds"] for k in range(len(c["xy"]) // 2)]
    else:
        pts = [struct.unpack_from("<ff", m["ds"], m["v_start"] + 12 * v) for v in sorted(set(sh["verts"]))]
    idx["pts"][si] = pts
    return pts


def mat_mul(p, c):
    """Матрица p после матрицы c: сначала c, потом p."""
    a1, b1, c1, d1, tx1, ty1 = p
    a2, b2, c2, d2, tx2, ty2 = c
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * tx2 + c1 * ty2 + tx1, b1 * tx2 + d1 * ty2 + ty1)


def clip_points(m, ci, matrix, depth, stack, out, cache, cap=400000):
    clip = m["clips"][ci]
    if not clip["n_el"] or len(out) > cap:
        return
    idx = model_index(m)
    flat = clip_flat(m, clip)
    mats = clip_effective(m, clip, cache)
    n0 = clip["frame0"] if clip["frame0"] else clip["n_el"]
    for k in range(min(n0, clip["n_el"])):
        inst = flat[3 * k]
        if inst >= len(clip["children"]):
            continue
        cid = clip["children"][inst]
        mm = mat_mul(matrix, mats[k])
        if cid in idx["shape"]:
            for x, y in shape_points(m, idx["shape"][cid]):
                out.append((mm[0] * x + mm[2] * y + mm[4], mm[1] * x + mm[3] * y + mm[5]))
        elif cid in idx["clip"] and depth < 8 and cid not in stack:
            clip_points(m, idx["clip"][cid], mm, depth + 1, stack + (cid,), out, cache, cap)
        elif cid in m["tf_bounds"]:
            l, t, r, b = m["tf_bounds"][cid]
            for x, y in ((l, t), (r, t), (l, b), (r, b)):
                out.append((mm[0] * x + mm[2] * y + mm[4], mm[1] * x + mm[3] * y + mm[5]))


def clip_bbox(m, ci):
    """(left, top, right, bottom) содержимого клипа на кадре 1 в координатах клипа или None."""
    pts = []
    clip_points(m, ci, IDENTITY, 0, (m["clips"][ci]["id"],), pts, {})
    if not pts:
        return None
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def fmt_bbox(bb):
    l, t, r, b = bb
    return (f"x {l:.1f}…{r:.1f} (центр {(l + r) / 2:.1f}), y {t:.1f}…{b:.1f} (центр {(t + b) / 2:.1f}), "
            f"размер {r - l:.1f}×{b - t:.1f}")


def clip_info_line(m, ci, id_to_name, with_pos=False):
    clip = m["clips"][ci]
    name = id_to_name.get(clip["id"])
    s = (f"{name} (id {clip['id']})" if name else f"id {clip['id']} (без ExportName)") \
        + f": {clip['fps']} fps, кадров {clip['nframes']}"
    if clip["fps"] and clip["nframes"] > 1:
        s += f" (~{clip['nframes'] / clip['fps']:.2f} с)"
    if with_pos:
        bb = clip_bbox(m, ci)
        s += "\n      положение (кадр 1, от точки (0,0) клипа): " + (fmt_bbox(bb) if bb else "содержимого нет")
    return s


def flow_fps():
    ensure_deps()
    src = resolve_input("Какой .sc менять", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    m = load_model(sc)
    exports, _s = get_exports(sc)
    names = [e["name"] for e in exports]
    id_to_name = {e["id"]: e["name"] for e in exports}
    print(f"  Клипов: {len(m['clips'])}, ExportName'ов: {len(names)}")
    print("  Кому менять частоту кадров:")
    print("    1 - клипу по ExportName, шаблону emoji_* или id")
    print("    2 - всем экспортным клипам файла")
    print("    3 - списком из файла: имя  fps")
    mode = ask("Кому менять", "1")
    targets = {}
    if mode == "1":
        text = ask("ExportName, шаблон со * или id клипа")
        hits = find_clips_by_pattern(m, exports, text)
        if not hits:
            near = [x for x in names if text and text.lower() in x.lower()]
            hint = ("\n  Похожие:\n    " + "\n    ".join(near[:15])) if near else ""
            raise UserError(f"Не нашёл клип «{text}».{hint}")
        print(f"  Найдено клипов: {len(hits)}")
        for ci in hits[:8]:
            print(gray("    " + clip_info_line(m, ci, id_to_name)))
        if len(hits) > 8:
            print(gray(f"    ... ещё {len(hits) - 8}"))
        if len(hits) == 1:
            cur = m["clips"][hits[0]]["fps"]
            print(f"  Сейчас у клипа {cur} fps")
            default = str(cur)
        else:
            dist = {}
            for ci in hits:
                dist[m["clips"][ci]["fps"]] = dist.get(m["clips"][ci]["fps"], 0) + 1
            print("  Сейчас: " + ", ".join(f"{f} fps × {n}" for f, n in sorted(dist.items())))
            default = ""
        raw = ask("Новая частота кадров (1-255)", default)
        try:
            fps = int(float(raw.replace(",", ".")))
        except ValueError:
            raise UserError("Частота кадров не задана или не число")
        targets = {ci: fps for ci in hits}
        summary = f"клипов: {len(hits)}, новая частота {fps} fps"
    elif mode == "2":
        ids = {e["id"] for e in exports}
        hits = [c["index"] for c in m["clips"] if c["id"] in ids]
        dist = {}
        for ci in hits:
            dist[m["clips"][ci]["fps"]] = dist.get(m["clips"][ci]["fps"], 0) + 1
        print(f"  Экспортных клипов: {len(hits)}. Сейчас: " + ", ".join(f"{f} fps × {n}" for f, n in sorted(dist.items())))
        try:
            fps = int(float(ask("Новая частота кадров (1-255)").replace(",", ".")))
        except ValueError:
            raise UserError("Частота кадров не задана или не число")
        targets = {ci: fps for ci in hits}
        summary = f"клипов: {len(hits)}, новая частота {fps} fps"
    elif mode == "3":
        rpath = unquote(ask("Файл со списком", "fps.txt"))
        if not os.path.isabs(rpath) and not os.path.dirname(rpath):
            rpath = os.path.join(IN_DIR, rpath)
        if not os.path.isfile(rpath):
            by_id = {c["id"]: c for c in m["clips"]}
            with open(rpath, "w", encoding="utf-8") as fh:
                fh.write("# имя  fps   (строки, где менять не надо, можно удалить)\n")
                for e in exports:
                    if e["id"] in by_id:
                        fh.write(f"{e['name']}  {by_id[e['id']]['fps']}\n")
            saved(rpath)
            raise UserError("Файла не было, я создал шаблон со всеми экспортами и их текущей частотой. "
                            "Отредактируй и запусти пункт снова.")
        with open(rpath, "r", encoding="utf-8-sig") as fh:
            for num, raw in enumerate(fh, 1):
                text = raw.strip()
                if not text or text.startswith("#"):
                    continue
                parts = [p for p in re.split(r"[;\t =]+", text) if p]
                if len(parts) != 2:
                    raise UserError(f"Строка {num}: нужен формат имя  fps")
                try:
                    fps = int(float(parts[1].replace(",", ".")))
                except ValueError:
                    raise UserError(f"Строка {num}: fps не число")
                hits = find_clips_by_pattern(m, exports, parts[0])
                if not hits:
                    raise UserError(f"Строка {num}: клипа «{parts[0]}» нет в файле")
                for ci in hits:
                    targets[ci] = fps
        targets = {ci: f for ci, f in targets.items() if m["clips"][ci]["fps"] != f}
        if not targets:
            raise UserError("В списке нет ни одного клипа, где частота меняется")
        summary = f"клипов по списку: {len(targets)}"
    else:
        raise UserError("Нет такого варианта")
    targets = {ci: f for ci, f in targets.items() if m["clips"][ci]["fps"] != f}
    if not targets:
        raise UserError("У всех выбранных клипов уже такая частота - менять нечего")
    for f in targets.values():
        if not 1 <= f <= 255:
            raise UserError("Частота кадров должна быть от 1 до 255")
    line()
    print(f"  {summary}")
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")
    print(gray("  Пересобираю и сжимаю..."))
    data = set_fps(sc, targets)
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    print(green("  ✔ Проверка пройдена: ") + gray("кроме частоты выбранных клипов ничего не изменилось"))
    return [out_path]


# ================================================================ правка TextField'ов (SC2)
#
# Запись TextField в SC2 - 40 байт (сверено по твоему data.msgpack, 19 полей): 0 id (u32), 4 имя шрифта
# (номер строки), 8 рамка (4 x int16: left, top, right, bottom), 16 цвет шрифта, 20 цвет обводки,
# 24 и 28 - два номера строк (текст и файл типографики), 32 флаги (u16), 34 размер шрифта (u8),
# 36 угол обводки (int32).
# Поля 24 и 28 — ссылки на строки. В старой реализации один общий слот
# выбирался эвристикой по расширению файла. Это опасно для файлов, где текст
# задан как ключ локализации (например TID_*) или где часть полей пустая.
# Теперь выбор делается с приоритетом локализационных ключей и отдельно
# сохраняется для каждой записи; fallback к старой эвристике остаётся только
# когда данных недостаточно.

TF_REC = 40
FONT_FILE_RE = re.compile(r"\.(ttf|otf|ttc|woff2?|fnt|fontbin|json|xml|atlas)$|[\\/]", re.I)
TID_RE = re.compile(r"^TID_[A-Za-z0-9_.:-]+$")

def is_localization_key(value):
    return bool(value and TID_RE.fullmatch(value.strip()))

def _tf_string(rec, strings, off):
    ref = rec["ref24"] if off == 24 else rec["ref28"]
    return strings[ref] if 0 <= ref < len(strings) else ""

def choose_text_slot(rec, strings, fallback=24):
    a, b = _tf_string(rec, strings, 24), _tf_string(rec, strings, 28)
    # Явный TID сильнее всех остальных эвристик. Это ключ текста, а не имя шрифта.
    if is_localization_key(a) and not is_localization_key(b):
        return 24, "localization_key"
    if is_localization_key(b) and not is_localization_key(a):
        return 28, "localization_key"
    # Если одна строка явно является файлом типографики, другая — текст.
    if FONT_FILE_RE.search(a) and not FONT_FILE_RE.search(b):
        return 28, "font_filename"
    if FONT_FILE_RE.search(b) and not FONT_FILE_RE.search(a):
        return 24, "font_filename"
    if a and not b:
        return 24, "only_nonempty"
    if b and not a:
        return 28, "only_nonempty"
    return fallback, "fallback"


def read_textfields(sc):
    """Разбирает TextFields и явно отмечает, какая ссылка является текстом.

    Запись остаётся совместимой со старым кодом (ref24/ref28), но дополнительно
    получает text_slot, typography_slot, text_kind и localization_key.
    """
    if sc.get("kind") == "sc1":
        raise UserError("В SC1 хаб текстовые поля не разбирает: байтовый вид их тегов не проверен")
    exports, strings = get_exports(sc)
    tfc = sc["chunks"][2]
    out = []
    if len(tfc) < 16:
        return out, strings
    tp = fb_slot(tfc, fb_root(tfc), 0)
    if tp is None:
        return out, strings
    vs, vn = fb_vec(tfc, tp)
    for i in range(vn):
        at = vs + TF_REC * i
        font_ref = u32(tfc, at + 4)
        out.append({"index": i, "pos": at, "id": u32(tfc, at),
                    "font": strings[font_ref] if font_ref < len(strings) else "",
                    "bounds": struct.unpack_from("<4h", tfc, at + 8),
                    "ref24": u32(tfc, at + 24), "ref28": u32(tfc, at + 28),
                    "size": tfc[at + 34]})
    # Определяем слот для каждого поля, а затем сводим его к наиболее вероятному
    # общему слоту для обратной совместимости со старыми функциями.
    for r in out:
        slot, kind = choose_text_slot(r, strings)
        r["text_slot"], r["typography_slot"], r["text_kind"] = slot, 28 if slot == 24 else 24, kind
        r["text_ref"] = r["ref24"] if slot == 24 else r["ref28"]
        r["typography_ref"] = r["ref28"] if slot == 24 else r["ref24"]
        value = strings[r["text_ref"]] if r["text_ref"] < len(strings) else ""
        r["localization_key"] = value if is_localization_key(value) else None
    return out, strings


def textfield_text_slot(recs, strings):
    """(общий слот, подтверждение, пояснение). Для mixed-файлов edit_textfields
    использует r['text_slot'], поэтому этот результат нужен только интерфейсу/старому коду."""
    if not recs:
        return 24, False, "TextFields пусты"
    slots = [choose_text_slot(r, strings)[0] for r in recs]
    c24, c28 = slots.count(24), slots.count(28)
    tid24 = sum(is_localization_key(_tf_string(r, strings, 24)) for r in recs)
    tid28 = sum(is_localization_key(_tf_string(r, strings, 28)) for r in recs)
    if c24 and not c28:
        return 24, True, f"слот 24 выбран для {c24} полей; TID_*: {tid24}"
    if c28 and not c24:
        return 28, True, f"слот 28 выбран для {c28} полей; TID_*: {tid28}"
    return (24 if c24 >= c28 else 28), False, (
        f"файл содержит неоднородные/неоднозначные поля: слот 24={c24}, слот 28={c28}; "
        f"TID_* в 24={tid24}, в 28={tid28}. Для каждой записи используется собственный слот."
    )


def textfield_text(rec, strings, text_off=None):
    if text_off is None:
        text_off = rec.get("text_slot", 24)
    ref = rec["ref24"] if text_off == 24 else rec["ref28"]
    return strings[ref] if ref < len(strings) else ""


def append_strings(ds, extras):
    """Дописывает строки в таблицу строк DataStorage (так же, как при переименовании: новая таблица в
    конце буфера, номера старых строк не меняются). Возвращает (новый ds, {строка: номер})."""
    t = fb_root(ds)
    p0 = fb_slot(ds, t, 0)
    s0, n = fb_vec(ds, p0)
    old_raw = []
    for i in range(n):
        tp = s0 + 4 * i + u32(ds, s0 + 4 * i)
        old_raw.append(bytes(ds[tp + 4:tp + 4 + u32(ds, tp)]))
    index_of = {}
    for i, r in enumerate(old_raw):
        index_of.setdefault(r.decode("utf-8", "replace"), i)
    all_raw = list(old_raw)
    for s in extras:
        if s not in index_of:
            index_of[s] = len(all_raw)
            all_raw.append(s.encode("utf-8"))
    if len(all_raw) == len(old_raw):
        return ds, index_of
    b = bytearray(ds)
    _align4(b)
    vec_pos = len(b)
    m = len(all_raw)
    b += struct.pack("<I", m) + bytes(4 * m)
    pos = []
    for raw in all_raw:
        pos.append(_append_string(b, raw))
    for i in range(m):
        e = vec_pos + 4 + 4 * i
        struct.pack_into("<I", b, e, pos[i] - e)
    struct.pack_into("<I", b, p0, vec_pos - p0)
    return bytes(b), index_of


def edit_textfields(sc, edits):
    """edits: {id поля: {"text": str|None, "size": int|None, "bounds": (l,t,r,b)|None}}. Возвращает байты .sc."""
    if sc.get("kind") == "sc1" or sc["version"] != 5 or len(sc["chunks"]) != 7 or sc["leftover"]:
        raise UserError("Правка TextField'ов умеет только SC2 версии 5 с 7 блоками")
    recs, strings = read_textfields(sc)
    by_id = {r["id"]: r for r in recs}
    for fid in edits:
        if fid not in by_id:
            raise UserError(f"Нет текстового поля с id {fid}")
    text_off, _confirmed, _note = textfield_text_slot(recs, strings)
    ds = sc["chunks"][0]
    extras = [e["text"] for e in edits.values() if e.get("text") is not None]
    new_ds, index_of = append_strings(ds, extras)
    tfc = bytearray(sc["chunks"][2])
    for fid, e in edits.items():
        r = by_id[fid]
        if e.get("text") is not None:
            struct.pack_into("<I", tfc, r["pos"] + r.get("text_slot", text_off), index_of[e["text"]])
        if e.get("size") is not None:
            if not 0 <= e["size"] <= 255:
                raise UserError("Размер шрифта должен быть от 0 до 255")
            tfc[r["pos"] + 34] = e["size"]
        if e.get("bounds") is not None:
            if len(e["bounds"]) != 4 or any(not -32768 <= v <= 32767 for v in e["bounds"]):
                raise UserError("Рамка: четыре целых числа от -32768 до 32767")
            struct.pack_into("<4h", tfc, r["pos"] + 8, *e["bounds"])
    chunks = list(sc["chunks"])
    chunks[0], chunks[2] = new_ds, bytes(tfc)
    data = finalize_sc(sc, chunks, sc["fd"])
    verify_textfield_edit(sc, data, edits, text_off)
    return data


def verify_textfield_edit(old, data, edits, text_off):
    new = parse_sc(data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if len(new["chunks"]) != 7 or new["leftover"]:
        fail("структура блоков нарушена")
    for i in (1, 3, 4, 5, 6):
        if new["chunks"][i] != old["chunks"][i]:
            fail(f"блок {CHUNK_NAMES[i]} изменился")
    if len(new["chunks"][0]) % 4:
        fail("длина DataStorage не кратна 4")
    ro, so = read_textfields(old)
    rn, sn = read_textfields(new)
    if len(ro) != len(rn):
        fail("число текстовых полей изменилось")
    if sn[:len(so)] != so:
        fail("старые строки в таблице изменились")
    o_ds, n_ds = old["chunks"][0], new["chunks"][0]
    p0 = fb_slot(o_ds, fb_root(o_ds), 0)
    if n_ds[:p0] != o_ds[:p0] or n_ds[p0 + 4:len(o_ds)] != o_ds[p0 + 4:]:
        fail("DataStorage изменился не только в таблице строк")
    for a, b in zip(ro, rn):
        e = edits.get(a["id"], {})
        slot = a.get("text_slot", text_off)
        want_text = e["text"] if e.get("text") is not None else textfield_text(a, so, slot)
        got_text = textfield_text(b, sn, text_off)
        want_size = e["size"] if e.get("size") is not None else a["size"]
        want_bounds = tuple(e["bounds"]) if e.get("bounds") is not None else tuple(a["bounds"])
        if (b["id"], got_text, b["size"], tuple(b["bounds"]), b["font"]) != (a["id"], want_text, want_size, want_bounds, a["font"]):
            fail(f"поле {a['id']} изменилось не так, как задумано")
    # всё остальное в записях - байт в байт
    oc, nc = old["chunks"][2], new["chunks"][2]
    mask = {}
    for a in ro:
        e = edits.get(a["id"], {})
        if e.get("text") is not None:
            mask[a["pos"] + a.get("text_slot", text_off)] = 4
        if e.get("size") is not None:
            mask[a["pos"] + 34] = 1
        if e.get("bounds") is not None:
            mask[a["pos"] + 8] = 8
    skip = set()
    for pos, ln in mask.items():
        skip.update(range(pos, pos + ln))
    if len(oc) != len(nc) or any(oc[i] != nc[i] for i in range(len(oc)) if i not in skip):
        fail("в блоке TextFields изменились посторонние байты")
    if u32(new["fd"], fb_slot(new["fd"], fb_root(new["fd"]), 8)) != 4 + len(n_ds):
        fail("смещение ресурсов в дескрипторе неверное")


def parse_bounds(text):
    parts = [p for p in re.split(r"[,\s;]+", text.strip()) if p]
    try:
        vals = tuple(int(float(p.replace(",", "."))) for p in parts)
    except ValueError:
        raise UserError(f"Не понял рамку «{text}» (нужно четыре числа: left,top,right,bottom)")
    if len(vals) != 4:
        raise UserError("Рамка - это четыре числа: left,top,right,bottom")
    return vals




# ================================================================ локализация (CSV из папки input)
#
# Формат таблиц: "TID","<ЯЗЫК>" и строка "string"; texts.csv - это EN. texts_patch.csv - таблица сразу на все языки,
# её строки перекрывают основные. Хаб читает только выбранный язык, чтобы не тратить память на 24 таблицы.

HUB_VERSION = "1.5.0"


def _csv_rows(path):
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        yield from csv.reader(fh)


def loc_scan(dirs=None):
    """Папки input (и input/localizations) -> {'ЯЗЫК': [(файл, номер колонки), ...]}; патчи идут последними."""
    dirs = dirs or [IN_DIR, os.path.join(IN_DIR, "localizations")]
    langs, files = {}, []
    for d in dirs:
        if os.path.isdir(d):
            files += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.lower().endswith(".csv")]
    files.sort(key=lambda p: ("patch" in os.path.basename(p).lower(), p))
    for p in files:
        try:
            head = next(_csv_rows(p), None)
        except (OSError, UnicodeDecodeError, Exception):
            continue
        if not head or head[0].strip().upper() != "TID" or len(head) < 2:
            continue
        for col, name in enumerate(head[1:], 1):
            if name.strip():
                langs.setdefault(name.strip().upper(), []).append((p, col))
    return langs


class Loc:
    def __init__(self, lang, table, sources):
        self.lang, self.table, self.sources = lang, table, sources

    def text(self, tid):
        return self.table.get(tid)

    def search(self, query, limit=15):
        """TID, чей текст или имя содержит запрос: сначала точное совпадение, потом начало, потом вхождение."""
        q = query.strip().lower()
        if not q:
            return []
        exact, start, inside = [], [], []
        for tid, txt in self.table.items():
            t = txt.lower()
            if t == q or tid.lower() == q:
                exact.append((tid, txt))
            elif t.startswith(q):
                start.append((tid, txt))
            elif q in t or q in tid.lower():
                inside.append((tid, txt))
            if len(exact) + len(start) + len(inside) > 4000:
                break
        return (exact + start + inside)[:limit]


def loc_load(lang, langs=None):
    langs = langs or loc_scan()
    if lang not in langs:
        raise UserError(f"Языка {lang} нет в таблицах (есть: {', '.join(sorted(langs))})")
    table, used = {}, []
    for p, col in langs[lang]:
        n = 0
        for row in _csv_rows(p):
            if len(row) > col and row[0].startswith("TID_"):
                table[row[0]] = row[col]
                n += 1
        used.append((os.path.basename(p), n))
    return Loc(lang, table, used)


def loc_ask(current=None):
    """Спрашивает язык, если в input есть таблицы. Возвращает Loc или None."""
    langs = loc_scan()
    if not langs:
        return None
    names = sorted(langs, key=lambda x: (x != "EN", x))
    print(gray(f"  Найдены таблицы локализации ({len(names)} яз.): {', '.join(names)}"))
    default = current.lang if current else ("EN" if "EN" in langs else names[0])
    ans = ask("Язык для поиска по тексту (Enter - " + default + ", минус - не использовать)", default).strip()
    if ans == "-":
        return None
    ans = ans.upper()
    if ans not in langs:
        print(red(f"  Нет такого языка: {ans}"))
        return current
    print(gray(f"  Читаю таблицы {ans}..."))
    loc = loc_load(ans, langs)
    print(green("  ✔ ") + f"{ans}: {len(loc.table)} строк" + gray(" (" + ", ".join(f"{n}: {c}" for n, c in loc.sources) + ")"))
    return loc


# ================================================================ тексты: имена экземпляров в клипах
#
# Игра берёт текст надписи не из записи TextField (там text пустой), а из ИМЕНИ экземпляра: в мувиклипе у
# ребёнка, который ссылается на TextField, лежит имя - либо ключ локализации (TID_...), либо готовая строка,
# либо служебное имя (txt, spacer), по которому код находит поле. Поэтому текст правится здесь: у
# конкретного экземпляра в конкретном клипе. Запись TextField (размер, рамка) общая для всех экземпляров.

SC1_TEXT_TAGS = (7, 15, 20, 21, 25, 33, 43, 44)


def tf_instances(sc):
    """Все экземпляры текстовых полей в клипах: [{'clip','clip_name','child','tf','name'}]."""
    if sc.get("kind") == "sc1":
        ids = set()
        for tag, body in sc["mdl"]["tags"]:
            if tag in SC1_TEXT_TAGS:
                ids.add(struct.unpack_from("<H", body, 0)[0])
        m = sc1_load_model(sc)
        exports, _ = sc1_exports(sc)
        id_to_name = {e["id"]: e["name"] for e in exports}
        out = []
        for c in m["clips"]:
            for k, inst in enumerate(c["parsed"]["inst"]):
                if inst["id"] in ids:
                    out.append({"clip": c["index"], "clip_id": c["id"], "clip_name": id_to_name.get(c["id"], ""),
                                "child": k, "tf": inst["id"], "name": sc1_text(inst["name"]),
                                "editable": True})
        return out
    recs, strings = read_textfields(sc)
    ids = {r["id"] for r in recs}
    m = load_model(sc)
    exports, _s = get_exports(sc)
    id_to_name = {e["id"]: e["name"] for e in exports}
    mcb = sc["chunks"][4]
    tabs = fb_table_vec(mcb, fb_slot(mcb, fb_root(mcb), 0))
    out = []
    for ci, (clip, tab) in enumerate(zip(m["clips"], tabs)):
        ps = dict(fb_slots(mcb, tab))
        if 6 in ps:
            vs, vn = fb_vec(mcb, ps[6])
        else:
            vs, vn = None, 0
        for k, cid in enumerate(clip["children"]):
            if cid not in ids:
                continue
            if vs is not None and k < vn:
                ref = u32(mcb, vs + 4 * k)
                name = strings[ref] if ref < len(strings) else ""
                out.append({"clip": ci, "clip_id": clip["id"], "clip_name": id_to_name.get(clip["id"], ""),
                            "child": k, "tf": cid, "name": name, "pos": vs + 4 * k, "editable": True})
            else:
                out.append({"clip": ci, "clip_id": clip["id"], "clip_name": id_to_name.get(clip["id"], ""),
                            "child": k, "tf": cid, "name": "", "editable": False})
    return out


def edit_tf_names(sc, changes):
    """changes: [(clip, child, новое имя)] (clip, child - из tf_instances). Возвращает байты .sc."""
    inst = {(i["clip"], i["child"]): i for i in tf_instances(sc)}
    for clip, child, _t in changes:
        i = inst.get((clip, child))
        if i is None:
            raise UserError("Такого экземпляра текстового поля нет")
        if not i["editable"]:
            raise UserError(f"У клипа {i['clip_name'] or i['clip_id']} нет таблицы имён, поменять имя нельзя")
    if sc.get("kind") == "sc1":
        return sc1_edit_tf_names(sc, changes, inst)
    if sc["version"] != 5 or len(sc["chunks"]) != 7 or sc["leftover"]:
        raise UserError("Правка имён умеет только SC2 версии 5 с 7 блоками")
    new_ds, index_of = append_strings(sc["chunks"][0], [t for _c, _k, t in changes])
    mcb = bytearray(sc["chunks"][4])
    for clip, child, text in changes:
        struct.pack_into("<I", mcb, inst[(clip, child)]["pos"], index_of[text])
    chunks = list(sc["chunks"])
    chunks[0], chunks[4] = new_ds, bytes(mcb)
    data = finalize_sc(sc, chunks, sc["fd"])
    new = parse_sc(data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if len(new["chunks"]) != 7 or new["leftover"]:
        fail("структура блоков нарушена")
    for k in (1, 2, 3, 5, 6):
        if new["chunks"][k] != sc["chunks"][k]:
            fail(f"блок {CHUNK_NAMES[k]} изменился")
    want = {(c, k): t for c, k, t in changes}
    after = {(i["clip"], i["child"]): i["name"] for i in tf_instances(new)}
    for key, i in inst.items():
        exp = want.get(key, i["name"])
        if after.get(key) != exp:
            fail(f"имя в клипе {i['clip_name'] or i['clip_id']} (экземпляр {i['child']}) вышло не таким")
    return data


def sc1_edit_tf_names(sc, changes, inst):
    sc1_require_writable(sc)
    m = sc1_load_model(sc)
    tags = [list(t) for t in sc["mdl"]["tags"]]
    by_clip = {}
    for clip, child, text in changes:
        by_clip.setdefault(clip, {})[child] = text
    for clip, edits in by_clip.items():
        c = dict(m["clips"][clip]["parsed"])
        c["inst"] = [dict(i) for i in c["inst"]]
        for child, text in edits.items():
            raw = text.encode("utf-8")
            if len(raw) > 254:
                raise UserError("Имя длиннее 254 байт в SC1 не поместится")
            c["inst"][child]["name"] = raw
        tags[m["clips"][clip]["pos"]][1] = sc1_build_clip(c)
    mdl = dict(sc["mdl"])
    mdl["tags"] = tags
    data = sc1_write(sc, sc1_build_stream(mdl))
    new = open_sc_bytes(data)
    if new["cont"]["md5_ok"] is not True:
        raise UserError("Проверка результата не прошла, файл не записан: MD5 в заголовке не совпал")
    for i, (a, b) in enumerate(zip(sc["mdl"]["tags"], new["mdl"]["tags"])):
        if a[0] != 49 and a != b:
            raise UserError("Проверка результата не прошла, файл не записан: изменился тег, не относящийся к клипам")
    want = {(c, k): t for c, k, t in changes}
    after = {(i["clip"], i["child"]): i["name"] for i in tf_instances(new)}
    for key, i in inst.items():
        if after.get(key) != want.get(key, i["name"]):
            raise UserError("Проверка результата не прошла, файл не записан: имя вышло не таким")
    return data


def parse_index_list(text, upto):
    """'1,3,5-7' или 'all' -> отсортированные номера (0..upto-1 показываются как 1..upto)."""
    text = text.strip().lower()
    if text in ("all", "*", "все"):
        return list(range(upto))
    out = set()
    for part in re.split(r"[,\s;]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise UserError(f"Не понял «{part}» (нужны номера через запятую, диапазон 3-7 или all)")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        for v in range(min(a, b), max(a, b) + 1):
            if not 1 <= v <= upto:
                raise UserError(f"Номер {v} вне списка (1..{upto})")
            out.add(v - 1)
    return sorted(out)


# ================================================================ слияние нескольких .sc в один
#
# Каждый файл сначала разбирается как в пункте 7 (SC1 дополнительно переводится в формат SC2: см. sc1_to_sc2_doc),
# затем документы склеиваются: объекты получают непересекающиеся id, текстуры и банки матриц дописываются
# подряд, экспорты с повторяющимися именами получают суффикс _2, _3... Результат - один SC2 с сырыми RGBA-текстурами.


def load_source_doc(path, tmp_dir):
    """Файл -> (документ в виде SC2, предупреждения, 'sc1'/'sc2'). Текстуры сохраняются PNG в tmp_dir."""
    rep = decode_file(path, tmp_dir)
    doc, _p = load_doc_dir(tmp_dir)
    warns = []
    if rep["kind"] == "sc1":
        with open(path, "rb") as fh:
            cont = sc1_container(fh.read())
        doc, warns = sc1_to_sc2_doc(cont, doc)
    elif not doc.get("is_sc2", True):
        raise UserError("Неожиданный формат документа")
    for i, t in enumerate(doc["textures"]):
        f = os.path.join(tmp_dir, t.get("file") or f"texture_{i}.png")
        if not os.path.isfile(f):
            raise UserError(f"{os.path.basename(path)}: текстура {i} не сохранилась в PNG (формат не поддержан?)")
    return doc, warns, rep["kind"]


def merge_docs(entries):
    """entries: [{'name','doc','dir'}] -> (документ, [(путь к PNG, новый номер)], отчёт)."""
    import copy
    merged = {"is_sc2": True, "version": 5, "exports": [], "matrix_banks": [], "textures": [], "shapes": [],
              "movieclips": [], "textfields": [], "movieclip_modifiers": []}
    for k in SC2_DOC_FLAGS:
        merged[k] = False
    used, names, tex_files = set(), set(), []
    rep = {"renamed": [], "remapped": {}, "files": []}
    cursor = [0]

    def alloc():
        while cursor[0] in used:
            cursor[0] += 1
        if cursor[0] > 65535:
            raise UserError("Не хватает id: в одном .sc их не может быть больше 65536")
        return cursor[0]

    for e in entries:
        d = copy.deepcopy(e["doc"])
        ids = {x["id"] for sec in ("shapes", "movieclips", "textfields", "movieclip_modifiers") for x in d[sec]}
        idmap, moved = {}, 0
        for oid in sorted(ids):
            if oid in used:
                nid = alloc()
                moved += 1
            else:
                nid = oid
            used.add(nid)
            idmap[oid] = nid
        bank_off, tex_off = len(merged["matrix_banks"]), len(merged["textures"])
        for sh in d["shapes"]:
            sh["id"] = idmap[sh["id"]]
            for cmd in sh["commands"]:
                cmd["texture_index"] += tex_off
        for c in d["movieclips"]:
            c["id"] = idmap[c["id"]]
            c["bank_index"] += bank_off
            for ch in c["childrens"]:
                ch["id"] = idmap.get(ch["id"], ch["id"])
        for t in d["textfields"] + d["movieclip_modifiers"]:
            t["id"] = idmap[t["id"]]
        for ex in d["exports"]:
            ex["id"] = idmap[ex["id"]]
            base, name, n = ex["name"], ex["name"], 1
            while name in names:
                n += 1
                name = f"{base}_{n}"
            if name != base:
                rep["renamed"].append((e["name"], base, name))
            ex["name"] = name
            names.add(name)
        for i, t in enumerate(d["textures"]):
            nt = dict(t)
            new_i = tex_off + i
            nt.update({"index": new_i, "file": f"texture_{new_i}.png", "encoding": 2, "khronos_format": 0,
                       "pixel_format": 0})
            nt.pop("tex_flags_raw", None)
            merged["textures"].append(nt)
            tex_files.append((os.path.join(e["dir"], t.get("file") or f"texture_{i}.png"), new_i))
        for sec in ("exports", "matrix_banks", "shapes", "movieclips", "textfields", "movieclip_modifiers"):
            merged[sec].extend(d[sec])
        rep["remapped"][e["name"]] = moved
        rep["files"].append((e["name"], len(d["exports"]), len(d["shapes"]), len(d["movieclips"]),
                             len(d["textures"])))
        e["_idmap"], e["_bank_off"], e["_tex_off"] = idmap, bank_off, tex_off
    return merged, tex_files, rep


def merge_verify(entries, merged):
    """Независимая проверка: объекты каждого исходника в слитом документе совпадают с исходными с точностью до id."""
    clips = {c["id"]: c for c in merged["movieclips"]}
    shapes = {x["id"]: x for x in merged["shapes"]}
    tfs = {x["id"]: x for x in merged["textfields"]}
    for e in entries:
        m, bo, to = e["_idmap"], e["_bank_off"], e["_tex_off"]
        for c in e["doc"]["movieclips"]:
            g = clips[m[c["id"]]]
            if (g["frame_rate"], g["frames"], g["frame_elements"], g.get("scaling_grid")) != \
                    (c["frame_rate"], c["frames"], c["frame_elements"], c.get("scaling_grid")):
                raise UserError(f"Проверка слияния: клип {c['id']} из {e['name']} изменился")
            if g["bank_index"] != c["bank_index"] + bo or [ch["id"] for ch in g["childrens"]] != \
                    [m.get(ch["id"], ch["id"]) for ch in c["childrens"]]:
                raise UserError(f"Проверка слияния: ссылки клипа {c['id']} из {e['name']} не сходятся")
            if c["frame_elements"] and merged["matrix_banks"][g["bank_index"]] != e["doc"]["matrix_banks"][c["bank_index"]]:
                raise UserError(f"Проверка слияния: банк матриц клипа {c['id']} из {e['name']} не совпал")
        for sh in e["doc"]["shapes"]:
            g = shapes[m[sh["id"]]]
            if [(c["type"], c["texture_index"] + to, c["vertices"]) for c in sh["commands"]] != \
                    [(c["type"], c["texture_index"], c["vertices"]) for c in g["commands"]]:
                raise UserError(f"Проверка слияния: шейп {sh['id']} из {e['name']} изменился")
        for t in e["doc"]["textfields"]:
            if tfs[m[t["id"]]] != dict(t, id=m[t["id"]]):
                raise UserError(f"Проверка слияния: текстовое поле {t['id']} из {e['name']} изменилось")


def sc_files_in(folder):
    out = []
    for n in sorted(os.listdir(folder)):
        low = n.lower()
        if low.endswith(".sc") and not low.endswith("_tex.sc"):
            out.append(os.path.join(folder, n))
    return out


def ask_astc():
    return ask_yes_no("Сжать текстуры в ASTC (файл станет в разы меньше, но с потерями качества, кодирование долгое)", False)


def write_merged_sc(entries, out_path, work, astc=False):
    """Склеивает документы, проверяет, собирает SC2 и пишет файл. Возвращает (документ, отчёт, предупреждения)."""
    import shutil
    merged, tex_files, rep = merge_docs(entries)
    merge_verify(entries, merged)
    mdir = os.path.join(work, "merged_out")
    shutil.rmtree(mdir, ignore_errors=True)
    os.makedirs(mdir)
    for src, i in tex_files:
        shutil.copyfile(src, os.path.join(mdir, f"texture_{i}.png"))
    main, _tex, warns = encode_doc(merged, mdir, astc)
    with open(out_path, "wb") as fh:
        fh.write(main)
    return merged, rep, warns


def print_merge_report(merged, rep, warns, skipped=()):
    line()
    print(f"  Итого: экспортов {len(merged['exports'])}, шейпов {len(merged['shapes'])}, клипов "
          f"{len(merged['movieclips'])}, текстовых полей {len(merged['textfields'])}, текстур {len(merged['textures'])}")
    moved = {k: v for k, v in rep["remapped"].items() if v}
    if moved:
        print(gray("  Пересечения id разрешены перенумерацией: " + ", ".join(f"{k} ({v})" for k, v in moved.items())))
    if rep["renamed"]:
        print(yellow(f"  ⚠ Повторяющиеся имена экспортов переименованы ({len(rep['renamed'])}):"))
        for src, a, b in rep["renamed"][:12]:
            print(f"      {src}: {a} → {b}")
        if len(rep["renamed"]) > 12:
            print(gray(f"      ... и ещё {len(rep['renamed']) - 12}"))
    for w in warns:
        print(yellow("  ⚠ " + w))
    if skipped:
        print(yellow("  ⚠ Не вошли: " + ", ".join(n for n, _ in skipped)))
    print(gray("  Все текстуры записаны сырым RGBA; чтобы уменьшить размер, можно перестроить атлас (пункт 9)."))


def flow_merge():
    import shutil
    import tempfile
    ensure_deps()
    folder = resolve_input("Папка с .sc для объединения (или один .sc - тогда он просто переведётся в один SC2)",
                           "input", [("SC", "*.sc"), ("Все файлы", "*.*")], allow_dir=True)
    if os.path.isdir(folder):
        files = sc_files_in(folder)
        if not files:
            raise UserError("В папке нет .sc-файлов")
    else:
        files = [folder]
    single = len(files) == 1
    if single:
        print(gray("  Один файл: пакет (.sc + _tex.sc + .sctx/.zktx) будет переведён в один самодостаточный SC2."))
    else:
        print(f"  Найдено .sc: {len(files)}. Разбираю каждый (большие пакеты - до пары минут)...")
    work = tempfile.mkdtemp(prefix="schub_merge_")
    entries, skipped = [], []
    try:
        for k, p in enumerate(files):
            nm = os.path.basename(p)
            sub = os.path.join(work, f"src{k}")
            os.makedirs(sub)
            try:
                doc, warns, kind = load_source_doc(p, sub)
            except UserError as e:
                if single:
                    raise
                skipped.append((nm, str(e)))
                print(red(f"  ✖ {nm}: ") + str(e).splitlines()[0])
                continue
            entries.append({"name": nm, "doc": doc, "dir": sub, "kind": kind, "warns": warns})
            print(green("  ✔ ") + f"{nm}: {kind.upper()}, экспортов {len(doc['exports'])}, шейпов {len(doc['shapes'])}, "
                  f"клипов {len(doc['movieclips'])}, текстур {len(doc['textures'])}")
            for w in warns[:2]:
                print(yellow("      ⚠ " + w))
        if not single:
            if len(entries) < 2:
                raise UserError("После пропуска ошибочных файлов осталось меньше двух - объединять нечего")
            if skipped and not ask_yes_no(f"Пропущено файлов: {len(skipped)}. Объединить остальные", True):
                raise UserError("Отменено")
        default = (os.path.splitext(os.path.basename(files[0]))[0] + "_single.sc") if single else "merged.sc"
        out_path = resolve_output(ask("Выходной файл", default), ".sc")
        if any(os.path.abspath(out_path) == os.path.abspath(p) for p in files):
            raise UserError("Выходной файл совпадает с одним из входных - укажи другое имя")
        astc = ask_astc()
        print(gray("  Собираю итоговый SC2 и проверяю результат (для больших текстур это долго)..."))
        merged, rep, warns = write_merged_sc(entries, out_path, work, astc)
        saved(out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print_merge_report(merged, rep, warns, skipped)
    return [out_path]


# ---- перенос экспортов между файлами и разделение .sc


def doc_subset(doc, names):
    """Часть документа: выбранные экспорты и всё, от чего они зависят. Банки обрезаются до используемых матриц,
    текстуры - до используемых (PNG целиком, без вырезания)."""
    import copy
    ex = {e["name"]: e for e in doc["exports"]}
    miss = [n for n in names if n not in ex]
    if miss:
        raise UserError("Нет экспортов: " + ", ".join(miss))
    clips = {c["id"]: c for c in doc["movieclips"]}
    need, stack = set(), [ex[n]["id"] for n in names]
    while stack:
        oid = stack.pop()
        if oid in need:
            continue
        need.add(oid)
        if oid in clips:
            stack.extend(ch["id"] for ch in clips[oid]["childrens"])
    shapes = [copy.deepcopy(x) for x in doc["shapes"] if x["id"] in need]
    cl = [copy.deepcopy(x) for x in doc["movieclips"] if x["id"] in need]
    tfs = [copy.deepcopy(x) for x in doc["textfields"] if x["id"] in need]
    mods = [copy.deepcopy(x) for x in doc["movieclip_modifiers"] if x["id"] in need]
    tex_used = sorted({c["texture_index"] for sh in shapes for c in sh["commands"]})
    tmap = {o: n for n, o in enumerate(tex_used)}
    for sh in shapes:
        for c in sh["commands"]:
            c["texture_index"] = tmap[c["texture_index"]]
    textures = []
    for n, o in enumerate(tex_used):
        t = copy.deepcopy(doc["textures"][o])
        t["file"] = t.get("file") or f"texture_{o}.png"
        t["index"] = n
        textures.append(t)
    used = {}
    for c in cl:
        for fe in c["frame_elements"]:
            m, k = used.setdefault(c["bank_index"], (set(), set()))
            if fe["matrix_index"] != 0xFFFF:
                m.add(fe["matrix_index"])
            if fe["colorTransform_index"] != 0xFFFF:
                k.add(fe["colorTransform_index"])
    banks, bmap, mmap, kmap = [], {}, {}, {}
    for b in sorted(used):
        ms, ks = sorted(used[b][0]), sorted(used[b][1])
        src = doc["matrix_banks"][b]
        banks.append({"matrices": [src["matrices"][i] for i in ms],
                      "color_transforms": [src["color_transforms"][i] for i in ks]})
        bmap[b] = len(banks) - 1
        mmap[b] = {o: n for n, o in enumerate(ms)}
        kmap[b] = {o: n for n, o in enumerate(ks)}
    if not banks:
        banks = [{"matrices": [], "color_transforms": []}]
    for c in cl:
        b = c["bank_index"]
        for fe in c["frame_elements"]:
            if fe["matrix_index"] != 0xFFFF:
                fe["matrix_index"] = mmap[b][fe["matrix_index"]]
            if fe["colorTransform_index"] != 0xFFFF:
                fe["colorTransform_index"] = kmap[b][fe["colorTransform_index"]]
        c["bank_index"] = bmap.get(b, 0)
    out = {k: v for k, v in doc.items() if k not in ("exports", "shapes", "movieclips", "textfields",
                                                      "movieclip_modifiers", "textures", "matrix_banks")}
    out.update({"exports": [copy.deepcopy(ex[n]) for n in names], "shapes": shapes, "movieclips": cl,
                "textfields": tfs, "movieclip_modifiers": mods, "textures": textures, "matrix_banks": banks})
    return out


def pick_exports(exports, title="Какие экспорты"):
    """Интерактивный выбор: поиск по подстроке, номера, диапазоны, all. Возвращает список имён в порядке выбора."""
    chosen = []
    print(gray(f"  Всего экспортов: {len(exports)}"))
    while True:
        q = ask("Что искать в именах (Enter - закончить выбор, * - показать все)", "").strip()
        if not q:
            break
        hits = [k for k, e in enumerate(exports) if q == "*" or q.lower() in e["name"].lower()]
        print(f"  Найдено: {len(hits)}" + (" (показаны первые 50)" if len(hits) > 50 else ""))
        for k in hits[:50]:
            print(f"    {k + 1:>4}. {exports[k]['name']}" + ("  ✔" if exports[k]["name"] in chosen else ""))
        if not hits:
            continue
        pick = ask("Какие взять (номера 1,3,5-7 или all; Enter - искать заново)", "").strip()
        if not pick:
            continue
        try:
            idx = [hits[k] for k in parse_index_list(pick, len(hits))] if pick.lower() in ("all", "*", "все") \
                else parse_index_list(pick, len(exports))
        except UserError as e:
            print(red("  " + str(e)))
            continue
        for k in idx:
            if exports[k]["name"] not in chosen:
                chosen.append(exports[k]["name"])
        print(green(f"  ✔ Выбрано всего: {len(chosen)}"))
    if not chosen:
        raise UserError("Ничего не выбрано")
    return chosen


def flow_export_transfer():
    import shutil
    import tempfile
    ensure_deps()
    print("  Что сделать:")
    print("    1 - вынести выбранные экспорты из файла в новый .sc")
    print("    2 - добавить выбранные экспорты из файла A в файл B (получится новый файл, B не меняется)")
    mode = ask("Режим", "1").strip()
    if mode not in ("1", "2"):
        raise UserError("Нужно 1 или 2")
    srcA = resolve_input("Файл, откуда брать экспорты (A)", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    srcB = None
    if mode == "2":
        srcB = resolve_input("Файл, куда добавить (B)", "target.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    work = tempfile.mkdtemp(prefix="schub_xfer_")
    try:
        dirA = os.path.join(work, "A")
        os.makedirs(dirA)
        docA, warnsA, kindA = load_source_doc(srcA, dirA)
        for w in warnsA[:2]:
            print(yellow("  ⚠ " + w))
        names = pick_exports(docA["exports"])
        sub = doc_subset(docA, names)
        print(gray(f"  В выборке: экспортов {len(sub['exports'])}, шейпов {len(sub['shapes'])}, клипов "
                   f"{len(sub['movieclips'])}, текстур {len(sub['textures'])} (из {len(docA['textures'])})"))
        entries = [{"name": os.path.basename(srcA), "doc": sub, "dir": dirA, "kind": kindA, "warns": []}]
        if mode == "2":
            dirB = os.path.join(work, "B")
            os.makedirs(dirB)
            docB, warnsB, kindB = load_source_doc(srcB, dirB)
            entries.insert(0, {"name": os.path.basename(srcB), "doc": docB, "dir": dirB, "kind": kindB, "warns": []})
        default = "extracted.sc" if mode == "1" else os.path.splitext(os.path.basename(srcB))[0] + "_plus.sc"
        out_path = resolve_output(ask("Выходной файл", default), ".sc")
        if any(os.path.abspath(out_path) == os.path.abspath(p) for p in (srcA, srcB) if p):
            raise UserError("Выходной файл совпадает с входным - укажи другое имя")
        astc = ask_astc()
        print(gray("  Собираю итоговый SC2 и проверяю результат..."))
        merged, rep, warns = write_merged_sc(entries, out_path, work, astc)
        saved(out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print_merge_report(merged, rep, warns)
    return [out_path]


# ---- таблица текстов (CSV): выгрузить для правки в Excel/таблицах и применить обратно

TEXT_TABLE_COLS = ["clip_id", "clip_name", "child", "tf_id", "name", "localized", "new_name"]
EMPTY_MARK = "<empty>"


def export_text_table(sc, path, loc=None):
    import csv
    rows = [i for i in tf_instances(sc) if i["editable"]]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(TEXT_TABLE_COLS[:5] + [f"localized_{loc.lang}" if loc else "localized", "new_name"])
        for i in rows:
            txt = loc.text(i["name"]) if loc and i["name"].startswith("TID_") else None
            w.writerow([i["clip_id"], i["clip_name"], i["child"], i["tf"], i["name"], txt or "", ""])
    return len(rows)


def read_text_table(path):
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    head = text.split("\n", 1)[0]
    delim = max(";,\t", key=head.count)
    rd = csv.DictReader(text.splitlines(), delimiter=delim)
    need = {"clip_id", "child", "tf_id", "name", "new_name"}
    if not rd.fieldnames or not need <= set(rd.fieldnames):
        raise UserError("В CSV нет нужных колонок (clip_id, child, tf_id, name, new_name) - это не таблица, "
                        "выгруженная хабом")
    return list(rd)


def apply_text_table(sc, rows):
    """Строки таблицы -> (изменения для edit_tf_names, список замечаний). Строка меняется, если new_name не пуст
    и отличается от текущего; перед этим сверяются клип, экземпляр, поле и старое имя - чтобы таблица от другого
    файла не испортила этот."""
    inst = {(i["clip_id"], i["child"]): i for i in tf_instances(sc)}
    changes, problems = [], []
    for n, r in enumerate(rows, 2):
        new = (r.get("new_name") or "").strip("\r\n")
        if new == "":
            continue
        try:
            key = (int(r["clip_id"]), int(r["child"]))
        except (TypeError, ValueError):
            problems.append(f"строка {n}: clip_id/child не числа")
            continue
        i = inst.get(key)
        if i is None:
            problems.append(f"строка {n}: в файле нет экземпляра клип {key[0]}, номер {key[1]}")
            continue
        if str(i["tf"]) != str(r["tf_id"]).strip() or i["name"] != (r.get("name") or ""):
            problems.append(f"строка {n}: поле/имя в файле другие («{i['name']}», поле {i['tf']}) - таблица от другого файла?")
            continue
        if new == EMPTY_MARK:
            new = ""
        if new != i["name"]:
            changes.append((i["clip"], i["child"], new))
    return changes, problems


def flow_text_table():
    ensure_deps()
    print("  1 - выгрузить тексты в CSV (для правки в таблице)")
    print("  2 - применить CSV обратно к .sc (результат - новый файл)")
    mode = ask("Режим", "1").strip()
    if mode not in ("1", "2"):
        raise UserError("Нужно 1 или 2")
    src = resolve_input("Какой .sc", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    if mode == "1":
        loc = loc_ask()
        default = "texts_" + os.path.splitext(os.path.basename(src))[0] + ".csv"
        out = resolve_output(ask("Куда сохранить CSV", default), ".csv")
        n = export_text_table(sc, out, loc)
        saved(out)
        print(gray(f"  Строк: {n}. Заполни колонку new_name там, где нужно поменять имя/текст; "
                   f"{EMPTY_MARK} в ней сделает имя пустым. Остальные колонки не трогай."))
        return [out]
    tbl = resolve_input("CSV с правками", "text_table.csv", [("CSV", "*.csv"), ("Все файлы", "*.*")])
    changes, problems = apply_text_table(sc, read_text_table(tbl))
    for p in problems[:10]:
        print(yellow("  ⚠ " + p))
    if len(problems) > 10:
        print(gray(f"  ... и ещё замечаний: {len(problems) - 10}"))
    if not changes:
        raise UserError("Нечего менять: в колонке new_name нет подходящих правок")
    print(f"  К применению: {len(changes)}")
    out = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя")
    data = edit_tf_names(sc, changes)
    with open(out, "wb") as fh:
        fh.write(data)
    saved(out)
    print(green("  ✔ Проверка пройдена: ") + gray("изменены только указанные экземпляры"))
    return [out]


# ---- диагностика: отчёт о файле для баг-репорта (только метаданные, содержимое файлов не включается)


def sctx_header_info(path):
    with open(path, "rb") as fh:
        data = fh.read()
    n = u32(data, 0)
    root = data[4:4 + n]
    if root[4:8] != b"SCTX":
        return "нет сигнатуры SCTX"
    sl = dict(fb_slots(root, fb_root(root)))
    g = lambda k, f: f(root, sl[k]) if k in sl else None
    mlen = u32(data, 4 + n)
    return (f"SCTX: тип пикселей {g(1, u32)}, {g(2, u16)}x{g(3, u16)}, mip-уровней {root[sl[4]] if 4 in sl else 1}, "
            f"флаги {g(6, u32) if 6 in sl else 0}, данных {g(7, u32)} байт, блок mip {mlen} байт, файл {len(data)} байт")


def zktx_header_info(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    ktx = _zstd_all(raw, os.path.basename(path))
    ki = ktx_info(ktx)
    if not ki:
        return f"zstd {len(raw)} -> {len(ktx)} байт, KTX1 не найден"
    gl, w, h, at, size = ki
    return f"KTX1: формат 0x{gl:X}, {w}x{h}, данных {size} байт (zstd {len(raw)} -> {len(ktx)})"


def build_report(path):
    import hashlib
    import platform
    import sys
    L = []

    def sec(t):
        L.append("")
        L.append(f"== {t} ==")

    def safe(title, fn):
        try:
            fn()
        except Exception as e:                              # отчёт не должен падать
            L.append(f"  [{title}: ошибка {type(e).__name__}: {e}]")

    sec("SC Hub")
    L.append(f"  версия {HUB_VERSION}; Python {sys.version.split()[0]}; {platform.system()} {platform.release()} {platform.machine()}")
    pk = []
    for mod in ("PIL", "zstandard", "msgpack", "numpy", "texture2ddecoder", "flatbuffers"):
        try:
            m = __import__(mod)
            pk.append(f"{mod} {getattr(m, '__version__', '?')}")
        except ImportError:
            pk.append(f"{mod} нет")
    L.append("  пакеты: " + ", ".join(pk))
    sec("Файл")
    with open(path, "rb") as fh:
        data = fh.read()
    L.append(f"  имя {os.path.basename(path)}, размер {len(data)} байт, sha256 {hashlib.sha256(data).hexdigest()[:16]}")
    L.append(f"  первые байты: {data[:16].hex(' ')}")
    kind = None

    def fmt():
        nonlocal kind
        kind = sniff_format(data)
        L.append(f"  формат: {kind.upper()}")
    safe("формат", fmt)

    def sc1_part():
        cont = sc1_container(data)
        L.append(f"  контейнер: версия {cont['ver']} (внутри {cont['real']}), сжатие {cont['comp']}, "
                 f"MD5 {'сошёлся' if cont['md5_ok'] else 'НЕ сошёлся'}, поток {len(cont['raw'])} байт, хвост {len(cont['tail'])}")
        m = sc1_parse_stream(cont["raw"])
        import collections
        hist = collections.Counter(t for t, _b in m["tags"])
        L.append("  счётчики шапки (шейпы, клипы, текстуры, текстовые поля, матрицы, цвета): " + str(m["counts"]))
        L.append("  теги (номер: сколько): " + ", ".join(f"{t}: {n}" for t, n in sorted(hist.items())))
        heads = [b for t, b in m["tags"] if t in SC1_TEXTURE_TAGS]
        L.append("  текстуры в основном файле: " + (", ".join(f"{struct.unpack_from('<2H', b, 1)[0]}x{struct.unpack_from('<2H', b, 1)[1]}"
                                                         f"{' (пиксели внутри)' if len(b) > 5 else ' (без пикселей)'}" for b in heads) or "нет"))
        safe("запись", lambda: (sc1_require_writable(open_sc(path)), L.append("  хаб может переписать файл: да (круговая сборка побайтно)")))
        sizes = [struct.unpack_from("<2H", b, 1) for b in heads if len(b) <= 5]
        cands = tex_sc_candidates(path)
        L.append("  файлы _tex.sc рядом: " + (", ".join(os.path.basename(c) for c in cands) or "нет"))
        pick = pick_tex_file(path, [tuple(x) for x in sizes])
        if pick:
            L.append(f"  выбран: {os.path.basename(pick)}")
            tc = sc1_container(open(pick, "rb").read())
            thist = collections.Counter(t for t, _b in sc1_parse_stream_tags(tc["raw"]))
            L.append("  теги внутри него: " + ", ".join(f"{t}: {n}" for t, n in sorted(thist.items())))
            for r in sc1_ext_refs(tc["raw"]):
                p = find_ext_file(r["name"], pick, path)
                L.append(f"  ссылка на {r['name']} ({r['w']}x{r['h']}): " + ("файл найден" if p else "ФАЙЛА НЕТ рядом"))
                if p:
                    safe(r["name"], lambda p=p: L.append("    " + (sctx_header_info(p) if p.lower().endswith(".sctx") else zktx_header_info(p))))
        elif sizes:
            L.append("  ВНИМАНИЕ: у текстур нет пикселей, а _tex.sc рядом не найден")

    def sc2_part():
        sc = parse_sc(data)
        L.append(f"  версия {sc['version']}, блоков {len(sc['chunks'])}: {[len(c) for c in sc['chunks']]}, остаток {sc['leftover']}")
        ex, _s = get_exports(sc)
        L.append(f"  экспортов: {len(ex)}")
        for k, t in enumerate(sc2_tex_infos(sc)):
            L.append(f"  текстура {k}: {t['w']}x{t['h']}, формат {t.get('fmt')}, флаги {t.get('flags')}")
    if kind == "sc1":
        safe("SC1", sc1_part)
    elif kind == "sc2":
        safe("SC2", sc2_part)

    def texts():
        sc = open_sc(path)
        ins = tf_instances(sc)
        tid = sum(1 for i in ins if i["name"].startswith("TID_"))
        L.append(f"  экземпляров текстовых полей: {len(ins)} (TID_: {tid}, пустых: {sum(1 for i in ins if not i['name'])}, "
                 f"нельзя править: {sum(1 for i in ins if not i['editable'])})")
    sec("Тексты")
    safe("тексты", texts)
    sec("Локализация")
    langs = loc_scan()
    L.append("  таблицы в input: " + (", ".join(sorted(langs)) if langs else "нет"))
    L.append("")
    L.append("В отчёте только метаданные и имена файлов; содержимое файлов не включено.")
    return "\n".join(L)


def flow_diagnostics():
    path = resolve_input("Какой файл проверить", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    rep = build_report(path)
    print(rep)
    out = resolve_output("diagnostics_" + os.path.splitext(os.path.basename(path))[0], ".txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(rep + "\n")
    saved(out)
    print(gray("  Этот текст можно приложить к сообщению об ошибке."))
    return [out]


# ---- очистка и оптимизация: убрать недостижимое, слить одинаковое, проверить, что каждый экспорт выглядит так же


def doc_signature(doc, name, thash=None):
    """Отпечаток внешнего вида экспорта: клипы, кадры, матрицы и цвета, вершины шейпов и файлы текстур, без id."""
    import hashlib
    ex = next((e for e in doc["exports"] if e["name"] == name), None)
    if ex is None:
        raise UserError(f"Нет экспорта {name}")
    clips = {c["id"]: c for c in doc["movieclips"]}
    shapes = {x["id"]: x for x in doc["shapes"]}
    tfs = {x["id"]: x for x in doc["textfields"]}
    mods = {x["id"]: x for x in doc["movieclip_modifiers"]}
    memo = {}

    def tup(v):
        return tuple(sorted(v.items())) if isinstance(v, dict) else tuple(v)

    def h(obj):
        return hashlib.sha1(repr(obj).encode("utf-8")).hexdigest()[:20]

    def sig(oid):
        if oid in memo:
            return memo[oid]
        memo[oid] = "cycle"
        if oid in shapes:
            r = h(("S", [(c["type"], (thash or {}).get(doc["textures"][c["texture_index"]].get("file"), doc["textures"][c["texture_index"]].get("file")),
                          [(v["x"], v["y"], v["u"], v["v"]) for v in c["vertices"]]) for c in shapes[oid]["commands"]]))
        elif oid in clips:
            c = clips[oid]
            bank = doc["matrix_banks"][c["bank_index"]] if c["frame_elements"] else None
            els = []
            for e in c["frame_elements"]:
                mi, ci = e["matrix_index"], e["colorTransform_index"]
                els.append((e["instance_index"], tup(bank["matrices"][mi]) if mi != 0xFFFF else None,
                            tup(bank["color_transforms"][ci]) if ci != 0xFFFF else None))
            r = h(("C", c["frame_rate"], [(f["label"], f["elements_count"]) for f in c["frames"]],
                   [(ch["blend_mode"], ch["name"], sig(ch["id"])) for ch in c["childrens"]], els,
                   c.get("scaling_grid"), c.get("unknown_flag")))
        elif oid in tfs:
            r = h(("T", sorted((k, str(v)) for k, v in tfs[oid].items() if k != "id")))
        elif oid in mods:
            r = h(("M", mods[oid].get("type")))
        else:
            r = "missing"
        memo[oid] = r
        return r
    return sig(ex["id"])


def doc_dedupe(doc):
    """Сливает одинаковые матрицы и цвета в банках и одинаковые шейпы. Меняет doc, возвращает статистику."""
    st = {"matrices": 0, "colors": 0, "shapes": 0}
    for b, bank in enumerate(doc["matrix_banks"]):
        def uniq(lst):
            seen, mp, out = {}, [], []
            for item in lst:
                key = repr(item)
                if key not in seen:
                    seen[key] = len(out)
                    out.append(item)
                mp.append(seen[key])
            return out, mp
        bank["matrices"], mm = uniq(bank["matrices"])
        bank["color_transforms"], cm = uniq(bank["color_transforms"])
        for c in doc["movieclips"]:
            if c["bank_index"] != b:
                continue
            for e in c["frame_elements"]:
                if e["matrix_index"] != 0xFFFF:
                    e["matrix_index"] = mm[e["matrix_index"]]
                if e["colorTransform_index"] != 0xFFFF:
                    e["colorTransform_index"] = cm[e["colorTransform_index"]]
    seen, canon = {}, {}
    for sh in doc["shapes"]:
        key = repr([(c["type"], c["texture_index"], [(v["x"], v["y"], v["u"], v["v"]) for v in c["vertices"]])
                    for c in sh["commands"]])
        if key in seen:
            canon[sh["id"]] = seen[key]
        else:
            seen[key] = sh["id"]
    if canon:
        doc["shapes"] = [x for x in doc["shapes"] if x["id"] not in canon]
        for c in doc["movieclips"]:
            for ch in c["childrens"]:
                ch["id"] = canon.get(ch["id"], ch["id"])
        for e in doc["exports"]:
            e["id"] = canon.get(e["id"], e["id"])
    st["shapes"] = len(canon)
    return st


def tex_hashes(doc, folder):
    import hashlib
    out = {}
    for i, t in enumerate(doc["textures"]):
        f = t.get("file") or f"texture_{i}.png"
        p = os.path.join(folder, f)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                out[f] = hashlib.sha1(fh.read()).hexdigest()
    return out


def doc_optimize(doc, folder):
    """(новый документ, статистика). Бросает UserError, если внешний вид какого-то экспорта изменился."""
    names = [e["name"] for e in doc["exports"]]
    th = tex_hashes(doc, folder)
    before = {n: doc_signature(doc, n, th) for n in names}
    out = doc_subset(doc, names)
    mats = lambda d: sum(len(b["matrices"]) for b in d["matrix_banks"])
    cols = lambda d: sum(len(b["color_transforms"]) for b in d["matrix_banks"])
    st = {"shapes": [len(doc["shapes"]), 0], "clips": [len(doc["movieclips"]), 0], "tfs": [len(doc["textfields"]), 0],
          "textures": [len(doc["textures"]), 0], "matrices": [mats(doc), 0], "colors": [cols(doc), 0]}
    seen, canon = {}, {}
    for i, t in enumerate(out["textures"]):                # одинаковые картинки с одинаковыми параметрами
        key = (th.get(t["file"], t["file"]), repr({k: v for k, v in t.items() if k not in ("index", "file")}))
        canon[i] = seen.setdefault(key, i)
    if any(k != v for k, v in canon.items()):
        for sh in out["shapes"]:
            for c in sh["commands"]:
                c["texture_index"] = canon[c["texture_index"]]
    doc_dedupe(out)
    out = doc_subset(out, names)                             # убрать то, что стало ненужным после слияния
    for k, v in (("shapes", out["shapes"]), ("clips", out["movieclips"]), ("tfs", out["textfields"]),
                 ("textures", out["textures"])):
        st[k][1] = len(v)
    st["matrices"][1], st["colors"][1] = mats(out), cols(out)
    bad = [n for n in names if doc_signature(out, n, th) != before[n]]
    if bad:
        raise UserError("Проверка оптимизации не прошла: изменился внешний вид экспортов "
                        + ", ".join(bad[:6]) + ". Файл не записан.")
    return out, {k: tuple(v) for k, v in st.items()}


# ================================================================ создание анимированных фонов меню
#
# Папка с картинками: gradient.png (низ фона), symbol.png (белый символ с прозрачностью), top.png (верхний слой с
# прозрачной частью, лежит поверх всего), flash.png (необязательно, свечение между градиентом и символами).
# Стадии: 1 - статичный фон; 2 - градиент + движущиеся символы; 3 - градиент + символы + верхняя часть.
# Система символов снята с образца background_sonic: поле повёрнуто целиком на ~10°, шахматная решётка с шагом 144,
# движение вдоль наклона на шаг решётки за цикл (скорость ~14 единиц/с).
# Устройство как в образце theme_bg: клип-экспорт (1 кадр) -> клип-сцена (1 кадр) -> слои; символы лежат в
# неподвижном клипе-поле, а клип-"двигатель" сдвигает поле на одну клетку решётки вправо-вверх (45°) за цикл.
# Внешний вид символов зависит только от i+j (номер диагонали), поэтому после сдвига на клетку картинка
# совпадает с начальной - цикл бесшовный.

BG_FILES = {"gradient": "gradient.png", "symbol": "symbol.png", "top": "top.png", "topgif": "top.gif", "flash": "flash.png"}
BG_REF_H = 778.0                      # высота области фона в единицах игры: из theme_minecraft.sc (2048x1152 px -> 1383.12 x 778)


def bg_find_assets(folder):
    names = {n.lower(): n for n in os.listdir(folder)}
    return {k: os.path.join(folder, names[v]) for k, v in BG_FILES.items() if v in names}


def bg_motion(p):
    """Смещение поля за один цикл (вектор на сцене) и длительность цикла, секунд.
    Поле повёрнуто целиком на угол p['angle'] по часовой стрелке (в ряду левый символ выше), как в образце;
    за цикл оно сдвигается на шаг решётки S вдоль своего "верха": при угле 0 - строго вверх, при 45 - на северо-восток."""
    import math
    S = p["spacing"]
    th = math.radians(p["angle"])
    return (S * math.sin(th), -S * math.cos(th)), S / p["speed"]


def bg_symbols(p):
    """Символы в момент t=0: [(x, y, угол°, масштаб, прозрачность 0..1)] в координатах сцены.
    Решётка строится в системе самого поля (stagger - шахматная как в образце: ряды через S/2, каждый сдвинут на S/2;
    grid - ровная; scatter - ровная со смещением, поворотом, размером и прозрачностью по столбцам), потом всё поле
    поворачивается на p['angle']. Берутся символы, чей путь за цикл попадает в кадр."""
    import math
    import random
    S, W, H = p["spacing"], p["W"], p["H"]
    th = math.radians(p["angle"])
    ct, st = math.cos(th), math.sin(th)
    scatter = p["layout"] == "scatter"
    r = p["sym_w"] * 0.75 + (0.25 * S if scatter else 0.0)
    (mx, my), _c = bg_motion(p)
    n = int(math.hypot(W, H) / S) + 5
    out = []
    for j in range(-2 * n, 2 * n + 1):
        for i in range(-2 * n, 2 * n + 1):
            if p["layout"] == "stagger":
                u, v = i * S + j * S / 2.0, j * S / 2.0
            else:
                u, v = i * S, j * S
            rot, sc, al = p["angle"] + p["rot"], 1.0, p["alpha"]
            if scatter:
                rng = random.Random(p["seed"] * 100003 + i)                  # зависит от столбца: сдвиг на клетку вдоль столбца ничего не меняет
                u += (rng.random() - 0.5) * 0.5 * S
                v += (rng.random() - 0.5) * 0.5 * S
                rot += (rng.random() * 2 - 1) * p["spread"]
                sc = 0.8 + 0.4 * rng.random()
                al = p["alpha"] * (0.55 + 0.45 * rng.random())
            x, y = u * ct - v * st, u * st + v * ct                         # поворот поля по часовой (y вниз)
            x0, x1 = min(x, x + mx), max(x, x + mx)
            y0, y1 = min(y, y + my), max(y, y + my)
            if x1 < -W / 2 - r or x0 > W / 2 + r or y1 < -H / 2 - r or y0 > H / 2 + r:
                continue
            out.append((x, y, rot, sc, al))
    out.sort(key=lambda q: (q[1], q[0]))
    return out


def _q20(v):
    return round(v * 20) / 20.0


def bg_fit_image(Image, im, W, H, blur):
    """Вписывает картинку im в холст W x H. blur=True (градиент): пустые края заполняет размытая копия;
    blur=False (верхний слой, вспышка): края остаются прозрачными."""
    from PIL import ImageFilter
    im = im.convert("RGBA")
    if blur:
        lanczos, bicubic = gif_resample(Image)
        k = min(W / im.size[0], H / im.size[1])
        if abs(k * im.size[0] - W) < 1 and abs(k * im.size[1] - H) < 1:
            return im.resize((W, H), lanczos)
        sw, sh = max(32, W // 4), max(32, H // 4)
        back = gif_cover(Image, im.convert("RGB"), sw, sh).filter(ImageFilter.GaussianBlur(max(2.0, max(W, H) // 96)))
        out = back.resize((W, H), bicubic).convert("RGBA")
        fg = im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))), lanczos)
        out.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
        return out
    k = min(W / im.size[0], H / im.size[1])
    fg = im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))), gif_resample(Image)[0])
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return out


def bg_extract_gif(path, work, scale=1.0, canvas=None):
    """Кадры top.gif -> PNG RGBA в папке work. Возвращает ([(имя ассета, сколько кадров 60 к/с занимает)], {имя: путь}).
    Одинаковые соседние кадры сливаются в один (их показ просто длится дольше)."""
    import hashlib
    from PIL import Image, ImageSequence
    seq, paths, last = [], {}, None
    with Image.open(path) as im:
        for k, fr in enumerate(ImageSequence.Iterator(im)):
            dur = fr.info.get("duration", 100) or 100
            if dur < 20:
                dur = 100                           # браузерное правило: 0-10 мс в гифках означает 100 мс
            rgba = fr.convert("RGBA")
            if canvas and rgba.size != tuple(canvas):
                rgba = bg_fit_image(Image, rgba, canvas[0], canvas[1], False)
            if scale < 0.999:
                rgba = rgba.resize((max(1, round(rgba.size[0] * scale)), max(1, round(rgba.size[1] * scale))), Image.LANCZOS)
            n = max(1, round(dur * 60 / 1000.0))
            h = hashlib.md5(rgba.tobytes()).hexdigest()
            if h == last:
                seq[-1] = (seq[-1][0], seq[-1][1] + n)
                continue
            last = h
            name = f"topf{len(paths)}"
            fp = os.path.join(work, f"_{name}.png")
            rgba.save(fp)
            paths[name] = fp
            seq.append((name, n))
    return seq, paths


def bg_build_doc(p):
    """Документ SC2 для стадии p['stage']. Возвращает (документ, [(файл PNG, индекс текстуры)])."""
    import math
    A = p["assets"]
    W, H, stage = p["W"], p["H"], p["stage"]
    doc = {"is_sc2": True, "version": 5, "exports": [], "matrix_banks": [], "textures": [], "shapes": [],
           "movieclips": [], "textfields": [], "movieclip_modifiers": []}
    for k in SC2_DOC_FLAGS:
        doc[k] = False
    mats, cts, mkey, ckey = [], [], {}, {}

    def M(a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0):
        m = {"a": float(a) + 0.0, "b": float(b) + 0.0, "c": float(c) + 0.0, "d": float(d) + 0.0, "tx": _q20(tx) + 0.0, "ty": _q20(ty) + 0.0}
        key = repr(m)
        if key not in mkey:
            mkey[key] = len(mats)
            mats.append(m)
        return mkey[key]

    def CT(alpha=1.0):
        al = max(0, min(255, round(alpha * 255)))
        ct = {"alpha": al, "multiply": {"r": 255, "g": 255, "b": 255}, "add": {"r": 0, "g": 0, "b": 0}}
        key = repr(ct)
        if key not in ckey:
            ckey[key] = len(cts)
            cts.append(ct)
        return ckey[key]
    textures, tex_idx = [], {}

    def tex(name):
        if name not in tex_idx:
            tex_idx[name] = len(textures)
            textures.append((A[name], name))
        return tex_idx[name]
    ids = iter(range(10000))
    shape_of = {}

    def quad(name, w, h):
        sid = next(ids)
        t = tex(name)
        hw, hh = w / 2.0, h / 2.0
        doc["shapes"].append({"id": sid, "commands": [{"type": 1, "texture_index": t, "vertices": [
            {"x": hw, "y": -hh, "u": 1.0, "v": 0.0}, {"x": hw, "y": hh, "u": 1.0, "v": 1.0},
            {"x": -hw, "y": -hh, "u": 0.0, "v": 0.0}, {"x": -hw, "y": hh, "u": 0.0, "v": 1.0}]}]})
        shape_of[name] = sid
        return sid

    def clip(children, frames):
        """frames: [[(индекс ребёнка, матрица, цвет или None), ...], ...]"""
        cid = next(ids)
        els, fr = [], []
        for f in frames:
            fr.append({"label": "", "elements_count": len(f)})
            for inst, mi, ci in f:
                els.append({"instance_index": inst, "matrix_index": mi, "colorTransform_index": 0xFFFF if ci is None else ci})
        doc["movieclips"].append({"id": cid, "frame_rate": 60, "bank_index": 0, "unknown_flag": False,
                                  "childrens": [{"id": c, "blend_mode": 0, "name": ""} for c in children],
                                  "frames": fr, "frame_elements": els})
        return cid
    gw, gh = p["g_size"]
    grad = quad("gradient", gw * p["unit"], gh * p["unit"])
    layers = [(grad, M(), None)]
    (mvx, mvy), cycle_s = bg_motion(p)
    L = max(2, round(cycle_s * 60))
    if stage >= 2:
        if p.get("flash"):
            fl = quad("flash", gw * p["unit"], gh * p["unit"])
            fclip = clip([fl], [[(0, M(), CT(0.55 + 0.45 * math.sin(2 * math.pi * f / L)))] for f in range(L)])
            layers.append((fclip, M(), None))
        sw = p["sym_w"]
        sh_ = sw * A["_symbol_hw"]
        sym = quad("symbol", sw, sh_)
        syms = bg_symbols(p)
        els = []
        for k, (x, y, rot, sc, al) in enumerate(syms):
            th = math.radians(rot)
            els.append((k, M(sc * math.cos(th), sc * math.sin(th), -sc * math.sin(th), sc * math.cos(th), x, y), CT(al)))
        field = clip([sym] * len(syms), [els])
        mover = clip([field], [[(0, M(tx=mvx * f / L, ty=mvy * f / L), None)] for f in range(L)])
        layers.append((mover, M(), None))
    if stage >= 3:
        tw, th_ = A["_top_size"]
        if A.get("_top_frames"):
            fq = {}
            seq = []
            for nm, rep_n in A["_top_frames"]:
                if nm not in fq:
                    fq[nm] = quad(nm, tw * p["unit"], th_ * p["unit"])
                seq.append((nm, rep_n))
            kids = list(fq.values())
            frames_ = []
            for nm, rep_n in seq:
                frames_ += [[(kids.index(fq[nm]), M(), None)]] * rep_n
            top = clip(kids, frames_)
        else:
            top = quad("top", tw * p["unit"], th_ * p["unit"])
        layers.append((top, M(tx=p["top_dx"]), None))
    scene = clip([l[0] for l in layers], [[(i, l[1], l[2]) for i, l in enumerate(layers)]])
    exp = clip([scene], [[(0, M(), None)]])
    doc["exports"].append({"id": exp, "name": p["name"]})
    doc["matrix_banks"] = [{"matrices": mats, "color_transforms": cts}]
    for i, (path, name) in enumerate(textures):
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        doc["textures"].append({"index": i, "file": f"texture_{i}.png", "pixel_format": 0, "encoding": 2,
                                "khronos_format": 0, "linear": True, "filtering": 0, "downscaling": True,
                                "width": w, "height": h})
    return doc, [(path, i) for i, (path, _n) in enumerate(textures)]


def render_doc(doc, folder, name, t=0, width=960, premultiplied=False):
    """Простой рендер экспорта в PNG (RGBA): четырёхугольники с прямоугольными UV, матрицы и цветовые преобразования.
    premultiplied=True - в PNG RGB уже умножен на альфу, иначе обычная прозрачность (умножается при рисовании).
    Нужен для превью и самопроверки. t - кадр при 60 к/с; doc['_view'] = {'w','h'} - видимая область в единицах."""
    import numpy as np
    from PIL import Image
    clips = {c["id"]: c for c in doc["movieclips"]}
    shapes = {x["id"]: x for x in doc["shapes"]}
    ex = next(e for e in doc["exports"] if e["name"] == name)
    bank = doc["matrix_banks"]
    texs = {i: Image.open(os.path.join(folder, tx_.get("file") or f"texture_{i}.png")).convert("RGBA")
            for i, tx_ in enumerate(doc["textures"])}
    sc = doc["_view"]
    vs = width / sc["w"]
    out_h = round(sc["h"] * vs)
    canvas = np.zeros((out_h, width, 4), np.float32)            # предумноженный RGBA, 0..255

    def mat3(m):
        return np.array([[m["a"], m["c"], m["tx"]], [m["b"], m["d"], m["ty"]], [0, 0, 1]], float)

    def draw(oid, G, alpha, mul, add):
        nonlocal canvas
        if oid in shapes:
            for cmd in shapes[oid]["commands"]:
                v = cmd["vertices"]
                xs, ys = [q["x"] for q in v], [q["y"] for q in v]
                us, vs_ = [q["u"] for q in v], [q["v"] for q in v]
                tex_im = texs[cmd["texture_index"]]
                TW, TH = tex_im.size
                px0, px1, py0, py1 = min(us) * TW, max(us) * TW, min(vs_) * TH, max(vs_) * TH
                if px1 <= px0 or py1 <= py0:
                    continue
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                crop = tex_im.crop((round(px0), round(py0), round(px1), round(py1)))
                cw, ch = crop.size
                T = np.array([[(x1 - x0) / cw, 0, x0], [0, (y1 - y0) / ch, y0], [0, 0, 1]], float)
                V = np.array([[vs, 0, width / 2.0], [0, vs, out_h / 2.0], [0, 0, 1]], float)
                inv = np.linalg.inv(V @ G @ T)
                layer = crop.transform((width, out_h), Image.AFFINE,
                                       (inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2]),
                                       resample=Image.BILINEAR)
                a = np.asarray(layer, dtype=np.float32).copy()
                if not premultiplied:
                    a[..., :3] = a[..., :3] * a[..., 3:4] / 255.0
                a[..., :3] = np.clip(a[..., :3] * np.array(mul, np.float32) / 255.0 + np.array(add, np.float32) * a[..., 3:4] / 255.0, 0, 255)
                a *= alpha                                       # альфа цветового преобразования умножает все каналы
                canvas = a + canvas * (1.0 - a[..., 3:4] / 255.0)
            return
        c = clips.get(oid)
        if c is None:
            return
        fi = int(t) % len(c["frames"])
        start = sum(f["elements_count"] for f in c["frames"][:fi])
        bk = bank[c["bank_index"]]
        for e in c["frame_elements"][start:start + c["frames"][fi]["elements_count"]]:
            m = mat3(bk["matrices"][e["matrix_index"]]) if e["matrix_index"] != 0xFFFF else np.eye(3)
            a2, mul2, add2 = alpha, list(mul), list(add)
            if e["colorTransform_index"] != 0xFFFF:
                ct = bk["color_transforms"][e["colorTransform_index"]]
                a2 = alpha * ct["alpha"] / 255.0
                cm, ca = ct["multiply"], ct["add"]
                mul2 = [mul[0] * cm["r"] / 255.0, mul[1] * cm["g"] / 255.0, mul[2] * cm["b"] / 255.0]
                add2 = [add[0] + mul[0] * ca["r"] / 255.0, add[1] + mul[1] * ca["g"] / 255.0, add[2] + mul[2] * ca["b"] / 255.0]
            draw(c["childrens"][e["instance_index"]]["id"], G @ m, a2, mul2, add2)
    draw(ex["id"], np.eye(3), 1.0, [255, 255, 255], [0, 0, 0])
    al = canvas[..., 3:4]
    rgb = np.where(al > 0, canvas[..., :3] * 255.0 / np.maximum(al, 1e-3), 0)       # обратно в обычную альфу для PNG
    return Image.fromarray(np.clip(np.concatenate([rgb, al], axis=2), 0, 255).astype(np.uint8), "RGBA")


def premultiply_png(src, dst):
    """Копирует PNG в dst, умножая RGB на альфу, если картинка ещё не предумножена. Возвращает True, если умножил.
    Предумножены считаются картинки, где под нулевой альфой RGB = 0 и нигде max(RGB) не больше альфы."""
    import numpy as np
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    a = np.asarray(im, dtype=np.uint16)
    al = a[..., 3]
    if al.min() == 255:
        im.save(dst)
        return False
    rgb = a[..., :3]
    already = bool((rgb[al == 0].sum() == 0) and (rgb.max(axis=2) <= al + 1).all())
    if already:
        im.save(dst)
        return False
    out = a.copy()
    out[..., :3] = (rgb * al[..., None] + 127) // 255
    Image.fromarray(out.astype(np.uint8), "RGBA").save(dst)
    return True


def bg_ask_params(assets):
    """Диалог параметров. Возвращает словарь для bg_build_doc."""
    from PIL import Image
    have = {k: True for k in assets}
    has_top = "top" in have or "topgif" in have
    max_stage = 3 if ("gradient" in have and "symbol" in have and has_top) else (2 if all(k in have for k in ("gradient", "symbol")) else 1)
    if "gradient" not in have:
        raise UserError("В папке нет gradient.png - это нижний слой фона, без него фон не собрать")
    print(f"  Найдено: {', '.join(BG_FILES[k] for k in BG_FILES if k in have)}")
    print("  Стадии: 1 - статичный фон; 2 - градиент + движущиеся символы; 3 - градиент + символы + верхняя часть")
    st = ask(f"Стадия (Enter - {max_stage})", str(max_stage)).strip() or str(max_stage)
    if st not in ("1", "2", "3") or int(st) > max_stage:
        raise UserError(f"Эта стадия недоступна: для неё не хватает файлов (максимум по вашим файлам - {max_stage})")
    stage = int(st)
    with Image.open(assets["gradient"]) as g:
        gsz = g.size
    A = {k: v for k, v in assets.items()}
    import tempfile

    def prep(key, W, H, blur, what):
        """Приводит картинку к размеру W x H (в временный PNG) и подменяет путь в A."""
        if "_tmpdir" not in A:
            A["_tmpdir"] = tempfile.mkdtemp(prefix="schub_bgfit_")
        with Image.open(A[key]) as im:
            fitted = bg_fit_image(Image, im, W, H, blur)
        dst = os.path.join(A["_tmpdir"], f"{key}.png")
        fitted.save(dst)
        A[key] = dst
        print(gray(f"  {BG_FILES[key]}: {what}"))
    if abs(gsz[0] / gsz[1] - 2048 / 1152) > 0.02 * (2048 / 1152):
        prep("gradient", 2048, 1152, True,
             f"{gsz[0]}x{gsz[1]} не 16:9 - вписан в 2048x1152, пустые края заполнены размытием")
        gsz = (2048, 1152)
    elif gsz != (2048, 1152):
        print(yellow(f"  ⚠ gradient.png {gsz[0]}x{gsz[1]}: рекомендуется 2048x1152; пропорции у него 16:9, "
                     f"соберу как есть."))
    if stage >= 2 and "flash" in have:
        with Image.open(A["flash"]) as fl:
            if fl.size != gsz:
                prep("flash", gsz[0], gsz[1], False, f"{fl.size[0]}x{fl.size[1]} вписан в {gsz[0]}x{gsz[1]}, края прозрачные")
    if stage >= 2:
        with Image.open(assets["symbol"]) as sy:
            A["_symbol_hw"] = sy.size[1] / sy.size[0]
            if sy.mode not in ("RGBA", "LA") and "transparency" not in sy.info:
                print(yellow("  ⚠ У symbol.png нет прозрачности: символ будет прямоугольником. Нужен белый символ с альфа-каналом."))
    if stage >= 3 and "topgif" in have:
        if "top" in have:
            print(gray("  Есть и top.gif, и top.png: беру анимированный top.gif"))
        with Image.open(assets["topgif"]) as tp:
            nfr = getattr(tp, "n_frames", 1)
            tsz = tp.size
        A["_top_size"] = gsz
        if tsz != gsz:
            A["_top_canvas"] = gsz
            print(gray(f"  top.gif: {tsz[0]}x{tsz[1]} вписан в {gsz[0]}x{gsz[1]}, края прозрачные"))
        print(gray(f"  top.gif: {nfr} кадров. Каждый кадр хранится отдельной текстурой, поэтому файл растёт с числом кадров "
                   f"(одинаковые кадры подряд хранятся один раз)."))
        dflt = "1" if nfr <= 8 else "0.5"
        A["_top_scale"] = float(ask(f"Размер кадров гифки в файле: 1 - как есть, 0.5 - вдвое меньше по каждой стороне, "
                                    f"в игре размер тот же (Enter - {dflt})", dflt).replace(",", "."))
        if not 0.1 <= A["_top_scale"] <= 1:
            raise UserError("Размер кадров должен быть от 0.1 до 1")
    elif stage >= 3:
        with Image.open(assets["top"]) as tp:
            tsz = tp.size
        A["_top_size"] = gsz
        if tsz != gsz:
            prep("top", gsz[0], gsz[1], False, f"{tsz[0]}x{tsz[1]} вписан в {gsz[0]}x{gsz[1]}, края прозрачные")
        with Image.open(A["top"]) as tp:
            if tp.mode != "RGBA" or tp.getchannel("A").getextrema()[0] == 255:
                print(yellow("  ⚠ В top.png нет прозрачных пикселей - он закроет и градиент, и символы целиком"))
    unit0 = round(BG_REF_H / gsz[1], 6)
    print(gray(f"  Размер градиента {gsz[0]}x{gsz[1]}. Область фона в игре: {BG_REF_H:g} единиц по высоте (как в theme_minecraft.sc), "
               f"то есть {gsz[0] * unit0:.2f}x{BG_REF_H:g}."))
    unit = float(ask(f"Единиц игры на пиксель (Enter - {unit0}; 1 - один к одному)", str(unit0)).replace(",", "."))
    p = {"assets": A, "stage": stage, "unit": unit, "g_size": gsz, "W": gsz[0] * unit, "H": gsz[1] * unit,
         "spacing": 190.0, "sym_w": 72.0, "speed": 14.3, "alpha": 0.25, "angle": 10.18, "rot": 0.0, "spread": 15.0, "seed": 1,
         "layout": "stagger", "flash": False, "top_dx": 0.0, "premult": False}
    if stage >= 2:
        print("  Расстановка символов (всё поле наклонено целиком, как в образце background_sonic):")
        print("    1 - шахматная, как в образце: ряды сдвинуты на пол-клетки")
        print("    2 - обычная: ровная сетка, всё симметрично")
        print("    3 - в разброс: у каждого столбца свой сдвиг, поворот, размер и прозрачность")
        lay = ask("Расстановка", "1").strip()
        if lay not in ("1", "2", "3"):
            raise UserError("Нужно 1, 2 или 3")
        p["layout"] = {"1": "stagger", "2": "grid", "3": "scatter"}[lay]
        print("  Плотность символов:")
        print("    1 - редкие (по умолчанию)")
        print("    2 - средние (как в образце background_sonic по шагу решётки)")
        print("    3 - частые")
        dn = ask("Плотность", "1").strip()
        if dn not in ("1", "2", "3"):
            raise UserError("Нужно 1, 2 или 3")
        p["spacing"] = {"1": 190.0, "2": 144.0, "3": 115.0}[dn]
        try:
            cnt = sum(1 for s_ in bg_symbols(p) if abs(s_[0]) <= p["W"] / 2 and abs(s_[1]) <= p["H"] / 2)
            print(gray(f"  Примерно {cnt} символов в кадре."))
        except Exception:
            pass
        if ask_yes_no("Настроить символы вручную (угол, расстояние, размер, скорость, прозрачность...)", False):
            p["angle"] = float(ask("Наклон всего поля, градусов по часовой (10.18 как в образце; 0 - без наклона; 45 - на северо-восток)", str(p["angle"])).replace(",", "."))
            p["spacing"] = float(ask("Шаг решётки, единиц (в образце 144)", str(p["spacing"])).replace(",", "."))
            p["sym_w"] = float(ask("Ширина символа, единиц (в образце около 80)", str(p["sym_w"])).replace(",", "."))
            p["speed"] = float(ask("Скорость, единиц в секунду (в образце около 14)", str(p["speed"])).replace(",", "."))
            p["alpha"] = float(ask("Непрозрачность символов, %", str(round(p["alpha"] * 100))).replace(",", ".")) / 100.0
            p["rot"] = float(ask("Дополнительный поворот каждого символа, градусов (0 - только наклон поля)", str(p["rot"])).replace(",", "."))
            if p["layout"] == "scatter":
                p["spread"] = float(ask("Разброс поворота, ±градусов", str(p["spread"])).replace(",", "."))
                p["seed"] = int(ask("Число для случайной раскладки (другое число - другая расстановка)", str(p["seed"])))
            p["premult"] = ask_yes_no("Умножать RGB на альфу (нужно, если вокруг символов видны светлые рамки или прямоугольники)", False)
        if "flash" in have:
            p["flash"] = ask_yes_no("Найден flash.png: добавить вспышку (свечение пульсирует за символами)", True)
    if stage >= 3:
        p["top_dx"] = float(ask("Сдвиг верхней части вправо, единиц (минус - влево; Enter - 0, по центру)", "0").replace(",", "."))
    if p["spacing"] <= 10 or p["sym_w"] <= 1 or p["speed"] <= 0.1 or not 0 < p["alpha"] <= 1:
        raise UserError("Неверные значения: расстояние, размер, скорость и непрозрачность должны быть положительными (непрозрачность до 100%)")
    return p


def flow_background():
    import shutil
    import tempfile
    ensure_deps()
    ensure_numpy_optional()
    folder = resolve_input("Папка с gradient.png, symbol.png, top.png или top.gif (и flash.png, если нужна вспышка)", "background",
                           [], allow_dir=True)
    if not os.path.isdir(folder):
        raise UserError("Нужна папка, а не файл")
    assets = bg_find_assets(folder)
    p = bg_ask_params(assets)
    name = ask("Экспортнейм для собранного фона", "theme_bg").strip()
    if not name or any(c in name for c in " /\\"):
        raise UserError("Экспортнейм не должен быть пустым и содержать пробелы или слэши")
    p["name"] = name
    out_path = resolve_output(ask("Выходной файл", name), ".sc")
    work = tempfile.mkdtemp(prefix="schub_bg_")
    try:
        print(gray("  Собираю фон..."))
        if p["stage"] >= 3 and p["assets"].get("topgif"):
            seq, fpaths = bg_extract_gif(p["assets"]["topgif"], work, p["assets"].get("_top_scale", 1.0), p["assets"].get("_top_canvas"))
            p["assets"].update(fpaths)
            p["assets"]["_top_frames"] = seq
            tot = sum(n for _a, n in seq)
            print(gray(f"  Гифка: {len(fpaths)} разных кадров, длительность {tot / 60:.2f} с"))
        doc, tex_files = bg_build_doc(p)
        for src, i in tex_files:
            dst = os.path.join(work, f"texture_{i}.png")
            if p["premult"]:
                if premultiply_png(src, dst):
                    print(gray(f"  {os.path.basename(src)}: RGB умножен на альфу"))
            else:
                shutil.copyfile(src, dst)
        astc = ask_astc()
        main, _tex, warns = encode_doc(doc, work, astc)
        with open(out_path, "wb") as fh:
            fh.write(main)
        saved(out_path)
        for w in warns:
            print(yellow("  ⚠ " + w))
        if ask_yes_no("Сохранить превью (PNG, кадр 0)", True):
            try:
                doc["_view"] = {"w": p["W"], "h": p["H"]}
                prev = os.path.splitext(out_path)[0] + f"_stage{p['stage']}.png"
                render_doc(doc, work, name, 0, 960, p["premult"]).save(prev)
                saved(prev)
            except Exception as e:                      # превью - удобство, не обязанность
                print(yellow(f"  Превью не получилось: {e}"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if p["assets"].get("_tmpdir"):
            shutil.rmtree(p["assets"]["_tmpdir"], ignore_errors=True)
    n_sym = len(bg_symbols(p)) if p["stage"] >= 2 else 0
    _mv, cyc = bg_motion(p)
    print(gray(f"  Стадия {p['stage']}: экспорт «{name}»" + (f", символов {n_sym}, поле наклонено на {p['angle']:g}°, цикл {cyc:.1f} с: "
                                                          f"за цикл символы смещаются на шаг {p['spacing']:g} вдоль наклона, "
                                                          f"скорость {p['speed']:g} ед/с" if n_sym else ", статичный")))
    return [out_path]


def flow_optimize():
    import shutil
    import tempfile
    ensure_deps()
    src = resolve_input("Какой .sc очистить и оптимизировать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    work = tempfile.mkdtemp(prefix="schub_opt_")
    try:
        sub = os.path.join(work, "src")
        os.makedirs(sub)
        print(gray("  Разбираю файл..."))
        doc, warns, kind = load_source_doc(src, sub)
        for w in warns[:2]:
            print(yellow("  ⚠ " + w))
        print(gray("  Убираю недостижимое, сливаю одинаковое и сверяю каждый экспорт до и после..."))
        new, st = doc_optimize(doc, sub)
        names = {"shapes": "шейпов", "clips": "клипов", "tfs": "текстовых полей", "textures": "текстур",
                 "matrices": "матриц", "colors": "цветовых преобразований"}
        line()
        for k, (a, b) in st.items():
            print(f"  {names[k]}: {a} → {b}" + (green(f"  (−{a - b})") if b < a else gray("  (без изменений)")))
        out_path = resolve_output(ask("Выходной файл", os.path.splitext(os.path.basename(src))[0] + "_opt.sc"), ".sc")
        if os.path.abspath(out_path) == os.path.abspath(src):
            raise UserError("Выходной файл совпадает со входным - укажи другое имя")
        astc = ask_astc()
        entry = {"name": os.path.basename(src), "doc": new, "dir": sub, "kind": kind, "warns": []}
        merged, rep, w2 = write_merged_sc([entry], out_path, work, astc)
        saved(out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    old_size = os.path.getsize(src) + sum(os.path.getsize(p) for p in tex_sc_candidates(src)) \
        if kind == "sc1" else os.path.getsize(src)
    print(gray(f"  Размер: было {old_size / 1048576:.2f} МБ (с файлами пакета), стало {os.path.getsize(out_path) / 1048576:.2f} МБ"))
    print(green("  ✔ Проверка пройдена: ") + gray("у каждого экспорта сошёлся отпечаток внешнего вида (клипы, кадры, "
                                                   "матрицы, цвета, вершины, текстуры)"))
    for w in w2:
        print(yellow("  ⚠ " + w))
    return [out_path]


def loc_short(loc, name, n=56):
    if not loc or not name.startswith("TID_"):
        return ""
    t = loc.text(name)
    if t is None:
        return gray("  [нет в таблице]")
    t = t.replace("\n", "\\n")
    return gray(f"  «{t[:n]}{'…' if len(t) > n else ''}»")


def pick_tid(loc, query):
    """Подбор TID по тексту из таблицы: показывает совпадения, возвращает выбранный TID или None."""
    hits = loc.search(query, 15)
    if not hits:
        print(gray("  В таблице такого текста нет"))
        return None
    for k, (tid, txt) in enumerate(hits, 1):
        t = txt.replace("\n", "\\n")
        print(f"      {k:>2}. {tid}  " + gray(f"«{t[:60]}{'…' if len(t) > 60 else ''}»"))
    pick = ask("Номер TID (Enter - не выбирать)", "").strip()
    if pick.isdigit() and 1 <= int(pick) <= len(hits):
        return hits[int(pick) - 1][0]
    return None


def flow_textfield_names(src, sc):
    insts = tf_instances(sc)
    if not insts:
        raise UserError("В файле нет экземпляров текстовых полей в клипах")
    tid = sum(1 for i in insts if i["name"].startswith("TID_"))
    empty = sum(1 for i in insts if not i["name"])
    blocked = sum(1 for i in insts if not i["editable"])
    print(f"  Экземпляров текстовых полей в клипах: {len(insts)} "
          f"(ключей TID_: {tid}, пустых имён: {empty}, прочих имён: {len(insts) - tid - empty})")
    print(gray("  Текст надписи в игре - это имя экземпляра. TID_... игра переводит по таблице локализации; "
               "обычную строку, видимо, показывает как есть. Служебные имена (txt, spacer...) использует код игры."))
    if blocked:
        print(yellow(f"  ⚠ У {blocked} экземпляров клип без таблицы имён: их поменять нельзя, они помечены ✖"))
    loc = loc_ask()

    def show(idx):
        i = insts[idx]
        where = i["clip_name"] or f"клип {i['clip_id']}"
        mark = "" if i["editable"] else red(" ✖")
        nm = f"«{i['name']}»" if i["name"] else "(пусто)"
        print(f"    {idx + 1:>4}. {nm}{loc_short(loc, i['name'])}   поле {i['tf']}, {where}, экземпляр {i['child']}{mark}")

    edits = {}
    while True:
        hint = "Что искать в именах/клипах" + (" и в тексте таблицы" if loc else "")
        q = ask(hint + " (Enter - закончить выбор, * - показать все" + (", lang - сменить язык" if loc_scan() else "") + ")", "").strip()
        if not q:
            break
        if q.lower() == "lang":
            loc = loc_ask(loc) or loc
            continue
        ql = q.lower()

        def match(i):
            if q == "*" or ql in i["name"].lower() or ql in i["clip_name"].lower() or q == str(i["tf"]):
                return True
            if loc and i["name"].startswith("TID_"):
                t = loc.text(i["name"])
                return t is not None and ql in t.lower()
            return False
        hits = [k for k, i in enumerate(insts) if match(i)]
        print(f"  Найдено: {len(hits)}" + (" (показаны первые 40)" if len(hits) > 40 else ""))
        for k in hits[:40]:
            show(k)
        if not hits:
            continue
        pick = ask("Какие править (номера 1,3,5-7 или all; Enter - искать заново)", "").strip()
        if not pick:
            continue
        try:
            sel = [hits[k] for k in parse_index_list(pick, len(hits))] if pick.lower() in ("all", "*", "все") \
                else parse_index_list(pick, len(insts))
        except UserError as e:
            print(red("  " + str(e)))
            continue
        sel = [k for k in sel if insts[k]["editable"]]
        if not sel:
            print(gray("  Нечего править"))
            continue
        services = [insts[k]["name"] for k in sel if insts[k]["name"] and not insts[k]["name"].startswith("TID_")]
        if services:
            print(yellow(f"  ⚠ Среди выбранных есть обычные имена ({', '.join(sorted(set(services))[:5])}): "
                         "код игры может искать поле по этому имени."))
        prompt = "Новое имя/текст (Enter - не менять" + (", ?слово - подобрать TID по тексту таблицы" if loc else "") + ")"
        new = ask(prompt, "")
        while loc and new.startswith("?"):
            chosen = pick_tid(loc, new[1:])
            if chosen:
                new = chosen
                break
            new = ask(prompt, "")
        if not new:
            print(gray("  Ничего не изменено"))
            continue
        for k in sel:
            edits[k] = new
        print(green(f"  ✔ В список правок добавлено: {len(sel)} (всего: {len(edits)})"))
    if not edits:
        raise UserError("Ни одной правки не задано")
    line()
    for k, t in list(edits.items())[:30]:
        i = insts[k]
        print(f"  {i['clip_name'] or i['clip_id']}[{i['child']}]: «{i['name']}» → «{t}»{loc_short(loc, t)}")
    if len(edits) > 30:
        print(gray(f"  ... и ещё {len(edits) - 30}"))
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")
    print(gray("  Пересобираю и сжимаю (несколько секунд)..."))
    data = edit_tf_names(sc, [(insts[k]["clip"], insts[k]["child"], t) for k, t in edits.items()])
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    print(green("  ✔ Проверка пройдена: ") + gray("изменены только имена выбранных экземпляров"))
    return [out_path]


def flow_textfield():
    ensure_deps()
    src = resolve_input("Какой .sc менять", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    if sc.get("kind") == "sc1":
        print(gray("  SC1: поддерживается правка текстов (имён экземпляров); размер и рамка поля - только в SC2."))
        return flow_textfield_names(src, sc)
    print("  Что править:")
    print("    1 - тексты надписей (имена экземпляров в клипах: так игра берёт текст)")
    print("    2 - размер шрифта и рамка текстового поля (запись TextField, общая для всех экземпляров)")
    if ask("Что править", "1") == "2":
        return flow_textfield_params(src, sc)
    return flow_textfield_names(src, sc)



def flow_textfield_params(src, sc):
    recs, strings = read_textfields(sc)
    if not recs:
        raise UserError("В файле нет текстовых полей")
    text_off, confirmed, note = textfield_text_slot(recs, strings)
    by_id = {r["id"]: r for r in recs}
    texts = {r["id"]: textfield_text(r, strings, r.get("text_slot", text_off)) for r in recs}
    nonempty = [r for r in recs if texts[r["id"]]]
    m = load_model(sc)
    exports, _s = get_exports(sc)
    id_to_name = {e["id"]: e["name"] for e in exports}
    users = {}
    for c in m["clips"]:
        for ch in set(c["children"]):
            if ch in by_id:
                users.setdefault(ch, []).append(id_to_name.get(c["id"], f"id {c['id']}"))
    print(f"  Текстовых полей: {len(recs)}, из них с непустым текстом: {len(nonempty)}")
    print(gray(f"  Где лежит текст: {note}"))
    if not confirmed:
        print(yellow("  ⚠ Это не подтверждено файлом (нет имён файлов типографики, по которым их можно было бы "
                     "отличить от текста): проверь результат в игре на одном поле, прежде чем править много."))
    if not nonempty:
        print(yellow("  Непустых текстов в файле нет, поиск по тексту ничего не найдёт. "
                     "Вводи ID поля сразу. Вот первые поля:"))
        for r in recs[:10]:
            print(gray(f"    id {r['id']}: шрифт {r['font']!r}, размер {r['size']}, рамка {tuple(r['bounds'])}"))

    def describe(r):
        t = texts[r["id"]]
        used = users.get(r["id"], [])
        used_s = (", используется в: " + ", ".join(used[:3]) + (f" и ещё {len(used) - 3}" if len(used) > 3 else "")) if used else ""
        kind = f", тип {r.get('text_kind', 'text')}"
        if r.get('localization_key'):
            kind += f", ключ {r['localization_key']!r}"
        return (f"id {r['id']}: «{t}»" if t else f"id {r['id']}: (пусто)") + \
            f", шрифт {r['font']!r}, размер {r['size']}, рамка {tuple(r['bounds'])}{kind}{used_s}"

    edits = {}
    while True:
        raw = ask("Введите текст, который ищете, или сразу ID поля (Enter - закончить выбор)", "").strip()
        if not raw:
            break
        rec = None
        if raw.isdigit() and int(raw) in by_id:
            rec = by_id[int(raw)]
        else:
            query = raw.strip('"')
            hits = [r for r in nonempty if query.lower() in texts[r["id"]].lower()]
            print(f"  Совпадений: {len(hits)}" + (f" (показаны первые 10)" if len(hits) > 10 else ""))
            for r in hits[:10]:
                print("    " + describe(r))
            if not hits:
                print(gray("    ничего не найдено; можно искать заново или ввести ID поля"))
            nxt = ask("ID поля для правки (Enter - искать заново)", "").strip()
            if not nxt:
                continue
            if not nxt.isdigit() or int(nxt) not in by_id:
                print(red(f"  Нет текстового поля с id «{nxt}»"))
                continue
            rec = by_id[int(nxt)]
        print("  " + describe(rec))
        e = {}
        cur = texts[rec["id"]]
        size = ask("Размер шрифта", str(rec["size"]))
        try:
            if int(float(size)) != rec["size"]:
                e["size"] = int(float(size))
        except ValueError:
            print(yellow(f"  Размер «{size}» не понял, оставляю {rec['size']}"))
        b_default = ",".join(str(v) for v in rec["bounds"])
        b_new = ask("Рамка left,top,right,bottom", b_default)
        if b_new.replace(" ", "") != b_default:
            e["bounds"] = parse_bounds(b_new)
        if e:
            edits[rec["id"]] = {**edits.get(rec["id"], {}), **e}
            print(green(f"  ✔ Поле {rec['id']} добавлено в список правок (всего полей: {len(edits)})"))
        else:
            print(gray("  Ничего не изменено"))
    if not edits:
        raise UserError("Ни одной правки не задано")
    line()
    for fid, e in edits.items():
        parts = []
        if "text" in e:
            parts.append(f"текст «{texts[fid]}» → «{e['text']}»")
        if "size" in e:
            parts.append(f"размер {by_id[fid]['size']} → {e['size']}")
        if "bounds" in e:
            parts.append(f"рамка {tuple(by_id[fid]['bounds'])} → {e['bounds']}")
        print(f"  поле {fid}: " + "; ".join(parts))
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")
    print(gray("  Пересобираю и сжимаю (несколько секунд)..."))
    data = edit_textfields(sc, edits)
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    print(green("  ✔ Проверка пройдена: ") + gray("изменены только выбранные поля, остальное содержимое без изменений"))
    return [out_path]


# ================================================================ перестройка атласов
#
# Берутся все фрагменты текстур, на которые реально ссылаются шейпы (прямоугольник по UV каждой команды
# шейпа, перекрывающиеся прямоугольники сливаются в один фрагмент, вокруг каждого берётся отступ),
# и упаковываются в атласы не больше заданного размера (по умолчанию 4096x4096): сначала заполняется
# первый атлас, потом второй и т.д. Последний атлас ужимается до минимального размера, в который
# влезают оставшиеся фрагменты. UV всех шейпов пересчитываются. Новые атласы записываются как сырой
# RGBA8 (так их пишет gifconv): ASTC/KTX хаб обратно не кодирует, поэтому такие текстуры после
# перестройки занимают в файле и в памяти больше. Текстуры, на которые не ссылается ни один шейп,
# выбрасываются.

ATLAS_LIMIT = 4096


class MaxRectsBin:
    """Упаковка прямоугольников (MaxRects). Эвристики: bssf, blsf, baf, bl; повороты (транспонирование)
    разрешаются флагом rot."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.free = [(0, 0, w, h)]

    def insert(self, iw, ih, heur="bssf", rot=False):
        best = None
        orients = [(iw, ih, 0)]
        if rot and iw != ih:
            orients.append((ih, iw, 1))
        for w, h, r in orients:
            for fx, fy, fw, fh in self.free:
                if w <= fw and h <= fh:
                    if heur == "bssf":
                        score = (min(fw - w, fh - h), max(fw - w, fh - h))
                    elif heur == "blsf":
                        score = (max(fw - w, fh - h), min(fw - w, fh - h))
                    elif heur == "baf":
                        score = (fw * fh - w * h, min(fw - w, fh - h))
                    else:
                        score = (fy + h, fx)
                    if best is None or score < best[0]:
                        best = (score, fx, fy, w, h, r)
        if best is None:
            return None
        _s, x, y, w, h, r = best
        self._place(x, y, w, h)
        return x, y, r

    def _place(self, x, y, w, h):
        new_free = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                new_free.append((fx, fy, fw, fh))
                continue
            if x > fx:
                new_free.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                new_free.append((x + w, fy, fx + fw - (x + w), fh))
            if y > fy:
                new_free.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                new_free.append((fx, y + h, fw, fy + fh - (y + h)))
        new_free.sort(key=lambda r: -(r[2] * r[3]))
        kept = []
        for r in new_free:
            rx, ry, rw, rh = r
            if any(rx >= kx and ry >= ky and rx + rw <= kx + kw and ry + rh <= ky + kh for kx, ky, kw, kh in kept):
                continue
            kept.append(r)
        self.free = kept


def _np():
    """numpy, если он есть (ускоряет упаковку в десятки раз); иначе None."""
    if "mod" not in _NP:
        try:
            import numpy
            _NP["mod"] = numpy
        except ImportError:
            _NP["mod"] = None
    return _NP["mod"]


_NP = {}


class MaxRectsBinNp:
    """То же, что MaxRectsBin, но список свободных прямоугольников хранится в numpy-массиве."""

    def __init__(self, w, h):
        np = _np()
        self.np = np
        self.free = np.array([[0, 0, w, h]], dtype=np.int64)

    def insert(self, iw, ih, heur="bssf", rot=False):
        np = self.np
        f = self.free
        best = None
        orients = [(iw, ih, 0)]
        if rot and iw != ih:
            orients.append((ih, iw, 1))
        for w, h, r in orients:
            ok = (f[:, 2] >= w) & (f[:, 3] >= h)
            idx = np.nonzero(ok)[0]
            if idx.size == 0:
                continue
            fx, fy, fw, fh = f[idx, 0], f[idx, 1], f[idx, 2], f[idx, 3]
            dw, dh = fw - w, fh - h
            if heur == "bssf":
                key = np.minimum(dw, dh) * 100000 + np.maximum(dw, dh)
            elif heur == "blsf":
                key = np.maximum(dw, dh) * 100000 + np.minimum(dw, dh)
            elif heur == "baf":
                key = (fw * fh - w * h) * 100000 + np.minimum(dw, dh)
            else:
                key = (fy + h) * 100000 + fx
            k = int(np.argmin(key))
            cand = (int(key[k]), int(fx[k]), int(fy[k]), w, h, r)
            if best is None or cand[0] < best[0]:
                best = cand
        if best is None:
            return None
        _k, x, y, w, h, r = best
        self._place(x, y, w, h)
        return x, y, r

    def _place(self, x, y, w, h):
        np = self.np
        f = self.free
        fx, fy, fw, fh = f[:, 0], f[:, 1], f[:, 2], f[:, 3]
        hit = ~((x >= fx + fw) | (x + w <= fx) | (y >= fy + fh) | (y + h <= fy))
        if not hit.any():
            return
        keep, hs = f[~hit], f[hit]
        hx, hy, hw, hh = hs[:, 0], hs[:, 1], hs[:, 2], hs[:, 3]
        pieces = []
        m = x > hx
        if m.any():
            pieces.append(np.stack([hx[m], hy[m], (x - hx)[m], hh[m]], axis=1))
        m = (x + w) < (hx + hw)
        if m.any():
            pieces.append(np.stack([np.full(int(m.sum()), x + w, dtype=np.int64), hy[m],
                                    (hx + hw - (x + w))[m], hh[m]], axis=1))
        m = y > hy
        if m.any():
            pieces.append(np.stack([hx[m], hy[m], hw[m], (y - hy)[m]], axis=1))
        m = (y + h) < (hy + hh)
        if m.any():
            pieces.append(np.stack([hx[m], np.full(int(m.sum()), y + h, dtype=np.int64), hw[m],
                                    (hy + hh - (y + h))[m]], axis=1))
        if not pieces:
            self.free = keep
            return
        new = np.concatenate(pieces, axis=0)
        if keep.shape[0]:
            nl, nt, nr, nb = new[:, 0], new[:, 1], new[:, 0] + new[:, 2], new[:, 1] + new[:, 3]
            kl, kt, kr, kb = keep[:, 0], keep[:, 1], keep[:, 0] + keep[:, 2], keep[:, 1] + keep[:, 3]
            inside = ((nl[:, None] >= kl[None, :]) & (nt[:, None] >= kt[None, :]) &
                      (nr[:, None] <= kr[None, :]) & (nb[:, None] <= kb[None, :])).any(axis=1)
            new = new[~inside]
        n = new.shape[0]
        if n > 1:
            nl, nt, nr, nb = new[:, 0], new[:, 1], new[:, 0] + new[:, 2], new[:, 1] + new[:, 3]
            cont = ((nl[:, None] >= nl[None, :]) & (nt[:, None] >= nt[None, :]) &
                    (nr[:, None] <= nr[None, :]) & (nb[:, None] <= nb[None, :]))
            np.fill_diagonal(cont, False)
            eq = cont & cont.T
            lower = np.tril(np.ones((n, n), dtype=bool), -1)
            drop = (cont & ~eq).any(axis=1) | (eq & lower).any(axis=1)
            new = new[~drop]
        self.free = np.concatenate([keep, new], axis=0) if keep.shape[0] else new


def make_bin(w, h):
    return MaxRectsBinNp(w, h) if _np() is not None else MaxRectsBin(w, h)


def sc2_tex_infos(sc):
    txb = sc["chunks"][6]
    out = []
    tp = fb_slot(txb, fb_root(txb), 0)
    for s in (fb_table_vec(txb, tp) if tp is not None else []):
        pd = fb_slot(txb, s, 1)
        td = pd + u32(txb, pd)
        ps = dict(fb_slots(txb, td))
        start, n = fb_vec(txb, ps[4]) if 4 in ps else (0, 0)
        out.append({"fmt": txb[ps[0]] if 0 in ps else 0, "flags": txb[ps[1]] if 1 in ps else 0,
                    "w": u16(txb, ps[2]) if 2 in ps else 0,
                    "h": u16(txb, ps[3]) if 3 in ps else 0, "payload": bytes(txb[start:start + n])})
    return out


def sc1_tex_infos(sc, tex_cont):
    refs = sc1_ext_refs(tex_cont["raw"]) if tex_cont is not None else []
    ext = (tex_cont.get("ext") if tex_cont is not None else None) or []
    if refs and len(ext) != len(refs):
        raise UserError("Текстуры пакета лежат во внешних .sctx/.zktx, но прочитать их не удалось")
    heads = [(pos, tag, body) for pos, (tag, body) in enumerate(sc["mdl"]["tags"]) if tag in SC1_TEXTURE_TAGS]
    pix = []
    if tex_cont is not None:
        pix = [(t, b) for t, b in sc1_parse_stream_tags(tex_cont["raw"]) if t in SC1_TEXTURE_TAGS]
    out, ext_i = [], 0
    for i, (pos, tag, body) in enumerate(heads):
        data = body[5:] if len(body) > 5 else b""
        if not data and i < len(pix) and len(pix[i][1]) > 5:
            data = pix[i][1][5:]
        w, h = struct.unpack_from("<H", body, 1)[0], struct.unpack_from("<H", body, 3)[0]
        is_ext = False
        if not data and ext_i < len(ext):
            data, w, h, is_ext = ext[ext_i]["rgba"], ext[ext_i]["w"], ext[ext_i]["h"], True
            ext_i += 1
        out.append({"fmt": body[0], "w": w, "h": h, "payload": bytes(data), "tag": tag, "pos": pos,
                    "inline": len(body) > 5, "ext": is_ext})
    return out


def atlas_collect(sc):
    """Все команды шейпов: [{'tex', 'uv': [(u, v)...], 'loc': ...}] (loc нужен, чтобы записать обратно)."""
    cmds = []
    if sc.get("kind") == "sc1":
        m = sc1_load_model(sc)
        for sh in m["shapes"]:
            for ci, c in enumerate(sh["cmds"]):
                cmds.append({"tex": c["tex"], "uv": [(c["uv"][2 * k], c["uv"][2 * k + 1]) for k in range(len(c["uv"]) // 2)],
                             "loc": (sh["index"], ci)})
        return cmds
    m = load_model(sc)
    shb, ds = sc["chunks"][3], m["ds"]
    sp = fb_slot(shb, fb_root(shb), 0)
    seen = set()
    for s in (fb_table_vec(shb, sp) if sp is not None else []):
        ps = dict(fb_slots(shb, s))
        if 1 not in ps:
            continue
        cs, cn = fb_vec(shb, ps[1])
        for ci in range(cn):
            at = cs + 16 * ci
            _t, tex, cnt, first = struct.unpack_from("<4I", shb, at)
            for k in range(first, first + cnt):
                if k in seen:
                    raise UserError("Команды шейпов делят общие вершины - перестройка атласов такое не поддерживает")
                seen.add(k)
            cmds.append({"tex": tex, "uv": [struct.unpack_from("<HH", ds, m["v_start"] + 12 * (first + k) + 8)
                                            for k in range(cnt)], "loc": (at, first, cnt)})
    return cmds


def cmd_rect(uvs, W, H):
    us, vs = [u for u, _v in uvs], [v for _u, v in uvs]
    x0 = max(0, int(math.floor(min(us) / 65535 * W + 1e-3)))
    y0 = max(0, int(math.floor(min(vs) / 65535 * H + 1e-3)))
    x1 = min(W, int(math.ceil(max(us) / 65535 * W - 1e-3)))
    y1 = min(H, int(math.ceil(max(vs) / 65535 * H - 1e-3)))
    return (x0, y0, max(x1, x0 + 1), max(y1, y0 + 1))


def merge_rects(rects):
    """Сливает перекрывающиеся прямоугольники. Возвращает [(общий прямоугольник, {исходные})]."""
    comps = [[list(r), {r}] for r in rects]
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(comps):
            j = i + 1
            while j < len(comps):
                a, b = comps[i][0], comps[j][0]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    a[0], a[1], a[2], a[3] = min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])
                    comps[i][1] |= comps[j][1]
                    del comps[j]
                    changed = True
                else:
                    j += 1
            i += 1
    return [(tuple(c[0]), c[1]) for c in comps]


def _occ(it, r):
    return (it["h"], it["w"]) if r else (it["w"], it["h"])


def _try_fit(ids, items, w, h, heur, rot):
    bn = make_bin(w, h)
    placed = {}
    for i in ids:
        res = bn.insert(items[i]["w"], items[i]["h"], heur, rot)
        if res is None:
            return None
        placed[i] = res
    return placed


def _round_up(v, k):
    return max(k, ((v + k - 1) // k) * k)


def fit_smallest(ids, items, limit, pot, heur="bssf", rot=False, prefer_limit=True, hint=None):
    """Наименьший размер атласа, в который влезают ids. Возвращает (w, h, {i: (x, y, r)}).
    hint - уже известная раскладка этих же фрагментов в бине limit x limit (ускоряет поиск)."""
    total = sum(items[i]["w"] * items[i]["h"] for i in ids)
    if pot:
        sizes = [2 ** k for k in range(4, 15) if 2 ** k <= limit]
        if limit not in sizes:
            sizes.append(limit)
        cands = sorted(((w, h) for w in sizes for h in sizes if w * h >= total),
                       key=lambda s: (s[0] * s[1], abs(s[0] - s[1])))
        for w, h in cands:
            res = _try_fit(ids, items, w, h, heur, rot)
            if res is not None:
                return w, h, res
    elif prefer_limit and total >= limit * limit / 6:
        # Содержимого достаточно: одна сторона = limit (4096), вторая - сколько нужно
        side = []
        base = None
        if hint is not None:
            base = {i: (hint[i][1], hint[i][2], hint[i][3]) for i in ids}
        for horizontal in (True, False):
            if base is not None:
                best = base
                ext = max((base[i][1] + _occ(items[i], base[i][2])[1]) if horizontal
                          else (base[i][0] + _occ(items[i], base[i][2])[0]) for i in ids)
                hi = min(limit, ext)
            else:
                best = _try_fit(ids, items, limit, limit, heur, rot)
                if best is None:
                    continue
                hi = limit
            lo = max(4, int(math.ceil(total / limit)))
            while hi - lo > 4:
                mid = (lo + hi) // 2
                res = _try_fit(ids, items, limit, mid, heur, rot) if horizontal else \
                    _try_fit(ids, items, mid, limit, heur, rot)
                if res is not None:
                    hi, best = mid, res
                else:
                    lo = mid + 1
            ext_w = max(best[i][0] + _occ(items[i], best[i][2])[0] for i in ids)
            ext_h = max(best[i][1] + _occ(items[i], best[i][2])[1] for i in ids)
            if horizontal:
                side.append((limit, min(limit, _round_up(ext_h, 4)), best))
            else:
                side.append((min(limit, _round_up(ext_w, 4)), limit, best))
        if side:
            return min(side, key=lambda c: c[0] * c[1])
    else:
        best_overall = None
        for ra, rb in ((1, 1), (1, 2), (2, 1)):
            hi = limit // max(ra, rb)
            lo = max(4, int(math.ceil(math.sqrt(total / (ra * rb)))))
            if lo > hi:
                continue
            found = _try_fit(ids, items, ra * hi, rb * hi, heur, rot)
            if found is None:
                continue
            best = found
            while hi - lo > 2:
                mid = (lo + hi) // 2
                res = _try_fit(ids, items, ra * mid, rb * mid, heur, rot)
                if res is not None:
                    hi, best = mid, res
                else:
                    lo = mid + 1
            w = min(limit, _round_up(max(best[i][0] + _occ(items[i], best[i][2])[0] for i in ids), 4))
            h = min(limit, _round_up(max(best[i][1] + _occ(items[i], best[i][2])[1] for i in ids), 4))
            if best_overall is None or w * h < best_overall[0] * best_overall[1]:
                best_overall = (w, h, best)
        if best_overall is not None:
            return best_overall
    res = _try_fit(ids, items, limit, limit, heur, rot)
    if res is None:
        raise UserError("Не удалось упаковать фрагменты в атлас заданного размера")
    return limit, limit, res


ATLAS_ORDERS = {
    "maxside": lambda it, i: (-max(it["w"], it["h"]), -it["w"] * it["h"], i),
    "area": lambda it, i: (-it["w"] * it["h"], -max(it["w"], it["h"]), i),
    "height": lambda it, i: (-it["h"], -it["w"], i),
    "width": lambda it, i: (-it["w"], -it["h"], i),
    "perimeter": lambda it, i: (-(it["w"] + it["h"]), -it["w"] * it["h"], i),
}


def pack_bins(items, limit, order_name, heur, rot):
    """Первый проход: фрагменты по очереди кладутся в бины limit x limit (сначала в первый, пока влезают)."""
    order = sorted(range(len(items)), key=lambda i: ATLAS_ORDERS[order_name](items[i], i))
    bins, place = [], {}
    for i in order:
        it = items[i]
        for bi, bn in enumerate(bins):
            res = bn.insert(it["w"], it["h"], heur, rot)
            if res is not None:
                place[i] = (bi, res[0], res[1], res[2])
                break
        else:
            bn = make_bin(limit, limit)
            res = bn.insert(it["w"], it["h"], heur, rot)
            bins.append(bn)
            place[i] = (len(bins) - 1, res[0], res[1], res[2])
    last = len(bins) - 1
    last_ids = [i for i in order if place[i][0] == last]
    return {"place": place, "nbins": len(bins), "last_ids": last_ids,
            "last_area": sum(items[i]["w"] * items[i]["h"] for i in last_ids),
            "order": order_name, "heur": heur}


def pack_finish(items, limit, pot, rot, prefer_limit, st):
    """Второй проход: последний атлас ужимается до нужного размера."""
    last = st["nbins"] - 1
    w, h, pl = fit_smallest(st["last_ids"], items, limit, pot, st["heur"], rot, prefer_limit, hint=st["place"])
    place = dict(st["place"])
    for i, (x, y, r) in pl.items():
        place[i] = (last, x, y, r)
    return {"place": place, "sizes": [(limit, limit)] * last + [(w, h)]}


def atlas_trials(n_items, rotate):
    """Какие варианты раскладки пробовать: чем больше фрагментов, тем меньше вариантов (время)."""
    heurs = ["bssf", "baf", "blsf", "bl"]
    if n_items <= 150:
        return [(o, h) for o in ATLAS_ORDERS for h in heurs]
    if n_items <= 600:
        return [("maxside", "bssf"), ("area", "bssf"), ("height", "baf"), ("maxside", "baf"), ("perimeter", "blsf")]
    return [("maxside", "bssf"), ("area", "baf"), ("height", "bssf")]


def atlas_plan(sizes, cmds, pad, limit, pot, merge=False, rotate=False, say=None, prefer_limit=True):
    """Раскладка с нуля: каждый фрагмент (прямоугольник по UV команды шейпа, одинаковые - один раз)
    размещается отдельно. merge=True - старый режим со слиянием перекрывающихся фрагментов."""
    items, comp_of = [], {}
    by_tex = {}
    for ci, c in enumerate(cmds):
        if not c["uv"]:
            continue
        W, H = sizes[c["tex"]]
        by_tex.setdefault(c["tex"], []).append((cmd_rect(c["uv"], W, H), ci))
    for tex in sorted(by_tex):
        W, H = sizes[tex]
        uniq = sorted({r for r, _ in by_tex[tex]})
        groups = merge_rects(uniq) if merge else [(r, {r}) for r in uniq]
        base = len(items)
        r2c = {}
        for k, (bb, members) in enumerate(groups):
            for r in members:
                r2c[r] = base + k
            sx0, sy0, sx1, sy1 = max(0, bb[0] - pad), max(0, bb[1] - pad), min(W, bb[2] + pad), min(H, bb[3] + pad)
            items.append({"tex": tex, "bbox": bb, "src": (sx0, sy0, sx1, sy1), "w": sx1 - sx0, "h": sy1 - sy0,
                          "cmds": []})
        for r, ci in by_tex[tex]:
            comp_of[ci] = r2c[r]
            items[r2c[r]]["cmds"].append(ci)
    big = [it for it in items if min(it["w"], it["h"]) > limit or (not rotate and max(it["w"], it["h"]) > limit)
           or max(it["w"], it["h"]) > limit]
    if big:
        raise UserError(f"Есть фрагмент {big[0]['w']}x{big[0]['h']}, он больше лимита атласа {limit}")
    if not items:
        raise UserError("В файле нет ни одной команды шейпа с текстурой - перестраивать нечего")
    stage1 = []
    combos = atlas_trials(len(items), rotate)
    for k, (order_name, heur) in enumerate(combos):
        st = pack_bins(items, limit, order_name, heur, rotate)
        stage1.append(st)
        if say:
            say(f"    вариант {k + 1}/{len(combos)} ({order_name}, {heur}): бинов {st['nbins']}, "
                f"в последнем {st['last_area'] / 1e6:.2f} МП")
    # пересчёт размера последнего атласа - только для двух самых многообещающих вариантов
    stage1.sort(key=lambda st: (st["nbins"], st["last_area"]))
    best = None
    for st in stage1[:2]:
        res = pack_finish(items, limit, pot, rotate, prefer_limit, st)
        metric = (sum(w * h for w, h in res["sizes"]), len(res["sizes"]))
        if best is None or metric < best[0]:
            best = (metric, res, st["order"], st["heur"])
    return {"items": items, "comp_of": comp_of, "place": best[1]["place"], "sizes": best[1]["sizes"],
            "trials": len(combos), "best": f"{best[2]}, {best[3]}", "rotate": rotate, "merge": merge}


def convex_hull(pts):
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def draw_mask(Image, size, polys, dilate):
    """Маска "L" (0 или 255): закрашенные выпуклые оболочки polys (списки точек), расширенные на dilate px."""
    from PIL import ImageDraw, ImageFilter
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    for hull in polys:
        if len(hull) >= 3:
            d.polygon(hull, fill=255)
            d.line(hull + [hull[0]], fill=255, width=1)
        elif len(hull) == 2:
            d.line(hull, fill=255, width=1)
        elif len(hull) == 1:
            d.point(hull, fill=255)
    if dilate > 0:
        mask = mask.filter(ImageFilter.MaxFilter(2 * dilate + 1))
    return mask


def cmd_poly(c, old_size, origin):
    Wo, Ho = old_size
    return convex_hull([(u / 65535 * Wo - origin[0], v / 65535 * Ho - origin[1]) for u, v in c["uv"]])


def atlas_build_pages(Image, plan, old_imgs, cmds, old_sizes, pad, mask=True):
    tr = getattr(Image, "Transpose", Image).TRANSPOSE
    pages = [Image.new("RGBA", s, (0, 0, 0, 0)) for s in plan["sizes"]]
    for k, it in enumerate(plan["items"]):
        bi, dx, dy, r = plan["place"][k]
        crop = old_imgs[it["tex"]].crop(it["src"])
        m = None
        if mask:
            polys = [cmd_poly(cmds[ci], old_sizes[it["tex"]], (it["src"][0], it["src"][1])) for ci in it["cmds"]]
            m = draw_mask(Image, (it["w"], it["h"]), polys, max(1, pad))
        if r:
            crop = crop.transpose(tr)
            m = m.transpose(tr) if m is not None else None
        if m is not None:
            pages[bi].paste(crop, (dx, dy), m)
        else:
            pages[bi].paste(crop, (dx, dy))
    return pages


def atlas_remap(plan, cmds, old_sizes):
    out = []
    for ci, c in enumerate(cmds):
        if not c["uv"]:
            out.append((0, []))
            continue
        k = plan["comp_of"][ci]
        it = plan["items"][k]
        bi, dx, dy, r = plan["place"][k]
        Wn, Hn = plan["sizes"][bi]
        Wo, Ho = old_sizes[c["tex"]]
        uv = []
        for u, v in c["uv"]:
            rx = u / 65535 * Wo - it["src"][0]
            ry = v / 65535 * Ho - it["src"][1]
            px, py = (dx + ry, dy + rx) if r else (dx + rx, dy + ry)
            uv.append((min(65535, max(0, int(round(px / Wn * 65535)))),
                       min(65535, max(0, int(round(py / Hn * 65535))))))
        out.append((bi, uv))
    return out


def atlas_apply_sc2(sc, cmds, new_cmds, pages):
    m = load_model(sc)
    ds, shb = bytearray(sc["chunks"][0]), bytearray(sc["chunks"][3])
    for c, (bi, uv) in zip(cmds, new_cmds):
        at, first, cnt = c["loc"]
        struct.pack_into("<I", shb, at + 4, bi)
        for k, (u, v) in enumerate(uv):
            struct.pack_into("<HH", ds, m["v_start"] + 12 * (first + k) + 8, u, v)
    import flatbuffers
    tex = build_textures(flatbuffers, [[img.tobytes(), 0, img.size[0], img.size[1]] for img in pages])
    chunks = list(sc["chunks"])
    chunks[0], chunks[3], chunks[6] = bytes(ds), bytes(shb), tex
    fd = bytearray(sc["fd"])
    for slot, val in ((4, len(pages)), (9, len(tex) + 4)):
        p = fb_slot(sc["fd"], fb_root(sc["fd"]), slot)
        if p is None:
            raise UserError(f"В дескрипторе нет поля {slot} - файл устроен иначе, чем ожидалось")
        struct.pack_into("<I", fd, p, val)
    return finalize_sc(sc, chunks, bytes(fd))


def atlas_apply_sc1(sc, tex_cont, infos, cmds, new_cmds, pages):
    """Возвращает (байты основного .sc, байты _tex.sc или None)."""
    sc1_require_writable(sc)
    if tex_cont is not None:
        sc1_require_writable({"cont": tex_cont, "mdl": {"counts": [0] * 6, "reserved": b"", "ids": [],
                                                       "names": [], "tags": [], "rest": b""},
                              "_rt": True})
    m = sc1_load_model(sc)
    tags = [list(t) for t in sc["mdl"]["tags"]]
    per_shape = {}
    for c, (bi, uv) in zip(cmds, new_cmds):
        si, ci = c["loc"]
        per_shape.setdefault(si, {})[ci] = (bi, uv)
    for si, upd in per_shape.items():
        sh = m["shapes"][si]
        if sc1_build_shape(sh["id"], sh["cmds"]) != tags[sh["pos"]][1]:
            raise UserError(f"Шейп {sh['id']} собирается обратно не байт-в-байт - не рискую его менять")
        cmds2 = []
        for ci, c in enumerate(sh["cmds"]):
            bi, uv = upd[ci]
            cmds2.append({"tex": bi, "xy": c["xy"], "uv": [x for pair in uv for x in pair]})
        tags[sh["pos"]][1] = sc1_build_shape(sh["id"], cmds2)
    tex_tag = infos[0]["tag"] if infos else 1
    inline = any(i["inline"] for i in infos)
    positions = [i["pos"] for i in infos]
    heads, bodies = [], []
    for img in pages:
        head = bytes([0]) + struct.pack("<2H", img.size[0], img.size[1])
        heads.append([tex_tag, head + img.tobytes() if inline or tex_cont is None else head])
        bodies.append(head + img.tobytes())
    first = min(positions)
    tags = [t for k, t in enumerate(tags) if k not in set(positions)]
    tags[first:first] = heads
    mdl = dict(sc["mdl"])
    mdl["tags"] = tags
    counts = list(mdl["counts"])
    counts[2] = len(pages)
    mdl["counts"] = counts
    main = sc1_write(sc, sc1_build_stream(mdl))
    tex_bytes = None
    refs = sc1_ext_refs(tex_cont["raw"]) if tex_cont is not None else []
    if refs and not inline:
        ext = tex_cont.get("ext") or []
        names, out_items = [], []
        base_name = refs[0]["name"]
        stem, extn = os.path.splitext(base_name)
        stem = re.sub(r"_\d+$", "", stem)
        for k, img in enumerate(pages):
            name = refs[k]["name"] if k < len(refs) else f"{stem}_{k}{extn}"
            names.append({"name": name, "pf": 0, "w": img.size[0], "h": img.size[1]})
            tpl = ext[k].get("path") if k < len(ext) else (ext[0].get("path") if ext else None)
            out_items.append({"name": name, "w": img.size[0], "h": img.size[1], "rgba": img.tobytes(), "template": tpl})
        tex_bytes = sc1_write({"cont": tex_cont}, sc1_build_ext_tex_raw(names))
        tex_cont["ext_out"] = out_items
    elif tex_cont is not None and not inline:
        raw = b"".join(bytes([tex_tag]) + struct.pack("<i", len(b)) + b for b in bodies) + bytes([0]) + struct.pack("<i", 0)
        tex_bytes = sc1_write({"cont": tex_cont}, raw)
    return main, tex_bytes


def atlas_verify(Image, kind, main_bytes, tex_bytes, cmds, new_cmds, plan, old_imgs, old_sizes, pages, pad):
    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    tr = getattr(Image, "Transpose", Image).TRANSPOSE
    new = open_sc_bytes(main_bytes)
    if new.get("kind") != kind:
        fail("результат читается как другой формат")
    if kind == "sc1":
        tex_cont2 = sc1_container(tex_bytes) if tex_bytes else None
        if tex_cont2 is not None and tex_cont2["md5_ok"] is not True:
            fail("MD5 файла текстур не совпал")
        if new["cont"]["md5_ok"] is not True:
            fail("MD5 основного файла не совпал")
        if tex_cont2 is not None and sc1_ext_refs(tex_cont2["raw"]):
            tex_cont2["ext"] = [{"w": img.size[0], "h": img.size[1], "rgba": img.tobytes()} for img in pages]
        infos2 = sc1_tex_infos(new, tex_cont2)
    else:
        infos2 = sc2_tex_infos(new)
    if len(infos2) != len(pages):
        fail("число атласов в файле не совпало с планом")
    for i, (info, img) in enumerate(zip(infos2, pages)):
        if (info["w"], info["h"]) != img.size or info["payload"] != img.tobytes():
            fail(f"атлас {i} в файле не совпал с собранным")
    cmds2 = atlas_collect(new)
    if len(cmds2) != len(cmds):
        fail("число команд шейпов изменилось")
    for c2, (bi, uv) in zip(cmds2, new_cmds):
        if c2["uv"] != uv or (uv and c2["tex"] != bi):
            fail("UV или номер атласа в файле не совпали с планом")
    # пиксели внутри каждой фигуры: те же, что в исходном атласе (проверка не зависит от масок и слияния)
    for ci, c in enumerate(cmds):
        if not c["uv"]:
            continue
        it = plan["items"][plan["comp_of"][ci]]
        bi, dx, dy, r = plan["place"][plan["comp_of"][ci]]
        size_o = old_sizes[c["tex"]]
        x0, y0, x1, y1 = cmd_rect(c["uv"], *size_o)
        w_, h_ = x1 - x0, y1 - y0
        pm = draw_mask(Image, (w_, h_), [cmd_poly(c, size_o, (x0, y0))], 0)
        blank = Image.new("RGBA", (w_, h_), (0, 0, 0, 0))
        old_part = blank.copy()
        old_part.paste(old_imgs[c["tex"]].crop((x0, y0, x1, y1)), (0, 0), pm)
        ox, oy = x0 - it["src"][0], y0 - it["src"][1]
        nx, ny, nw, nh = (dx + oy, dy + ox, h_, w_) if r else (dx + ox, dy + oy, w_, h_)
        reg = pages[bi].crop((nx, ny, nx + nw, ny + nh))
        if r:
            reg = reg.transpose(tr)
        new_part = blank.copy()
        new_part.paste(reg, (0, 0), pm)
        if old_part.tobytes() != new_part.tobytes():
            fail(f"пиксели фигуры команды #{ci} не совпали с оригиналом")


def atlas_prepare(sc, tex_cont, limit=ATLAS_LIMIT, pad=2, pot=False, merge="auto", rotate=False, say=None,
                  prefer_limit=True):
    """Читает текстуры и считает раскладку. Файлы не пишет."""
    from PIL import Image
    kind = sc.get("kind")
    if kind == "sc1":
        infos = sc1_tex_infos(sc, tex_cont)
        if any(not i["payload"] for i in infos):
            raise UserError("У SC1 нет пикселей текстур: положи рядом файл <имя>_tex.sc")
    else:
        if sc["version"] != 5 or len(sc["chunks"]) != 7:
            raise UserError("Перестройка атласов умеет только SC2 версии 5 с 7 блоками")
        infos = sc2_tex_infos(sc)
    cmds = atlas_collect(sc)
    for c in cmds:
        if c["uv"] and c["tex"] >= len(infos):
            raise UserError(f"Команда шейпа ссылается на несуществующий атлас {c['tex']}")
    used = sorted({c["tex"] for c in cmds if c["uv"]})
    old_imgs, old_sizes = {}, {}
    for t in used:
        img, why = texture_image(Image, infos[t], {})
        if img is None:
            raise UserError(f"Атлас {t} не удалось распаковать: {why}")
        old_imgs[t], old_sizes[t] = img, img.size
    if merge == "auto":
        plan_a = atlas_plan(old_sizes, cmds, pad, limit, pot, False, rotate, say, prefer_limit)
        plan_b = atlas_plan(old_sizes, cmds, pad, limit, pot, True, rotate, say, prefer_limit)
        area = lambda p: (sum(w * h for w, h in p["sizes"]), len(p["sizes"]))
        plan = plan_a if area(plan_a) <= area(plan_b) else plan_b
        plan["auto_note"] = (f"автовыбор: с нуля {area(plan_a)[0] / 1e6:.2f} МП, со слиянием перекрытий "
                             f"{area(plan_b)[0] / 1e6:.2f} МП -> взят режим «"
                             f"{'с нуля' if plan is plan_a else 'со слиянием'}»")
    else:
        plan = atlas_plan(old_sizes, cmds, pad, limit, pot, merge, rotate, say, prefer_limit)
    return {"sc": sc, "tex_cont": tex_cont, "kind": kind, "infos": infos, "cmds": cmds, "used": used,
            "old_imgs": old_imgs, "old_sizes": old_sizes, "plan": plan, "pad": pad, "limit": limit,
            "old_px": sum(w * h for w, h in old_sizes.values()),
            "new_px": sum(w * h for w, h in plan["sizes"]),
            "need_px": sum(it["w"] * it["h"] for it in plan["items"])}


def atlas_clean_and_prepare(sc, tex_cont, gc=True, **kw):
    """Сначала (если gc) выбрасывает объекты, недостижимые ни от одного ExportName, потом считает раскладку
    атласов только под оставшиеся шейпы. Возвращает состояние для atlas_finish."""
    gc_plan = None
    if gc:
        data, gc_plan = gc_unused(sc)
        if data is not None:
            sc = open_sc_bytes(data, sc.get("path", ""))
    st = atlas_prepare(sc, tex_cont, **kw)
    st["gc_plan"] = gc_plan
    return st


def atlas_finish(st, mask=True):
    """Собирает атласы, пишет в байты и проверяет. Возвращает (основной файл, _tex.sc или None)."""
    from PIL import Image
    plan = st["plan"]
    pages = atlas_build_pages(Image, plan, st["old_imgs"], st["cmds"], st["old_sizes"], st["pad"], mask)
    new_cmds = atlas_remap(plan, st["cmds"], st["old_sizes"])
    if st["kind"] == "sc1":
        main_b, tex_b = atlas_apply_sc1(st["sc"], st["tex_cont"], st["infos"], st["cmds"], new_cmds, pages)
    else:
        main_b, tex_b = atlas_apply_sc2(st["sc"], st["cmds"], new_cmds, pages), None
    atlas_verify(Image, st["kind"], main_b, tex_b, st["cmds"], new_cmds, plan, st["old_imgs"],
                 st["old_sizes"], pages, st["pad"])
    return main_b, tex_b


def atlas_summary(st):
    plan = st["plan"]
    full = plan["sizes"][:-1]
    desc = (f"{len(full)} × {st['limit']}x{st['limit']} + " if full else "") + \
        f"{plan['sizes'][-1][0]}x{plan['sizes'][-1][1]}"
    gc = st.get("gc_plan")
    gc_line = []
    if gc and not gc_nothing(gc):
        gc_line = [f"Выброшено объектов, недостижимых ни от одного ExportName: клипов {len(gc['del_clips'])}, "
                   f"шейпов {len(gc['del_shapes'])}" + (f", текстовых полей {len(gc['del_tfs'])}" if gc["del_tfs"] else "")]
    return gc_line + [f"Фрагментов: {len(plan['items'])} (из {len(st['cmds'])} команд шейпов), "
            f"варианты раскладки: {plan['trials']}, лучший: {plan['best']}",
            f"Было атласов: {len(st['used'])} ({st['old_px'] / 1e6:.1f} МП), нужно пикселей под фигуры: "
            f"{st['need_px'] / 1e6:.1f} МП, станет атласов: {len(plan['sizes'])} ({st['new_px'] / 1e6:.1f} МП)",
            f"Размеры новых атласов: {desc}"] + ([f"Режим: {plan['auto_note']}"] if plan.get("auto_note") else [])


def write_ext_outputs(tex_cont, out_path):
    """Записывает внешние текстуры (.sctx/.zktx), которые подготовила перестройка атласов, рядом с результатом."""
    items = (tex_cont or {}).get("ext_out") or []
    out = []
    for it in items:
        path = os.path.join(os.path.dirname(os.path.abspath(out_path)), it["name"])
        print(gray(f"  Кодирую {it['name']} ({it['w']}x{it['h']}, ASTC; на больших текстурах это минуты)..."))
        same = write_ext_texture(path, it["rgba"], it["w"], it["h"], template=it["template"])
        saved(path)
        out.append(path)
        if same:
            print(gray("    файл не менялся (картинка та же), оставлен как был"))
        elif it["w"] * it["h"] <= 4_000_000:
            try:
                import numpy as np
                back = np.frombuffer(read_ext_texture(path)["rgba"], np.uint8).astype(float)
                src_px = np.frombuffer(it["rgba"], np.uint8).astype(float)
                m = ((back - src_px) ** 2).mean()
                print(gray(f"    сжатие ASTC с потерями: PSNR ~{99 if m == 0 else 10 * np.log10(255 ** 2 / m):.1f} дБ"))
            except Exception:
                pass
    if items:
        print(yellow("  ⚠ Внешние текстуры закодированы встроенным энкодером ASTC: он проще astcenc, поэтому "
                     "картинка чуть хуже (блоки, которые не менялись, взяты из оригинала без потерь). "
                     "Как это выглядит в игре, хаб проверить не может."))
    return out



def flow_atlas():
    ensure_deps()
    src = resolve_input("Какой .sc перестраивать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    kind = sc.get("kind")
    tex_cont, tex_path = None, None
    if kind == "sc1":
        tex_path, tex_cont = load_tex_cont(src)
    try:
        limit = int(float(ask("Максимальный размер атласа в пикселях", str(ATLAS_LIMIT)).replace(",", ".")))
        pad = int(float(ask("Отступ вокруг каждого фрагмента, px", "2").replace(",", ".")))
    except ValueError:
        raise UserError("Размер и отступ должны быть числами")
    if not 64 <= limit <= 16384 or not 0 <= pad <= 64:
        raise UserError("Размер атласа должен быть от 64 до 16384, отступ от 0 до 64")
    print("  Режим: 1 - с нуля, каждый фрагмент отдельно; 2 - сливать перекрывающиеся фрагменты;")
    print("         3 - автоматически: посчитать оба и взять тот, где атласы меньше")
    mode = ask("Режим", "3")
    merge = {"1": False, "2": True}.get(mode, "auto")
    rotate = ask_yes_no("Разрешить поворот фрагментов на 90° ради плотности", False)
    mask = ask_yes_no("Выбросить пиксели вне фигур шейпов (контур + отступ), в том числе остатки удалённого", True)
    gc = ask_yes_no("Выбросить объекты, до которых не дойти ни от одного ExportName (клипы без экспорта, на "
                    "которые никто не ссылается, их шейпы и куски атласа)", True)
    print("  Размеры атласов: 1 - впритык, одна сторона " + str(limit) + " если содержимого хватает; "
          "2 - степени двойки")
    pot = ask("Размеры", "1") == "2"
    print(gray("  Распаковываю текстуры и ищу лучшую раскладку (на больших файлах это до минуты)..."))
    t0 = time.time()
    st = atlas_clean_and_prepare(sc, tex_cont, gc, limit=limit, pad=pad, pot=pot, merge=merge, rotate=rotate)
    line()
    print(f"  Формат: {'SC1' if kind == 'sc1' else 'SC2'}, атласов в файле: {len(st['infos'])}"
          + (f", не используются: {len(st['infos']) - len(st['used'])} (будут выброшены)" if len(st["used"]) < len(st["infos"]) else ""))
    for l in atlas_summary(st):
        print("  " + l)
    print(gray(f"  Раскладка заняла {time.time() - t0:.1f} с, в памяти игры (RGBA8): ~{st['new_px'] * 4 / 1048576:.0f} МБ"))
    if any(ktx_info(st["infos"][t]["payload"]) for t in st["used"]):
        print(yellow("  ⚠ Исходные текстуры в сжатом формате (ASTC): новые пишутся как сырой RGBA8, "
                     "файл и память игры станут заметно больше."))
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    tex_out = None
    if kind == "sc1" and tex_cont is not None and not any(i["inline"] for i in st["infos"]):
        tex_out = os.path.splitext(out_path)[0] + "_tex.sc"
        print(gray(f"  Текстуры SC1 будут записаны отдельным файлом: {os.path.basename(tex_out)}"))
    worse = st["new_px"] > st["old_px"]
    if worse:
        print(yellow(f"  ⚠ Новые атласы получаются больше прежних ({st['new_px'] / 1e6:.1f} МП против "
                     f"{st['old_px'] / 1e6:.1f} МП). Попробуй размер «впритык»."))
    if not ask_yes_no("Продолжить", not worse):
        raise UserError("Отменено")
    print(gray("  Собираю атласы и файл..."))
    main_b, tex_b = atlas_finish(st, mask)
    with open(out_path, "wb") as fh:
        fh.write(main_b)
    saved(out_path)
    results = [out_path]
    if tex_b is not None and tex_out:
        with open(tex_out, "wb") as fh:
            fh.write(tex_b)
        saved(tex_out)
        results.append(tex_out)
    results += write_ext_outputs(tex_cont, out_path)
    print(green("  ✔ Проверка пройдена: ") + gray("пиксели внутри каждой фигуры и все UV совпали с исходными, "
                                                   "остальное содержимое файла не менялось"))
    return results


# ================================================================ удаление ExportName'ов вместе с клипами и шейпами, которые нужны только им
#
# Удаляется всё, до чего можно дойти от удаляемых экспортов и до чего НЕЛЬЗЯ дойти от оставшихся:
#   клипы (через вложенные клипы), шейпы и текстовые поля (SC2). Всё, что нужно ещё какому-то
#   оставшемуся экспорту или клипу, остаётся. Клипы, до которых вообще не дойти ни от какого экспорта
#   (бесхозные), тоже считаются "живыми", чтобы не оставить у них висящих ссылок.
# SC2: векторы клипов, шейпов, текстовых полей, экспортов уплотняются на месте (длина вектора
# уменьшается, хвост зануляется), счётчики в дескрипторе обновляются, кадры и вершины удалённых
# клипов и шейпов зануляются. Матрицы и цвета в банках, строки и текстуры остаются как были
# (их индексы общие, сжатие бесплатно уменьшит размер); освободившиеся фрагменты атласов можно
# убрать потом пунктом "Перестроить атласы".
# SC1: удаляются теги шейпов и клипов, обновляются шапка, счётчики и хвост файла.

def reach_from(m, roots):
    """От корневых id клипов: (множество id клипов, шейпов, текстовых полей), в том числе сами корни."""
    clip_by_id = {c["id"]: c for c in m["clips"]}
    shape_ids = {s["id"] for s in m["shapes"]}
    tf_ids = set(m.get("tf_bounds", {}))
    clips, shapes, tfs = set(), set(), set()
    for r in roots:
        if r in shape_ids:
            shapes.add(r)
        elif r in tf_ids:
            tfs.add(r)
    stack = [r for r in roots if r in clip_by_id]
    while stack:
        cid = stack.pop()
        if cid in clips:
            continue
        clips.add(cid)
        for ch in clip_by_id[cid]["children"]:
            if ch in clip_by_id:
                if ch not in clips:
                    stack.append(ch)
            elif ch in shape_ids:
                shapes.add(ch)
            elif ch in tf_ids:
                tfs.add(ch)
    return clips, shapes, tfs


def plan_delete(m, exports, names):
    all_names = [e["name"] for e in exports]
    unknown = [n for n in names if n not in all_names]
    if unknown:
        raise UserError("Нет таких ExportName'ов в файле: " + ", ".join(repr(x) for x in unknown[:8]))
    del_names = set(names)
    keep_exports = [e for e in exports if e["name"] not in del_names]
    gone_exports = [e for e in exports if e["name"] in del_names]
    if not gone_exports:
        raise UserError("Не выбрано ни одного ExportName для удаления")
    if not keep_exports:
        raise UserError("Нельзя удалить все ExportName'ы: в файле ничего бы не осталось")
    all_clips, _s, _t = reach_from(m, [e["id"] for e in exports])
    orphans = [c["id"] for c in m["clips"] if c["id"] not in all_clips]
    keep_c, keep_s, keep_t = reach_from(m, [e["id"] for e in keep_exports] + orphans)
    gone_c, gone_s, gone_t = reach_from(m, [e["id"] for e in gone_exports])
    del_c, del_s, del_t = gone_c - keep_c, gone_s - keep_s, gone_t - keep_t
    return {"names": del_names, "gone_exports": gone_exports, "keep_exports": keep_exports,
            "del_clips": del_c, "del_shapes": del_s, "del_tfs": del_t,
            "shared_clips": len(gone_c & keep_c), "shared_shapes": len(gone_s & keep_s),
            "orphans": len(orphans)}


def vec_compact(buf, field_pos, keep, esize, offsets=False):
    """Оставляет в векторе только элементы с индексами keep (по порядку), сдвигая их к началу.
    Длина вектора уменьшается, хвост зануляется. offsets=True - элементы это uoffset на таблицы."""
    tgt = field_pos + u32(buf, field_pos)
    n = u32(buf, tgt)
    if keep and max(keep) >= n:
        raise UserError("Индекс вне вектора - файл устроен иначе, чем ожидалось")
    start = tgt + 4
    if offsets:
        targets = [start + 4 * i + u32(buf, start + 4 * i) for i in keep]
        for j, t in enumerate(targets):
            pos = start + 4 * j
            if t <= pos:
                raise UserError("Не могу сдвинуть вектор: ссылка указывала бы назад")
            struct.pack_into("<I", buf, pos, t - pos)
    else:
        data = [bytes(buf[start + esize * i:start + esize * (i + 1)]) for i in keep]
        for j, d in enumerate(data):
            buf[start + esize * j:start + esize * (j + 1)] = d
    new_end, old_end = start + esize * len(keep), start + esize * n
    buf[new_end:old_end] = bytes(old_end - new_end)
    struct.pack_into("<I", buf, tgt, len(keep))


def plan_gc(m, exports):
    """Что не нужно ни одному ExportName: клипы, шейпы и текстовые поля, до которых не дойти от экспортов."""
    keep_c, keep_s, keep_t = reach_from(m, [e["id"] for e in exports])
    return {"names": set(), "gone_exports": [], "keep_exports": list(exports),
            "del_clips": {c["id"] for c in m["clips"]} - keep_c,
            "del_shapes": {s["id"] for s in m["shapes"]} - keep_s,
            "del_tfs": set(m.get("tf_bounds", {})) - keep_t,
            "shared_clips": 0, "shared_shapes": 0, "orphans": 0}


def gc_nothing(plan):
    return not (plan["del_clips"] or plan["del_shapes"] or plan["del_tfs"])


def delete_exports_sc2(sc, names):
    if sc["version"] != 5 or len(sc["chunks"]) != 7 or sc["leftover"]:
        raise UserError("Удаление умеет только SC2 версии 5 с 7 блоками (как у твоего файла)")
    m = load_model(sc)
    exports, _strings = get_exports(sc)
    plan = plan_delete(m, exports, names)
    return remove_objects_sc2(sc, m, exports, plan), plan


def gc_unused_sc2(sc):
    if sc["version"] != 5 or len(sc["chunks"]) != 7 or sc["leftover"]:
        raise UserError("Очистка умеет только SC2 версии 5 с 7 блоками (как у твоего файла)")
    m = load_model(sc)
    exports, _strings = get_exports(sc)
    plan = plan_gc(m, exports)
    if gc_nothing(plan):
        return None, plan
    return remove_objects_sc2(sc, m, exports, plan), plan


def remove_objects_sc2(sc, m, exports, plan):
    if fd_export_names(sc["fd"]) != [e["name"] for e in exports]:
        raise UserError("Список экспортов в дескрипторе не совпал с блоком ExportNames - не рискую менять")
    ds = bytearray(sc["chunks"][0])
    en, shb, mcb = bytearray(sc["chunks"][1]), bytearray(sc["chunks"][3]), bytearray(sc["chunks"][4])
    tfc = bytearray(sc["chunks"][2])
    fd = bytearray(sc["fd"])

    keep_ex = [i for i, e in enumerate(exports) if e["name"] not in plan["names"]]
    et = fb_root(en)
    vec_compact(en, fb_slot(en, et, 0), keep_ex, 2)
    vec_compact(en, fb_slot(en, et, 1), keep_ex, 4)
    ft = fb_root(fd)
    vec_compact(fd, fb_slot(fd, ft, 10), keep_ex, 4, offsets=True)

    # номер экспорта у клипа (слот 1) = позиция имени в списке + 1: после удаления позиции сдвигаются
    pos_new = export_positions([exports[i] for i in keep_ex])
    mc_old = sc["chunks"][4]
    tabs_all = fb_table_vec(mc_old, fb_slot(mc_old, fb_root(mc_old), 0))
    for clip in m["clips"]:
        if clip["id"] in plan["del_clips"]:
            continue
        p1 = fb_slot(mc_old, tabs_all[clip["index"]], 1)
        want = pos_new[clip["id"]] + 1 if clip["id"] in pos_new else 0
        if p1 is not None:
            struct.pack_into("<I", mcb, p1, want)
        elif clip["id"] in pos_new:
            raise UserError(f"У экспортируемого клипа {clip['id']} нет поля номера экспорта - не рискую менять")

    keep_sh = [s["index"] for s in m["shapes"] if s["id"] not in plan["del_shapes"]]
    vec_compact(shb, fb_slot(shb, fb_root(shb), 0), keep_sh, 4, offsets=True)
    keep_cl = [c["index"] for c in m["clips"] if c["id"] not in plan["del_clips"]]
    vec_compact(mcb, fb_slot(mcb, fb_root(mcb), 0), keep_cl, 4, offsets=True)

    recs, _s2 = read_textfields(sc)
    if plan["del_tfs"]:
        keep_tf = [r["index"] for r in recs if r["id"] not in plan["del_tfs"]]
        vec_compact(tfc, fb_slot(tfc, fb_root(tfc), 0), keep_tf, TF_REC)

    for slot, delta in ((2, len(plan["del_shapes"])), (3, len(plan["del_clips"])), (5, len(plan["del_tfs"]))):
        if not delta:
            continue
        p = fb_slot(sc["fd"], ft, slot)
        if p is None:
            raise UserError(f"В дескрипторе нет поля {slot} - файл устроен иначе, чем ожидалось")
        struct.pack_into("<I", fd, p, u32(sc["fd"], p) - delta)

    # кадры удалённых клипов и вершины удалённых шейпов - нули
    for c in m["clips"]:
        if c["id"] in plan["del_clips"] and c["n_el"]:
            a = m["e_start"] + 2 * c["offset"]
            ds[a:a + 6 * c["n_el"]] = bytes(6 * c["n_el"])
    for s in m["shapes"]:
        if s["id"] in plan["del_shapes"]:
            for v in set(s["verts"]):
                a = m["v_start"] + 12 * v
                ds[a:a + 12] = bytes(12)

    chunks = list(sc["chunks"])
    chunks[0], chunks[1], chunks[2], chunks[3], chunks[4] = bytes(ds), bytes(en), bytes(tfc), bytes(shb), bytes(mcb)
    data = finalize_sc(sc, chunks, bytes(fd))
    verify_delete(sc, data, plan, m, recs)
    return data


def verify_delete(old, data, plan, mo, recs_old):
    new = parse_sc(data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if len(new["chunks"]) != 7 or new["leftover"]:
        fail("структура блоков нарушена")
    for i in (5, 6):
        if new["chunks"][i] != old["chunks"][i]:
            fail(f"блок {CHUNK_NAMES[i]} изменился")
    if len(new["chunks"][0]) != len(old["chunks"][0]):
        fail("длина DataStorage изменилась")
    mn = load_model(new)
    exports_o, _ = get_exports(old)
    exports_n, _ = get_exports(new)
    want_ex = [(e["id"], e["name"]) for e in exports_o if e["name"] not in plan["names"]]
    if [(e["id"], e["name"]) for e in exports_n] != want_ex:
        fail("список ExportName'ов не совпал с ожидаемым")
    if fd_export_names(new["fd"]) != [n for _i, n in want_ex]:
        fail("имена в дескрипторе не совпали")
    # шейпы и клипы: остались ровно нужные, и они не изменились
    if [s["id"] for s in mn["shapes"]] != [s["id"] for s in mo["shapes"] if s["id"] not in plan["del_shapes"]]:
        fail("набор шейпов не совпал с ожидаемым")
    if [c["id"] for c in mn["clips"]] != [c["id"] for c in mo["clips"] if c["id"] not in plan["del_clips"]]:
        fail("набор клипов не совпал с ожидаемым")
    ds_o, ds_n = mo["ds"], mn["ds"]
    kept_shapes_o = [s for s in mo["shapes"] if s["id"] not in plan["del_shapes"]]
    for so, sn in zip(kept_shapes_o, mn["shapes"]):
        po = [ds_o[mo["v_start"] + 12 * v:mo["v_start"] + 12 * v + 12] for v in so["verts"]]
        pn = [ds_n[mn["v_start"] + 12 * v:mn["v_start"] + 12 * v + 12] for v in sn["verts"]]
        if po != pn:
            fail(f"вершины оставшегося шейпа {so['id']} изменились")
    cache_o, cache_n = {}, {}
    kept_clips_o = [c for c in mo["clips"] if c["id"] not in plan["del_clips"]]
    for co, cn in zip(kept_clips_o, mn["clips"]):
        if (co["id"], co["bank"], co["offset"], co["n_el"], co["children"], co["fps"], co["nframes"]) != \
                (cn["id"], cn["bank"], cn["offset"], cn["n_el"], cn["children"], cn["fps"], cn["nframes"]):
            fail(f"описание оставшегося клипа {co['id']} изменилось")
        if clip_flat(mo, co) != clip_flat(mn, cn) or clip_effective(mo, co, cache_o) != clip_effective(mn, cn, cache_n) \
                or clip_colors(mo, co) != clip_colors(mn, cn):
            fail(f"кадры оставшегося клипа {co['id']} изменились")
    # висящих ссылок нет
    alive = {c["id"] for c in mn["clips"]} | {s["id"] for s in mn["shapes"]} | set(mn["tf_bounds"])
    removed = plan["del_clips"] | plan["del_shapes"] | plan["del_tfs"]
    for c in mn["clips"]:
        bad = [ch for ch in c["children"] if ch in removed]
        if bad:
            fail(f"у оставшегося клипа {c['id']} остались ссылки на удалённые объекты {bad[:3]}")
    for e in exports_n:
        if e["id"] not in alive:
            fail(f"экспорт {e['name']} ссылается на удалённый клип")
    pos_n = export_positions(exports_n)
    for sp in read_clip_specs(new):
        want_s = pos_n[sp["id"]] + 1 if sp["id"] in pos_n else None
        if sp["slot1"] != want_s and not (want_s is None and sp["slot1"] in (None, 0)):
            fail(f"номер экспорта у клипа {sp['id']} неверный после сдвига позиций")
    # текстовые поля
    rn, _ = read_textfields(new)
    keep_recs = [r for r in recs_old if r["id"] not in plan["del_tfs"]]
    if [(r["id"], r["bounds"], r["size"], r["font"]) for r in rn] != [(r["id"], r["bounds"], r["size"], r["font"]) for r in keep_recs]:
        fail("оставшиеся текстовые поля изменились")
    # дескриптор
    o_fd, n_fd = old["fd"], new["fd"]
    for slot, delta in ((2, len(plan["del_shapes"])), (3, len(plan["del_clips"])), (5, len(plan["del_tfs"]))):
        po, pn = fb_slot(o_fd, fb_root(o_fd), slot), fb_slot(n_fd, fb_root(n_fd), slot)
        if delta == 0:
            if (po is None) != (pn is None) or (po is not None and u32(n_fd, pn) != u32(o_fd, po)):
                fail(f"счётчик {slot} в дескрипторе изменился без причины")
            continue
        if po is None or pn is None or u32(n_fd, pn) != u32(o_fd, po) - delta:
            fail(f"счётчик {slot} в дескрипторе неверный")
    if u32(n_fd, fb_slot(n_fd, fb_root(n_fd), 8)) != 4 + len(new["chunks"][0]):
        fail("смещение ресурсов в дескрипторе неверное")


SC1_OTHER_CLIP_TAGS = (3, 10, 12, 14, 35)


def sc1_require_known_clips(sc):
    """Хаб разбирает клипы SC1 только в виде тега 49. Если есть клипы других тегов, он не видит их
    детей, а значит не может честно сказать, что шейп никому не нужен."""
    found = sorted({t for t, _b in sc["mdl"]["tags"] if t in SC1_OTHER_CLIP_TAGS})
    if found:
        raise UserError(f"В файле есть клипы с тегами {found}, которые хаб не разбирает: он не видит их детей "
                        f"и не может безопасно решить, какие шейпы никому не нужны")


def delete_exports_sc1(sc, names):
    sc1_require_writable(sc)
    sc1_require_known_clips(sc)
    m = sc1_load_model(sc)
    exports, _ = sc1_exports(sc)
    plan = plan_delete(m, exports, names)
    return remove_objects_sc1(sc, m, plan), plan


def gc_unused_sc1(sc):
    sc1_require_writable(sc)
    sc1_require_known_clips(sc)
    m = sc1_load_model(sc)
    exports, _ = sc1_exports(sc)
    plan = plan_gc(m, exports)
    if gc_nothing(plan):
        return None, plan
    return remove_objects_sc1(sc, m, plan), plan


def remove_objects_sc1(sc, m, plan):
    mdl = dict(sc["mdl"])
    drop_pos = {s["pos"] for s in m["shapes"] if s["id"] in plan["del_shapes"]}
    drop_pos |= {c["pos"] for c in m["clips"] if c["id"] in plan["del_clips"]}
    mdl["tags"] = [t for k, t in enumerate(sc["mdl"]["tags"]) if k not in drop_pos]
    exports, _ = sc1_exports(sc)
    keep_ex = [i for i, e in enumerate(exports) if e["name"] not in plan["names"]]
    mdl["ids"] = [sc["mdl"]["ids"][i] for i in keep_ex]
    mdl["names"] = [sc["mdl"]["names"][i] for i in keep_ex]
    counts = list(mdl["counts"])
    counts[0] -= len(plan["del_shapes"])
    counts[1] -= len(plan["del_clips"])
    mdl["counts"] = counts
    foot = sc1_footer(sc["cont"]["tail"])
    new_tail = None
    if foot:
        ents, rest = foot
        new_tail = b"START" + b"".join(n.encode("utf-8") + b"\0\x10" + hsh for n, hsh in ents
                                      if n not in plan["names"]) + rest
    data = sc1_write(sc, sc1_build_stream(mdl), new_tail)
    verify_delete_sc1(sc, data, plan, m)
    return data


def verify_delete_sc1(old, data, plan, mo):
    new = open_sc_bytes(data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if new["kind"] != "sc1" or new["cont"]["md5_ok"] is not True:
        fail("результат не читается как SC1 или MD5 не совпал")
    ex_o, _ = sc1_exports(old)
    ex_n, _ = sc1_exports(new)
    if [(e["id"], e["name"]) for e in ex_n] != [(e["id"], e["name"]) for e in ex_o if e["name"] not in plan["names"]]:
        fail("список ExportName'ов не совпал с ожидаемым")
    mn = sc1_load_model(new)
    if [s["id"] for s in mn["shapes"]] != [s["id"] for s in mo["shapes"] if s["id"] not in plan["del_shapes"]]:
        fail("набор шейпов не совпал с ожидаемым")
    if [c["id"] for c in mn["clips"]] != [c["id"] for c in mo["clips"] if c["id"] not in plan["del_clips"]]:
        fail("набор клипов не совпал с ожидаемым")
    keep = lambda t: t[0] not in (18, 49)
    if [t for t in old["mdl"]["tags"] if keep(t)] != [t for t in new["mdl"]["tags"] if keep(t)]:
        fail("изменились теги, которые не должны были меняться")
    for so, sn in zip([s for s in mo["shapes"] if s["id"] not in plan["del_shapes"]], mn["shapes"]):
        if old["mdl"]["tags"][so["pos"]][1] != new["mdl"]["tags"][sn["pos"]][1]:
            fail(f"оставшийся шейп {so['id']} изменился")
    for co, cn in zip([c for c in mo["clips"] if c["id"] not in plan["del_clips"]], mn["clips"]):
        if old["mdl"]["tags"][co["pos"]][1] != new["mdl"]["tags"][cn["pos"]][1]:
            fail(f"оставшийся клип {co['id']} изменился")
    removed = plan["del_clips"] | plan["del_shapes"]
    for c in mn["clips"]:
        if any(ch in removed for ch in c["children"]):
            fail(f"у оставшегося клипа {c['id']} остались ссылки на удалённые объекты")
    c_o, c_n = old["mdl"]["counts"], new["mdl"]["counts"]
    if c_n[0] != c_o[0] - len(plan["del_shapes"]) or c_n[1] != c_o[1] - len(plan["del_clips"]):
        fail("счётчики в шапке неверные")
    foot_o, foot_n = sc1_footer(old["cont"]["tail"]), sc1_footer(new["cont"]["tail"])
    if foot_o and (not foot_n or [n for n, _h in foot_n[0]] != [n for n, _h in foot_o[0] if n not in plan["names"]]):
        fail("записи в хвосте файла не совпали")


def delete_exports(sc, names):
    if sc.get("kind") == "sc1":
        return delete_exports_sc1(sc, names)
    return delete_exports_sc2(sc, names)


def gc_unused(sc):
    """Выбрасывает всё, до чего не дойти ни от одного ExportName. Возвращает (байты .sc или None, план)."""
    if sc.get("kind") == "sc1":
        return gc_unused_sc1(sc)
    return gc_unused_sc2(sc)


def flow_delete():
    ensure_deps()
    src = resolve_input("Из какого .sc удалять", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    exports, _strings = get_exports(sc)
    names = [e["name"] for e in exports]
    m = load_model(sc)
    print(f"  Формат: {'SC2' if sc.get('kind') != 'sc1' else 'SC1'}, ExportName'ов: {len(names)}, "
          f"клипов: {len(m['clips'])}, шейпов: {len(m['shapes'])}")
    for x in names[:8]:
        print(gray("    " + x))
    if len(names) > 8:
        print(gray(f"    ... ещё {len(names) - 8}"))
    print("  Что удалить: имена через запятую, шаблон со * (emoji_*) или @файл со списком (по имени в строке)")
    raw = ask("ExportName'ы для удаления")
    import fnmatch
    tokens = []
    for tok in re.split(r"[,;\s]+", raw.strip()):
        if not tok:
            continue
        if tok.startswith("@"):
            fpath = unquote(tok[1:])
            if not os.path.isabs(fpath) and not os.path.dirname(fpath):
                fpath = os.path.join(IN_DIR, fpath)
            if not os.path.isfile(fpath):
                raise UserError(f"Нет файла со списком: {fpath}")
            with open(fpath, "r", encoding="utf-8-sig") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        tokens.append(ln)
        else:
            tokens.append(tok)
    chosen = []
    for tok in tokens:
        hits = [n for n in names if fnmatch.fnmatchcase(n, tok)] if any(ch in tok for ch in "*?[") else \
            ([tok] if tok in names else [])
        if not hits:
            near = [n for n in names if tok.lower() in n.lower()][:10]
            hint = ("\n  Похожие:\n    " + "\n    ".join(near)) if near else ""
            raise UserError(f"Не нашёл ExportName «{tok}».{hint}")
        for n in hits:
            if n not in chosen:
                chosen.append(n)
    if not chosen:
        raise UserError("Не выбрано ни одного ExportName")
    plan = plan_delete(m, exports, chosen)
    line()
    print(f"  Будет удалено ExportName'ов: {len(plan['gone_exports'])}")
    for n in chosen[:10]:
        print(gray("    " + n))
    if len(chosen) > 10:
        print(gray(f"    ... ещё {len(chosen) - 10}"))
    print(f"  Вместе с ними удалятся: клипов {len(plan['del_clips'])}, шейпов {len(plan['del_shapes'])}"
          + (f", текстовых полей {len(plan['del_tfs'])}" if plan["del_tfs"] else ""))
    if plan["shared_clips"] or plan["shared_shapes"]:
        print(gray(f"  Останутся, потому что нужны другим: клипов {plan['shared_clips']}, шейпов {plan['shared_shapes']}"))
    if plan["orphans"]:
        print(gray(f"  Бесхозных клипов (на них никто не ссылается) в файле {plan['orphans']}: они не трогаются"))
    rebuild = ask_yes_no("Сразу убрать из атласов куски удалённых объектов (перестроить атласы с нуля)", True)
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if os.path.abspath(out_path) == os.path.abspath(src):
        raise UserError("Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")
    print(gray("  Пересобираю и сжимаю (несколько секунд)..."))
    data, plan = delete_exports(sc, chosen)
    print(green("  ✔ Удаление проверено: ") + gray("все оставшиеся клипы, шейпы и текстовые поля не изменились, "
                                                   "висящих ссылок нет"))
    tex_data, tex_out, ext_cont = None, None, None
    if rebuild:
        try:
            sc_new = open_sc_bytes(data, out_path)
            tex_cont = None
            if sc_new.get("kind") == "sc1":
                _tp, tex_cont = load_tex_cont(src)
            print(gray("  Перестраиваю атласы с нуля (распаковка текстур и поиск раскладки)..."))
            st = atlas_clean_and_prepare(sc_new, tex_cont, True, limit=ATLAS_LIMIT, pad=2, pot=False, merge="auto")
            for l in atlas_summary(st):
                print(gray("  " + l))
            main_b, tex_b = atlas_finish(st, True)
            data, tex_data, ext_cont = main_b, tex_b, tex_cont
            if tex_b is not None:
                tex_out = os.path.splitext(out_path)[0] + "_tex.sc"
            print(green("  ✔ Атласы перестроены и проверены"))
        except UserError as e:
            print(yellow(f"  ⚠ Атласы не перестроены, файл записан с прежними атласами: {e}"))
    with open(out_path, "wb") as fh:
        fh.write(data)
    saved(out_path)
    results = [out_path]
    if tex_data is not None and tex_out:
        with open(tex_out, "wb") as fh:
            fh.write(tex_data)
        saved(tex_out)
        results.append(tex_out)
    results += write_ext_outputs(ext_cont, out_path)
    if not rebuild:
        print(gray("  Куски удалённых объектов остались в атласах: убрать их можно пунктом «Перестроить атласы»."))
    return results


# ================================================================ имена для клипов без ExportName; общие сборщики блоков SC2
#
# Проверено на твоём файле: у клипа с экспортом в таблице есть поле "номер экспорта" (слот 1) = позиция
# имени в списке ExportName'ов + 1 (313 из 313), у клипов без экспорта этого поля нет. Поэтому:
#   * при добавлении имени клипу таблица этого клипа пересобирается (в таблице появляется слот 1);
#   * при удалении экспортов номера у оставшихся клипов пересчитываются.

def fb_build_export_names(fb, ids, refs):
    b = fb.Builder(1024 + 8 * len(ids))
    b.StartVector(2, len(ids), 2)
    for v in reversed(ids):
        b.PrependUint16(v)
    oid = b.EndVector()
    b.StartVector(4, len(refs), 4)
    for v in reversed(refs):
        b.PrependUint32(v)
    nrf = b.EndVector()
    b.StartObject(2)
    b.PrependUOffsetTRelativeSlot(1, nrf, 0)
    b.PrependUOffsetTRelativeSlot(0, oid, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def fb_build_fd_like(fb, old_fd, names, **override):
    """Дескриптор с новым списком имён экспортов. Остальные поля (все по 4 байта) копируются из old_fd;
    override задаёт новые значения для некоторых слотов."""
    ft = fb_root(old_fd)
    slots = {s: u32(old_fd, p) for s, p in fb_slots(old_fd, ft) if s != 10}
    vt = ft - i32(old_fd, ft)
    if u16(old_fd, vt + 2) != 4 + 4 * (len(slots) + 1):
        raise UserError("Дескриптор устроен иначе, чем ожидалось (поля не по 4 байта) - не рискую его пересобирать")
    for k, v in override.items():
        slots[int(k.lstrip("s"))] = v
    b = fb.Builder(2048 + 64 * len(names))
    tabs = []
    for n in names:
        so = b.CreateString(n)
        b.StartObject(1)
        b.PrependUOffsetTRelativeSlot(0, so, 0)
        tabs.append(b.EndObject())
    b.StartVector(4, len(tabs), 4)
    for t in reversed(tabs):
        b.PrependUOffsetTRelative(t)
    ex = b.EndVector()
    b.StartObject(13)
    for s in range(13):
        if s == 10:
            b.PrependUOffsetTRelativeSlot(10, ex, 0)
        elif s in slots:
            b.PrependUint32Slot(s, slots[s], None)
    b.Finish(b.EndObject())
    return bytes(b.Output())


CLIP_KEYS = ("id", "slot1", "fps", "nframes", "flag", "children", "names", "blend", "frames", "offset", "bank", "grid")


def read_clip_specs(sc):
    """Все поля таблиц клипов (None = поля в таблице нет). Неизвестные поля -> отказ."""
    mcb = sc["chunks"][4]
    tabs = fb_table_vec(mcb, fb_slot(mcb, fb_root(mcb), 0))
    out = []
    for t in tabs:
        ps = dict(fb_slots(mcb, t))
        extra = sorted(set(ps) - set(range(12)))
        if extra:
            raise UserError(f"В таблице клипа есть неизвестные поля {extra} - не рискую пересобирать блок клипов")
        sp = {"id": u16(mcb, ps[0]) if 0 in ps else 0,
              "slot1": u32(mcb, ps[1]) if 1 in ps else None,
              "fps": mcb[ps[2]] if 2 in ps else None,
              "nframes": u16(mcb, ps[3]) if 3 in ps else None,
              "flag": mcb[ps[4]] if 4 in ps else None,
              "children": None, "names": None, "blend": None, "frames": None,
              "offset": u32(mcb, ps[9]) if 9 in ps else None,
              "bank": u32(mcb, ps[10]) if 10 in ps else None,
              "grid": u32(mcb, ps[11]) if 11 in ps else None}
        if 5 in ps:
            s, n = fb_vec(mcb, ps[5])
            sp["children"] = list(struct.unpack_from(f"<{n}H", mcb, s)) if n else []
        if 6 in ps:
            s, n = fb_vec(mcb, ps[6])
            sp["names"] = list(struct.unpack_from(f"<{n}I", mcb, s)) if n else []
        if 7 in ps:
            s, n = fb_vec(mcb, ps[7])
            sp["blend"] = list(mcb[s:s + n])
        if 8 in ps:
            s, n = fb_vec(mcb, ps[8])
            sp["frames"] = [struct.unpack_from("<2I", mcb, s + 8 * i) for i in range(n)]
        out.append(sp)
    return out


def fb_build_clips(fb, specs):
    b = fb.Builder(1 << 16)
    offs = []
    for sp in specs:
        vec = {}
        if sp["children"] is not None:
            b.StartVector(2, len(sp["children"]), 2)
            for v in reversed(sp["children"]):
                b.PrependUint16(v)
            vec[5] = b.EndVector()
        if sp["names"] is not None:
            b.StartVector(4, len(sp["names"]), 4)
            for v in reversed(sp["names"]):
                b.PrependUint32(v)
            vec[6] = b.EndVector()
        if sp["blend"] is not None:
            b.StartVector(1, len(sp["blend"]), 1)
            for v in reversed(sp["blend"]):
                b.PrependUint8(v)
            vec[7] = b.EndVector()
        if sp["frames"] is not None:
            b.StartVector(8, len(sp["frames"]), 4)
            for cnt, lab in reversed(sp["frames"]):
                b.Prep(4, 8)
                b.PrependUint32(lab)
                b.PrependUint32(cnt)
            vec[8] = b.EndVector()
        b.StartObject(13)
        b.PrependUint16Slot(0, sp["id"], 0)
        if sp["slot1"] is not None:
            b.PrependUint32Slot(1, sp["slot1"], None)
        if sp["fps"] is not None:
            b.PrependUint8Slot(2, sp["fps"], 0)
        if sp["nframes"] is not None:
            b.PrependUint16Slot(3, sp["nframes"], 0)
        if sp["flag"] is not None:
            b.PrependUint8Slot(4, sp["flag"], 0)
        for slot in (5, 6, 7, 8):
            if slot in vec:
                b.PrependUOffsetTRelativeSlot(slot, vec[slot], 0)
        if sp["offset"] is not None:
            b.PrependUint32Slot(9, sp["offset"], None)
        if sp["bank"] is not None:
            b.PrependUint32Slot(10, sp["bank"], 0)
        if sp.get("grid") is not None:
            b.PrependUint32Slot(11, sp["grid"], None)
        offs.append(b.EndObject())
    b.StartVector(4, len(offs), 4)
    for o in reversed(offs):
        b.PrependUOffsetTRelative(o)
    cv = b.EndVector()
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, cv, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def export_positions(exports):
    """{id клипа: позиция его первого имени в списке}"""
    first = {}
    for pos, e in enumerate(exports):
        first.setdefault(e["id"], pos)
    return first


def check_new_export_names(existing_names, new_names):
    seen = set(existing_names)
    for n in new_names:
        if not n:
            raise UserError("Пустое имя экспорта")
        if "\0" in n or any(ord(c) < 32 for c in n):
            raise UserError(f"В имени «{n}» есть управляющие символы")
        if n in seen:
            raise UserError(f"Имя «{n}» уже занято (или повторяется в списке)")
        seen.add(n)


def add_exports_sc2(sc, additions):
    """additions: {id клипа: новое имя}. Только для клипов, у которых ExportName'а ещё нет."""
    if sc["version"] != 5 or len(sc["chunks"]) != 7 or sc["leftover"]:
        raise UserError("Добавление имён умеет только SC2 версии 5 с 7 блоками")
    import flatbuffers
    m = load_model(sc)
    exports, _strings = get_exports(sc)
    clip_ids = {c["id"] for c in m["clips"]}
    known = clip_ids | {s_["id"] for s_ in m["shapes"]} | set(m["tf_bounds"])
    have = {e["id"] for e in exports}
    for cid in additions:
        if cid not in known:
            raise UserError(f"Нет объекта (клипа, шейпа или текстового поля) с id {cid}")
        if cid in have:
            raise UserError(f"У объекта {cid} уже есть ExportName - для смены имени используй переименование")
    new_names = list(additions.values())
    check_new_export_names([e["name"] for e in exports], new_names)
    if fd_export_names(sc["fd"]) != [e["name"] for e in exports]:
        raise UserError("Список экспортов в дескрипторе не совпал с блоком ExportNames - не рискую менять")
    new_ds, index_of = append_strings(sc["chunks"][0], new_names)
    ids_all = [e["id"] for e in exports] + list(additions)
    refs_all = [e["ref"] for e in exports] + [index_of[n] for n in new_names]
    names_all = [e["name"] for e in exports] + new_names
    pos = export_positions([{"id": i} for i in ids_all])
    specs = read_clip_specs(sc)
    for sp in specs:
        if sp["id"] in pos:
            sp["slot1"] = pos[sp["id"]] + 1
    chunks = list(sc["chunks"])
    chunks[0] = new_ds
    chunks[1] = fb_build_export_names(flatbuffers, ids_all, refs_all)
    chunks[4] = fb_build_clips(flatbuffers, specs)
    fd = fb_build_fd_like(flatbuffers, sc["fd"], names_all)
    data = finalize_sc(sc, chunks, fd)
    verify_add_exports(sc, data, additions, specs)
    return data


def verify_add_exports(old, data, additions, specs_expected):
    new = parse_sc(data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if len(new["chunks"]) != 7 or new["leftover"]:
        fail("структура блоков нарушена")
    for i in (2, 3, 5, 6):
        if new["chunks"][i] != old["chunks"][i]:
            fail(f"блок {CHUNK_NAMES[i]} изменился")
    o_ds, n_ds = old["chunks"][0], new["chunks"][0]
    p0 = fb_slot(o_ds, fb_root(o_ds), 0)
    if n_ds[:p0] != o_ds[:p0] or n_ds[p0 + 4:len(o_ds)] != o_ds[p0 + 4:] or len(n_ds) % 4:
        fail("DataStorage изменился не только в таблице строк")
    eo, so = get_exports(old)
    en, sn = get_exports(new)
    if sn[:len(so)] != so:
        fail("старые строки в таблице изменились")
    want = [(e["id"], e["name"]) for e in eo] + list(additions.items())
    if [(e["id"], e["name"]) for e in en] != want:
        fail("список ExportName'ов не совпал с ожидаемым")
    if fd_export_names(new["fd"]) != [n for _i, n in want]:
        fail("имена в дескрипторе не совпали")
    o_fd, n_fd = old["fd"], new["fd"]
    for slot in (0, 1, 2, 3, 4, 5, 9):
        po, pn = fb_slot(o_fd, fb_root(o_fd), slot), fb_slot(n_fd, fb_root(n_fd), slot)
        if (po is None) != (pn is None) or (po is not None and u32(o_fd, po) != u32(n_fd, pn)):
            fail(f"поле дескриптора {slot} изменилось")
    if u32(n_fd, fb_slot(n_fd, fb_root(n_fd), 8)) != 4 + len(n_ds):
        fail("смещение ресурсов в дескрипторе неверное")
    if read_clip_specs(new) != specs_expected:
        fail("таблицы клипов не совпали с ожидаемыми")
    mo, mn = load_model(old), load_model(new)
    cache_o, cache_n = {}, {}
    for co, cn in zip(mo["clips"], mn["clips"]):
        if (co["id"], co["bank"], co["offset"], co["n_el"], co["children"], co["fps"], co["nframes"]) != \
                (cn["id"], cn["bank"], cn["offset"], cn["n_el"], cn["children"], cn["fps"], cn["nframes"]):
            fail(f"описание клипа {co['id']} изменилось")
        if clip_flat(mo, co) != clip_flat(mn, cn) or clip_effective(mo, co, cache_o) != clip_effective(mn, cn, cache_n):
            fail(f"кадры клипа {co['id']} изменились")
    pos = export_positions(en)
    for sp in read_clip_specs(new):
        want_s = pos[sp["id"]] + 1 if sp["id"] in pos else None
        if sp["slot1"] != want_s and not (want_s is None and sp["slot1"] in (None, 0)):
            fail(f"номер экспорта у клипа {sp['id']} неверный")


def add_exports_sc1(sc, additions):
    sc1_require_writable(sc)
    m = sc1_load_model(sc)
    exports, _ = sc1_exports(sc)
    known = {c["id"] for c in m["clips"]} | {s_["id"] for s_ in m["shapes"]}
    have = {e["id"] for e in exports}
    for cid in additions:
        if cid not in known:
            raise UserError(f"Нет клипа или шейпа с id {cid}")
        if cid in have:
            raise UserError(f"У объекта {cid} уже есть ExportName - для смены имени используй переименование")
    new_names = list(additions.values())
    check_new_export_names([e["name"] for e in exports], new_names)
    mdl = dict(sc["mdl"])
    nul = any(r is not None and r.endswith(b"\0") for r in mdl["names"])
    raws = []
    for n in new_names:
        b = n.encode("utf-8") + (b"\0" if nul else b"")
        if len(b) > 254:
            raise UserError(f"Имя «{n}» длиннее 254 байт - в SC1 длина строки один байт")
        raws.append(b)
    mdl["ids"] = list(mdl["ids"]) + list(additions)
    mdl["names"] = list(mdl["names"]) + raws
    data = sc1_write(sc, sc1_build_stream(mdl))
    new = open_sc_bytes(data)
    ex_n, _ = sc1_exports(new)
    if [(e["id"], e["name"]) for e in ex_n] != [(e["id"], e["name"]) for e in exports] + list(additions.items()):
        raise UserError("Проверка результата не прошла, файл не записан: список ExportName'ов не совпал")
    if new["mdl"]["tags"] != sc["mdl"]["tags"] or new["cont"]["md5_ok"] is not True:
        raise UserError("Проверка результата не прошла, файл не записан: содержимое изменилось или MD5 не совпал")
    return data


def add_exports(sc, additions):
    if not additions:
        raise UserError("Не задано ни одного нового имени")
    if sc.get("kind") == "sc1":
        return add_exports_sc1(sc, additions)
    return add_exports_sc2(sc, additions)


# ================================================================ сборка .sc обратно из JSON/msgpack + PNG
#
# Обратная операция к разбору (пункт 7). Берёт папку с data.json или data.msgpack и texture_N.png
# (либо texture_N.ktx, если ASTC не удалось развернуть: тогда сжатые данные кладутся обратно как есть).
# SC2: файл собирается заново целиком. SC1: поток тегов собирается заново, сжатые текстуры - в _tex.sc.
# Текстуры из PNG пишутся как сырой RGBA8 (ASTC хаб не кодирует).
# Готовый файл сразу разбирается обратно и сравнивается с исходным JSON: всё, что было в JSON,
# должно совпасть (иначе файл не записывается).

def mp_unpack(b, i=0):
    t = b[i]
    if t <= 0x7f:
        return t, i + 1
    if t >= 0xe0:
        return t - 256, i + 1
    if 0x80 <= t <= 0x8f:
        return _mp_map(b, i + 1, t & 0xf)
    if 0x90 <= t <= 0x9f:
        return _mp_arr(b, i + 1, t & 0xf)
    if 0xa0 <= t <= 0xbf:
        n = t & 0x1f
        return bytes(b[i + 1:i + 1 + n]).decode("utf-8"), i + 1 + n
    if t == 0xc0:
        return None, i + 1
    if t == 0xc2:
        return False, i + 1
    if t == 0xc3:
        return True, i + 1
    if t in (0xc4, 0xc5, 0xc6):
        w = {0xc4: 1, 0xc5: 2, 0xc6: 4}[t]
        n = int.from_bytes(b[i + 1:i + 1 + w], "big")
        return bytes(b[i + 1 + w:i + 1 + w + n]), i + 1 + w + n
    if t == 0xca:
        return struct.unpack_from(">f", b, i + 1)[0], i + 5
    if t == 0xcb:
        return struct.unpack_from(">d", b, i + 1)[0], i + 9
    fmts = {0xcc: (">B", 1), 0xcd: (">H", 2), 0xce: (">I", 4), 0xcf: (">Q", 8),
            0xd0: (">b", 1), 0xd1: (">h", 2), 0xd2: (">i", 4), 0xd3: (">q", 8)}
    if t in fmts:
        f, w = fmts[t]
        return struct.unpack_from(f, b, i + 1)[0], i + 1 + w
    if t in (0xd9, 0xda, 0xdb):
        w = {0xd9: 1, 0xda: 2, 0xdb: 4}[t]
        n = int.from_bytes(b[i + 1:i + 1 + w], "big")
        return bytes(b[i + 1 + w:i + 1 + w + n]).decode("utf-8"), i + 1 + w + n
    if t == 0xdc:
        return _mp_arr(b, i + 3, struct.unpack_from(">H", b, i + 1)[0])
    if t == 0xdd:
        return _mp_arr(b, i + 5, struct.unpack_from(">I", b, i + 1)[0])
    if t == 0xde:
        return _mp_map(b, i + 3, struct.unpack_from(">H", b, i + 1)[0])
    if t == 0xdf:
        return _mp_map(b, i + 5, struct.unpack_from(">I", b, i + 1)[0])
    raise UserError(f"msgpack: неизвестный тип 0x{t:02x}")


def _mp_arr(b, i, n):
    out = []
    for _ in range(n):
        v, i = mp_unpack(b, i)
        out.append(v)
    return out, i


def _mp_map(b, i, n):
    out = {}
    for _ in range(n):
        k, i = mp_unpack(b, i)
        v, i = mp_unpack(b, i)
        out[k] = v
    return out, i


def load_doc_dir(folder):
    import json
    cands = [os.path.join(folder, n) for n in ("data.json", "data.msgpack") if os.path.isfile(os.path.join(folder, n))]
    if not cands:
        raise UserError(f"В папке нет data.json или data.msgpack:\n  {folder}")
    path = max(cands, key=os.path.getmtime)
    try:
        if path.endswith(".json"):
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        else:
            sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
            with open(path, "rb") as fh:
                raw = fh.read()
            doc, end = mp_unpack(raw)
    except (ValueError, struct.error, IndexError) as e:
        raise UserError(f"Не смог прочитать {os.path.basename(path)}: {e}")
    if not isinstance(doc, dict):
        raise UserError("В корне файла данных должен быть объект")
    return doc, path


DOC_SECTIONS = ("exports", "matrix_banks", "textures", "shapes", "movieclips", "textfields", "movieclip_modifiers")


def validate_doc(doc):
    for k in DOC_SECTIONS:
        if not isinstance(doc.get(k), list):
            raise UserError(f"В данных нет раздела «{k}» (или он не список)")

    def u16(v, what):
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 65535:
            raise UserError(f"{what}: нужно целое от 0 до 65535, а там {v!r}")

    ids = {}
    for kind, items in (("шейп", doc["shapes"]), ("клип", doc["movieclips"]), ("текстовое поле", doc["textfields"])):
        for it in items:
            u16(it.get("id"), f"id ({kind})")
            if it["id"] in ids:
                raise UserError(f"Повторяется id {it['id']}: {ids[it['id']]} и {kind}")
            ids[it["id"]] = kind
    clip_ids = {c["id"] for c in doc["movieclips"]}
    seen = set()
    for e in doc["exports"]:
        u16(e.get("id"), "id экспорта")
        if e["id"] not in ids:
            raise UserError(f"Экспорт «{e.get('name')}» ссылается на несуществующий объект {e['id']}")
        if not isinstance(e.get("name"), str) or not e["name"]:
            raise UserError(f"У экспорта клипа {e['id']} нет имени")
        if e["name"] in seen:
            raise UserError(f"Имя экспорта «{e['name']}» повторяется")
        seen.add(e["name"])
    mod_ids = {m.get("id") for m in doc["movieclip_modifiers"]}
    n_banks = len(doc["matrix_banks"])
    n_tex = len(doc["textures"])
    for sh in doc["shapes"]:
        for cmd in sh["commands"]:
            if not 0 <= cmd["texture_index"] < max(n_tex, 1):
                raise UserError(f"Шейп {sh['id']}: texture_index {cmd['texture_index']} вне списка текстур")
    for c in doc["movieclips"]:
        if c["bank_index"] >= max(n_banks, 1) and c["frame_elements"]:
            raise UserError(f"Клип {c['id']}: bank_index {c['bank_index']} вне списка банков")
        total = sum(f["elements_count"] for f in c["frames"])
        if total != len(c["frame_elements"]):
            raise UserError(f"Клип {c['id']}: сумма elements_count по кадрам ({total}) не равна числу "
                            f"frame_elements ({len(c['frame_elements'])})")
        bank = doc["matrix_banks"][c["bank_index"]] if c["frame_elements"] else None
        for ch in c["childrens"]:
            if ch["id"] not in ids and ch["id"] not in mod_ids and doc.get("is_sc2", True):
                raise UserError(f"Клип {c['id']}: ребёнок {ch['id']} не найден ни среди шейпов, клипов, "
                                f"текстовых полей, ни среди модификаторов")
        for e in c["frame_elements"]:
            if e["instance_index"] >= len(c["childrens"]):
                raise UserError(f"Клип {c['id']}: instance_index {e['instance_index']} вне списка детей")
            if e["matrix_index"] != 0xFFFF and e["matrix_index"] >= len(bank["matrices"]):
                raise UserError(f"Клип {c['id']}: matrix_index {e['matrix_index']} вне банка {c['bank_index']}")
            if e["colorTransform_index"] != 0xFFFF and e["colorTransform_index"] >= len(bank["color_transforms"]):
                raise UserError(f"Клип {c['id']}: colorTransform_index {e['colorTransform_index']} вне банка {c['bank_index']}")
    for bank in doc["matrix_banks"]:
        if len(bank["matrices"]) > 65535 or len(bank["color_transforms"]) > 65535:
            raise UserError("В банке больше 65535 матриц или цветов")


def docs_diff(a, b, path="", out=None, f32=True, skip_root=()):
    """Всё, что есть в a, должно быть и в b с тем же значением (лишние ключи в b не мешают)."""
    out = [] if out is None else out
    if len(out) >= 8:
        return out
    if isinstance(a, dict):
        if not isinstance(b, dict):
            out.append(f"{path}: ожидался объект")
            return out
        for k, v in a.items():
            if path == "" and k in skip_root:
                continue
            if k not in b:
                out.append(f"{path}/{k}: нет в результате")
            else:
                docs_diff(v, b[k], f"{path}/{k}", out, f32, skip_root)
    elif isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            out.append(f"{path}: длина {len(a)} против {len(b) if isinstance(b, list) else '-'}")
            return out
        for i, (x, y) in enumerate(zip(a, b)):
            docs_diff(x, y, f"{path}[{i}]", out, f32, skip_root)
    elif isinstance(a, float) or isinstance(b, float):
        try:
            if f32:
                same = struct.pack("<f", float(a)) == struct.pack("<f", float(b))
            else:
                same = abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
        except (TypeError, ValueError, OverflowError):
            same = False
        if not same:
            out.append(f"{path}: {a!r} против {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} против {b!r}")
    return out


def png_to_ktx_astc(rgba, w, h, bw=8, bh=8):
    """RGBA8 -> несжатый KTX1 с ASTC (так хранятся встроенные текстуры SC2)."""
    gl = next(k for k, v in ASTC_BLOCKS.items() if v == (bw, bh))
    blocks = astc_encode(rgba, w, h, bw, bh)
    return KTX_HEAD.pack(KTX_ID, 0x04030201, 0, 1, 0, gl, 0x1908, w, h, 0, 0, 1, 1, 0) + struct.pack("<I", len(blocks)) + blocks


def load_doc_textures(doc, folder, astc=False):
    """Читает файлы текстур. Возвращает список {kind, w, h, payload, fmt, file}. astc=True - PNG кодируются в ASTC 8x8."""
    from PIL import Image
    out = []
    for i, t in enumerate(doc["textures"]):
        name = t.get("file") or f"texture_{i}.png"
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            alt = [os.path.join(folder, f"texture_{i}.png"), os.path.join(folder, f"texture_{i}.ktx")]
            alt = [p for p in alt if os.path.isfile(p)]
            if not alt:
                raise UserError(f"Нет файла текстуры {name} (текстура #{i}) в папке {folder}")
            path = alt[0]
        if path.lower().endswith(".ktx"):
            with open(path, "rb") as fh:
                payload = fh.read()
            ki = ktx_info(payload)
            if not ki:
                raise UserError(f"{os.path.basename(path)} не похож на KTX")
            out.append({"kind": "ktx", "w": ki[1], "h": ki[2], "payload": payload, "fmt": 8, "file": path,
                        "flags": t.get("tex_flags_raw", 0) if isinstance(t.get("tex_flags_raw", 0), int) else 0})
        else:
            with Image.open(path) as im:
                im.load()
                rgba = im.convert("RGBA")
            w, h = rgba.size
            if max(w, h) > 65535:
                raise UserError(f"Текстура {os.path.basename(path)} слишком большая")
            flags = t.get("tex_flags_raw", 0) if isinstance(t.get("tex_flags_raw", 0), int) else 0
            if astc:
                print(gray(f"  Кодирую текстуру {i} ({w}x{h}) в ASTC..."))
                out.append({"kind": "ktx", "w": w, "h": h, "payload": png_to_ktx_astc(rgba.tobytes(), w, h),
                            "fmt": 8, "file": path, "flags": flags})
            else:
                out.append({"kind": "raw", "w": w, "h": h, "payload": rgba.tobytes(), "fmt": 0, "file": path,
                            "flags": flags})
    return out


def fb_build_struct_vec_chunk(fb, data, esize, align):
    n = len(data) // esize
    b = fb.Builder(len(data) + 256)
    b.StartVector(esize, n, align)
    b.head -= len(data)
    b.Bytes[b.head:b.head + len(data)] = data
    vec = b.EndVector()
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def fb_build_ds(fb, strings, flat, vertex_bytes, banks, grids=()):
    b = fb.Builder(1 << 20)
    offs = [b.CreateString(s) for s in strings]
    b.StartVector(4, len(offs), 4)
    for o in reversed(offs):
        b.PrependUOffsetTRelative(o)
    strings_vec = b.EndVector()
    points = b.CreateByteVector(bytes(vertex_bytes))
    b.StartVector(2, len(flat), 2)
    for v in reversed(flat):
        b.PrependUint16(v)
    elems = b.EndVector()
    bank_offs = []
    for mats, cols in banks:
        b.StartVector(24, len(mats), 4)
        for a, bb, c, d, tx, ty in reversed(mats):
            b.Prep(4, 24)
            for v in (ty, tx, d, c, bb, a):
                b.PrependFloat32(v)
        mv = b.EndVector()
        b.StartVector(7, len(cols), 1)
        for raw in reversed(cols):
            for byte in reversed(raw):
                b.PrependUint8(byte)
        cv = b.EndVector()
        b.StartObject(3)
        b.PrependUOffsetTRelativeSlot(0, mv, 0)
        b.PrependUOffsetTRelativeSlot(1, cv, 0)
        bank_offs.append(b.EndObject())
    b.StartVector(4, len(bank_offs), 4)
    for o in reversed(bank_offs):
        b.PrependUOffsetTRelative(o)
    banks_vec = b.EndVector()
    grids_vec = None
    if grids:
        b.StartVector(16, len(grids), 4)
        for gl, gt, gr, gb in reversed(grids):
            b.Prep(4, 16)
            for v in (gb, gr, gt, gl):
                b.PrependFloat32(v)
        grids_vec = b.EndVector()
    b.StartObject(7)
    b.PrependUOffsetTRelativeSlot(0, strings_vec, 0)
    if grids_vec is not None:
        b.PrependUOffsetTRelativeSlot(3, grids_vec, 0)
    b.PrependUOffsetTRelativeSlot(4, elems, 0)
    b.PrependUOffsetTRelativeSlot(5, points, 0)
    b.PrependUOffsetTRelativeSlot(6, banks_vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def fb_build_shapes_full(fb, shapes):
    """shapes: [(id, [(type, texture, число вершин, первая вершина), ...])]"""
    b = fb.Builder(1 << 16)
    offs = []
    for sid, cmds in shapes:
        cv = None
        if cmds:
            b.StartVector(16, len(cmds), 4)
            for typ, tex, cnt, first in reversed(cmds):
                b.Prep(4, 16)
                b.PrependUint32(first)
                b.PrependUint32(cnt)
                b.PrependUint32(tex)
                b.PrependUint32(typ)
            cv = b.EndVector()
        b.StartObject(2)
        b.PrependUint16Slot(0, sid, 0)
        if cv is not None:
            b.PrependUOffsetTRelativeSlot(1, cv, 0)
        offs.append(b.EndObject())
    b.StartVector(4, len(offs), 4)
    for o in reversed(offs):
        b.PrependUOffsetTRelative(o)
    vec = b.EndVector()
    b.StartObject(1)
    b.PrependUOffsetTRelativeSlot(0, vec, 0)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def textfield_flags(tf):
    if "flags_raw" in tf and isinstance(tf["flags_raw"], int):
        return tf["flags_raw"] & 0xFFFF
    f = 0
    if tf.get("multiline"):
        f |= 0x10
    if tf.get("outlined") or tf.get("use_device_font"):
        f |= 0x02
    if tf.get("auto_kern"):
        f |= 0x40
    if tf.get("unknown_flag"):
        f |= 0x20
    if tf.get("font_horizontal_align") == 1:
        f |= 0x0200
    if tf.get("font_vertical_align") == 1:
        f |= 0x1000
    return f


def doc_to_sc2(doc, texs):
    import flatbuffers
    fb = flatbuffers
    strings, sidx = [""], {"": 0}

    def S(s):
        if s not in sidx:
            sidx[s] = len(strings)
            strings.append(s)
        return sidx[s]

    for e in doc["exports"]:
        S(e["name"])
    for t in doc["textfields"]:
        S(t.get("font_name", ""))
        S(t.get("text", ""))
        S(t.get("typography_file", ""))
    for c in doc["movieclips"]:
        for ch in c["childrens"]:
            S(ch.get("name", ""))
        for f in c["frames"]:
            S(f.get("label", ""))

    vertex_bytes, shapes = bytearray(), []
    n_verts = 0
    for sh in doc["shapes"]:
        cmds = []
        for cmd in sh["commands"]:
            first = n_verts
            for v in cmd["vertices"]:
                vertex_bytes += struct.pack("<ffHH", v["x"], v["y"], int(round(v["u"] * 65535)),
                                            int(round(v["v"] * 65535)))
            n_verts += len(cmd["vertices"])
            cmds.append((0 if cmd["type"] == 1 else 1, cmd["texture_index"], len(cmd["vertices"]), first))
        shapes.append((sh["id"], cmds))

    flat, specs, grids = [], [], []
    first_pos = {}
    for pos, e in enumerate(doc["exports"]):
        first_pos.setdefault(e["id"], pos)
    for c in doc["movieclips"]:
        start = len(flat)
        for e in c["frame_elements"]:
            flat += [e["instance_index"], e["matrix_index"], e["colorTransform_index"]]
        has_names = any(ch.get("name") for ch in c["childrens"])
        grid_index = None
        if c.get("scaling_grid"):
            g = c["scaling_grid"]
            grid_index = len(grids)
            grids.append((g["left"], g["top"], g["right"], g["bottom"]))
        specs.append({
            "id": c["id"],
            "slot1": first_pos[c["id"]] + 1 if c["id"] in first_pos else None,
            "fps": c["frame_rate"] or None, "nframes": len(c["frames"]) or None,
            "flag": 1 if c.get("unknown_flag") else None,
            "children": [ch["id"] for ch in c["childrens"]] or None,
            "names": [S(ch.get("name", "")) for ch in c["childrens"]] if has_names else None,
            "blend": [ch.get("blend_mode", 0) for ch in c["childrens"]] or None,
            "frames": [(f["elements_count"], S(f.get("label", ""))) for f in c["frames"]] or None,
            "offset": start,
            "bank": c["bank_index"] or None,
            "grid": grid_index})

    banks = []
    for bk in doc["matrix_banks"]:
        mats = [(m["a"], m["b"], m["c"], m["d"], m["tx"], m["ty"]) for m in bk["matrices"]]
        cols = [bytes([ct["multiply"]["r"], ct["multiply"]["g"], ct["multiply"]["b"], ct["alpha"],
                       ct["add"]["r"], ct["add"]["g"], ct["add"]["b"]]) for ct in bk["color_transforms"]]
        banks.append((mats, cols))
    if not banks:
        banks = [([], [])]

    ds = fb_build_ds(fb, strings, flat, vertex_bytes, banks, grids)
    ids_all = [e["id"] for e in doc["exports"]]
    refs_all = [sidx[e["name"]] for e in doc["exports"]]
    en = fb_build_export_names(fb, ids_all, refs_all)
    recs = bytearray()
    for t in doc["textfields"]:
        bnds = t.get("bounds", {})
        recs += struct.pack("<II4hIIIIHBBi", t["id"], S(t.get("font_name", "")),
                            bnds.get("left", 0), bnds.get("top", 0), bnds.get("right", 0), bnds.get("bottom", 0),
                            t.get("font_color", 0) & 0xFFFFFFFF, t.get("outline_color", 0) & 0xFFFFFFFF,
                            S(t.get("text", "")), S(t.get("typography_file", "")),
                            textfield_flags(t), t.get("font_size", 0) & 0xFF, 0, int(t.get("outline_angle", 0)))
    tfc = fb_build_struct_vec_chunk(fb, bytes(recs), 40, 4) if doc["textfields"] else TEXTFIELDS_CHUNK[4:]
    shb = fb_build_shapes_full(fb, shapes)
    mcb = fb_build_clips(fb, specs)
    mods = b"".join(struct.pack("<HH", m["id"], m["type"]) for m in doc["movieclip_modifiers"])
    mdc = fb_build_struct_vec_chunk(fb, mods, 4, 2) if doc["movieclip_modifiers"] else MODIFIERS_CHUNK[4:]
    tex = build_textures(fb, [[t["payload"], t["fmt"], t["w"], t["h"], t.get("flags", 0)] for t in texs])
    inner = b"".join(struct.pack("<I", len(c)) + c for c in (ds, en, tfc, shb, mcb, mdc, tex))
    comp = zstd_pack(inner)
    slots = {0: 1, 1: 1, 2: len(shapes), 3: len(specs), 4: len(texs), 8: 4 + len(ds), 9: len(tex) + 4,
             11: len(comp)}
    if doc["textfields"]:
        slots[5] = len(doc["textfields"])
    fd = fb_build_fd_from_slots(fb, slots, [e["name"] for e in doc["exports"]])
    return b"SC" + struct.pack("<I", 5) + struct.pack("<I", len(fd)) + fd + comp


def fb_build_fd_from_slots(fb, slots, names):
    b = fb.Builder(2048 + 64 * len(names))
    tabs = []
    for n in names:
        so = b.CreateString(n)
        b.StartObject(1)
        b.PrependUOffsetTRelativeSlot(0, so, 0)
        tabs.append(b.EndObject())
    b.StartVector(4, len(tabs), 4)
    for t in reversed(tabs):
        b.PrependUOffsetTRelative(t)
    ex = b.EndVector()
    b.StartObject(13)
    for s in range(13):
        if s == 10:
            b.PrependUOffsetTRelativeSlot(10, ex, 0)
        elif s in slots:
            b.PrependUint32Slot(s, slots[s], None)
    b.Finish(b.EndObject())
    return bytes(b.Output())


def sc1_pack(ver, real, raw, tail=b""):
    import hashlib
    head = b"SC" + struct.pack(">I", ver)
    if ver == 4:
        head += struct.pack(">I", real)
    head += struct.pack(">I", 16) + hashlib.md5(raw).digest()
    return head + zstd_pack(raw) + tail


def doc_to_sc1(doc, texs):
    """Возвращает (основной файл, файл текстур или None)."""
    meta = doc.get("sc1") or {}
    if meta.get("unparsed_tags"):
        raise UserError(f"В исходном SC1 были теги {meta['unparsed_tags']}, которых нет в JSON - собрать без потерь нельзя")
    if doc["textfields"] or doc["movieclip_modifiers"]:
        raise UserError("Текстовые поля и модификаторы в SC1 хаб не собирает (их теги не изучены)")
    if len(doc["matrix_banks"]) != 1:
        raise UserError("SC1 поддерживает один банк матриц")
    names_nul = bool(meta.get("names_nul"))
    exports_ids = [e["id"] for e in doc["exports"]]
    names = []
    for e in doc["exports"]:
        raw = e["name"].encode("utf-8") + (b"\0" if names_nul else b"")
        if len(raw) > 254:
            raise UserError(f"Имя «{e['name']}» длиннее 254 байт")
        names.append(raw)

    def q(v, k):
        return max(-2 ** 31, min(2 ** 31 - 1, int(round(v * k))))

    bodies = {18: [], 8: [], 9: [], 49: []}
    for sh in doc["shapes"]:
        cmds = []
        for cmd in sh["commands"]:
            xy, uv = [], []
            for v in cmd["vertices"]:
                xy += [q(v["x"], 20), q(v["y"], 20)]
                uv += [min(65535, max(0, int(round(v["u"] * 65535)))), min(65535, max(0, int(round(v["v"] * 65535))))]
            cmds.append({"tex": cmd["texture_index"], "xy": xy, "uv": uv})
        bodies[18].append(sc1_build_shape(sh["id"], cmds))
    bank = doc["matrix_banks"][0]
    for m in bank["matrices"]:
        bodies[8].append(struct.pack("<6i", q(m["a"], 1024), q(m["b"], 1024), q(m["c"], 1024), q(m["d"], 1024),
                                     q(m["tx"], 20), q(m["ty"], 20)))
    for ct in bank["color_transforms"]:
        bodies[9].append(bytes([ct["add"]["r"], ct["add"]["g"], ct["add"]["b"], ct["alpha"],
                                ct["multiply"]["r"], ct["multiply"]["g"], ct["multiply"]["b"]]))
    for c in doc["movieclips"]:
        inst = []
        for ch in c["childrens"]:
            nm = None if ch.get("name_null") else ch.get("name", "").encode("utf-8")
            blend = ch["blend_raw"] if isinstance(ch.get("blend_raw"), int) else \
                (ch.get("blend_mode", 0) & 0x3F) | (0x40 if ch.get("hidden") else 0)
            inst.append({"id": ch["id"], "blend": blend & 0xFF, "name": nm})
        frames = []
        for f in c["frames"]:
            lab = None if f.get("label_null") else f.get("label", "").encode("utf-8")
            frames.append({"count": f["elements_count"], "label": lab})
        flat = []
        for e in c["frame_elements"]:
            flat += [e["instance_index"], e["matrix_index"], e["colorTransform_index"]]
        bodies[49].append(sc1_build_clip({"id": c["id"], "fps": c["frame_rate"], "nframes": len(c["frames"]),
                                          "flag": 1 if c.get("unknown_flag") else 0, "flat": flat, "inst": inst,
                                          "frames": frames}))
    external = bool(doc.get("use_external_textures"))
    tex_by_tag = {}
    heads_by_tag = {}
    pixel_bodies = []
    for i, t in enumerate(doc["textures"]):
        tag = t.get("sc1_tag", 1)
        if tag not in SC1_TEXTURE_TAGS:
            tag = 1
        info = texs[i]
        if info["kind"] != "raw":
            raise UserError("Для SC1 нужны PNG-текстуры (KTX/ASTC в SC1 не бывает)")
        head = bytes([0]) + struct.pack("<2H", info["w"], info["h"])
        pix = head + info["payload"]
        tex_by_tag.setdefault(tag, []).append(head if external else pix)
        pixel_bodies.append((tag, pix))
    queues = {t: list(v) for t, v in tex_by_tag.items()}
    queues.update({t: list(v) for t, v in bodies.items()})
    order = [tuple(x) for x in meta.get("tag_order") or []]
    if not order:
        order = [(t, 1) for t in meta.get("flag_tags", [])] + [(1, len(doc["textures"])) if doc["textures"] else (1, 0),
                (18, len(bodies[18])), (8, len(bodies[8])), (9, len(bodies[9])), (49, len(bodies[49])), (0, 1)]
    tags = []
    for tag, count in order:
        if tag == 0:
            continue
        for _ in range(count):
            if tag in (23, 26) or tag not in queues:
                tags.append([tag, b""])
            elif queues[tag]:
                tags.append([tag, queues[tag].pop(0)])
    for tag in sorted(queues):
        for body in queues[tag]:
            tags.append([tag, body])
    tags.append([0, b""])
    n_tex = len(doc["textures"])
    mdl = {"counts": [len(bodies[18]), len(bodies[49]), n_tex, 0, len(bodies[8]), len(bodies[9])],
           "reserved": bytes.fromhex(meta.get("reserved_hex", "0000000000")), "ids": exports_ids, "names": names,
           "tags": tags, "rest": b""}
    raw = sc1_build_stream(mdl)
    ver = meta.get("container_version", 3)
    real = doc.get("version", 3)
    tail = b""
    foot_names = meta.get("footer_exports")
    old_tail = bytes.fromhex(meta.get("footer_hex", "")) if meta.get("footer_hex") else b""
    foot = sc1_footer(old_tail) if old_tail else None
    if foot:
        ents, rest = foot
        hashes = dict(ents)
        tail = b"START" + b"".join(e["name"].encode("utf-8") + b"\0\x10" + hashes[e["name"]]
                                  for e in doc["exports"] if e["name"] in hashes) + rest
    main = sc1_pack(ver, real, raw, tail)
    tex_bytes = None
    if external and doc["textures"]:
        tex_raw = b"".join(bytes([tag]) + struct.pack("<i", len(pix)) + pix for tag, pix in pixel_bodies) \
            + bytes([0]) + struct.pack("<i", 0)
        tc = meta.get("tex_container") or {}
        tex_bytes = sc1_pack(tc.get("ver", 3), tc.get("real", 3), tex_raw)
    return main, tex_bytes


def encode_doc(doc, folder, astc=False):
    """Собирает .sc из doc и текстур в folder. Возвращает (основной файл, _tex.sc или None, предупреждения).
    astc=True: для SC2 текстуры кодируются в ASTC (файл меньше, но с потерями)."""
    validate_doc(doc)
    texs = load_doc_textures(doc, folder, astc and doc.get("is_sc2", True))
    warns = []
    if doc.get("is_sc2", True):
        if any(t["kind"] == "raw" and doc["textures"][i].get("encoding") == 1 for i, t in enumerate(texs)):
            warns.append("ASTC-текстуры из PNG записаны как сырой RGBA8: файл и память игры станут больше")
        main = doc_to_sc2(doc, texs)
        tex_b = None
        got, _ = sc2_to_doc(parse_sc(main))
        diff = docs_diff(doc, got, skip_root=("textures", "sc1", "use_external_textures", "use_external_texture",
                                              "use_multi_resolution", "use_low_resolution", "use_precision_matrix",
                                              "use_half_precision_matrices", "use_short_frames"))
        f32 = True
    else:
        main, tex_b = doc_to_sc1(doc, texs)
        tc = sc1_container(tex_b) if tex_b else None
        got, _ = sc1_to_doc(sc1_container(main), tc)
        diff = docs_diff(doc, got, f32=False, skip_root=("textures", "sc1", "use_external_textures",
                                                         "use_external_texture", "use_multi_resolution",
                                                         "use_low_resolution", "use_precision_matrix",
                                                         "use_half_precision_matrices", "use_short_frames"))
    if diff:
        raise UserError("Проверка результата не прошла, файл не записан: после сборки и обратного разбора "
                        "данные не совпали с твоим JSON:\n    " + "\n    ".join(diff[:6]))
    if len(got["textures"]) != len(doc["textures"]):
        raise UserError("Проверка результата не прошла, файл не записан: число текстур не совпало")
    return main, tex_b, warns


def flow_build():
    ensure_deps()
    stem_default = "input"
    raw = ask("Папка с data.json (или data.msgpack) и PNG", f"output/{stem_default}_decoded")
    folder = unquote(raw)
    if not os.path.isabs(folder):
        for base in (APP_DIR, OUT_DIR, IN_DIR, os.getcwd()):
            cand = os.path.join(base, folder)
            if os.path.isdir(cand):
                folder = cand
                break
        else:
            folder = os.path.join(APP_DIR, folder)
    if not os.path.isdir(folder):
        raise UserError(f"Нет такой папки: {folder}")
    doc, doc_path = load_doc_dir(folder)
    kind = "SC2" if doc.get("is_sc2", True) else "SC1"
    print(f"  Данные: {os.path.basename(doc_path)}, формат {kind}, экспортов {len(doc['exports'])}, "
          f"шейпов {len(doc['shapes'])}, клипов {len(doc['movieclips'])}, текстур {len(doc['textures'])}")
    out_path = resolve_output(ask("Выходной файл", "output.sc"), ".sc")
    if not ask_yes_no("Продолжить", True):
        raise UserError("Отменено")
    astc = ask_astc() if doc.get("is_sc2", True) else False
    print(gray("  Собираю (для больших файлов это до минуты)..."))
    main_b, tex_b, warns = encode_doc(doc, folder, astc)
    with open(out_path, "wb") as fh:
        fh.write(main_b)
    saved(out_path)
    results = [out_path]
    if tex_b is not None:
        tex_out = os.path.splitext(out_path)[0] + "_tex.sc"
        with open(tex_out, "wb") as fh:
            fh.write(tex_b)
        saved(tex_out)
        results.append(tex_out)
    for w in warns:
        print(yellow("  ⚠ " + w))
    print(green("  ✔ Проверка пройдена: ") + gray("готовый файл разобран обратно, всё, что было в JSON, совпало"))
    return results


# ================================================================ удаление объектов по id (без ExportName): убрать из клипов
#
# Шейп, вложенный клип или текстовое поле убираются из списка детей клипов (из всех или из одного),
# у этих клипов пересчитываются дети, кадры и номера экземпляров; сами клипы остаются.
# Объекты, которые после этого никому не нужны (до них нельзя дойти от оставшихся экспортов и от
# бесхозных клипов, которые были в файле и раньше), удаляются целиком, как при удалении экспорта.

def refs_by_ids(m, ids, scope=None):
    """{индекс клипа: множество индексов детей-экземпляров, чей id в ids}."""
    out = {}
    for c in m["clips"]:
        if scope is not None and c["id"] not in scope:
            continue
        r = {k for k, cid in enumerate(c["children"]) if cid in ids}
        if r:
            out[c["index"]] = r
    return out


def filter_elements(flat, frame_counts, removed):
    """flat - тройки (экземпляр, матрица, цвет), frame_counts - число элементов в каждом кадре,
    removed - индексы удаляемых экземпляров. Возвращает (новые тройки, новые числа по кадрам, сколько убрано)."""
    new_flat, new_counts, pos, dropped = [], [], 0, 0
    rem = sorted(removed)
    for cnt in frame_counts:
        kept = 0
        for _ in range(cnt):
            inst, mi, ci = flat[3 * pos], flat[3 * pos + 1], flat[3 * pos + 2]
            pos += 1
            if inst in removed:
                dropped += 1
                continue
            new_flat += [inst - sum(1 for r in rem if r < inst), mi, ci]
            kept += 1
        new_counts.append(kept)
    if pos != len(flat) // 3:
        raise UserError("Сумма элементов по кадрам не равна числу элементов клипа - файл устроен иначе, чем ожидалось")
    return new_flat, new_counts, dropped


def simulate_after_removal(m, refs):
    """Модель-заготовка: те же клипы, но без удалённых детей (для расчёта, что станет мусором)."""
    clips = []
    for c in m["clips"]:
        if c["index"] in refs:
            c = dict(c, children=[cid for k, cid in enumerate(c["children"]) if k not in refs[c["index"]]])
        clips.append(c)
    return {"clips": clips, "shapes": m["shapes"], "tf_bounds": m.get("tf_bounds", {})}


def plan_after_removal(m_before, m_after, exports, ids):
    all_c, _s, _t = reach_from(m_before, [e["id"] for e in exports])
    orphans = [c["id"] for c in m_before["clips"] if c["id"] not in all_c]
    keep_c, keep_s, keep_t = reach_from(m_after, [e["id"] for e in exports] + orphans)
    gone_c, gone_s, gone_t = reach_from(m_before, list(ids))
    return {"names": set(), "gone_exports": [], "keep_exports": list(exports),
            "del_clips": gone_c - keep_c, "del_shapes": gone_s - keep_s, "del_tfs": gone_t - keep_t,
            "shared_clips": len(gone_c & keep_c), "shared_shapes": len(gone_s & keep_s), "orphans": len(orphans)}


def remove_children_sc2(sc, ids, scope=None):
    if sc["version"] != 5 or len(sc["chunks"]) != 7 or sc["leftover"]:
        raise UserError("Удаление по id умеет только SC2 версии 5 с 7 блоками")
    import flatbuffers
    m = load_model(sc)
    refs = refs_by_ids(m, ids, scope)
    if not refs:
        return None, {"instances": 0, "elements": 0, "clips": []}
    specs = read_clip_specs(sc)
    ds = bytearray(sc["chunks"][0])
    new_specs = [dict(sp) for sp in specs]
    inst_total = el_total = 0
    for ci, removed in refs.items():
        clip, sp = m["clips"][ci], new_specs[ci]
        flat = clip_flat(m, clip)
        counts = [cnt for cnt, _lab in (sp["frames"] or [])]
        new_flat, new_counts, dropped = filter_elements(flat, counts, removed)
        keep = [k for k in range(len(clip["children"])) if k not in removed]
        sp["children"] = [clip["children"][k] for k in keep]
        if sp["names"] is not None:
            sp["names"] = [sp["names"][k] for k in keep]
        if sp["blend"] is not None:
            sp["blend"] = [sp["blend"][k] for k in keep]
        if sp["frames"] is not None:
            sp["frames"] = [(nc, lab) for nc, (_c, lab) in zip(new_counts, sp["frames"])]
        if clip["n_el"]:
            base = m["e_start"] + 2 * clip["offset"]
            old_len = 3 * clip["n_el"]
            vals = new_flat + [0] * (old_len - len(new_flat))
            struct.pack_into(f"<{old_len}H", ds, base, *vals)
        inst_total += len(removed)
        el_total += dropped
    chunks = list(sc["chunks"])
    chunks[0] = bytes(ds)
    chunks[4] = fb_build_clips(flatbuffers, new_specs)
    data = finalize_sc(sc, chunks, sc["fd"])
    verify_remove_children(sc, data, refs, specs, new_specs)
    return data, {"instances": inst_total, "elements": el_total, "clips": [m["clips"][ci]["id"] for ci in refs]}


def verify_remove_children(old, data, refs, specs_old, specs_new_expected):
    new = parse_sc(data)

    def fail(msg):
        raise UserError("Проверка результата не прошла, файл не записан: " + msg)

    if len(new["chunks"]) != 7 or new["leftover"]:
        fail("структура блоков нарушена")
    for i in (1, 2, 3, 5, 6):
        if new["chunks"][i] != old["chunks"][i]:
            fail(f"блок {CHUNK_NAMES[i]} изменился")
    if read_clip_specs(new) != specs_new_expected:
        fail("таблицы клипов не совпали с ожидаемыми")
    mo, mn = load_model(old), load_model(new)
    mask = bytearray(len(old["chunks"][0]))
    for ci in refs:
        c = mo["clips"][ci]
        if c["n_el"]:
            a = mo["e_start"] + 2 * c["offset"]
            for k in range(a, a + 6 * c["n_el"]):
                mask[k] = 1
    o_ds, n_ds = old["chunks"][0], new["chunks"][0]
    if len(o_ds) != len(n_ds) or any(o_ds[k] != n_ds[k] for k in range(len(o_ds)) if not mask[k]):
        fail("DataStorage изменился за пределами кадров затронутых клипов")
    cache_o, cache_n = {}, {}
    for co, cn in zip(mo["clips"], mn["clips"]):
        if co["index"] not in refs:
            if (co["children"], co["n_el"]) != (cn["children"], cn["n_el"]) or clip_flat(mo, co) != clip_flat(mn, cn):
                fail(f"изменился незатронутый клип {co['id']}")
            continue
        removed = refs[co["index"]]
        eo, en_ = clip_effective(mo, co, cache_o), clip_effective(mn, cn, cache_n)
        co_cols, cn_cols = clip_colors(mo, co), clip_colors(mn, cn)
        fo = clip_flat(mo, co)
        want_m, want_c, want_i = [], [], []
        rem = sorted(removed)
        for k in range(co["n_el"]):
            inst = fo[3 * k]
            if inst in removed:
                continue
            want_m.append(eo[k])
            want_c.append(co_cols[k])
            want_i.append(inst - sum(1 for r in rem if r < inst))
        fn = clip_flat(mn, cn)
        if fn[0::3] != want_i or en_ != want_m or cn_cols != want_c:
            fail(f"кадры клипа {co['id']} не совпали с ожидаемыми")
        if cn["children"] != [cid for k, cid in enumerate(co["children"]) if k not in removed]:
            fail(f"дети клипа {co['id']} не совпали с ожидаемыми")


def remove_children_sc1(sc, ids, scope=None):
    sc1_require_writable(sc)
    sc1_require_known_clips(sc)
    m = sc1_load_model(sc)
    refs = refs_by_ids(m, ids, scope)
    if not refs:
        return None, {"instances": 0, "elements": 0, "clips": []}
    tags = [list(t) for t in sc["mdl"]["tags"]]
    inst_total = el_total = 0
    for ci, removed in refs.items():
        clip = m["clips"][ci]
        parsed = dict(clip["parsed"])
        if sc1_build_clip(parsed) != tags[clip["pos"]][1]:
            raise UserError(f"Клип {clip['id']} собирается обратно не байт-в-байт - не рискую его менять")
        new_flat, new_counts, dropped = filter_elements(parsed["flat"], [f["count"] for f in parsed["frames"]], removed)
        parsed["flat"] = new_flat
        parsed["frames"] = [dict(f, count=nc) for f, nc in zip(parsed["frames"], new_counts)]
        parsed["inst"] = [i for k, i in enumerate(parsed["inst"]) if k not in removed]
        tags[clip["pos"]][1] = sc1_build_clip(parsed)
        inst_total += len(removed)
        el_total += dropped
    mdl = dict(sc["mdl"])
    mdl["tags"] = tags
    data = sc1_write(sc, sc1_build_stream(mdl))
    new = open_sc_bytes(data)
    mn = sc1_load_model(new)
    if new["cont"]["md5_ok"] is not True or new["cont"]["tail"] != sc["cont"]["tail"]:
        raise UserError("Проверка результата не прошла, файл не записан: MD5 или хвост файла не совпали")
    keep = lambda t: t[0] != 49
    if [t for t in sc["mdl"]["tags"] if keep(t)] != [t for t in new["mdl"]["tags"] if keep(t)]:
        raise UserError("Проверка результата не прошла, файл не записан: изменились посторонние теги")
    for co, cn in zip(m["clips"], mn["clips"]):
        if co["index"] not in refs:
            if sc["mdl"]["tags"][co["pos"]][1] != new["mdl"]["tags"][cn["pos"]][1]:
                raise UserError(f"Проверка результата не прошла, файл не записан: изменился клип {co['id']}")
        elif cn["children"] != [cid for k, cid in enumerate(co["children"]) if k not in refs[co["index"]]]:
            raise UserError(f"Проверка результата не прошла, файл не записан: дети клипа {co['id']} не совпали")
    return data, {"instances": inst_total, "elements": el_total, "clips": [m["clips"][ci]["id"] for ci in refs]}


def remove_by_ids(sc, ids, scope=None):
    """Возвращает (байты нового .sc, отчёт). Отчёт: затронутые клипы, убранные вхождения, удалённые объекты."""
    ids = set(ids)
    sc1 = sc.get("kind") == "sc1"
    m0 = sc1_load_model(sc) if sc1 else load_model(sc)
    exports, _ = get_exports(sc)
    data1, info = (remove_children_sc1 if sc1 else remove_children_sc2)(sc, ids, scope)
    if data1 is None:
        raise UserError("Ни в одном клипе нет этих объектов (или они вне выбранного клипа) - убирать нечего")
    sc_a = open_sc_bytes(data1, sc.get("path", ""))
    m_a = sc1_load_model(sc_a) if sc1 else load_model(sc_a)
    plan = plan_after_removal(m0, m_a, exports, ids)
    info["plan"] = plan
    if gc_nothing(plan):
        return data1, info
    if sc1:
        data2 = remove_objects_sc1(sc_a, m_a, plan)
    else:
        data2 = remove_objects_sc2(sc_a, m_a, exports, plan)
    return data2, info


def flow_inspect_id():
    """Безопасный диагностический просмотр объекта: ничего не меняет и не записывает."""
    ensure_deps()
    src = resolve_input("Какой .sc проверить", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    raw = ask("ID объекта (например 34)", "").strip()
    if not raw.isdigit():
        raise UserError("Нужен числовой ID")
    oid = int(raw)
    m = sc1_load_model(sc) if sc.get("kind") == "sc1" else load_model(sc)
    exports, _ = get_exports(sc)
    names = {e["id"]: e["name"] for e in exports}
    clips = [c for c in m["clips"] if c["id"] == oid]
    shapes = [s for s in m["shapes"] if s["id"] == oid]
    tfs = [] if sc.get("kind") == "sc1" else [r for r in read_textfields(sc)[0] if r["id"] == oid]
    if not clips and not shapes and not tfs:
        raise UserError(f"Объект с ID {oid} не найден")
    print(f"  ID: {oid}")
    print(f"  ExportName: {names.get(oid, '<нет>')}")
    for c in clips:
        print(f"  Type: MovieClip, детей: {len(c['children'])}, элементов кадров: {c['n_el']}")
        used = []
        for parent in m["clips"]:
            positions = [i for i, ch in enumerate(parent["children"]) if ch == oid]
            if positions:
                used.append((parent["id"], positions))
        print("  Родители:")
        if not used: print("    <нет>")
        for pid, pos in used:
            print(f"    MovieClip {names.get(pid, f'id {pid}')}: позиции {', '.join(map(str,pos))}")
    for sh in shapes:
        print(f"  Type: Shape")
        verts = sorted(set(sh.get("verts", [])))
        print(f"  Вершин: {len(verts)}")
    for r in tfs:
        print(f"  Type: TextField")
        print(f"  Текст: {textfield_text(r, read_textfields(sc)[1], r.get('text_slot'))!r}")
        if r.get("localization_key"): print(f"  Localization key: {r['localization_key']}")
        print(f"  Font: {r['font']!r}, size: {r['size']}, bounds: {tuple(r['bounds'])}")
    parents = []
    for parent in m["clips"]:
        positions = [i for i, ch in enumerate(parent["children"]) if ch == oid]
        if positions: parents.append((parent["id"], positions))
    print("  Используется в MovieClip'ах:")
    if not parents: print("    <нет>")
    for pid, pos in parents:
        print(f"    {names.get(pid, f'id {pid}')}: позиции {', '.join(map(str,pos))}")
    print_sc_package(src)
    return []


def flow_sc_package():
    ensure_deps()
    src = resolve_input("Какой .sc проверить", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    pkg = print_sc_package(src)
    print("  Важно: дополнительные .sctx/.zktx пока только обнаруживаются; содержимое не меняется.")
    return []


def flow_open_output():
    ensure_dirs()
    try:
        if os.name == "nt":
            os.startfile(OUT_DIR)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", OUT_DIR])
        else:
            subprocess.Popen(["xdg-open", OUT_DIR])
        print(green("  Открыл: ") + OUT_DIR)
    except Exception:
        print("  Папка: " + OUT_DIR)
    return []


# ================================================================ загрузочный экран и части .sc: общие помощники
#
# render_scene - рендер экспорта документа: шейпы любой формы (полосы треугольников с UV), матрицы, цветовые
# преобразования, маски (модификаторы 38/39/40). Нужен для превью и самопроверки собранных файлов.
# DocEdit - правки документа: новые матрицы/цвета/текстуры/шейпы/клипы без ручного учёта банков и id.
# Остальное: вырезание частей картинок из шейпов (с учётом поворота в атласе), перепаковка атласа документа.

def doc_premultiplied(doc, folder):
    """True, если пиксели атласов документа предумножены на альфу (так хранят игры Supercell)."""
    import numpy as np
    from PIL import Image
    tot = ok = 0
    for i, t in enumerate(doc["textures"]):
        f = os.path.join(folder, t.get("file") or f"texture_{i}.png")
        if not os.path.isfile(f):
            continue
        with Image.open(f) as im:
            a = np.asarray(im.convert("RGBA"), dtype=np.uint16)
        sel = a[..., 3] > 0
        if not sel.any():
            continue
        px = a[sel]
        tot += len(px)
        ok += int((px[:, :3].max(axis=1) <= px[:, 3] + 1).sum())
    return tot > 0 and ok / tot >= 0.995


def render_scene(doc, folder, name, t=0, width=960, view=None, bg=None, premultiplied=False):
    """Рендер экспорта name в кадре t (60 к/с). view = (cx, cy, w, h) в единицах сцены. Возвращает PIL-картинку."""
    import numpy as np
    from PIL import Image
    clips = {c["id"]: c for c in doc["movieclips"]}
    shapes = {s["id"]: s for s in doc["shapes"]}
    mods = {m_["id"]: m_["type"] for m_ in doc.get("movieclip_modifiers", [])}
    banks = doc["matrix_banks"]
    ex = next(e for e in doc["exports"] if e["name"] == name)
    texs = {}
    for i, tx in enumerate(doc["textures"]):
        with Image.open(os.path.join(folder, tx.get("file") or f"texture_{i}.png")) as im:
            a = np.array(im.convert("RGBA"), np.float32)
        if not premultiplied:
            a[..., :3] *= a[..., 3:4] / 255.0
        texs[i] = a
    cx, cy, vw, vh = view
    k = width / vw
    H, W = max(1, round(vh * k)), width

    def new():
        return np.zeros((H, W, 4), np.float32)

    def sample(tex, u, v):
        th, tw = tex.shape[:2]
        x = np.clip(u * tw - 0.5, 0, max(tw - 1.001, 0))
        y = np.clip(v * th - 0.5, 0, max(th - 1.001, 0))
        x0, y0 = x.astype(np.int32), y.astype(np.int32)
        x1, y1 = np.minimum(x0 + 1, tw - 1), np.minimum(y0 + 1, th - 1)
        fx, fy = (x - x0)[:, None], (y - y0)[:, None]
        return (tex[y0, x0] * (1 - fx) * (1 - fy) + tex[y0, x1] * fx * (1 - fy)
                + tex[y1, x0] * (1 - fx) * fy + tex[y1, x1] * fx * fy)

    def tri(canvas, P, UV, tex, mul, add, alpha, blend):
        x0, x1 = max(0, int(np.floor(P[:, 0].min()))), min(W - 1, int(np.ceil(P[:, 0].max())))
        y0, y1 = max(0, int(np.floor(P[:, 1].min()))), min(H - 1, int(np.ceil(P[:, 1].max())))
        if x1 < x0 or y1 < y0:
            return
        (ax, ay), (bx, by), (cx_, cy_) = P
        den = (by - cy_) * (ax - cx_) + (cx_ - bx) * (ay - cy_)
        if abs(den) < 1e-9:
            return
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        px, py = xs + 0.5, ys + 0.5
        l1 = ((by - cy_) * (px - cx_) + (cx_ - bx) * (py - cy_)) / den
        l2 = ((cy_ - ay) * (px - cx_) + (ax - cx_) * (py - cy_)) / den
        l3 = 1 - l1 - l2
        ins = (l1 >= -0.02) & (l2 >= -0.02) & (l3 >= -0.02)
        if not ins.any():
            return
        l1, l2, l3 = l1[ins], l2[ins], l3[ins]
        c = sample(tex, l1 * UV[0, 0] + l2 * UV[1, 0] + l3 * UV[2, 0], l1 * UV[0, 1] + l2 * UV[1, 1] + l3 * UV[2, 1])
        c[:, :3] = np.clip(c[:, :3] * mul + add * c[:, 3:4] / 255.0, 0, 255)
        c *= alpha
        yy, xx = ys[ins], xs[ins]
        dst = canvas[yy, xx]
        if blend in (8, 9, 10, 64):
            canvas[yy, xx] = np.clip(dst + c, 0, 255)
        else:
            canvas[yy, xx] = c + dst * (1.0 - c[:, 3:4] / 255.0)

    def draw(canvas, oid, G, alpha, mul, add, blend, tt):
        if oid in shapes:
            for cmd in shapes[oid]["commands"]:
                v = cmd["vertices"]
                if len(v) < 3:
                    continue
                pts = np.array([[G[0][0] * q["x"] + G[0][1] * q["y"] + G[0][2], G[1][0] * q["x"] + G[1][1] * q["y"] + G[1][2]] for q in v])
                scr = np.stack([(pts[:, 0] - cx) * k + W / 2.0, (pts[:, 1] - cy) * k + H / 2.0], 1)
                uv = np.array([[q["u"], q["v"]] for q in v])
                for i in range(len(v) - 2):
                    tri(canvas, scr[i:i + 3], uv[i:i + 3], texs[cmd["texture_index"]], mul, add, alpha, blend)
            return
        c = clips.get(oid)
        if c is None:
            return
        fi = int(tt) % len(c["frames"])
        start = sum(f["elements_count"] for f in c["frames"][:fi])
        bk = banks[c["bank_index"]] if c["bank_index"] < len(banks) else {"matrices": [], "color_transforms": []}
        main = canvas
        saved_main = content = mask_a = None
        for e in c["frame_elements"][start:start + c["frames"][fi]["elements_count"]]:
            ch = c["childrens"][e["instance_index"]]
            mt = mods.get(ch["id"])
            if mt == 38:
                saved_main = main
                main = new()
                continue
            if mt == 39:
                mask_a = main[..., 3] / 255.0
                main = content = new()
                continue
            if mt == 40:
                if saved_main is not None and content is not None and mask_a is not None:
                    s_ = content * mask_a[..., None]
                    saved_main[:] = s_ + saved_main * (1.0 - s_[..., 3:4] / 255.0)
                    main = saved_main
                saved_main = content = mask_a = None
                continue
            mi, ci = e["matrix_index"], e["colorTransform_index"]
            m_ = bk["matrices"][mi] if mi < len(bk["matrices"]) else None
            M = [[1, 0, 0], [0, 1, 0]] if m_ is None else [[m_["a"], m_["c"], m_["tx"]], [m_["b"], m_["d"], m_["ty"]]]
            G2 = [[G[0][0] * M[0][0] + G[0][1] * M[1][0], G[0][0] * M[0][1] + G[0][1] * M[1][1], G[0][0] * M[0][2] + G[0][1] * M[1][2] + G[0][2]],
                  [G[1][0] * M[0][0] + G[1][1] * M[1][0], G[1][0] * M[0][1] + G[1][1] * M[1][1], G[1][0] * M[0][2] + G[1][1] * M[1][2] + G[1][2]]]
            a2, mul2, add2 = alpha, mul, add
            if ci < len(bk["color_transforms"]):
                ct = bk["color_transforms"][ci]
                a2 = alpha * ct["alpha"] / 255.0
                mul2 = mul * np.array([ct["multiply"]["r"], ct["multiply"]["g"], ct["multiply"]["b"]], np.float32) / 255.0
                add2 = add + np.array([ct["add"]["r"], ct["add"]["g"], ct["add"]["b"]], np.float32)
            draw(main, ch["id"], G2, a2, mul2, add2, ch["blend_mode"] or blend, tt)
        if saved_main is not None and content is not None:      # маска не закрыта - дорисуем как есть
            saved_main[:] = content + saved_main * (1.0 - content[..., 3:4] / 255.0)

    canvas = new()
    draw(canvas, ex["id"], [[1, 0, 0], [0, 1, 0]], 1.0, np.ones(3, np.float32), np.zeros(3, np.float32), 0, t)
    al = canvas[..., 3:4]
    rgb = np.where(al > 0, canvas[..., :3] * 255.0 / np.maximum(al, 1e-3), 0)
    out = Image.fromarray(np.clip(np.concatenate([rgb, al], 2), 0, 255).astype(np.uint8), "RGBA")
    if bg is not None:
        base = Image.new("RGBA", out.size, tuple(bg) + (255,))
        base.alpha_composite(out)
        return base.convert("RGB")
    return out


class DocEdit:
    """Правки документа SC2: новые id, матрицы, цвета, текстуры, шейпы и клипы."""

    def __init__(self, doc, folder):
        self.doc, self.folder = doc, folder
        self.clips = {c["id"]: c for c in doc["movieclips"]}
        self.shapes = {s["id"]: s for s in doc["shapes"]}
        self.mods = {m_["id"]: m_["type"] for m_ in doc["movieclip_modifiers"]}
        ids = [x["id"] for sec in ("shapes", "movieclips", "textfields", "movieclip_modifiers") for x in doc[sec]]
        ids += [e["id"] for e in doc["exports"]]
        self._nid = 1 + max(ids + [0])
        self._mcache, self._ccache = {}, {}
        if not doc["matrix_banks"]:
            doc["matrix_banks"].append({"matrices": [], "color_transforms": []})

    def new_id(self):
        while self._nid in self.clips or self._nid in self.shapes or self._nid in self.mods:
            self._nid += 1
        if self._nid > 65535:
            raise UserError("Не хватает id: в одном .sc их не может быть больше 65536")
        self._nid += 1
        return self._nid - 1

    def matrix(self, a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0, bank=0):
        m_ = {"a": float(a) + 0.0, "b": float(b) + 0.0, "c": float(c) + 0.0, "d": float(d) + 0.0,
              "tx": _q20(tx) + 0.0, "ty": _q20(ty) + 0.0}
        key = (bank, tuple(m_.values()))
        if key not in self._mcache:
            lst = self.doc["matrix_banks"][bank]["matrices"]
            lst.append(m_)
            self._mcache[key] = len(lst) - 1
        return self._mcache[key]

    def color(self, alpha=255, mul=(255, 255, 255), add=(0, 0, 0), bank=0):
        ct = {"alpha": int(max(0, min(255, round(alpha)))), "multiply": {"r": mul[0], "g": mul[1], "b": mul[2]},
              "add": {"r": add[0], "g": add[1], "b": add[2]}}
        key = (bank, repr(ct))
        if key not in self._ccache:
            lst = self.doc["matrix_banks"][bank]["color_transforms"]
            lst.append(ct)
            self._ccache[key] = len(lst) - 1
        return self._ccache[key]

    def texture(self, img):
        """PIL RGBA -> новая текстура документа (PNG в папке документа). Возвращает её номер."""
        idx = len(self.doc["textures"])
        name = f"texture_{idx}.png"
        img.convert("RGBA").save(os.path.join(self.folder, name))
        self.doc["textures"].append({"index": idx, "file": name, "pixel_format": 0, "encoding": 2, "khronos_format": 0,
                                     "linear": True, "filtering": 0, "downscaling": True,
                                     "width": img.size[0], "height": img.size[1]})
        return idx

    def shape(self, commands, sid=None):
        sid = self.new_id() if sid is None else sid
        self.doc["shapes"].append({"id": sid, "commands": commands})
        self.shapes[sid] = self.doc["shapes"][-1]
        return sid

    @staticmethod
    def quad_cmd(tex, x0, y0, x1, y1, uv=(0.0, 0.0, 1.0, 1.0)):
        u0, v0, u1, v1 = uv
        return {"type": 1, "texture_index": tex, "vertices": [
            {"x": x1, "y": y0, "u": u1, "v": v0}, {"x": x1, "y": y1, "u": u1, "v": v1},
            {"x": x0, "y": y0, "u": u0, "v": v0}, {"x": x0, "y": y1, "u": u0, "v": v1}]}

    def clip(self, frames, fps=60, bank=0, cid=None):
        """frames: [[(id ребёнка, индекс матрицы или None, индекс цвета или None), ...], ...]. Возвращает id клипа."""
        cid = self.new_id() if cid is None else cid
        c = {"id": cid, "frame_rate": fps, "bank_index": bank, "unknown_flag": False, "childrens": [], "frames": [],
             "frame_elements": []}
        self.doc["movieclips"].append(c)
        self.clips[cid] = c
        self.fill_clip(cid, frames)
        return cid

    def fill_clip(self, cid, frames, keep_children=False):
        c = self.clips[cid]
        old = {(ch["id"], ch["blend_mode"], ch["name"]) for ch in c["childrens"]} if keep_children else set()
        kids, idx, els, fr = list(c["childrens"]) if keep_children else [], {}, [], []
        for i, ch in enumerate(kids):
            idx.setdefault((ch["id"], ch["blend_mode"], ch["name"]), i)
        for f in frames:
            fr.append({"label": "", "elements_count": len(f)})
            for it in f:
                oid, mi, ci = it[0], it[1], it[2]
                blend, name = (it[3] if len(it) > 3 else 0), (it[4] if len(it) > 4 else "")
                key = (oid, blend, name)
                if key not in idx:
                    idx[key] = len(kids)
                    kids.append({"id": oid, "blend_mode": blend, "name": name})
                els.append({"instance_index": idx[key], "matrix_index": 0xFFFF if mi is None else mi,
                            "colorTransform_index": 0xFFFF if ci is None else ci})
        c["childrens"], c["frames"], c["frame_elements"] = kids, fr, els

    def clip_frame(self, cid, fi):
        """Элементы кадра fi клипа: [(id ребёнка, matrix_index, color_index, blend, name), ...]."""
        c = self.clips[cid]
        start = sum(f["elements_count"] for f in c["frames"][:fi])
        out = []
        for e in c["frame_elements"][start:start + c["frames"][fi]["elements_count"]]:
            ch = c["childrens"][e["instance_index"]]
            out.append((ch["id"], None if e["matrix_index"] == 0xFFFF else e["matrix_index"],
                        None if e["colorTransform_index"] == 0xFFFF else e["colorTransform_index"],
                        ch["blend_mode"], ch["name"]))
        return out

    def mat(self, bank, mi):
        """Матрица из банка как кортеж (a, b, c, d, tx, ty); None = единичная."""
        if mi is None:
            return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        m_ = self.doc["matrix_banks"][bank]["matrices"][mi]
        return (m_["a"], m_["b"], m_["c"], m_["d"], m_["tx"], m_["ty"])


def mat_apply(m_, x, y):
    return (m_[0] * x + m_[2] * y + m_[4], m_[1] * x + m_[3] * y + m_[5])




# ---------------------------------------------------------------- части картинок: шейп -> PNG и обратно

def shape_quad(doc, shape, tex_sizes):
    """Если шейп - одна команда, чьи вершины лежат в прямоугольнике и текстурные координаты линейно зависят от
    положения (обычный прямоугольник, прямоугольник с обрезанными углами, повёрнутый в атласе на 90° или
    отражённый), возвращает описание: tex, rect (x0, y0, x1, y1) в единицах клипа, box (px0, py0, px1, py1) -
    кусок текстуры в пикселях, trans (поворот/отражение). Иначе None."""
    import numpy as np
    cmds = shape["commands"]
    if len(cmds) != 1 or len(cmds[0]["vertices"]) < 4:
        return None
    c = cmds[0]
    TW, TH = tex_sizes[c["texture_index"]]
    v = c["vertices"]
    xs, ys = np.array([q["x"] for q in v]), np.array([q["y"] for q in v])
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
        return None
    U = np.array([q["u"] * TW for q in v])
    V = np.array([q["v"] * TH for q in v])
    if (U.max() - U.min()) < 1.5 or (V.max() - V.min()) < 1.5:
        return None                                                     # один тексель - сплошная заливка
    A = np.stack([xs, ys, np.ones_like(xs)], 1)
    cu, *_ = np.linalg.lstsq(A, U, rcond=None)
    cv, *_ = np.linalg.lstsq(A, V, rcond=None)
    if np.abs(A @ cu - U).max() > 1.0 or np.abs(A @ cv - V).max() > 1.0:
        return None                                                     # текстура искажена - не прямоугольная часть
    corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    P = [(cu[0] * x + cu[1] * y + cu[2], cv[0] * x + cv[1] * y + cv[2]) for x, y in corners]
    px0, px1 = min(p[0] for p in P), max(p[0] for p in P)
    py0, py1 = min(p[1] for p in P), max(p[1] for p in P)
    tl, tr, bl = P[0], P[1], P[2]
    dxp = (tr[0] - tl[0], tr[1] - tl[1])                                # куда идёт ось x
    dyp = (bl[0] - tl[0], bl[1] - tl[1])                                # куда идёт ось y
    if abs(dxp[1]) <= 0.02 * abs(dxp[0]) + 0.75 and abs(dyp[0]) <= 0.02 * abs(dyp[1]) + 0.75:
        trans = ("none", dxp[0] < 0, dyp[1] < 0)
    elif abs(dxp[0]) <= 0.02 * abs(dxp[1]) + 0.75 and abs(dyp[1]) <= 0.02 * abs(dyp[0]) + 0.75:
        trans = ("swap", dxp[1] < 0, dyp[0] < 0)
    else:
        return None
    return {"tex": c["texture_index"], "rect": (float(x0), float(y0), float(x1), float(y1)),
            "box": (float(px0), float(py0), float(px1), float(py1)), "trans": trans}


def shape_part_image(Image, tex_img, q):
    """Вырезает часть из атласа и возвращает её "как есть на экране" (без поворота и отражений)."""
    px0, py0, px1, py1 = [int(round(t)) for t in q["box"]]
    crop = tex_img.crop((px0, py0, px1, py1))
    kind, fx, fy = q["trans"]
    tr = getattr(Image, "Transpose", Image)
    if kind == "swap":
        crop = crop.transpose(tr.TRANSPOSE)
    if fx:
        crop = crop.transpose(tr.FLIP_LEFT_RIGHT)
    if fy:
        crop = crop.transpose(tr.FLIP_TOP_BOTTOM)
    return crop


def shape_part_cut(Image, img, shape, q, tex_sizes):
    """Убирает с вырезанной части всё, что не входит в многоугольник шейпа (соседние объекты атласа в углах
    прямоугольника): пиксели вне многоугольника становятся прозрачными. img - результат shape_part_image."""
    from PIL import ImageDraw
    cmd = shape["commands"][0]
    if len(cmd["vertices"]) <= 4:
        return img
    TW, TH = tex_sizes[q["tex"]]
    px0, py0, px1, py1 = [int(round(t)) for t in q["box"]]
    w, h = px1 - px0, py1 - py0
    K = 4                                                       # сглаживание краёв: рисуем в 4 раза крупнее
    big = Image.new("L", (w * K, h * K), 0)
    dr = ImageDraw.Draw(big)
    pts = [((v["u"] * TW - px0) * K, (v["v"] * TH - py0) * K) for v in cmd["vertices"]]
    for i in range(len(pts) - 2):
        dr.polygon([pts[i], pts[i + 1], pts[i + 2]], fill=255)
    mask = big.resize((w, h), getattr(Image, "Resampling", Image).BOX)
    mask = mask.point(lambda v: 255 if v > 0 else 0)            # любой захваченный пиксель остаётся целиком
    tr = getattr(Image, "Transpose", Image)
    kind, fx, fy = q["trans"]
    if kind == "swap":
        mask = mask.transpose(tr.TRANSPOSE)
    if fx:
        mask = mask.transpose(tr.FLIP_LEFT_RIGHT)
    if fy:
        mask = mask.transpose(tr.FLIP_TOP_BOTTOM)
    out = img.convert("RGBA")
    a = out.getchannel("A")
    from PIL import ImageChops
    out.putalpha(ImageChops.multiply(a, mask))
    return out


def unpremultiply(Image, im):
    import numpy as np
    a = np.asarray(im.convert("RGBA"), dtype=np.float32)
    al = a[..., 3:4]
    rgb = np.where(al > 0, np.minimum(a[..., :3] * 255.0 / np.maximum(al, 1.0), 255.0), 0.0)
    return Image.fromarray(np.concatenate([rgb, al], 2).round().astype(np.uint8), "RGBA")


def premultiply_img(Image, im):
    import numpy as np
    a = np.asarray(im.convert("RGBA"), dtype=np.float32)
    rgb = a[..., :3] * a[..., 3:4] / 255.0
    return Image.fromarray(np.concatenate([rgb, a[..., 3:4]], 2).round().astype(np.uint8), "RGBA")


def fit_rect(rect, iw, ih, mode, scale=1.0):
    """Куда положить картинку iw x ih относительно прямоугольника rect.
    mode: 'stretch' - растянуть на весь прямоугольник; 'contain' - вписать целиком (центр сохраняется);
    'cover' - закрыть весь прямоугольник (лишнее обрежется окном, здесь возвращается большой прямоугольник).
    scale - дополнительный множитель (для contain/cover). Возвращает (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    if mode == "stretch":
        return rect
    k = (min if mode == "contain" else max)(w / iw, h / ih) * scale
    return (cx - iw * k / 2.0, cy - ih * k / 2.0, cx + iw * k / 2.0, cy + ih * k / 2.0)


def doc_tex_sizes(doc):
    return {i: (t["width"], t["height"]) for i, t in enumerate(doc["textures"])}


def doc_repack_atlas(doc, folder, limit=4096, pad=2, say=None):
    """Пересобирает атласы документа: только то, что нужно шейпам (с нуля или со слиянием - что меньше), до limit
    по стороне. Документ меняется на месте, новые текстуры пишутся в folder/atlas/. Возвращает (было_пикс, стало_пикс)."""
    from PIL import Image
    cmds = []
    for sh in doc["shapes"]:
        for c in sh["commands"]:
            if len(c["vertices"]) >= 3:
                cmds.append({"tex": c["texture_index"], "ref": c,
                             "uv": [(int(round(q["u"] * 65535)), int(round(q["v"] * 65535))) for q in c["vertices"]]})
    if not cmds:
        return 0, 0
    used = sorted({c["tex"] for c in cmds})
    old_imgs, old_sizes = {}, {}
    for t in used:
        tx = doc["textures"][t]
        with Image.open(os.path.join(folder, tx.get("file") or f"texture_{t}.png")) as im:
            old_imgs[t] = im.convert("RGBA")
        old_sizes[t] = old_imgs[t].size
    plan_a = atlas_plan(old_sizes, cmds, pad, limit, False, False, False, say, True)
    plan_b = atlas_plan(old_sizes, cmds, pad, limit, False, True, False, say, True)
    area = lambda p_: (sum(w * h for w, h in p_["sizes"]), len(p_["sizes"]))
    plan = plan_a if area(plan_a) <= area(plan_b) else plan_b
    pages = atlas_build_pages(Image, plan, old_imgs, cmds, old_sizes, pad, True)
    new_cmds = atlas_remap(plan, cmds, old_sizes)
    out_dir = os.path.join(folder, "atlas")
    os.makedirs(out_dir, exist_ok=True)
    for c, (bi, uv) in zip(cmds, new_cmds):
        ref = c["ref"]
        ref["texture_index"] = bi
        for q, (u, v) in zip(ref["vertices"], uv):
            q["u"], q["v"] = u / 65535.0, v / 65535.0
    base = dict(doc["textures"][used[0]])
    textures = []
    for i, pg in enumerate(pages):
        name = os.path.join("atlas", f"texture_{i}.png")
        pg.save(os.path.join(folder, name))
        t = dict(base)
        t.update({"index": i, "file": name, "pixel_format": 0, "encoding": 2, "khronos_format": 0,
                  "width": pg.size[0], "height": pg.size[1]})
        t.pop("tex_flags_raw", None)
        textures.append(t)
    before = sum(w * h for w, h in old_sizes.values())
    doc["textures"] = textures
    return before, sum(p_.size[0] * p_.size[1] for p_ in pages)


def file_md5(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()




# ================================================================ ЗАГРУЗОЧНЫЙ ЭКРАН (loading.sc)
#
# Разбор: из loading.sc вырезаются картинки-части (фон экрана, лого игры по языкам, лого SUPERCELL при запуске,
# иконки и кнопки) в обычные PNG "как на экране" - без поворота, который у них в атласе. Рядом лежат
# parts.json (где каждая часть стоит в клипах) и template.sc (весь исходный файл как шаблон: клипы, текстовые
# поля, полоса загрузки, кнопки). Правишь нужные PNG, собираешь обратно - заменяются только изменённые части.

LOADING_SIZE_MIN = 12.0            # части меньше (в единицах клипа) не вырезаем - это точки и блики внутри клипов


def loading_find_parts(doc, folder, min_size=None, roles=True):
    """Все одиночные прямоугольные шейпы с текстурой, достижимые от экспортов. [{name, shape, export, ...}]"""
    from PIL import Image
    clips = {c["id"]: c for c in doc["movieclips"]}
    shapes = {s["id"]: s for s in doc["shapes"]}
    sizes = doc_tex_sizes(doc)
    found, seen_clips = {}, set()

    def walk(oid, names, export):
        if oid in shapes:
            if oid in found:
                return
            q = shape_quad(doc, shapes[oid], sizes)
            if q is None:
                return
            w, h = q["rect"][2] - q["rect"][0], q["rect"][3] - q["rect"][1]
            if min(w, h) < (LOADING_SIZE_MIN if min_size is None else min_size):
                return
            found[oid] = {"shape": oid, "export": export, "base": names[-1] if names else export, "q": q,
                          "area": w * h}
            return
        c = clips.get(oid)
        if c is None or oid in seen_clips:
            return
        seen_clips.add(oid)
        for ch in c["childrens"]:
            walk(ch["id"], names + ([ch["name"]] if ch["name"] else []), export)

    for e in doc["exports"]:
        walk(e["id"], [], e["name"])
    parts = sorted(found.values(), key=lambda p_: (p_["export"], -p_["area"], p_["shape"]))
    # роли: самая большая картинка экрана - фон; в интро - лого SUPERCELL и блик
    by_exp = {}
    for p_ in parts:
        by_exp.setdefault(p_["export"], []).append(p_)
    for ex, lst in (by_exp.items() if roles else ()):
        low = ex.lower()
        if "intro" in low:
            names = ["supercell_logo", "supercell_shine"]
            for p_, nm in zip(lst, names):
                p_["base"] = nm
        elif "loading" in low and lst and lst[0]["area"] > 200000:
            lst[0]["base"] = "background"
    used, out = set(), []
    for p_ in parts:
        base = re.sub(r"[^A-Za-z0-9_.-]+", "_", p_["base"]).strip("_") or f"shape_{p_['shape']}"
        nm, n = base, 2
        while nm.lower() in used:
            nm, n = f"{base}_{n}", n + 1
        used.add(nm.lower())
        p_["name"] = nm
        out.append(p_)
    return out


LOADING_README = """Части загрузочного экрана (разобрано хабом SC Hub)

Что здесь лежит
  parts.json   - список частей: где каждая стоит в клипах (не трогай, по нему идёт сборка)
  template.sc  - весь исходный файл как шаблон (клипы, текстовые поля, полоса загрузки, кнопки)
  *.png        - части картинками "как на экране", без поворотов атласа; прозрачность обычная (не предумноженная)
  preview.png  - как выглядит экран сейчас

Как менять
  1. Открой нужный PNG в редакторе и перерисуй (можно другого размера: картинка вписывается в ту же рамку,
     фон по умолчанию закрывает рамку целиком с обрезкой лишнего).
  2. В SC Hub: пункт 20, режим «собрать» - выбери эту папку. Заменяются только те части, которые изменились.
  3. Лого игры: либо поменяй logo_EN.png, logo_KR.png и остальные, либо при сборке укажи один PNG для всех языков.
  4. Лого SUPERCELL при запуске: поменяй supercell_logo.png или выбери при сборке плавную смену
     «SUPERCELL -> своё лого» (диагональный переход от левого нижнего угла к правому верхнему).
"""


def loading_split(src, out_dir, report=print, min_size=None, roles=True, readme=None):
    """Разбирает loading-файл src в папку out_dir. Возвращает список созданных файлов."""
    import json
    import shutil
    import tempfile
    from PIL import Image
    tmp = tempfile.mkdtemp(prefix="schub_load_")
    made = []
    try:
        doc, warns, kind = load_source_doc(src, tmp)
        premult = doc_premultiplied(doc, tmp)
        parts = loading_find_parts(doc, tmp, min_size, roles)
        if not parts:
            raise UserError("В файле не нашлось картинок-частей (одиночных прямоугольных шейпов нужного размера)")
        texs = {}
        shapes_by_id = {x["id"]: x for x in doc["shapes"]}
        sizes = doc_tex_sizes(doc)
        manifest_parts = []
        os.makedirs(out_dir, exist_ok=True)
        for p_ in parts:
            q = p_["q"]
            if q["tex"] not in texs:
                with Image.open(os.path.join(tmp, doc["textures"][q["tex"]].get("file") or f"texture_{q['tex']}.png")) as im:
                    texs[q["tex"]] = im.convert("RGBA")
            img = shape_part_image(Image, texs[q["tex"]], q)
            img = shape_part_cut(Image, img, shapes_by_id[p_["shape"]], q, sizes)
            if premult:
                img = unpremultiply(Image, img)
            fname = p_["name"] + ".png"
            fp = os.path.join(out_dir, fname)
            img.save(fp)
            made.append(fp)
            manifest_parts.append({"name": p_["name"], "file": fname, "shape": p_["shape"], "export": p_["export"],
                                   "rect": [round(t, 3) for t in q["rect"]], "px": list(img.size),
                                   "md5": file_md5(fp)})
        tpl = os.path.join(out_dir, "template.sc")
        if kind == "sc2":
            shutil.copyfile(src, tpl)
        else:
            main, _t, _w = encode_doc(doc, tmp, False)
            with open(tpl, "wb") as fh:
                fh.write(main)
        made.append(tpl)
        man = {"format": "schub-loading-parts", "version": 1, "hub": HUB_VERSION, "source": os.path.basename(src),
               "template": "template.sc", "premultiplied": premult, "parts": manifest_parts}
        mp = os.path.join(out_dir, "parts.json")
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(man, fh, ensure_ascii=False, indent=1)
        made.append(mp)
        rp = os.path.join(out_dir, "README.txt")
        with open(rp, "w", encoding="utf-8") as fh:
            fh.write(readme or LOADING_README)
        made.append(rp)
        for e in doc["exports"]:
            if e["name"].lower().startswith("loading") or e["name"].lower().startswith("sc_intro"):
                pass
        try:
            pv = loading_preview(doc, tmp, premult) if roles else None
            if pv is not None:
                pp = os.path.join(out_dir, "preview.png")
                pv.save(pp)
                made.append(pp)
        except Exception as e:                           # превью - удобство
            report(yellow(f"  Превью не получилось: {e}"))
        return made, manifest_parts, warns
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- сборка загрузочного экрана из частей

def poly_clip_halfplane(poly, f, lo=None, hi=None):
    """Отсекает выпуклый многоугольник poly [(x, y)] по условиям lo <= f(x, y) <= hi (f линейна)."""
    def clip(pts, g):                                   # оставляет g(p) >= 0
        out = []
        for i, p_ in enumerate(pts):
            q = pts[(i + 1) % len(pts)]
            gp, gq = g(p_), g(q)
            if gp >= 0:
                out.append(p_)
            if (gp >= 0) != (gq >= 0):
                k = gp / (gp - gq)
                out.append((p_[0] + (q[0] - p_[0]) * k, p_[1] + (q[1] - p_[1]) * k))
        return out
    if lo is not None and poly:
        poly = clip(poly, lambda p_: f(*p_) - lo)
    if hi is not None and poly:
        poly = clip(poly, lambda p_: hi - f(*p_))
    return poly


def poly_to_strip(poly):
    """Выпуклый многоугольник -> порядок вершин для полосы треугольников (зигзаг с двух концов)."""
    out, i, j = [], 0, len(poly) - 1
    while i <= j:
        out.append(poly[i])
        if i != j:
            out.append(poly[j])
        i, j = i + 1, j - 1
    return out


def loading_load_image(path, premult, max_side=4096):
    """PNG/что умеет PIL -> RGBA для атласа (предумноженный, если так хранит шаблон), не больше max_side."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            img = im.convert("RGBA")
    except Exception as e:
        raise UserError(f"Не смог открыть картинку «{path}»: {e}")
    if max(img.size) > max_side:
        k = max_side / max(img.size)
        img = img.resize((max(1, round(img.size[0] * k)), max(1, round(img.size[1] * k))), Image.LANCZOS)
    return premultiply_img(Image, img) if premult else img


def loading_set_part(ed, shape_id, img, rect, mode, scale=1.0):
    """Заменяет картинку шейпа: новая текстура + один прямоугольник по правилу mode относительно старого rect."""
    tex = ed.texture(img)
    x0, y0, x1, y1 = fit_rect(rect, img.size[0], img.size[1], mode, scale)
    ed.shapes[shape_id]["commands"] = [ed.quad_cmd(tex, x0, y0, x1, y1)]
    return (x0, y0, x1, y1)


def loading_find_intro_clip(doc, shape_id):
    """Самый длинный клип, у которого шейп - прямой ребёнок (таймлайн интро)."""
    best = None
    for c in doc["movieclips"]:
        if any(ch["id"] == shape_id for ch in c["childrens"]) and (best is None or len(c["frames"]) > len(best["frames"])):
            best = c
    return best


def loading_intro_transition(ed, clip, logo_shape, rect, img1, img2, scale2, seconds, hold_frames=12, bands=28):
    """Меняет таймлайн интро: SUPERCELL -> img2 плавной диагональной волной от левого нижнего угла к правому
    верхнему. Волна - диагональные полосы с нарастающей задержкой (у каждой своё плавное затухание/появление),
    маски не нужны. Возвращает (кадров_было, кадров_стало, id целого шейпа второго лого)."""
    cid, fps = clip["id"], clip["frame_rate"] or 60
    n = len(clip["frames"])
    frames = [ed.clip_frame(cid, i) for i in range(n)]
    logo_idx = [[k for k, it in enumerate(f) if it[0] == logo_shape] for f in frames]
    # A - первый кадр затухания логотипа (до него он показан полностью)
    first = next((i for i, k in enumerate(logo_idx) if k), None)
    if first is None:
        raise UserError("В таймлайне интро не нашёл кадров с логотипом SUPERCELL")
    ctof = lambda i: frames[i][logo_idx[i][0]][2] if logo_idx[i] else "gone"
    full = next((i for i in range(first, n) if ctof(i) is None), None)       # логотип появился полностью
    A = None
    if full is not None:
        A = next((i for i in range(full, n) if ctof(i) is not None), None)  # начало затухания
    if A is None:
        A = next((i for i in range(first, n) if not logo_idx[i]), n) - 5
    A = max(first + 1, A)
    base = frames[A - 1]
    li = logo_idx[A - 1][0]
    mi, ci0 = base[li][1], base[li][2]
    rect2 = fit_rect(rect, img2.size[0], img2.size[1], "contain", scale2)
    rects = [rect, rect2]
    R = (min(rect[0], rect2[0]), min(rect[1], rect2[1]), max(rect[2], rect2[2]), max(rect[3], rect2[3]))
    RW, RH = R[2] - R[0], R[3] - R[1]
    pn = lambda x, y: ((x - R[0]) / RW + (R[3] - y) / RH) / 2.0       # 0 - левый нижний угол, 1 - правый верхний
    tex1, tex2 = ed.texture(img1), ed.texture(img2)
    whole2 = ed.shape([ed.quad_cmd(tex2, *rect2)])
    band_shapes = []
    for j in range(bands):
        lo, hi = j / bands, (j + 1) / bands
        pair = []
        for rc, tex in zip(rects, (tex1, tex2)):
            poly = [(rc[0], rc[1]), (rc[2], rc[1]), (rc[2], rc[3]), (rc[0], rc[3])]
            poly = poly_clip_halfplane(poly, pn, lo, hi)
            if len(poly) < 3:
                pair.append(None)
                continue
            strip = poly_to_strip(poly)
            verts = [{"x": x, "y": y, "u": (x - rc[0]) / (rc[2] - rc[0]), "v": (y - rc[1]) / (rc[3] - rc[1])}
                     for x, y in strip]
            pair.append(ed.shape([{"type": 1, "texture_index": tex, "vertices": verts}]))
        band_shapes.append((pair, (j + 0.5) / bands))
    D = max(8, int(round(seconds * fps)))
    w = 0.4
    sm = lambda t: t * t * (3 - 2 * t)
    new = [list(f) for f in frames[:A]]
    for t in range(D):
        g = t / (D - 1)
        f = []
        for k, it in enumerate(base):
            if k != li:
                f.append(it)
                continue
            for pair, pc in band_shapes:
                s = sm(max(0.0, min(1.0, (g * (1 + w) - pc) / w)))
                for sid, a in ((pair[0], 1 - s), (pair[1], s)):
                    if sid is not None and a > 0.004:
                        f.append((sid, mi, ed.color(255 * a), 0, ""))
        new.append(f)
    for _ in range(hold_frames):
        new.append([(whole2, mi, ci0, 0, "") if it[0] == logo_shape else it for it in base])
    for i in range(A, n):
        new.append([(whole2, it[1], it[2], it[3], it[4]) if it[0] == logo_shape else it for it in frames[i]])
    ed.fill_clip(cid, new)
    return n, len(new), whole2


def loading_hide_children(doc, names):
    """Копия документа без детей с данными именами (для превью: не показывать все языковые лого сразу)."""
    import copy
    names = set(names)
    out = copy.copy(doc)
    clips = []
    for c in doc["movieclips"]:
        drop = {i for i, ch in enumerate(c["childrens"]) if ch["name"] in names}
        if not drop:
            clips.append(c)
            continue
        c2 = dict(c)
        els, fr, pos = [], [], 0
        for f in c["frames"]:
            keep = [e for e in c["frame_elements"][pos:pos + f["elements_count"]] if e["instance_index"] not in drop]
            pos += f["elements_count"]
            els.extend(keep)
            fr.append(dict(f, elements_count=len(keep)))
        c2["frames"], c2["frame_elements"] = fr, els
        clips.append(c2)
    out["movieclips"] = clips
    return out


def loading_preview(doc, folder, premult, shots_t=None):
    """Превью одной картинкой: экран (только EN-лого) и кадры интро shots_t (по умолчанию - три по таймлайну)."""
    from PIL import Image
    names = {e["name"].lower(): e["name"] for e in doc["exports"]}
    shots = []
    scr = next((names[n] for n in names if n.startswith("loading")), None)
    if scr:
        d2 = loading_hide_children(doc, {"logo_KR", "logo_JP", "logo_CNT", "logo_CNS", "icon_prc_age", "buttons_tencent", "buttons_yoozoo"})
        shots.append(render_scene(d2, folder, scr, 0, 720, (0, 0, 1300, 780), bg=(70, 70, 70), premultiplied=premult))
    intro = next((names[n] for n in names if n.startswith("sc_intro")), None)
    if intro:
        ex = next(e for e in doc["exports"] if e["name"] == intro)
        ic = next(c for c in doc["movieclips"] if c["id"] == ex["id"])
        n = len(ic["frames"])
        ts = [t for t in (shots_t or [40, 85, n - 12]) if 0 <= t < n]
        row = [render_scene(doc, folder, intro, t, 1440 // len(ts), (0, 0, 1000, 560), bg=(70, 70, 70), premultiplied=premult)
               for t in ts]
        sheet = Image.new("RGB", (sum(r.size[0] for r in row), row[0].size[1]))
        x = 0
        for r in row:
            sheet.paste(r, (x, 0))
            x += r.size[0]
        shots.append(sheet)
    if not shots:
        return None
    W = max(s.size[0] for s in shots)
    out = Image.new("RGB", (W, sum(s.size[1] for s in shots)), (70, 70, 70))
    y = 0
    for s in shots:
        out.paste(s, (0, y))
        y += s.size[1]
    return out


def doc_finalize(doc, tmp, out_path, astc=False, report=print):
    """Подрезает документ до достижимого, пересобирает атлас (до 4096), кодирует SC2 и пишет файл."""
    sub = doc_subset(doc, [e["name"] for e in doc["exports"]])
    before, after = doc_repack_atlas(sub, tmp)
    report(gray(f"  Атлас: {before / 1e6:.1f} -> {after / 1e6:.1f} млн пикселей"))
    main, _t, warns = encode_doc(sub, tmp, astc)
    with open(out_path, "wb") as fh:
        fh.write(main)
    return sub, warns


def loading_build(folder, out_path, o, report=print):
    """Собирает загрузочный экран из папки частей. o: bg_image, bg_mode, bg_scale, logo_all, logo_scale, intro
    ('keep' | 'replace' | 'transition'), intro_logo, intro_scale, intro_seconds, astc, preview. Возвращает пути."""
    import json
    import shutil
    import tempfile
    from PIL import Image
    mp = os.path.join(folder, "parts.json")
    if not os.path.isfile(mp):
        raise UserError("В папке нет parts.json - выбери папку, которую создал режим «разобрать»")
    with open(mp, encoding="utf-8") as fh:
        man = json.load(fh)
    tpl = os.path.join(folder, man["template"])
    if not os.path.isfile(tpl):
        raise UserError("В папке нет template.sc")
    tmp = tempfile.mkdtemp(prefix="schub_loadb_")
    results = []
    try:
        doc, warns, _k = load_source_doc(tpl, tmp)
        premult = bool(man.get("premultiplied"))
        ed = DocEdit(doc, tmp)
        changed = []
        parts = {p_["name"]: p_ for p_ in man["parts"]}
        for p_ in man["parts"]:
            name, fp = p_["name"], os.path.join(folder, p_["file"])
            if p_["shape"] not in ed.shapes:
                continue
            src, mode, scale = None, "contain", 1.0
            if name == "background" and o.get("bg_image"):
                src, mode, scale = o["bg_image"], o.get("bg_mode", "cover"), o.get("bg_scale", 1.0)
            elif name.startswith("logo_") and o.get("logo_all"):
                src, scale = o["logo_all"], o.get("logo_scale", 1.0)
            elif name == "supercell_logo" and o.get("intro") == "replace" and o.get("intro_logo"):
                src, scale = o["intro_logo"], o.get("intro_scale", 1.0)
            elif os.path.isfile(fp) and file_md5(fp) != p_["md5"]:
                src = fp
                mode = o.get("bg_mode", "cover") if name == "background" else "contain"
            if src is None:
                continue
            img = loading_load_image(src, premult)
            with Image.open(fp if os.path.isfile(fp) else src) as ref:
                same = tuple(img.size) == tuple(p_["px"]) and src == fp
            loading_set_part(ed, p_["shape"], img, tuple(p_["rect"]), "stretch" if same else mode, scale)
            changed.append(name)
        if changed:
            report(gray("  Заменено: " + ", ".join(changed)))
        sc = parts.get("supercell_logo")
        shots_t = None
        if o.get("intro") == "transition":
            if not sc or not o.get("intro_logo"):
                raise UserError("Для перехода нужны supercell_logo.png и второе лого")
            clip = loading_find_intro_clip(doc, sc["shape"])
            if clip is None:
                raise UserError("Не нашёл клип интро с логотипом SUPERCELL")
            img1 = loading_load_image(os.path.join(folder, sc["file"]), premult)
            img2 = loading_load_image(o["intro_logo"], premult)
            D_ = max(8, int(round(o.get("intro_seconds", 0.8) * 60)))
            a, b, _w = loading_intro_transition(ed, clip, sc["shape"], tuple(sc["rect"]), img1, img2,
                                                o.get("intro_scale", 1.0), o.get("intro_seconds", 0.8))
            shots_t = [80 + D_ // 5, 80 + D_ * 2 // 5, 80 + D_ * 3 // 5, 80 + D_ * 4 // 5, b - 20]
            report(gray(f"  Интро: {a} -> {b} кадров ({a / 60:.1f} -> {b / 60:.1f} с при 60 к/с)"))
        sub, w2 = doc_finalize(doc, tmp, out_path, o.get("astc", False), report)
        results.append(out_path)
        saved(out_path)
        for w_ in list(warns) + list(w2):
            report(yellow("  ⚠ " + w_))
        if o.get("preview", True):
            pdir = tempfile.mkdtemp(prefix="schub_loadp_")
            try:
                d2, _w, _k = load_source_doc(out_path, pdir)           # смотрим на готовый файл, а не на черновик
                img = loading_preview(d2, pdir, premult, shots_t)
                if img is not None:
                    pp = os.path.splitext(out_path)[0] + "_preview.png"
                    img.save(pp)
                    saved(pp)
                    results.append(pp)
            except Exception as e:
                report(yellow(f"  Превью не получилось: {e}"))
            finally:
                shutil.rmtree(pdir, ignore_errors=True)
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- пункты меню 20 и 21 (загрузочный экран, части .sc)

def need_numpy():
    ensure_deps()
    ensure_numpy_optional()
    try:
        import numpy  # noqa: F401
    except ImportError:
        raise UserError("Для этого пункта нужен numpy (pip install numpy) - поставить автоматически не получилось")


def ask_float(prompt, default):
    raw = ask(prompt, str(default)).replace(",", ".")
    try:
        v = float(raw)
    except ValueError:
        raise UserError(f"«{raw}» - не число")
    if v <= 0:
        raise UserError("Нужно число больше нуля")
    return v


def flow_loading():
    need_numpy()
    print("  Что сделать:")
    print("    1 - разобрать загрузочный экран (loading.sc) на части: PNG + шаблон")
    print("    2 - собрать загрузочный экран из папки с частями (заменить фон, лого игры, лого SUPERCELL)")
    mode = ask("Что сделать", "1").strip()
    if mode == "2":
        return flow_loading_build()
    src = resolve_input("Загрузочный файл (loading.sc)", "loading.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    out_dir = resolve_out_dir(ask("Папка для частей", "loading_parts"))
    print(gray("  Разбираю..."))
    made, parts, warns = loading_split(src, out_dir, print)
    for p_ in parts:
        print(f"    {p_['name']:<24}{p_['px'][0]} x {p_['px'][1]}  ({p_['export']})")
    for w in warns:
        print(yellow("  ⚠ " + w))
    print(green("  ✔ Части и шаблон: ") + os.path.abspath(out_dir))
    print(gray("  Перерисуй нужные PNG (или оставь как есть) и запусти этот пункт ещё раз в режиме 2."))
    return made


def flow_loading_build():
    folder = resolve_input("Папка с частями (её создал режим 1)", "loading_parts", [], allow_dir=True)
    if not os.path.isdir(folder):
        raise UserError("Нужна папка, а не файл")
    o = {}
    print(gray("  Пустой ответ = не менять. Файлы можно указывать полным путём или именем из папки input."))
    def pick(prompt):
        raw = unquote(ask(prompt + gray(" (? - выбрать в окне)"), "")).strip()
        if not raw:
            return None
        if raw == "?":
            raw = file_dialog(prompt, [("Картинки", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Все файлы", "*.*")])
            if not raw:
                return None
        for cand in ([raw] if os.path.isabs(raw) else [os.path.join(IN_DIR, raw), os.path.join(OUT_DIR, raw),
                                                         os.path.join(APP_DIR, raw), os.path.abspath(raw)]):
            if os.path.isfile(cand):
                return cand
        raise UserError(f"Не нашёл «{raw}». Положи файл в папку input:\n  {IN_DIR}")
    bg = pick("Свой фон экрана (картинка)")
    if bg:
        o["bg_image"] = bg
        m = ask("Как вписать: 1 - закрыть весь экран с обрезкой, 2 - целиком с полями, 3 - растянуть", "1")
        o["bg_mode"] = {"2": "contain", "3": "stretch"}.get(m, "cover")
        if o["bg_mode"] != "stretch":
            o["bg_scale"] = ask_float("Дополнительный масштаб фона", 1)
    lg = pick("Своё лого игры - одна картинка для всех языков")
    if lg:
        o["logo_all"] = lg
        o["logo_scale"] = ask_float("Масштаб лого (1 = вписать в рамку прежнего)", 1)
    print("  Лого SUPERCELL при запуске: 1 - как есть (или как в supercell_logo.png), 2 - заменить своим, "
          "3 - сначала SUPERCELL, потом плавный переход к своему лого")
    im = ask("Выбор", "1").strip()
    if im in ("2", "3"):
        il = pick("Своё лого для интро")
        if not il:
            raise UserError("Нужна картинка для лого")
        o["intro"] = "replace" if im == "2" else "transition"
        o["intro_logo"] = il
        o["intro_scale"] = ask_float("Масштаб лого (1 = вписать в рамку SUPERCELL)", 1)
        if im == "3":
            o["intro_seconds"] = ask_float("Длительность перехода, сек", 0.8)
    o["astc"] = ask_astc()
    name = ask("Выходной файл", "loading").strip()
    out = resolve_output(name, ".sc")
    print(gray("  Собираю..."))
    res = loading_build(folder, out, o, print)
    print(gray("  Проверено только по рендеру и разбору готового файла; в игре поведение не проверялось."))
    return res


PARTS_README = """Части .sc (разобрано хабом SC Hub)

  parts.json   - список частей и где они стоят (не трогай, по нему идёт сборка)
  template.sc  - весь исходный файл как шаблон
  *.png        - части картинками "как на экране", без поворотов атласа; прозрачность обычная (не предумноженная);
                 всё, что не входило в форму шейпа (соседи по атласу), убрано

Как менять: перерисуй нужные PNG (можно другого размера - вписывается в ту же рамку) и собери обратно в SC Hub,
пункт 21, режим 2. Заменяются только изменённые файлы. Части с анимацией по кадрам, кривые многоугольники и
шейпы из нескольких кусков в части не попадают и остаются как в шаблоне.
"""


def flow_parts():
    need_numpy()
    print("  Что сделать:")
    print("    1 - разобрать любой .sc на части (PNG + шаблон)")
    print("    2 - собрать .sc обратно из папки с частями (после правок PNG)")
    if ask("Что сделать", "1").strip() == "2":
        folder = resolve_input("Папка с частями (её создал режим 1)", "sc_parts", [], allow_dir=True)
        if not os.path.isdir(folder):
            raise UserError("Нужна папка, а не файл")
        o = {"preview": False}
        m = ask("Если размер PNG изменился: 1 - вписать целиком, 2 - растянуть на прежнюю рамку, 3 - закрыть рамку с обрезкой", "1")
        o["bg_mode"] = {"2": "stretch", "3": "cover"}.get(m, "contain")
        o["astc"] = ask_astc()
        out = resolve_output(ask("Выходной файл", "rebuilt"), ".sc")
        print(gray("  Собираю..."))
        res = loading_build(folder, out, o, print)
        print(gray("  Результат всегда SC2 (даже если исходник был SC1). В игре не проверялось."))
        return res
    src = resolve_input("Какой .sc разобрать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    out_dir = resolve_out_dir(ask("Папка для частей", "sc_parts"))
    ms = ask_float("Минимальный размер части (меньшие пропускаются: точки, блики)", 12)
    print(gray("  Разбираю..."))
    made, parts, warns = loading_split(src, out_dir, print, ms, False, PARTS_README)
    print(f"  Частей: {len(parts)}")
    for p_ in parts[:40]:
        print(f"    {p_['name']:<28}{p_['px'][0]} x {p_['px'][1]}  ({p_['export']})")
    if len(parts) > 40:
        print(gray(f"    ... и ещё {len(parts) - 40}"))
    for w in warns:
        print(yellow("  ⚠ " + w))
    print(green("  ✔ Части и шаблон: ") + os.path.abspath(out_dir))
    return made


# ================================================================ английский интерфейс

I18N_EN = {'Части загрузочного экрана (разобрано хабом SC Hub)\n\nЧто здесь лежит\n  parts.json   - список частей: где каждая стоит в клипах (не трогай, по нему идёт сборка)\n  template.sc  - весь исходный файл как шаблон (клипы, текстовые поля, полоса загрузки, кнопки)\n  *.png        - части картинками "как на экране", без поворотов атласа; прозрачность обычная (не предумноженная)\n  preview.png  - как выглядит экран сейчас\n\nКак менять\n  1. Открой нужный PNG в редакторе и перерисуй (можно другого размера: картинка вписывается в ту же рамку,\n     фон по умолчанию закрывает рамку целиком с обрезкой лишнего).\n  2. В SC Hub: пункт 20, режим «собрать» - выбери эту папку. Заменяются только те части, которые изменились.\n  3. Лого игры: либо поменяй logo_EN.png, logo_KR.png и остальные, либо при сборке укажи один PNG для всех языков.\n  4. Лого SUPERCELL при запуске: поменяй supercell_logo.png или выбери при сборке плавную смену\n     «SUPERCELL -> своё лого» (диагональный переход от левого нижнего угла к правому верхнему).\n': 'Loading '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'screen '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'parts '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '(unpacked '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'by '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'SC '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'Hub)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          "What's "
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'in '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'here\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'parts.json   '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'list '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'of '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'parts: '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'where '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'each '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'one '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'sits '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'in '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'clips '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          "(don't "
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'touch, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'build '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'relies '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'on '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'it)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'template.sc  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'whole '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'source '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'file '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'as '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'a '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'template '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '(clips, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'text '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'fields, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'loading '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'bar, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'buttons)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '*.png        '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'parts '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'as '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'images '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '"as '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'on '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'screen", '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'without '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'atlas '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'rotations; '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'normal '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '(not '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'premultiplied) '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'transparency\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'preview.png  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'how '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'screen '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'looks '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'right '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'now\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'How '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'to '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'edit\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '1. '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'Open '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'PNG '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'you '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'need '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'in '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'an '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'editor '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'and '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'redraw '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'it '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '(a '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'different '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'size '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'is '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'fine: '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'image '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'is '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'fitted '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'into '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'same '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'frame,\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '     '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'background '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'covers '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'whole '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'frame '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'by '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'default, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'cropping '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'excess).\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '2. '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'In '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'SC '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'Hub: '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'item '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '20, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '"build" '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'mode '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'pick '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'this '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'folder. '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'Only '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'parts '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'that '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'changed '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'are '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'replaced.\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '3. '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'Game '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'logo: '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'either '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'edit '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'logo_EN.png, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'logo_KR.png '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'and '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'others, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'or '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'pick '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'a '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'single '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'PNG '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'for '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'all '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'languages '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'when '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'building.\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '4. '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'SUPERCELL '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'logo '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'at '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'launch: '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'edit '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'supercell_logo.png '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'or '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'pick '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'smooth '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'transition\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '     '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '"SUPERCELL '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '-> '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'your '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'logo" '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'when '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'building '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          '(a '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'diagonal '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'transition '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'from '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'bottom-left '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'corner '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'to '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          'top-right).\n',
 'Части .sc (разобрано хабом SC Hub)\n\n  parts.json   - список частей и где они стоят (не трогай, по нему идёт сборка)\n  template.sc  - весь исходный файл как шаблон\n  *.png        - части картинками "как на экране", без поворотов атласа; прозрачность обычная (не предумноженная);\n                 всё, что не входило в форму шейпа (соседи по атласу), убрано\n\nКак менять: перерисуй нужные PNG (можно другого размера - вписывается в ту же рамку) и собери обратно в SC Hub,\nпункт 21, режим 2. Заменяются только изменённые файлы. Части с анимацией по кадрам, кривые многоугольники и\nшейпы из нескольких кусков в части не попадают и остаются как в шаблоне.\n': '.sc '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'parts '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '(unpacked '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'by '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'SC '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'Hub)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'parts.json   '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'list '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'of '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'parts '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'and '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'where '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'they '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'sit '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         "(don't "
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'touch, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'build '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'relies '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'on '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'it)\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'template.sc  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'whole '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'source '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'file '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'as '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'a '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'template\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '  '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '*.png        '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'parts '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'as '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'images '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '"as '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'on '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'screen", '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'without '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'atlas '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'rotations; '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'normal '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '(not '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'premultiplied) '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'transparency;\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '                 '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'everything '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'outside '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'shape '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '(atlas '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'neighbors) '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'is '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'removed\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'How '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'to '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'edit: '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'redraw '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'PNGs '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'you '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'need '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '(a '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'different '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'size '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'is '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'fine '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '- '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'it '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'is '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'fitted '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'into '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'same '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'frame) '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'and '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'build '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'back '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'in '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'SC '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'Hub,\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'item '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '21, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'mode '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         '2. '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'Only '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'changed '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'files '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'are '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'replaced. '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'Frame-by-frame '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'animated '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'parts, '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'curved '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'polygons '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'and\n'
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'shapes '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'made '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'of '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'several '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'pieces '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'are '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'not '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'included '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'in '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'parts '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'and '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'stay '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'as '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'in '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'the '
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         'template.\n',
 'SC HUB  ·  инструменты для .sc': 'SC HUB  ·  tools for .sc',
 'Сводка по структуре, как её пишет gifconv/png2sc. Для чужих файлов может не совпасть.': 'Structure summary as written by gifconv/png2sc. '
                                                                                          'May not match for files from other sources.',
 'не сырой RGBA, извлечь нельзя': 'not raw RGBA, cannot extract',
 'Возвращает (байты нового .sc, {старое: новое}). Исходные данные не меняются.': 'Returns (bytes of the new .sc, {old: new}). The source '
                                                                                 'data is not modified.',
 'Плоский список uint16: (ребёнок, матрица, цвет) * число элементов.': 'Flat uint16 list: (child, matrix, color) * number of elements.',
 'Какие экспорты': 'Which exports',
 '  Связанный SC-пакет:': '  Linked SC package:',
 'Входной PNG или папка с PNG': 'Input PNG or folder with PNGs',
 'Размер клипа: число (0.5 = вдвое меньше, 2 = вдвое больше) или итог 135x100': 'Clip size: a number (0.5 = half size, 2 = double) or a '
                                                                                'final size like 135x100',
 '  Точка привязки (масштаб идёт от неё; для пинов, стоящих на земле - bottom):': '  Anchor point (scaling goes from it; for pins standing '
                                                                                  'on the ground - bottom):',
 'Режим масштаба: 1 - в размере шейпа, 2 - матрицей клипа': 'Scale mode: 1 - at shape size, 2 - by clip matrix',
 'Расширенные настройки (premultiply, размер шейпа)': 'Advanced settings (premultiply, shape size)',
 'Входной GIF': 'Input GIF',
 '  Точка привязки (масштаб идёт от неё):': '  Anchor point (scaling goes from it):',
 'Расширенные настройки (premultiply, размер клипа до масштаба)': 'Advanced settings (premultiply, clip size before scaling)',
 'Проигрывать туда-обратно (пинг-понг)': 'Play back and forth (ping-pong)',
 'Сохранить превью (PNG, первый кадр)': 'Save preview (PNG, first frame)',
 'Файловый дескриптор (FileDescriptor):': 'File descriptor (FileDescriptor):',
 'Какой .sc разобрать': 'Which .sc to unpack',
 '  Вводи id объекта (клип, шейп или текстовое поле), потом имя. Пустой ввод - закончить.': '  Enter the object id (clip, shape or text '
                                                                                            'field), then the name. Empty input - finish.',
 'клип': 'clip',
 'шейп': 'shape',
 'текстовое поле': 'text field',
 'Какой .sc переименовывать': 'Which .sc to rename',
 '  Как переименовать:': '  How to rename:',
 '    1 - одно имя': '    1 - a single name',
 '    2 - заменить часть имени сразу во всех (например glowbert_ на bea_)': '    2 - replace part of the name in all of them at once (e.g. '
                                                                            'glowbert_ with bea_)',
 '    3 - списком из файла, по строке на имя: старое=новое': '    3 - from a list in a file, one name per line: old=new',
 '    4 - дать ExportName объектам, у которых его ещё нет: клипам, шейпам, текстовым полям (по id)': '    4 - give an ExportName to '
                                                                                                     "objects that don't have one yet: "
                                                                                                     'clips, shapes, text fields (by id)',
 'Способ': 'Mode',
 'размер ×': 'size ×',
 ', сдвиг ': ', shift ',
 ' по X и ': ' on X and ',
 ' по Y': ' on Y',
 'Какой .sc менять': 'Which .sc to modify',
 '  Что менять:': '  What to modify:',
 '    1 - MovieClip целиком (по ExportName, шаблону emoji_* или id)': '    1 - whole MovieClip (by ExportName, emoji_* pattern or id)',
 '    2 - Shape (по id или все шейпы клипа)': '    2 - Shape (by id or all shapes of a clip)',
 '    3 - клипы списком из файла: имя  коэффициент  dx  dy': '    3 - clips from a list in a file: name  factor  dx  dy',
 'Что менять': 'What to modify',
 '  Что сделать:': '  What to do:',
 '    1 - разобрать .sc в JSON (или msgpack, если JSON больше 20 МБ) + PNG': '    1 - unpack .sc to JSON (or msgpack if the JSON is over '
                                                                             '20 MB) + PNG',
 '    2 - собрать .sc обратно из папки с data.json/data.msgpack + PNG': '    2 - rebuild .sc from a folder with data.json/data.msgpack + '
                                                                        'PNG',
 'Какой .sc разобрать в JSON/msgpack + PNG': 'Which .sc to unpack to JSON/msgpack + PNG',
 "ExportName'ы:": 'ExportNames:',
 'Список тегов потока (номер, длина тела):': 'Stream tag list (number, body length):',
 ' (центр ': ' (center ',
 '), размер ': '), size ',
 '  Кому менять частоту кадров:': '  Whose frame rate to change:',
 '    1 - клипу по ExportName, шаблону emoji_* или id': '    1 - a clip by ExportName, emoji_* pattern or id',
 '    2 - всем экспортным клипам файла': '    2 - all exported clips of the file',
 '    3 - списком из файла: имя  fps': '    3 - from a list in a file: name  fps',
 'Кому менять': 'Whose to change',
 'Сжать текстуры в ASTC (файл станет в разы меньше, но с потерями качества, кодирование долгое)': 'Compress textures to ASTC (file becomes '
                                                                                                  'several times smaller, but with quality '
                                                                                                  'loss, encoding is slow)',
 'Папка с .sc для объединения (или один .sc - тогда он просто переведётся в один SC2)': 'Folder with .sc files to merge (or a single .sc - '
                                                                                        'then it is simply converted to one SC2)',
 '    1 - вынести выбранные экспорты из файла в новый .sc': '    1 - extract selected exports from a file into a new .sc',
 '    2 - добавить выбранные экспорты из файла A в файл B (получится новый файл, B не меняется)': '    2 - add selected exports from file '
                                                                                                  'A to file B (produces a new file, B is '
                                                                                                  'not changed)',
 'Файл, откуда брать экспорты (A)': 'File to take exports from (A)',
 '  1 - выгрузить тексты в CSV (для правки в таблице)': '  1 - export texts to CSV (for editing in a spreadsheet)',
 '  2 - применить CSV обратно к .sc (результат - новый файл)': '  2 - apply the CSV back to the .sc (result is a new file)',
 'Какой .sc': 'Which .sc',
 'CSV с правками': 'CSV with edits',
 'нет сигнатуры SCTX': 'no SCTX signature',
 'SCTX: тип пикселей ': 'SCTX: pixel type ',
 ', mip-уровней ': ', mip levels ',
 ', флаги ': ', flags ',
 ', данных ': ', data ',
 ' байт, блок mip ': ' bytes, mip block ',
 ' байт, файл ': ' bytes, file ',
 ' байт': ' bytes',
 'KTX1: формат 0x': 'KTX1: format 0x',
 ' байт (zstd ': ' bytes (zstd ',
 'Файл': 'File',
 'формат': 'format',
 'Тексты': 'Texts',
 'тексты': 'texts',
 'Локализация': 'Localization',
 'В отчёте только метаданные и имена файлов; содержимое файлов не включено.': 'The report contains only metadata and file names; file '
                                                                              'contents are not included.',
 'Какой файл проверить': 'Which file to check',
 '  Стадии: 1 - статичный фон; 2 - градиент + движущиеся символы; 3 - градиент + символы + верхняя часть': '  Stages: 1 - static '
                                                                                                           'background; 2 - gradient + '
                                                                                                           'moving symbols; 3 - gradient + '
                                                                                                           'symbols + top part',
 'Папка с gradient.png, symbol.png, top.png или top.gif (и flash.png, если нужна вспышка)': 'Folder with gradient.png, symbol.png, top.png '
                                                                                            'or top.gif (and flash.png if a flash is '
                                                                                            'needed)',
 'Какой .sc очистить и оптимизировать': 'Which .sc to clean up and optimize',
 '  Что править:': '  What to edit:',
 '    1 - тексты надписей (имена экземпляров в клипах: так игра берёт текст)': '    1 - label texts (instance names in clips: this is how '
                                                                               'the game gets the text)',
 '    2 - размер шрифта и рамка текстового поля (запись TextField, общая для всех экземпляров)': '    2 - font size and text field frame '
                                                                                                 '(TextField record, shared by all '
                                                                                                 'instances)',
 'Какой .sc перестраивать': 'Which .sc to rebuild',
 '  Режим: 1 - с нуля, каждый фрагмент отдельно; 2 - сливать перекрывающиеся фрагменты;': '  Mode: 1 - from scratch, each fragment '
                                                                                          'separately; 2 - merge overlapping fragments;',
 '         3 - автоматически: посчитать оба и взять тот, где атласы меньше': '         3 - automatic: compute both and pick the one with '
                                                                             'smaller atlases',
 'Режим': 'Mode',
 'Разрешить поворот фрагментов на 90° ради плотности': 'Allow rotating fragments by 90° for denser packing',
 'Выбросить пиксели вне фигур шейпов (контур + отступ), в том числе остатки удалённого': 'Discard pixels outside the shape outlines '
                                                                                         '(contour + padding), including leftovers of '
                                                                                         'deleted ones',
 'Выбросить объекты, до которых не дойти ни от одного ExportName (клипы без экспорта, на которые никто не ссылается, их шейпы и куски атласа)': 'Discard '
                                                                                                                                                'objects '
                                                                                                                                                'not '
                                                                                                                                                'reachable '
                                                                                                                                                'from '
                                                                                                                                                'any '
                                                                                                                                                'ExportName '
                                                                                                                                                '(clips '
                                                                                                                                                'without '
                                                                                                                                                'export '
                                                                                                                                                'that '
                                                                                                                                                'nobody '
                                                                                                                                                'references, '
                                                                                                                                                'their '
                                                                                                                                                'shapes '
                                                                                                                                                'and '
                                                                                                                                                'atlas '
                                                                                                                                                'pieces)',
 'Из какого .sc удалять': 'Which .sc to delete from',
 '  Что удалить: имена через запятую, шаблон со * (emoji_*) или @файл со списком (по имени в строке)': '  What to delete: comma-separated '
                                                                                                       'names, a pattern with * (emoji_*) '
                                                                                                       'or @file with a list (one name per '
                                                                                                       'line)',
 "ExportName'ы для удаления": 'ExportNames to delete',
 'Сразу убрать из атласов куски удалённых объектов (перестроить атласы с нуля)': 'Remove pieces of deleted objects from the atlases right '
                                                                                 'away (rebuild atlases from scratch)',
 'Папка с data.json (или data.msgpack) и PNG': 'Folder with data.json (or data.msgpack) and PNG',
 'Какой .sc проверить': 'Which .sc to check',
 "  Используется в MovieClip'ах:": '  Used in MovieClips:',
 '  Важно: дополнительные .sctx/.zktx пока только обнаруживаются; содержимое не меняется.': '  Note: extra .sctx/.zktx files are only '
                                                                                            'detected for now; their contents are not '
                                                                                            'changed.',
 '    1 - разобрать загрузочный экран (loading.sc) на части: PNG + шаблон': '    1 - split the loading screen (loading.sc) into parts: PNG '
                                                                            '+ template',
 '    2 - собрать загрузочный экран из папки с частями (заменить фон, лого игры, лого SUPERCELL)': '    2 - build the loading screen from '
                                                                                                   'a parts folder (replace background, '
                                                                                                   'game logo, SUPERCELL logo)',
 'Загрузочный файл (loading.sc)': 'Loading file (loading.sc)',
 'Папка с частями (её создал режим 1)': 'Parts folder (created by mode 1)',
 'Свой фон экрана (картинка)': 'Custom screen background (image)',
 'Своё лого игры - одна картинка для всех языков': 'Custom game logo - one image for all languages',
 '  Лого SUPERCELL при запуске: 1 - как есть (или как в supercell_logo.png), 2 - заменить своим, 3 - сначала SUPERCELL, потом плавный переход к своему лого': '  '
                                                                                                                                                              'SUPERCELL '
                                                                                                                                                              'logo '
                                                                                                                                                              'at '
                                                                                                                                                              'startup: '
                                                                                                                                                              '1 '
                                                                                                                                                              '- '
                                                                                                                                                              'as '
                                                                                                                                                              'is '
                                                                                                                                                              '(or '
                                                                                                                                                              'as '
                                                                                                                                                              'in '
                                                                                                                                                              'supercell_logo.png), '
                                                                                                                                                              '2 '
                                                                                                                                                              '- '
                                                                                                                                                              'replace '
                                                                                                                                                              'with '
                                                                                                                                                              'your '
                                                                                                                                                              'own, '
                                                                                                                                                              '3 '
                                                                                                                                                              '- '
                                                                                                                                                              'SUPERCELL '
                                                                                                                                                              'first, '
                                                                                                                                                              'then '
                                                                                                                                                              'a '
                                                                                                                                                              'smooth '
                                                                                                                                                              'transition '
                                                                                                                                                              'to '
                                                                                                                                                              'your '
                                                                                                                                                              'logo',
 '    1 - разобрать любой .sc на части (PNG + шаблон)': '    1 - split any .sc into parts (PNG + template)',
 '    2 - собрать .sc обратно из папки с частями (после правок PNG)': '    2 - rebuild .sc from a parts folder (after editing PNGs)',
 'Минимальный размер части (меньшие пропускаются: точки, блики)': 'Minimum part size (smaller ones are skipped: dots, highlights)',
 'картинка в .sc, масштаб клипа': 'image into .sc, clip scale',
 'анимация в .sc: любой размер, прозрачность, масштаб': 'animation into .sc: any size, transparency, scale',
 'Разобрать .sc': 'Inspect .sc',
 'сводка + подробный дамп в .txt (SC1 и SC2)': 'summary + detailed dump to .txt (SC1 and SC2)',
 'Переименовать экспорты': 'Rename exports',
 "ExportName'ы в готовом .sc (SC1 и SC2)": 'ExportNames in a finished .sc (SC1 and SC2)',
 'Размер и положение': 'Size and position',
 'MovieClip / Shape: коэффициент и сдвиг в px': 'MovieClip / Shape: scale factor and offset in px',
 'FPS мувиклипов': 'MovieClip FPS',
 'сменить частоту кадров клипам': 'change the frame rate of clips',
 'разобрать .sc и собрать обратно (SC1 и SC2)': 'decode .sc and rebuild it (SC1 and SC2)',
 "Правка TextField'ов": 'Edit TextFields',
 'тексты (имена в клипах, SC1/SC2), размер шрифта, рамка': 'texts (names in clips, SC1/SC2), font size, frame',
 'Перестроить атласы': 'Rebuild atlases',
 'с нуля + выброс неиспользуемого (до 4096 по стороне)': 'from scratch + drop unused (up to 4096 per side)',
 'Удалить экспорты': 'Delete exports',
 'ExportName + клипы и шейпы только их (SC1 и SC2)': 'ExportName + clips and shapes used only by them (SC1 and SC2)',
 'Папка output': 'Output folder',
 'открыть в проводнике': 'open in file explorer',
 'Информация по ID': 'Info by ID',
 'объект, родители, ExportName, TextField/localization': 'object, parents, ExportName, TextField/localization',
 'SC-пакет': 'SC package',
 'найти _tex.sc и main_<int>.sctx/.zktx': 'find _tex.sc and main_<int>.sctx/.zktx',
 'Объединить / в один SC2': 'Merge / into one SC2',
 'папка .sc -> один SC2; один пакет -> один файл': 'folder of .sc -> one SC2; one package -> one file',
 'Таблица текстов (CSV)': 'Text table (CSV)',
 'выгрузить имена/тексты и применить обратно': 'export names/texts and apply them back',
 'Перенос экспортов': 'Transfer exports',
 'вынести в новый .sc или добавить в другой': 'move into a new .sc or add to another',
 'Диагностика файла': 'File diagnostics',
 'отчёт для баг-репорта (только метаданные)': 'bug report summary (metadata only)',
 'Очистить / оптимизировать': 'Clean up / optimize',
 'убрать недостижимое, слить одинаковое': 'remove unreachable, merge duplicates',
 'Создать фон меню': 'Create menu background',
 'градиент + движущиеся символы + верхняя часть': 'gradient + moving symbols + top part',
 'Загрузочный экран': 'Loading screen',
 'разобрать loading.sc на части и собрать со своими картинками': 'split loading.sc into parts and rebuild with your own images',
 'Части .sc <-> .sc': 'Parts .sc <-> .sc',
 'разобрать любой .sc на PNG-части и собрать после правок': 'split any .sc into PNG parts and rebuild after editing',
 ' КБ': ' KB',
 ' МБ': ' MB',
 'Не нашёл «': 'Not found: «',
 '». Положи файл в папку input:\n  ': '». Put the file into the input folder:\n  ',
 'Текстура больше 2 ГБ - не влезает в flatbuffers': 'Texture is larger than 2 GB - does not fit in flatbuffers',
 'Масштаб должен быть больше нуля': 'Scale must be greater than zero',
 '  Размер шейпа до масштаба (по умолчанию размер PNG), например 270x200': '  Shape size before scaling (defaults to the PNG size), e.g. '
                                                                           '270x200',
 '  Читаю GIF...': '  Reading GIF...',
 '  Анимированный фон для меню собирается в пункте 19 (там можно использовать top.gif).': '  An animated menu background is built in '
                                                                                          'option 19 (you can use top.gif there).',
 'Выходной файл': 'Output file',
 '  Размер шейпа до масштаба (по умолчанию размер GIF), например 270x200': '  Shape size before scaling (defaults to the GIF size), e.g. '
                                                                           '270x200',
 'Это не .sc (нет заголовка SC)': 'This is not an .sc (no SC header)',
 ', извлечь пока нельзя': ', cannot be extracted yet',
 'Дамп: ': 'Dump: ',
 'шейпов': 'shapes',
 'клипов': 'clips',
 'текстур': 'textures',
 "TextField'ов": 'TextFields',
 'смещение ресурсов': 'resource offset',
 'длина текстур': 'textures length',
 '  ВНИМАНИЕ: эвристика ниже иногда принимает такой счётчик за ссылку на вектор ("вектор ссылок?" на слотах 3-4 - ложное срабатывание). Опирайся на строку выше.': '  '
                                                                                                                                                                   'WARNING: '
                                                                                                                                                                   'the '
                                                                                                                                                                   'heuristic '
                                                                                                                                                                   'below '
                                                                                                                                                                   'sometimes '
                                                                                                                                                                   'mistakes '
                                                                                                                                                                   'such '
                                                                                                                                                                   'a '
                                                                                                                                                                   'counter '
                                                                                                                                                                   'for '
                                                                                                                                                                   'a '
                                                                                                                                                                   'vector '
                                                                                                                                                                   'reference '
                                                                                                                                                                   '("vector '
                                                                                                                                                                   'of '
                                                                                                                                                                   'references?" '
                                                                                                                                                                   'on '
                                                                                                                                                                   'slots '
                                                                                                                                                                   '3-4 '
                                                                                                                                                                   'is '
                                                                                                                                                                   'a '
                                                                                                                                                                   'false '
                                                                                                                                                                   'positive). '
                                                                                                                                                                   'Rely '
                                                                                                                                                                   'on '
                                                                                                                                                                   'the '
                                                                                                                                                                   'line '
                                                                                                                                                                   'above.',
 '  Формат: ': '  Format: ',
 'Куда сохранить подробный дамп': 'Where to save the detailed dump',
 '  Дамп можно отправить мне вместо самого .sc - по нему видна вся структура.': '  You can send me the dump instead of the .sc itself - it '
                                                                                'shows the whole structure.',
 'Смещение ресурсов в дескрипторе не равно 4+длина DataStorage - файл устроен иначе, чем ожидалось, не рискую его менять': 'Resource '
                                                                                                                           'offset in the '
                                                                                                                           'descriptor is '
                                                                                                                           'not '
                                                                                                                           '4+DataStorage '
                                                                                                                           'length - the '
                                                                                                                           'file is laid '
                                                                                                                           'out '
                                                                                                                           'differently '
                                                                                                                           'than expected, '
                                                                                                                           'not risking '
                                                                                                                           'changing it',
 'В новом дескрипторе нет поля смещения ресурсов': 'The new descriptor has no resource offset field',
 'В DataStorage нет таблицы строк': 'DataStorage has no string table',
 "В файле нет ExportName'ов": 'The file has no ExportNames',
 'Число id и имён в блоке ExportNames не совпадает': 'The number of ids and names in the ExportNames block does not match',
 'Переименование умеет только SC2 версии 5 с 7 блоками (как у твоего файла)': 'Renaming only supports SC2 version 5 with 7 blocks (like '
                                                                              'your file)',
 'Все новые имена совпадают со старыми - менять нечего': 'All new names match the old ones - nothing to change',
 'В дескрипторе нет списка экспортов': 'The descriptor has no export list',
 'структура блоков нарушена': 'block structure is broken',
 "список ExportName'ов не совпал с ожидаемым": 'ExportName list did not match the expected one',
 'старые строки в таблице изменились': 'old strings in the table changed',
 'DataStorage изменился не только в одном поле': 'DataStorage changed in more than one field',
 'длина DataStorage не кратна 4': 'DataStorage length is not a multiple of 4',
 'имена в дескрипторе не совпали': 'names in the descriptor did not match',
 'смещение ресурсов в дескрипторе неверное': 'resource offset in the descriptor is wrong',
 'длина дескриптора не кратна 4': 'descriptor length is not a multiple of 4',
 'У всех объектов в файле уже есть ExportName': 'All objects in the file already have an ExportName',
 '  Без ExportName: клипов ': '  Without ExportName: clips ',
 ' (верхнеуровневых, ни в одном клипе не лежат: ': ' (top-level, not inside any clip: ',
 '), шейпов ': '), shapes ',
 ', текстовых полей ': ', text fields ',
 'текстовое поле id ': 'text field id ',
 '; лежит в клипах: ': '; used in clips: ',
 '  Верхнеуровневые клипы без имени (положение считается по кадру 1, от точки (0,0) клипа):': '  Top-level clips without a name (position '
                                                                                              "is computed from frame 1, from the clip's "
                                                                                              '(0,0) point):',
 '  «shapes» - показать шейпы без имени, «@файл» - строки «id имя».': '  «shapes» - show shapes without a name, «@file» - lines of «id '
                                                                      'name».',
 'Ни одного имени не задано': 'No names given',
 'В SC1 хаб не знает текстовых полей': 'SC1: the hub does not support text fields',
 'Выходной файл совпадает со входным - укажи другое имя, оригинал не трогаю': 'Output file is the same as the input - give another name, '
                                                                              'the original is left untouched',
 'Продолжить': 'Continue',
 'Отменено': 'Cancelled',
 '  Пересобираю и сжимаю (несколько секунд)...': '  Rebuilding and compressing (a few seconds)...',
 ", ExportName'ов в файле: ": ', ExportNames in file: ',
 'Какое имя переименовать': 'Name to rename',
 'Новое имя': 'New name',
 '  Будет переименовано: ': '  Will be renamed: ',
 'Умею работать только с SC2 версии 5 и 7 блоками (как у твоего файла)': 'Only SC2 version 5 with 7 blocks is supported (like your file)',
 'блок MovieClips изменился не только номерами банков у перенесённых клипов': 'MovieClips block changed in more than just the bank numbers '
                                                                              'of the moved clips',
 'длина блока не кратна 4': 'block length is not a multiple of 4',
 "ExportName'ы или таблица строк изменились": 'ExportNames or the string table changed',
 'имена в дескрипторе изменились': 'names in the descriptor changed',
 'число клипов, шейпов или банков изменилось': 'number of clips, shapes or banks changed',
 'число вершин изменилось': 'number of vertices changed',
 'Коэффициент должен быть числом больше нуля (или двумя: для X и для Y)': 'Factor must be a number greater than zero (or two: for X and '
                                                                          'for Y)',
 'Коэффициент размера (1 - не менять, 0.5 - вдвое меньше, 2 - вдвое больше)': 'Size factor (1 - unchanged, 0.5 - half size, 2 - double '
                                                                              'size)',
 'Сдвиг по X в пикселях (вправо +, влево -)': 'Shift X in pixels (right +, left -)',
 'Сдвиг по Y в пикселях (вниз +, вверх -)': 'Shift Y in pixels (down +, up -)',
 'Коэффициент 1 и сдвиг 0 - менять нечего': 'Factor 1 and shift 0 - nothing to change',
 ' по X, ': ' for X, ',
 ', клипов: ': ', clips: ',
 ', шейпов: ': ', shapes: ',
 ", ExportName'ов: ": ', ExportNames: ',
 'ExportName, шаблон со * или id клипа': 'ExportName, pattern with * or clip id',
 '  Исходные данные (положение считается по кадру 1, от точки (0,0) клипа; Y растёт вниз):': '  Source data (position is measured at frame '
                                                                                             "1, from the clip's (0,0) point; Y grows "
                                                                                             'downward):',
 'Разбор SC2 умеет только версию 5 с 7 блоками (как у твоего файла)': 'SC2 parsing only supports version 5 with 7 blocks (like your file)',
 'Слишком короткий поток SC1': 'SC1 stream is too short',
 'Формат пикселей ': 'Pixel format ',
 ' не поддерживается': ' is not supported',
 'в файле нет пикселей (внешняя текстура: положи рядом <имя>_tex.sc)': 'file has no pixels (external texture: put <name>_tex.sc next to '
                                                                       'it)',
 'ASTC: данных меньше, чем нужно для картинки': 'ASTC: less data than the image needs',
 'Тип пикселей ': 'Pixel type ',
 ' для записи не поддержан (умею ASTC и RGBA/BGRA)': ' is not supported for writing (supported: ASTC and RGBA/BGRA)',
 ': неизвестный тип внешней текстуры (жду .sctx или .zktx)': ': unknown external texture type (expected .sctx or .zktx)',
 'Что сделать': 'What to do',
 'Папка для результата': 'Output folder',
 '  Разбираю (для больших файлов это занимает до минуты)...': '  Decoding (large files can take up to a minute)...',
 ', экспортов ': ', exports ',
 ', шейпов ': ', shapes ',
 ', клипов ': ', clips ',
 ', текстур ': ', textures ',
 'У этого SC1-файла сжатие LZMA: хаб умеет его читать и разбирать, но перезаписывать только файлы со сжатием zstd': 'This SC1 file uses '
                                                                                                                    'LZMA compression: the '
                                                                                                                    'hub can read and '
                                                                                                                    'decode it, but only '
                                                                                                                    'rewrites files with '
                                                                                                                    'zstd compression',
 'Хеш в заголовке не 16 байт - не рискую пересобирать этот файл': 'Header hash is not 16 bytes - not risking a rebuild of this file',
 'Поток этого файла не собирается обратно байт-в-байт (в нём есть нестандартные данные), не рискую его менять': "This file's stream does "
                                                                                                                'not rebuild byte-for-byte '
                                                                                                                '(it has non-standard '
                                                                                                                'data), not risking '
                                                                                                                'changing it',
 'В этом SC1 несколько банков матриц (тег 42). Как клип выбирает свой банк, хаб ещё не знает, поэтому менять матрицы (трансформ) не рискую. Переименование, FPS и правка имён текстовых полей работают.': 'This '
                                                                                                                                                                                                          'SC1 '
                                                                                                                                                                                                          'has '
                                                                                                                                                                                                          'several '
                                                                                                                                                                                                          'matrix '
                                                                                                                                                                                                          'banks '
                                                                                                                                                                                                          '(tag '
                                                                                                                                                                                                          '42). '
                                                                                                                                                                                                          'The '
                                                                                                                                                                                                          'hub '
                                                                                                                                                                                                          'does '
                                                                                                                                                                                                          'not '
                                                                                                                                                                                                          'yet '
                                                                                                                                                                                                          'know '
                                                                                                                                                                                                          'how '
                                                                                                                                                                                                          'a '
                                                                                                                                                                                                          'clip '
                                                                                                                                                                                                          'picks '
                                                                                                                                                                                                          'its '
                                                                                                                                                                                                          'bank, '
                                                                                                                                                                                                          'so '
                                                                                                                                                                                                          'it '
                                                                                                                                                                                                          'will '
                                                                                                                                                                                                          'not '
                                                                                                                                                                                                          'risk '
                                                                                                                                                                                                          'changing '
                                                                                                                                                                                                          'matrices '
                                                                                                                                                                                                          '(transforms). '
                                                                                                                                                                                                          'Renaming, '
                                                                                                                                                                                                          'FPS '
                                                                                                                                                                                                          'and '
                                                                                                                                                                                                          'TextField '
                                                                                                                                                                                                          'name '
                                                                                                                                                                                                          'edits '
                                                                                                                                                                                                          'work.',
 'результат не читается как SC1': 'result does not read as SC1',
 'MD5 в заголовке не совпал с данными': 'MD5 in the header does not match the data',
 'хвост файла изменился': 'file tail changed',
 'таблица экспортов изменилась': 'export table changed',
 'изменились теги, которые не должны были меняться (текстуры, цвета и т.д.)': 'tags that should not have changed did change (textures, '
                                                                              'colors, etc.)',
 'число клипов или шейпов изменилось': 'number of clips or shapes changed',
 'Проверка результата не прошла, файл не записан: имена экспортов не совпали': 'Result check failed, file not written: export names do not '
                                                                               'match',
 'Проверка результата не прошла, файл не записан: теги изменились': 'Result check failed, file not written: tags changed',
 'Проверка результата не прошла, файл не записан: MD5 в заголовке не совпал': 'Result check failed, file not written: MD5 in the header '
                                                                              'does not match',
 'Теги (номер: сколько): ': 'Tags (number: count): ',
 'Шапка: шейпов ': 'Header: shapes ',
 ', матриц ': ', matrices ',
 ', цветов ': ', colors ',
 'Хвост (hex): ': 'Tail (hex): ',
 'Проверка результата не прошла, файл не записан: блок MovieClips изменился не только частотой': 'Result check failed, file not written: '
                                                                                                 'MovieClips block changed in more than '
                                                                                                 'just the frame rate',
 ' fps, кадров ': ' fps, frames ',
 ' с)': ' s)',
 '\n      положение (кадр 1, от точки (0,0) клипа): ': "\n      position (frame 1, from the clip's (0,0) point): ",
 '  Клипов: ': '  Clips: ',
 'Новая частота кадров (1-255)': 'New frame rate (1-255)',
 'клипов: ': 'clips: ',
 ', новая частота ': ', new rate ',
 'У всех выбранных клипов уже такая частота - менять нечего': 'All selected clips already have this frame rate - nothing to change',
 '  Пересобираю и сжимаю...': '  Rebuilding and compressing...',
 'В SC1 хаб текстовые поля не разбирает: байтовый вид их тегов не проверен': 'The hub does not parse text fields in SC1: the byte form of '
                                                                             'their tags is unverified',
 'TextFields пусты': 'TextFields are empty',
 'файл содержит неоднородные/неоднозначные поля: слот 24=': 'file contains mixed/ambiguous fields: slot 24=',
 ', слот 28=': ', slot 28=',
 '; TID_* в 24=': '; TID_* in 24=',
 ', в 28=': ', in 28=',
 '. Для каждой записи используется собственный слот.': '. Each entry uses its own slot.',
 "Правка TextField'ов умеет только SC2 версии 5 с 7 блоками": 'TextField editing only supports SC2 version 5 with 7 blocks',
 'число текстовых полей изменилось': 'number of text fields changed',
 'DataStorage изменился не только в таблице строк': 'DataStorage changed in more than just the string table',
 'в блоке TextFields изменились посторонние байты': 'unrelated bytes changed in the TextFields block',
 'Рамка - это четыре числа: left,top,right,bottom': 'Frame is four numbers: left,top,right,bottom',
 'Правка имён умеет только SC2 версии 5 с 7 блоками': 'Name editing only supports SC2 version 5 with 7 blocks',
 'все': 'all',
 '  Итого: экспортов ': '  Total: exports ',
 '  Все текстуры записаны сырым RGBA; чтобы уменьшить размер, можно перестроить атлас (пункт 9).': '  All textures are written as raw '
                                                                                                   'RGBA; to reduce the size, you can '
                                                                                                   'rebuild the atlas (option 9).',
 'Ничего не выбрано': 'Nothing selected',
 'Нужно 1 или 2': 'Must be 1 or 2',
 'Файл, куда добавить (B)': 'File to add to (B)',
 'В CSV нет нужных колонок (clip_id, child, tf_id, name, new_name) - это не таблица, выгруженная хабом': 'CSV lacks the required columns '
                                                                                                         '(clip_id, child, tf_id, name, '
                                                                                                         'new_name) - this is not a table '
                                                                                                         'exported by the hub',
 'Нечего менять: в колонке new_name нет подходящих правок': 'Nothing to change: no valid edits in the new_name column',
 '  К применению: ': '  To apply: ',
 'Выходной файл совпадает со входным - укажи другое имя': 'Output file is the same as the input - give another name',
 ' байт, KTX1 не найден': ' bytes, KTX1 not found',
 '  версия ': '  version ',
 '  пакеты: ': '  packages: ',
 '  имя ': '  name ',
 ', размер ': ', size ',
 ' байт, sha256 ': ' bytes, sha256 ',
 '  первые байты: ': '  first bytes: ',
 'запись': 'write',
 '  таблицы в input: ': '  tables in input: ',
 '  Этот текст можно приложить к сообщению об ошибке.': '  You can attach this text to an error report.',
 'В папке нет gradient.png - это нижний слой фона, без него фон не собрать': 'gradient.png is missing from the folder - it is the bottom '
                                                                             'background layer, the background cannot be built without it',
 '  Найдено: ': '  Found: ',
 '  Расстановка символов (всё поле наклонено целиком, как в образце background_sonic):': '  Symbol layout (the whole field is tilted as '
                                                                                         'one, like the background_sonic sample):',
 '    1 - шахматная, как в образце: ряды сдвинуты на пол-клетки': '    1 - staggered, like the sample: rows are offset by half a cell',
 '    2 - обычная: ровная сетка, всё симметрично': '    2 - regular: even grid, fully symmetric',
 '    3 - в разброс: у каждого столбца свой сдвиг, поворот, размер и прозрачность': '    3 - scattered: each column has its own offset, '
                                                                                    'rotation, size and opacity',
 '  Плотность символов:': '  Symbol density:',
 '    1 - редкие (по умолчанию)': '    1 - sparse (default)',
 '    2 - средние (как в образце background_sonic по шагу решётки)': '    2 - medium (like the background_sonic sample by grid step)',
 '    3 - частые': '    3 - dense',
 'Настроить символы вручную (угол, расстояние, размер, скорость, прозрачность...)': 'Customize symbols manually (angle, spacing, size, '
                                                                                    'speed, opacity...)',
 'Неверные значения: расстояние, размер, скорость и непрозрачность должны быть положительными (непрозрачность до 100%)': 'Invalid values: '
                                                                                                                         'distance, size, '
                                                                                                                         'speed and '
                                                                                                                         'opacity must be '
                                                                                                                         'positive '
                                                                                                                         '(opacity up to '
                                                                                                                         '100%)',
 'Нужна папка, а не файл': 'A folder is required, not a file',
 'Экспортнейм не должен быть пустым и содержать пробелы или слэши': 'ExportName must not be empty or contain spaces or slashes',
 'Сохранить превью (PNG, кадр 0)': 'Save preview (PNG, frame 0)',
 'текстовых полей': 'text fields',
 'матриц': 'matrices',
 'цветовых преобразований': 'color transforms',
 '  [нет в таблице]': '  [not in table]',
 'В файле нет экземпляров текстовых полей в клипах': 'The file has no text field instances in clips',
 '  Экземпляров текстовых полей в клипах: ': '  Text field instances in clips: ',
 ' (ключей TID_: ': ' (TID_ keys: ',
 ', пустых имён: ': ', empty names: ',
 ', прочих имён: ': ', other names: ',
 '  Текст надписи в игре - это имя экземпляра. TID_... игра переводит по таблице локализации; обычную строку, видимо, показывает как есть. Служебные имена (txt, spacer...) использует код игры.': '  '
                                                                                                                                                                                                   'The '
                                                                                                                                                                                                   'text '
                                                                                                                                                                                                   'shown '
                                                                                                                                                                                                   'in '
                                                                                                                                                                                                   'the '
                                                                                                                                                                                                   'game '
                                                                                                                                                                                                   'is '
                                                                                                                                                                                                   'the '
                                                                                                                                                                                                   'instance '
                                                                                                                                                                                                   'name. '
                                                                                                                                                                                                   'TID_... '
                                                                                                                                                                                                   'is '
                                                                                                                                                                                                   'translated '
                                                                                                                                                                                                   'by '
                                                                                                                                                                                                   'the '
                                                                                                                                                                                                   'game '
                                                                                                                                                                                                   'via '
                                                                                                                                                                                                   'the '
                                                                                                                                                                                                   'localization '
                                                                                                                                                                                                   'table; '
                                                                                                                                                                                                   'a '
                                                                                                                                                                                                   'plain '
                                                                                                                                                                                                   'string '
                                                                                                                                                                                                   'is '
                                                                                                                                                                                                   'apparently '
                                                                                                                                                                                                   'shown '
                                                                                                                                                                                                   'as '
                                                                                                                                                                                                   'is. '
                                                                                                                                                                                                   'Service '
                                                                                                                                                                                                   'names '
                                                                                                                                                                                                   '(txt, '
                                                                                                                                                                                                   'spacer...) '
                                                                                                                                                                                                   'are '
                                                                                                                                                                                                   'used '
                                                                                                                                                                                                   'by '
                                                                                                                                                                                                   'the '
                                                                                                                                                                                                   'game '
                                                                                                                                                                                                   'code.',
 '(пусто)': '(empty)',
 'Что искать в именах/клипах': 'What to search for in names/clips',
 'Ни одной правки не задано': 'No edits specified',
 'Что править': 'What to edit',
 'В файле нет текстовых полей': 'The file has no text fields',
 '  Текстовых полей: ': '  Text fields: ',
 ', из них с непустым текстом: ': ', with non-empty text: ',
 ', тип ': ', type ',
 'Размер шрифта': 'Font size',
 'Рамка left,top,right,bottom': 'Box left,top,right,bottom',
 'Текстуры пакета лежат во внешних .sctx/.zktx, но прочитать их не удалось': 'The package textures are in external .sctx/.zktx files, but '
                                                                             'they could not be read',
 'Не удалось упаковать фрагменты в атлас заданного размера': 'Could not pack the fragments into an atlas of the given size',
 'В файле нет ни одной команды шейпа с текстурой - перестраивать нечего': 'The file has no shape commands with a texture - nothing to '
                                                                          'rebuild',
 'результат читается как другой формат': 'result reads as a different format',
 'число атласов в файле не совпало с планом': 'atlas count in the file does not match the plan',
 'число команд шейпов изменилось': 'shape command count changed',
 'автовыбор: с нуля ': 'auto: from scratch ',
 ' МП, со слиянием перекрытий ': ' MP, merging overlaps ',
 ' МП -> взят режим «': ' MP -> chose mode «',
 'Размер атласа должен быть от 64 до 16384, отступ от 0 до 64': 'Atlas size must be 64 to 16384, padding 0 to 64',
 ' если содержимого хватает; 2 - степени двойки': ' if the content fits; 2 - powers of two',
 'Размеры': 'Sizes',
 '  Распаковываю текстуры и ищу лучшую раскладку (на больших файлах это до минуты)...': '  Unpacking textures and searching for the best '
                                                                                        'layout (up to a minute on large files)...',
 '  Собираю атласы и файл...': '  Building atlases and file...',
 'Не выбрано ни одного ExportName для удаления': 'No ExportName selected for deletion',
 "Нельзя удалить все ExportName'ы: в файле ничего бы не осталось": 'Cannot delete all ExportNames: nothing would be left in the file',
 'Индекс вне вектора - файл устроен иначе, чем ожидалось': 'Index out of vector range - the file is structured differently than expected',
 'Удаление умеет только SC2 версии 5 с 7 блоками (как у твоего файла)': 'Deletion only supports SC2 version 5 with 7 blocks (like your '
                                                                        'file)',
 'Очистка умеет только SC2 версии 5 с 7 блоками (как у твоего файла)': 'Cleanup only supports SC2 version 5 with 7 blocks (like your file)',
 'Список экспортов в дескрипторе не совпал с блоком ExportNames - не рискую менять': 'The export list in the descriptor does not match the '
                                                                                     'ExportNames block - not risking changes',
 'длина DataStorage изменилась': 'DataStorage length changed',
 'набор шейпов не совпал с ожидаемым': 'shape set does not match the expected one',
 'набор клипов не совпал с ожидаемым': 'clip set does not match the expected one',
 'оставшиеся текстовые поля изменились': 'remaining text fields changed',
 'результат не читается как SC1 или MD5 не совпал': 'result does not read as SC1 or MD5 mismatch',
 'изменились теги, которые не должны были меняться': 'tags that should not have changed were changed',
 'счётчики в шапке неверные': 'header counters are wrong',
 'записи в хвосте файла не совпали': 'file tail records do not match',
 'Не выбрано ни одного ExportName': 'No ExportName selected',
 "  Будет удалено ExportName'ов: ": '  ExportNames to delete: ',
 'Дескриптор устроен иначе, чем ожидалось (поля не по 4 байта) - не рискую его пересобирать': 'The descriptor is structured differently '
                                                                                              'than expected (fields not 4 bytes) - not '
                                                                                              'risking rebuilding it',
 'Добавление имён умеет только SC2 версии 5 с 7 блоками': 'Adding names only supports SC2 version 5 with 7 blocks',
 'таблицы клипов не совпали с ожидаемыми': 'clip tables do not match the expected ones',
 "Проверка результата не прошла, файл не записан: список ExportName'ов не совпал": 'Result check failed, file not written: ExportName list '
                                                                                   'does not match',
 'Проверка результата не прошла, файл не записан: содержимое изменилось или MD5 не совпал': 'Result check failed, file not written: '
                                                                                            'content changed or MD5 mismatch',
 'Не задано ни одного нового имени': 'No new names specified',
 'msgpack: неизвестный тип 0x': 'msgpack: unknown type 0x',
 'В корне файла данных должен быть объект': 'The data file root must be an object',
 'id экспорта': 'export id',
 'Текстовые поля и модификаторы в SC1 хаб не собирает (их теги не изучены)': 'SC1 build in the hub does not support text fields and '
                                                                             'modifiers (their tags are not studied)',
 'SC1 поддерживает один банк матриц': 'SC1 supports only one matrix bank',
 'Проверка результата не прошла, файл не записан: число текстур не совпало': 'Result check failed, file not written: texture count does '
                                                                             'not match',
 '  Данные: ': '  Data: ',
 ', формат ': ', format ',
 '  Собираю (для больших файлов это до минуты)...': '  Building (up to a minute for large files)...',
 'Сумма элементов по кадрам не равна числу элементов клипа - файл устроен иначе, чем ожидалось': 'The sum of elements across frames does '
                                                                                                 "not equal the clip's element count - the "
                                                                                                 'file is structured differently than '
                                                                                                 'expected',
 'Удаление по id умеет только SC2 версии 5 с 7 блоками': 'Deletion by id only supports SC2 version 5 with 7 blocks',
 'DataStorage изменился за пределами кадров затронутых клипов': 'DataStorage changed outside the frames of the affected clips',
 'Проверка результата не прошла, файл не записан: MD5 или хвост файла не совпали': 'Result check failed, file not written: MD5 or file '
                                                                                   'tail mismatch',
 'Проверка результата не прошла, файл не записан: изменились посторонние теги': 'Result check failed, file not written: unrelated tags '
                                                                                'changed',
 'Ни в одном клипе нет этих объектов (или они вне выбранного клипа) - убирать нечего': 'No clip contains these objects (or they are '
                                                                                       'outside the selected clip) - nothing to remove',
 'Нужен числовой ID': 'A numeric ID is required',
 '  Родители:': '  Parents:',
 '    <нет>': '    <none>',
 'В таймлайне интро не нашёл кадров с логотипом SUPERCELL': 'No frames with the SUPERCELL logo found in the intro timeline',
 'В папке нет parts.json - выбери папку, которую создал режим «разобрать»': 'No parts.json in the folder - pick the folder created by the '
                                                                            '«split» mode',
 'В папке нет template.sc': 'No template.sc in the folder',
 'Нужно число больше нуля': 'A number greater than zero is required',
 'Папка для частей': 'Parts folder',
 '  Разбираю...': '  Splitting...',
 '  Перерисуй нужные PNG (или оставь как есть) и запусти этот пункт ещё раз в режиме 2.': '  Redraw the PNGs you need (or leave them as '
                                                                                          'is) and run this option again in mode 2.',
 '  Пустой ответ = не менять. Файлы можно указывать полным путём или именем из папки input.': '  Empty answer = no change. Files can be '
                                                                                              'given as a full path or a name from the '
                                                                                              'input folder.',
 'Как вписать: 1 - закрыть весь экран с обрезкой, 2 - целиком с полями, 3 - растянуть': 'How to fit: 1 - cover the whole screen with '
                                                                                        'cropping, 2 - fit whole with margins, 3 - stretch',
 'Масштаб лого (1 = вписать в рамку прежнего)': "Logo scale (1 = fit into the old logo's box)",
 'Своё лого для интро': 'Custom intro logo',
 'Масштаб лого (1 = вписать в рамку SUPERCELL)': 'Logo scale (1 = fit into the SUPERCELL box)',
 '  Собираю...': '  Building...',
 '  Проверено только по рендеру и разбору готового файла; в игре поведение не проверялось.': '  Verified only by rendering and parsing the '
                                                                                             'finished file; behavior in the game was not '
                                                                                             'tested.',
 'Если размер PNG изменился: 1 - вписать целиком, 2 - растянуть на прежнюю рамку, 3 - закрыть рамку с обрезкой': 'If the PNG size changed: '
                                                                                                                 '1 - fit whole, 2 - '
                                                                                                                 'stretch to the old box, '
                                                                                                                 '3 - cover the box with '
                                                                                                                 'cropping',
 '  Частей: ': '  Parts: ',
 '  Enter на любом вопросе = значение в [скобках]': '  Enter on any prompt = the value in [brackets]',
 'Пока!': 'Bye!',
 '\nEnter - вернуться в меню...': '\nEnter - back to menu...',
 'Файл не выбран': 'No file selected',
 '    основной: ': '    main: ',
 '    дополнительных файлов рядом не найдено': '    no additional files found nearby',
 'Ставлю недостающие библиотеки: ': 'Installing missing libraries: ',
 'Не понял размер «': 'Could not parse size «',
 '» (нужно 135x100 или 135)': '» (expected 135x100 or 135)',
 'Все файлы': 'All files',
 'Точка привязки': 'Anchor point',
 'В файле один кадр - это не анимация. Для одной картинки используй пункт 1 (PNG → SC)': 'The file has one frame - it is not an animation. '
                                                                                         'For a single image use option 1 (PNG → SC)',
 'Кадров в секунду в игре (целое, до ': 'Frames per second in game (integer, up to ',
 'Ширина кадра в файле, px (Enter - авто; меньше - легче файл, хуже чёткость)': 'Frame width in file, px (Enter - auto; smaller - lighter '
                                                                                'file, lower sharpness)',
 '  Слотов: ': '  Slots: ',
 ' с при ': ' s at ',
 ' кадр/с), кадров в текстурах: ': ' fps), frames in textures: ',
 ', размер кадра ': ', frame size ',
 ', текстуры ~': ', textures ~',
 '  Собираю текстуры: ': '  Building textures: ',
 ' шт. по ': ' textures, ',
 ' кадров': ' frames each',
 'Не удалось прочитать все кадры GIF: прочитано ': 'Could not read all GIF frames: read ',
 ' из ': ' of ',
 '  Сжимаю ': '  Compressing ',
 ' МБ, это может занять пару минут...': ' MB, this may take a couple of minutes...',
 ' МБ, ': ' MB, ',
 ' с, ': ' s, ',
 ' слотов, ': ' slots, ',
 ' кадр/с, кадров ': ' fps, frames ',
 ', в памяти игры ~': ', in game memory ~',
 ', шейп ': ', shape ',
 ', масштаб ': ', scale ',
 ' → в игре ': ' → in game ',
 ', режим ': ', mode ',
 ', привязка ': ', pivot ',
 '    в моде: "FileName": "sc/': '    in the mod: "FileName": "sc/',
 'Файл: ': 'File: ',
 ', версия ': ', version ',
 ', распаковано ': ', unpacked ',
 ', блоков: ': ', chunks: ',
 '  Блоков не 7, как в файлах gifconv/png2sc - структура другая, смотри подробный дамп.': '  Not 7 chunks like in gifconv/png2sc files - '
                                                                                          'different structure, see the detailed dump.',
 'DataStorage: строк ': 'DataStorage: strings ',
 ', вершин ': ', vertices ',
 ', элементов кадров ': ', frame elements ',
 'Текстуры: ': 'Textures: ',
 '  по схеме gifconv (сверено на реальном файле): ': '  per the gifconv layout (checked against a real file): ',
 '--- блок ': '--- chunk ',
 ' байт ---': ' bytes ---',
 '  (пустой)': '  (empty)',
 "Получились одинаковые ExportName'ы: ": 'Duplicate ExportNames resulted: ',
 'Проверка результата не прошла, файл не записан: ': 'Result check failed, file not written: ',
 '  ✔ Проверка пройдена: ': '  ✔ Check passed: ',
 '  ⚠ SC1: в хвосте файла (START…) для новых имён записей нет: что означают их 16-байтные значения, я не выяснил.': '  ⚠ SC1: the file '
                                                                                                                    'tail (START…) has no '
                                                                                                                    'entries for the new '
                                                                                                                    'names: I could not '
                                                                                                                    'work out what their '
                                                                                                                    '16-byte values mean.',
 'Новое имя не задано': 'New name not set',
 'Какой кусок имени заменить': 'Which part of the name to replace',
 'На что заменить (Enter - просто убрать)': 'Replace with (Enter - just remove)',
 'Клип ссылается на несуществующий банк матриц ': 'Clip references a nonexistent matrix bank ',
 'старые цветовые преобразования в банке изменились': 'old color transforms in the bank changed',
 '  Найдено клипов: ': '  Clips found: ',
 'id шейпов (5, 7-9) или ExportName/шаблон клипа, чьи шейпы менять': 'Shape ids (5, 7-9) or a clip ExportName/pattern whose shapes to '
                                                                     'change',
 '  Исходные координаты шейпов (в координатах шейпа, Y растёт вниз):': '  Original shape coordinates (in shape coordinates, Y grows down):',
 '  Масштабировать от:': '  Scale from:',
 '    1 - точки (0,0) шейпа': '    1 - shape point (0,0)',
 '    2 - центра шейпа': '    2 - shape center',
 'у всех остальных клипов и шейпов итоговые матрицы и вершины такие же, как в оригинале': 'all other clips and shapes have the same final '
                                                                                          'matrices and vertices as the original',
 'Файл сжат LZHAM (SCLZ) - этот формат сжатия хаб не умеет': 'File is LZHAM-compressed (SCLZ) - the hub does not support this compression '
                                                             'format',
 '. Такой файл можно исключить из слияния; поддержку тега можно добавить по образцу.': '. Such a file can be excluded from the merge; '
                                                                                       'support for the tag can be added using a sample.',
 'Пикселей меньше, чем w*h*4': 'Fewer pixels than w*h*4',
 'Пикселей меньше, чем w*h*2': 'Fewer pixels than w*h*2',
 'Для кодирования ASTC нужен numpy: pip install numpy': 'ASTC encoding requires numpy: pip install numpy',
 ': файл слишком короткий для .sctx': ': file too short for .sctx',
 ': нет сигнатуры SCTX - это не .sctx': ': no SCTX signature - not an .sctx',
 ': в шапке нет ширины, высоты или размера данных': ': header is missing width, height or data size',
 ': размер данных ': ': data size ',
 ' не равен заявленному ': ' does not match the declared ',
 ': это не .sctx': ': not an .sctx',
 'Формат KTX 0x': 'KTX format 0x',
 ' для записи не поддержан (умею ASTC)': ' is not supported for writing (only ASTC)',
 'Жду .sctx или .zktx': 'Expected .sctx or .zktx',
 ': внутри нет KTX1': ': no KTX1 inside',
 'У пакета несколько _tex.sc (highres/lowres): атласы для такого пакета пока не перестраиваются, чтобы не рассинхронизировать разрешения': 'Package '
                                                                                                                                           'has '
                                                                                                                                           'several '
                                                                                                                                           '_tex.sc '
                                                                                                                                           '(highres/lowres): '
                                                                                                                                           'atlases '
                                                                                                                                           'for '
                                                                                                                                           'such '
                                                                                                                                           'a '
                                                                                                                                           'package '
                                                                                                                                           'are '
                                                                                                                                           'not '
                                                                                                                                           'rebuilt '
                                                                                                                                           'yet, '
                                                                                                                                           'to '
                                                                                                                                           'avoid '
                                                                                                                                           'desyncing '
                                                                                                                                           'the '
                                                                                                                                           'resolutions',
 '  ⚠ Сборка обратно из JSON положит текстуры сырым RGBA в _tex.sc, а не в .sctx/.zktx: кодировать ASTC хаб пока не умеет. Для правок без текстур используй пункты переименования, FPS, текстов - они сохраняют .sctx/.zktx как есть.': '  '
                                                                                                                                                                                                                                        '⚠ '
                                                                                                                                                                                                                                        'Rebuilding '
                                                                                                                                                                                                                                        'from '
                                                                                                                                                                                                                                        'JSON '
                                                                                                                                                                                                                                        'will '
                                                                                                                                                                                                                                        'put '
                                                                                                                                                                                                                                        'textures '
                                                                                                                                                                                                                                        'as '
                                                                                                                                                                                                                                        'raw '
                                                                                                                                                                                                                                        'RGBA '
                                                                                                                                                                                                                                        'in '
                                                                                                                                                                                                                                        '_tex.sc, '
                                                                                                                                                                                                                                        'not '
                                                                                                                                                                                                                                        'in '
                                                                                                                                                                                                                                        '.sctx/.zktx: '
                                                                                                                                                                                                                                        'the '
                                                                                                                                                                                                                                        'hub '
                                                                                                                                                                                                                                        'cannot '
                                                                                                                                                                                                                                        'encode '
                                                                                                                                                                                                                                        'ASTC '
                                                                                                                                                                                                                                        'yet. '
                                                                                                                                                                                                                                        'For '
                                                                                                                                                                                                                                        'edits '
                                                                                                                                                                                                                                        'without '
                                                                                                                                                                                                                                        'textures '
                                                                                                                                                                                                                                        'use '
                                                                                                                                                                                                                                        'the '
                                                                                                                                                                                                                                        'rename, '
                                                                                                                                                                                                                                        'FPS '
                                                                                                                                                                                                                                        'and '
                                                                                                                                                                                                                                        'text '
                                                                                                                                                                                                                                        'options '
                                                                                                                                                                                                                                        '- '
                                                                                                                                                                                                                                        'they '
                                                                                                                                                                                                                                        'keep '
                                                                                                                                                                                                                                        '.sctx/.zktx '
                                                                                                                                                                                                                                        'as '
                                                                                                                                                                                                                                        'is.',
 '  JSON вышел бы больше 20 МБ, поэтому записан msgpack.': '  JSON would be larger than 20 MB, so msgpack was written.',
 'Матриц получилось бы больше 65535 - в SC1 это упирается в лимит списка (банки SC1 хаб не поддерживает). Выбери меньше клипов за раз.': 'There '
                                                                                                                                         'would '
                                                                                                                                         'be '
                                                                                                                                         'more '
                                                                                                                                         'than '
                                                                                                                                         '65535 '
                                                                                                                                         'matrices '
                                                                                                                                         '- '
                                                                                                                                         'in '
                                                                                                                                         'SC1 '
                                                                                                                                         'this '
                                                                                                                                         'hits '
                                                                                                                                         'the '
                                                                                                                                         'list '
                                                                                                                                         'limit '
                                                                                                                                         '(the '
                                                                                                                                         'hub '
                                                                                                                                         'does '
                                                                                                                                         'not '
                                                                                                                                         'support '
                                                                                                                                         'SC1 '
                                                                                                                                         'banks). '
                                                                                                                                         'Pick '
                                                                                                                                         'fewer '
                                                                                                                                         'clips '
                                                                                                                                         'at '
                                                                                                                                         'a '
                                                                                                                                         'time.',
 'Проверка результата не прошла, файл не записан: имена в хвосте не совпали': 'Result check failed, file not written: footer names do not '
                                                                              'match',
 'Проверка результата не прошла, файл не записан: изменился тег, не относящийся к клипам': 'Result check failed, file not written: a tag '
                                                                                           'unrelated to clips changed',
 ', сжатие ': ', compression ',
 ', MD5 в заголовке ': ', MD5 in header ',
 '  клип id ': '  clip id ',
 '  ... ещё ': '  ... ',
 '  клип ': '  clip ',
 ' кадр., ': ' frames, ',
 ' fps, детей ': ' fps, children ',
 'Хвост файла: START + ': 'File footer: START + ',
 ' записей экспортов + ': ' export records + ',
 ': тег ': ': tag ',
 'Частота кадров должна быть целым числом от 1 до 255': 'Frame rate must be an integer from 1 to 255',
 ' (без ExportName)': ' (no ExportName)',
 'содержимого нет': 'no content',
 'Частота кадров должна быть от 1 до 255': 'Frame rate must be from 1 to 255',
 'кроме частоты выбранных клипов ничего не изменилось': 'nothing changed except the frame rate of the selected clips',
 'слот 24 выбран для ': 'slot 24 chosen for ',
 ' полей; TID_*: ': ' fields; TID_*: ',
 'слот 28 выбран для ': 'slot 28 chosen for ',
 'Языка ': 'Language ',
 ' нет в таблицах (есть: ': ' not found in the tables (available: ',
 '  Найдены таблицы локализации (': '  Localization tables found (',
 ' яз.): ': ' langs): ',
 '  Читаю таблицы ': '  Reading tables ',
 'Такого экземпляра текстового поля нет': 'No such text field instance',
 'Проверка результата не прошла, файл не записан: имя вышло не таким': 'Result check failed, file not written: the name came out different',
 'Неожиданный формат документа': 'Unexpected document format',
 'Не хватает id: в одном .sc их не может быть больше 65536': 'Out of ids: one .sc cannot have more than 65536',
 'В папке нет .sc-файлов': 'No .sc files in the folder',
 '  Один файл: пакет (.sc + _tex.sc + .sctx/.zktx) будет переведён в один самодостаточный SC2.': '  Single file: the package (.sc + '
                                                                                                 '_tex.sc + .sctx/.zktx) will be converted '
                                                                                                 'into one self-contained SC2.',
 '  Найдено .sc: ': '  .sc found: ',
 '. Разбираю каждый (большие пакеты - до пары минут)...': '. Parsing each one (big packages take up to a couple of minutes)...',
 'Выходной файл совпадает с одним из входных - укажи другое имя': 'Output file matches one of the input files - specify a different name',
 '  Собираю итоговый SC2 и проверяю результат (для больших текстур это долго)...': '  Building the final SC2 and checking the result (slow '
                                                                                   'for large textures)...',
 'Нет экспортов: ': 'No exports: ',
 '  Всего экспортов: ': '  Total exports: ',
 'Выходной файл совпадает с входным - укажи другое имя': 'Output file matches the input file - specify a different name',
 '  Собираю итоговый SC2 и проверяю результат...': '  Building the final SC2 and checking the result...',
 'Куда сохранить CSV': 'Where to save the CSV',
 'изменены только указанные экземпляры': 'only the specified instances were changed',
 '  формат: ': '  format: ',
 '  контейнер: версия ': '  container: version ',
 ' (внутри ': ' (inside ',
 '), сжатие ': '), compression ',
 ', поток ': ', stream ',
 ' байт, хвост ': ' bytes, tail ',
 '  счётчики шапки (шейпы, клипы, текстуры, текстовые поля, матрицы, цвета): ': '  header counters (shapes, clips, textures, text fields, '
                                                                                'matrices, colors): ',
 '  теги (номер: сколько): ': '  tags (number: count): ',
 '  текстуры в основном файле: ': '  textures in the main file: ',
 '  файлы _tex.sc рядом: ': '  _tex.sc files alongside: ',
 ', блоков ': ', chunks ',
 ', остаток ': ', leftover ',
 '  экспортов: ': '  exports: ',
 '  экземпляров текстовых полей: ': '  text field instances: ',
 ', пустых: ': ', empty: ',
 ', нельзя править: ': ', cannot be edited: ',
 'Нет экспорта ': 'No export ',
 '. Файл не записан.': '. File not written.',
 'Эта стадия недоступна: для неё не хватает файлов (максимум по вашим файлам - ': 'This stage is unavailable: required files are missing '
                                                                                  '(the maximum for your files is ',
 ' не 16:9 - вписан в 2048x1152, пустые края заполнены размытием': ' is not 16:9 - fitted into 2048x1152, empty edges filled with blur',
 'Размер кадров должен быть от 0.1 до 1': 'Frame size must be from 0.1 to 1',
 '  Размер градиента ': '  Gradient size ',
 '. Область фона в игре: ': '. In-game background area: ',
 ' единиц по высоте (как в theme_minecraft.sc), то есть ': ' units tall (as in theme_minecraft.sc), i.e. ',
 'Нужно 1, 2 или 3': 'Enter 1, 2 or 3',
 'Умножать RGB на альфу (нужно, если вокруг символов видны светлые рамки или прямоугольники)': 'Multiply RGB by alpha (needed if light '
                                                                                               'borders or rectangles are visible around '
                                                                                               'symbols)',
 'Найден flash.png: добавить вспышку (свечение пульсирует за символами)': 'flash.png found: add a flash (the glow pulses behind the '
                                                                          'symbols)',
 'Экспортнейм для собранного фона': 'ExportName for the built background',
 '  Собираю фон...': '  Building background...',
 '  Разбираю файл...': '  Parsing file...',
 '  Убираю недостижимое, сливаю одинаковое и сверяю каждый экспорт до и после...': '  Removing unreachable items, merging identical ones '
                                                                                   'and checking every export before and after...',
 '  Размер: было ': '  Size: was ',
 ' МБ (с файлами пакета), стало ': ' MB (with package files), now ',
 'у каждого экспорта сошёлся отпечаток внешнего вида (клипы, кадры, матрицы, цвета, вершины, текстуры)': 'every export has a matching '
                                                                                                         'appearance fingerprint (clips, '
                                                                                                         'frames, matrices, colors, '
                                                                                                         'vertices, textures)',
 '  В таблице такого текста нет': '  No such text in the table',
 'Номер TID (Enter - не выбирать)': 'TID number (Enter - skip)',
 'клип ': 'clip ',
 '   поле ': '   field ',
 ', экземпляр ': ', instance ',
 ' и в тексте таблицы': ' and in table text',
 'Новое имя/текст (Enter - не менять': 'New name/text (Enter - keep unchanged',
 'изменены только имена выбранных экземпляров': 'only the names of the selected instances were changed',
 '  SC1: поддерживается правка текстов (имён экземпляров); размер и рамка поля - только в SC2.': '  SC1: editing texts (instance names) is '
                                                                                                 'supported; field size and bounds - SC2 '
                                                                                                 'only.',
 '  Где лежит текст: ': '  Where the text is stored: ',
 '  ⚠ Это не подтверждено файлом (нет имён файлов типографики, по которым их можно было бы отличить от текста): проверь результат в игре на одном поле, прежде чем править много.': '  '
                                                                                                                                                                                    '⚠ '
                                                                                                                                                                                    'This '
                                                                                                                                                                                    'is '
                                                                                                                                                                                    'not '
                                                                                                                                                                                    'confirmed '
                                                                                                                                                                                    'by '
                                                                                                                                                                                    'the '
                                                                                                                                                                                    'file '
                                                                                                                                                                                    '(there '
                                                                                                                                                                                    'are '
                                                                                                                                                                                    'no '
                                                                                                                                                                                    'typography '
                                                                                                                                                                                    'file '
                                                                                                                                                                                    'names '
                                                                                                                                                                                    'to '
                                                                                                                                                                                    'tell '
                                                                                                                                                                                    'them '
                                                                                                                                                                                    'apart '
                                                                                                                                                                                    'from '
                                                                                                                                                                                    'text): '
                                                                                                                                                                                    'check '
                                                                                                                                                                                    'the '
                                                                                                                                                                                    'result '
                                                                                                                                                                                    'in '
                                                                                                                                                                                    'the '
                                                                                                                                                                                    'game '
                                                                                                                                                                                    'on '
                                                                                                                                                                                    'a '
                                                                                                                                                                                    'single '
                                                                                                                                                                                    'field '
                                                                                                                                                                                    'before '
                                                                                                                                                                                    'editing '
                                                                                                                                                                                    'many.',
 '  Непустых текстов в файле нет, поиск по тексту ничего не найдёт. Вводи ID поля сразу. Вот первые поля:': '  There are no non-empty '
                                                                                                            'texts in the file, so text '
                                                                                                            'search will find nothing. '
                                                                                                            'Enter the field ID directly. '
                                                                                                            'Here are the first fields:',
 ', ключ ': ', key ',
 ', шрифт ': ', font ',
 ', рамка ': ', bounds ',
 'изменены только выбранные поля, остальное содержимое без изменений': 'only the selected fields were changed, all other content is '
                                                                       'unchanged',
 'Есть фрагмент ': 'There is a fragment ',
 ', он больше лимита атласа ': ', it is larger than the atlas limit ',
 'MD5 файла текстур не совпал': 'Texture file MD5 mismatch',
 'MD5 основного файла не совпал': 'Main file MD5 mismatch',
 'UV или номер атласа в файле не совпали с планом': 'UV or atlas index in the file does not match the plan',
 'У SC1 нет пикселей текстур: положи рядом файл <имя>_tex.sc': 'SC1 has no texture pixels: put the <name>_tex.sc file next to it',
 'Перестройка атласов умеет только SC2 версии 5 с 7 блоками': 'Atlas rebuild only supports SC2 version 5 with 7 blocks',
 '  ⚠ Внешние текстуры закодированы встроенным энкодером ASTC: он проще astcenc, поэтому картинка чуть хуже (блоки, которые не менялись, взяты из оригинала без потерь). Как это выглядит в игре, хаб проверить не может.': '  '
                                                                                                                                                                                                                            '⚠ '
                                                                                                                                                                                                                            'External '
                                                                                                                                                                                                                            'textures '
                                                                                                                                                                                                                            'were '
                                                                                                                                                                                                                            'encoded '
                                                                                                                                                                                                                            'with '
                                                                                                                                                                                                                            'the '
                                                                                                                                                                                                                            'built-in '
                                                                                                                                                                                                                            'ASTC '
                                                                                                                                                                                                                            'encoder: '
                                                                                                                                                                                                                            'it '
                                                                                                                                                                                                                            'is '
                                                                                                                                                                                                                            'simpler '
                                                                                                                                                                                                                            'than '
                                                                                                                                                                                                                            'astcenc, '
                                                                                                                                                                                                                            'so '
                                                                                                                                                                                                                            'the '
                                                                                                                                                                                                                            'image '
                                                                                                                                                                                                                            'is '
                                                                                                                                                                                                                            'slightly '
                                                                                                                                                                                                                            'worse '
                                                                                                                                                                                                                            '(blocks '
                                                                                                                                                                                                                            'that '
                                                                                                                                                                                                                            'did '
                                                                                                                                                                                                                            'not '
                                                                                                                                                                                                                            'change '
                                                                                                                                                                                                                            'are '
                                                                                                                                                                                                                            'taken '
                                                                                                                                                                                                                            'from '
                                                                                                                                                                                                                            'the '
                                                                                                                                                                                                                            'original '
                                                                                                                                                                                                                            'losslessly). '
                                                                                                                                                                                                                            'The '
                                                                                                                                                                                                                            'hub '
                                                                                                                                                                                                                            'cannot '
                                                                                                                                                                                                                            'check '
                                                                                                                                                                                                                            'how '
                                                                                                                                                                                                                            'it '
                                                                                                                                                                                                                            'looks '
                                                                                                                                                                                                                            'in '
                                                                                                                                                                                                                            'the '
                                                                                                                                                                                                                            'game.',
 'Размер и отступ должны быть числами': 'Size and padding must be numbers',
 '  Размеры атласов: 1 - впритык, одна сторона ': '  Atlas sizes: 1 - tight fit, one side ',
 ', атласов в файле: ': ', atlases in file: ',
 '  Раскладка заняла ': '  Layout took ',
 ' с, в памяти игры (RGBA8): ~': ' s, in-game memory (RGBA8): ~',
 '  ⚠ Исходные текстуры в сжатом формате (ASTC): новые пишутся как сырой RGBA8, файл и память игры станут заметно больше.': '  ⚠ Source '
                                                                                                                            'textures are '
                                                                                                                            'in a '
                                                                                                                            'compressed '
                                                                                                                            'format '
                                                                                                                            '(ASTC): new '
                                                                                                                            'ones are '
                                                                                                                            'written as '
                                                                                                                            'raw RGBA8, '
                                                                                                                            'the file and '
                                                                                                                            'in-game '
                                                                                                                            'memory will '
                                                                                                                            'be noticeably '
                                                                                                                            'larger.',
 'пиксели внутри каждой фигуры и все UV совпали с исходными, остальное содержимое файла не менялось': 'pixels inside every shape and all '
                                                                                                      'UVs match the originals, the rest '
                                                                                                      'of the file content is unchanged',
 "Нет таких ExportName'ов в файле: ": 'No such ExportNames in the file: ',
 'В файле есть клипы с тегами ': 'The file has clips with tags ',
 ', которые хаб не разбирает: он не видит их детей и не может безопасно решить, какие шейпы никому не нужны': ', which the hub does not '
                                                                                                              'parse: it cannot see their '
                                                                                                              'children and cannot safely '
                                                                                                              'decide which shapes are not '
                                                                                                              'needed by anyone',
 '  Вместе с ними удалятся: клипов ': '  Deleted along with them: clips ',
 '  ✔ Удаление проверено: ': '  ✔ Deletion verified: ',
 'все оставшиеся клипы, шейпы и текстовые поля не изменились, висящих ссылок нет': 'all remaining clips, shapes and text fields are '
                                                                                   'unchanged, no dangling references',
 '  Куски удалённых объектов остались в атласах: убрать их можно пунктом «Перестроить атласы».': '  Pieces of the deleted objects remain '
                                                                                                 'in the atlases: you can remove them with '
                                                                                                 'the "Rebuild atlases" option.',
 'Пустое имя экспорта': 'Empty export name',
 'В папке нет data.json или data.msgpack:\n  ': 'No data.json or data.msgpack in the folder:\n  ',
 'В банке больше 65535 матриц или цветов': 'The bank has more than 65535 matrices or colors',
 'В исходном SC1 были теги ': 'The original SC1 had tags ',
 ', которых нет в JSON - собрать без потерь нельзя': ', which are not in the JSON - cannot rebuild without loss',
 'Для SC1 нужны PNG-текстуры (KTX/ASTC в SC1 не бывает)': 'SC1 needs PNG textures (KTX/ASTC does not exist in SC1)',
 'ASTC-текстуры из PNG записаны как сырой RGBA8: файл и память игры станут больше': 'ASTC textures from PNG were written as raw RGBA8: the '
                                                                                    'file and in-game memory will be larger',
 'Проверка результата не прошла, файл не записан: после сборки и обратного разбора данные не совпали с твоим JSON:\n    ': 'Result check '
                                                                                                                           'failed, file '
                                                                                                                           'not written: '
                                                                                                                           'after building '
                                                                                                                           'and parsing '
                                                                                                                           'back, the data '
                                                                                                                           'did not match '
                                                                                                                           'your JSON:\n'
                                                                                                                           '    ',
 'Нет такой папки: ': 'No such folder: ',
 'готовый файл разобран обратно, всё, что было в JSON, совпало': 'the finished file was parsed back, everything that was in the JSON '
                                                                 'matched',
 'ID объекта (например 34)': 'Object ID (e.g. 34)',
 'Объект с ID ': 'Object with ID ',
 ' не найден': ' not found',
 '  Type: MovieClip, детей: ': '  Type: MovieClip, children: ',
 ', элементов кадров: ': ', frame elements: ',
 '  Вершин: ': '  Vertices: ',
 '  Текст: ': '  Text: ',
 ': позиции ': ': positions ',
 'В файле не нашлось картинок-частей (одиночных прямоугольных шейпов нужного размера)': 'No image parts found in the file (single '
                                                                                        'rectangular shapes of the required size)',
 '  Атлас: ': '  Atlas: ',
 ' млн пикселей': ' million pixels',
 'Для этого пункта нужен numpy (pip install numpy) - поставить автоматически не получилось': 'This option needs numpy (pip install numpy) '
                                                                                             '- automatic installation failed',
 '  ✔ Части и шаблон: ': '  ✔ Parts and template: ',
 'Дополнительный масштаб фона': 'Extra background scale',
 'Выбор': 'Choice',
 'Нужна картинка для лого': 'An image for the logo is required',
 'Длительность перехода, сек': 'Transition duration, sec',
 '  Результат всегда SC2 (даже если исходник был SC1). В игре не проверялось.': '  The result is always SC2 (even if the source was SC1). '
                                                                                'Not tested in the game.',
 '  ✔ Сохранено: ': '  ✔ Saved: ',
 '    внешний _tex.sc: ': '    external _tex.sc: ',
 '  PNG больше ': '  PNG is larger than ',
 'px, текстура ужата до ': 'px, texture downscaled to ',
 ' (размер в игре не меняется)': ' (in-game size does not change)',
 'В папке нет PNG: ': 'No PNG in the folder: ',
 '  найдено PNG: ': '  PNG found: ',
 'Слотов ': 'Slots: ',
 ', уникальных кадров ': ', unique frames: ',
 '. Хранить каждый кадр один раз (экономит память)': '. Store each frame once (saves memory)',
 'Не получилось открыть файл как анимацию (': 'Could not open the file as an animation (',
 '). Проверь, что это GIF': '). Check that it is a GIF',
 ' кадров (разных: ': ' frames (distinct: ',
 ' с': ' s',
 ', есть прозрачность': ', has transparency',
 '    страница ': '    page ',
 'Не смог распаковать данные (не zstd или другой контейнер): ': 'Could not decompress the data (not zstd or another container): ',
 ', лишних байт в конце: ': ', extra bytes at the end: ',
 '<строка #': '<string #',
 '(не таблица)': '(not a table)',
 '... (обрезано)': '... (truncated)',
 '  ошибка: ': '  error: ',
 'SC1 (теги)': 'SC1 (tags)',
 'Экспорт #': 'Export #',
 ' ссылается на несуществующую строку ': ' refers to a nonexistent string ',
 'Пустое новое имя для «': 'Empty new name for «',
 'В новом имени «': 'The new name «',
 '» есть управляющие символы': '» contains control characters',
 'блок ': 'block ',
 ' изменился': ' changed',
 'поле дескриптора ': 'descriptor field ',
 ' изменилось': ' changed',
 '    ... ещё ': '    ... more: ',
 'id объекта без имени': 'id of the unnamed object',
 '  ✔ Имён в списке: ': '  ✔ Names in the list: ',
 'Объект ': 'Object ',
 ' нельзя назвать: у него уже есть ExportName или его нет в файле': ' cannot be named: it already has an ExportName or is not in the file',
 'добавлено имён: ': 'names added: ',
 ', остальное содержимое без изменений': ', the rest of the content is unchanged',
 '\n  Похожие:\n    ': '\n  Similar:\n    ',
 'Нет ExportName «': 'No ExportName «',
 'Кусок для замены не задан': 'No fragment to replace was given',
 'Файл со списком': 'List file',
 'Нет такого способа': 'No such method',
 'переименовано ': 'renamed ',
 ', остальное содержимое файла без изменений': ', the rest of the file content is unchanged',
 'У клипа ': 'MovieClip ',
 ' элементы кадров лежат за пределами вектора': ' has frame elements outside the vector bounds',
 'описание клипа ': 'MovieClip description ',
 'у клипа ': 'MovieClip ',
 ' изменились дети': ' children changed',
 ' изменились цвета': ' colors changed',
 'вершина ': 'vertex ',
 ' посчитана не так, как ожидалось': ' was computed differently than expected',
 'Не понял коэффициент «': 'Could not parse the factor «',
 '» (нужно 0.5 или 0.5,0.8 для X и Y)': '» (expected 0.5 or 0.5,0.8 for X and Y)',
 'Не понял сдвиг по ': 'Could not parse the shift for ',
 'Не нашёл клип «': 'MovieClip not found «',
 ', от центра шейпа': ', from the shape center',
 ', от (0,0)': ', from (0,0)',
 'клипов по списку: ': 'MovieClips from the list: ',
 'Нет такого варианта': 'No such option',
 'В одном банке матриц есть и обычные, и короткие (int16) матрицы - порядок индексов для такого сочетания не известен, не рискую разбирать этот файл': 'One '
                                                                                                                                                       'matrix '
                                                                                                                                                       'bank '
                                                                                                                                                       'contains '
                                                                                                                                                       'both '
                                                                                                                                                       'regular '
                                                                                                                                                       'and '
                                                                                                                                                       'short '
                                                                                                                                                       '(int16) '
                                                                                                                                                       'matrices '
                                                                                                                                                       '- '
                                                                                                                                                       'the '
                                                                                                                                                       'index '
                                                                                                                                                       'order '
                                                                                                                                                       'for '
                                                                                                                                                       'this '
                                                                                                                                                       'combination '
                                                                                                                                                       'is '
                                                                                                                                                       'unknown, '
                                                                                                                                                       'so '
                                                                                                                                                       'I '
                                                                                                                                                       "won't "
                                                                                                                                                       'risk '
                                                                                                                                                       'parsing '
                                                                                                                                                       'this '
                                                                                                                                                       'file',
 'Повреждён заголовок сжатия': 'Corrupted compression header',
 'Тег ': 'Tag ',
 ' выходит за пределы потока (позиция ': ' goes beyond the end of the stream (position ',
 'Текстовое поле ': 'TextField ',
 ' (тег ': ' (tag ',
 '): после разбора остались лишние байты - формат отличается от известного': '): extra bytes left after parsing - the format differs from '
                                                                             'the known one',
 'поле ': 'field ',
 ': bold/italic/выравнивание (': ': bold/italic/alignment (',
 ') переведены приблизительно': ') were converted approximately',
 'В этом SC1 есть теги, которые хаб не умеет переносить в SC2 (тег: сколько): ': 'This SC1 contains tags the hub cannot carry over to SC2 '
                                                                                 '(tag: count): ',
 'В файле текстур тег ': 'In the texture file, tag ',
 '  Ставлю texture2ddecoder для распаковки ASTC-текстур...': '  Installing texture2ddecoder to unpack ASTC textures...',
 'KTX с форматом 0x': 'KTX with format 0x',
 ', декодера нет': ', no decoder available',
 'нет такого диапазона цветов': 'no such color range',
 ': не удалось распаковать zstd (': ': failed to decompress zstd (',
 ': тип пикселей ': ': pixel type ',
 ' байт) хаб пока не умеет разбирать - поддерживается ASTC и RGBA8': ' bytes) the hub cannot parse yet - ASTC and RGBA8 are supported',
 ': формат KTX 0x': ': KTX format 0x',
 ' хаб не умеет разбирать (поддерживается ASTC и RGBA8)': ' the hub cannot parse (ASTC and RGBA8 are supported)',
 ' ссылается на «': ' references «',
 '», но такого файла нет рядом с .sc (положи его туда же или в папку input)': '», but there is no such file next to the .sc (put it there '
                                                                              'or in the input folder)',
 '  Текстуры взяты из ': '  Textures taken from ',
 '  Внешние файлы: ': '  External files: ',
 'Матрица нестандартной длины - файл устроен иначе, чем ожидалось': 'Matrix of non-standard length - the file is structured differently '
                                                                    'than expected',
 'у шейпа ': 'shape ',
 ' изменились текстурные координаты': ' texture coordinates changed',
 ' изменились дети или цвета': ' children or colors changed',
 'Проверка результата не прошла, файл не записан: клип ': 'Result check failed, file not written: MovieClip ',
 ' изменился не так': ' changed incorrectly',
 'SC1: контейнер версии ': 'SC1: container version ',
 'Теги, которых я не знаю (хаб сохраняет их как есть, но не разбирает): ': "Tags I don't know (the hub keeps them as is but does not parse "
                                                                           'them): ',
 ' в файле нет поля частоты кадров (значение 0) - поменять на месте нельзя': ' has no frame rate field in the file (value 0) - cannot '
                                                                             'change it in place',
 'Проверка результата не прошла, файл не записан: блок ': 'Result check failed, file not written: block ',
 '  Сейчас у клипа ': '  Current fps of MovieClip ',
 '  Сейчас: ': '  Currently: ',
 'Частота кадров не задана или не число': 'Frame rate is missing or not a number',
 'Нет текстового поля с id ': 'No TextField with id ',
 'Размер шрифта должен быть от 0 до 255': 'Font size must be from 0 to 255',
 'Рамка: четыре целых числа от -32768 до 32767': 'Bounds: four integers from -32768 to 32767',
 ' изменилось не так, как задумано': ' changed differently than intended',
 'Не понял рамку «': 'Could not parse the bounds «',
 '» (нужно четыре числа: left,top,right,bottom)': '» (expected four numbers: left,top,right,bottom)',
 ', минус - не использовать)': ", minus - don't use)",
 '  Нет такого языка: ': '  No such language: ',
 ' строк': ' strings',
 ' нет таблицы имён, поменять имя нельзя': ' has no name table, the name cannot be changed',
 'имя в клипе ': 'name in MovieClip ',
 ' (экземпляр ': ' (instance ',
 ') вышло не таким': ') came out wrong',
 'Имя длиннее 254 байт в SC1 не поместится': 'A name longer than 254 bytes will not fit in SC1',
 'Не понял «': 'Could not parse «',
 '» (нужны номера через запятую, диапазон 3-7 или all)': '» (expected comma-separated numbers, a range like 3-7, or all)',
 ': текстура ': ': texture ',
 ' не сохранилась в PNG (формат не поддержан?)': ' was not saved as PNG (format not supported?)',
 '  Пересечения id разрешены перенумерацией: ': '  id collisions resolved by renumbering: ',
 '  ⚠ Повторяющиеся имена экспортов переименованы (': '  ⚠ Duplicate export names were renamed (',
 '  ⚠ Не вошли: ': '  ⚠ Not included: ',
 'После пропуска ошибочных файлов осталось меньше двух - объединять нечего': 'Fewer than two files remain after skipping the faulty ones - '
                                                                             'nothing to merge',
 'Что искать в именах (Enter - закончить выбор, * - показать все)': 'What to search for in names (Enter - finish selection, * - show all)',
 ' (показаны первые 50)': ' (first 50 shown)',
 'Какие взять (номера 1,3,5-7 или all; Enter - искать заново)': 'Which to take (numbers 1,3,5-7 or all; Enter - search again)',
 '  ✔ Выбрано всего: ': '  ✔ Total selected: ',
 '  В выборке: экспортов ': '  In selection: exports ',
 ' (из ': ' (of ',
 'строка ': 'line ',
 ': в файле нет экземпляра клип ': ': the file has no such instance: MovieClip ',
 ', номер ': ', number ',
 ': поле/имя в файле другие («': ': the field/name in the file differ («',
 '», поле ': '», field ',
 ') - таблица от другого файла?': ') - is the table from a different file?',
 '  Строк: ': '  Lines: ',
 '. Заполни колонку new_name там, где нужно поменять имя/текст; ': '. Fill in the new_name column where you want to change the name/text; ',
 ' в ней сделает имя пустым. Остальные колонки не трогай.': " in it makes the name empty. Don't touch the other columns.",
 '  ... и ещё замечаний: ': '  ... and more warnings: ',
 '  выбран: ': '  selected: ',
 '  теги внутри него: ': '  tags inside it: ',
 '  ВНИМАНИЕ: у текстур нет пикселей, а _tex.sc рядом не найден': '  WARNING: the textures have no pixels, and no _tex.sc was found nearby',
 '  текстура ': '  texture ',
 'Проверка оптимизации не прошла: изменился внешний вид экспортов ': 'Optimization check failed: the appearance of the exports changed ',
 '  Есть и top.gif, и top.png: беру анимированный top.gif': '  Both top.gif and top.png exist: using the animated top.gif',
 ' кадров. Каждый кадр хранится отдельной текстурой, поэтому файл растёт с числом кадров (одинаковые кадры подряд хранятся один раз).': ' '
                                                                                                                                        'frames. '
                                                                                                                                        'Each '
                                                                                                                                        'frame '
                                                                                                                                        'is '
                                                                                                                                        'stored '
                                                                                                                                        'as '
                                                                                                                                        'a '
                                                                                                                                        'separate '
                                                                                                                                        'texture, '
                                                                                                                                        'so '
                                                                                                                                        'the '
                                                                                                                                        'file '
                                                                                                                                        'grows '
                                                                                                                                        'with '
                                                                                                                                        'the '
                                                                                                                                        'frame '
                                                                                                                                        'count '
                                                                                                                                        '(identical '
                                                                                                                                        'consecutive '
                                                                                                                                        'frames '
                                                                                                                                        'are '
                                                                                                                                        'stored '
                                                                                                                                        'once).',
 'Расстановка': 'Layout',
 'Плотность': 'Density',
 '  Стадия ': '  Stage ',
 ': экспорт «': ': export «',
 ', статичный': ', static',
 '  ⚠ У ': '  ⚠ For ',
 ' экземпляров клип без таблицы имён: их поменять нельзя, они помечены ✖': ' instances the clip has no name table: they cannot be changed '
                                                                           'and are marked ✖',
 ' (показаны первые 40)': ' (first 40 shown)',
 'Какие править (номера 1,3,5-7 или all; Enter - искать заново)': 'Which to edit (numbers 1,3,5-7 or all; Enter - search again)',
 '  Нечего править': '  Nothing to edit',
 ', ?слово - подобрать TID по тексту таблицы': ', ?word - find a TID by name table text',
 '  Ничего не изменено': '  Nothing changed',
 '  ✔ В список правок добавлено: ': '  ✔ Added to the edit list: ',
 ' (всего: ': ' (total: ',
 '  ... и ещё ': '  ... and ',
 ', используется в: ': ', used in: ',
 ': (пусто)': ': (empty)',
 'Введите текст, который ищете, или сразу ID поля (Enter - закончить выбор)': 'Enter the text you are looking for, or a field ID directly '
                                                                              '(Enter - finish picking)',
 'текст «': 'text «',
 'размер ': 'size ',
 'рамка ': 'frame ',
 '  поле ': '  field ',
 '    вариант ': '    variant ',
 '): бинов ': '): bins ',
 ', в последнем ': ', in the last one ',
 ' МП': ' MP',
 'В дескрипторе нет поля ': 'The descriptor has no field ',
 ' - файл устроен иначе, чем ожидалось': ' - the file is structured differently than expected',
 'Шейп ': 'Shape ',
 ' собирается обратно не байт-в-байт - не рискую его менять': ' does not rebuild byte-for-byte - not risking changing it',
 'атлас ': 'atlas ',
 ' в файле не совпал с собранным': ' in the file did not match the rebuilt one',
 'пиксели фигуры команды #': 'pixels of the shape for command #',
 ' не совпали с оригиналом': ' did not match the original',
 'Команда шейпа ссылается на несуществующий атлас ': 'A shape command refers to a nonexistent atlas ',
 'Атлас ': 'Atlas ',
 ' не удалось распаковать: ': ' could not be unpacked: ',
 'с нуля': 'from scratch',
 'со слиянием': 'with merging',
 'Выброшено объектов, недостижимых ни от одного ExportName: клипов ': 'Dropped objects unreachable from any ExportName: clips ',
 'Фрагментов: ': 'Fragments: ',
 ' команд шейпов), варианты раскладки: ': ' shape commands), layout variants: ',
 ', лучший: ': ', best: ',
 'Было атласов: ': 'Atlases before: ',
 ' МП), нужно пикселей под фигуры: ': ' MP), pixels needed for shapes: ',
 ' МП, станет атласов: ': ' MP, atlases after: ',
 ' МП)': ' MP)',
 'Размеры новых атласов: ': 'New atlas sizes: ',
 'Режим: ': 'Mode: ',
 '  Кодирую ': '  Encoding ',
 ', ASTC; на больших текстурах это минуты)...': ', ASTC; large textures take minutes)...',
 '    файл не менялся (картинка та же), оставлен как был': '    file unchanged (same image), left as it was',
 ', не используются: ': ', unused: ',
 ' (будут выброшены)': ' (will be dropped)',
 '  Текстуры SC1 будут записаны отдельным файлом: ': '  SC1 textures will be written to a separate file: ',
 '  ⚠ Новые атласы получаются больше прежних (': '  ⚠ The new atlases are larger than the old ones (',
 ' МП против ': ' MP vs ',
 ' МП). Попробуй размер «впритык».': ' MP). Try the "tight" size.',
 'Не могу сдвинуть вектор: ссылка указывала бы назад': 'Cannot shift the vector: the reference would point backwards',
 'вершины оставшегося шейпа ': 'vertices of the remaining shape ',
 ' изменились': ' changed',
 'описание оставшегося клипа ': 'description of the remaining clip ',
 'кадры оставшегося клипа ': 'frames of the remaining clip ',
 'у оставшегося клипа ': 'remaining clip ',
 ' остались ссылки на удалённые объекты ': ' still has references to deleted objects ',
 'экспорт ': 'export ',
 ' ссылается на удалённый клип': ' refers to a deleted clip',
 'номер экспорта у клипа ': 'export index of clip ',
 ' неверный после сдвига позиций': ' is wrong after the position shift',
 'счётчик ': 'counter ',
 ' в дескрипторе неверный': ' in the descriptor is wrong',
 'оставшийся шейп ': 'remaining shape ',
 'оставшийся клип ': 'remaining clip ',
 ' остались ссылки на удалённые объекты': ' still has references to deleted objects',
 'Не нашёл ExportName «': 'Could not find ExportName «',
 '  Останутся, потому что нужны другим: клипов ': '  Kept because others need them: clips ',
 '  Бесхозных клипов (на них никто не ссылается) в файле ': '  Orphan clips (nothing references them) in the file: ',
 ': они не трогаются': ': they are left untouched',
 '  Перестраиваю атласы с нуля (распаковка текстур и поиск раскладки)...': '  Rebuilding atlases from scratch (unpacking textures and '
                                                                           'searching for a layout)...',
 '  ✔ Атласы перестроены и проверены': '  ✔ Atlases rebuilt and verified',
 'В таблице клипа есть неизвестные поля ': 'The clip table has unknown fields ',
 ' - не рискую пересобирать блок клипов': ' - not risking rebuilding the clip block',
 'В имени «': 'The name «',
 'Имя «': 'The name «',
 '» уже занято (или повторяется в списке)': '» is already taken (or duplicated in the list)',
 'Нет объекта (клипа, шейпа или текстового поля) с id ': 'No object (clip, shape or TextField) with id ',
 'У объекта ': 'Object ',
 ' уже есть ExportName - для смены имени используй переименование': ' already has an ExportName - use rename to change the name',
 'кадры клипа ': 'frames of clip ',
 ' неверный': ' is wrong',
 'Нет клипа или шейпа с id ': 'No clip or shape with id ',
 '» длиннее 254 байт - в SC1 длина строки один байт': '» is longer than 254 bytes - in SC1 the string length is one byte',
 'Не смог прочитать ': 'Could not read ',
 'В данных нет раздела «': 'The data has no section «',
 '» (или он не список)': '» (or it is not a list)',
 ': нужно целое от 0 до 65535, а там ': ': expected an integer from 0 to 65535, got ',
 'Экспорт «': 'Export «',
 '» ссылается на несуществующий объект ': '» refers to a nonexistent object ',
 'У экспорта клипа ': 'Export of clip ',
 ' нет имени': ' has no name',
 'Имя экспорта «': 'Export name «',
 '» повторяется': '» is duplicated',
 'Клип ': 'Clip ',
 ' вне списка банков': ' is outside the bank list',
 ': сумма elements_count по кадрам (': ': sum of elements_count over frames (',
 ') не равна числу frame_elements (': ') does not equal the number of frame_elements (',
 ': ожидался объект': ': expected an object',
 '» длиннее 254 байт': '» is longer than 254 bytes',
 ' не совпали с ожидаемыми': ' did not match the expected ones',
 'дети клипа ': 'children of clip ',
 '<нет>': '<none>',
 '  Открыл: ': '  Opened: ',
 '  Папка: ': '  Folder: ',
 'Не смог открыть картинку «': 'Could not open the image «',
 'Для перехода нужны supercell_logo.png и второе лого': 'The transition needs supercell_logo.png and a second logo',
 'Не нашёл клип интро с логотипом SUPERCELL': 'Could not find the intro clip with the SUPERCELL logo',
 '» - не число': '» - not a number',
 '    ... и ещё ': '    ... and ',
 'Выход': 'Exit',
 '  Файлы пакета положил рядом с результатом (без изменений): ': '  Package files placed next to the result (unchanged): ',
 '(? - выбрать в окне)': '(? - pick in window)',
 'Не понял масштаб «': "Couldn't parse scale «",
 '  Нет такой точки «': '  No such point «',
 '», беру center': '», using center',
 '\r  анализ кадров: ': '\r  analyzing frames: ',
 'Не понял число «': "Couldn't parse number «",
 'Анимация слишком длинная: ': 'Animation is too long: ',
 ' слотов при 1 кадр/с (максимум ': ' slots at 1 frame/s (max ',
 '  Получилось ': '  Got ',
 ' слотов таймлайна, максимум ': ' timeline slots, max ',
 '. Выбери FPS поменьше.': '. Pick a lower FPS.',
 'В GIF слишком много разных кадров: ': 'Too many distinct frames in GIF: ',
 ' (максимум ': ' (max ',
 '  При ': '  At ',
 ' кадр/с выходит ': ' frames/s you get ',
 ' разных кадров, максимум ': ' distinct frames, max ',
 '  Раздельных кадров вышло бы больше максимума - оставляю экономный режим': '  Separate frames would exceed the max - keeping economy '
                                                                             'mode',
 '  Ширина ': '  Width ',
 'px не влезает в лимиты формата, оставляю ': "px doesn't fit the format limits, keeping ",
 'DataStorage: не разобрать по схеме gifconv (': "DataStorage: can't parse with the gifconv schema (",
 'ExportNames: не разобрать по схеме gifconv (': "ExportNames: can't parse with the gifconv schema (",
 'Shapes: не разобрать по схеме gifconv (': "Shapes: can't parse with the gifconv schema (",
 'MovieClips: не разобрать по схеме gifconv (': "MovieClips: can't parse with the gifconv schema (",
 'Textures: не разобрать по схеме gifconv (': "Textures: can't parse with the gifconv schema (",
 ' (запись 40 байт: id, шрифт, рамка int16 x4, ...)': ' (40-byte record: id, font, int16 x4 frame, ...)',
 'TextFields: не разобрать (': "TextFields: can't parse (",
 '   -> строка ': '   -> string ',
 ': нет': ': none',
 'Строка ': 'Line ',
 ' в ': ' in ',
 ': нужен формат старое=новое': ': expected format old=new',
 ': пустое имя': ': empty name',
 'шейп id ': 'shape id ',
 ' вершин, ': ' vertices, ',
 'вершин нет': 'no vertices',
 'Имя для этого объекта': 'Name for this object',
 '  Имя не задано, объект пропущен': '  No name set, object skipped',
 'Ни одно имя не содержит «': 'No name contains «',
 'Файла не было, я создал шаблон со всеми именами. Отредактируй и запусти пункт снова.': "The file didn't exist, so I created a template "
                                                                                         'with all names. Edit it and run this option '
                                                                                         'again.',
 ': номер матрицы ': ': matrix index ',
 ' вне банка ': ' is outside bank ',
 ' нет элементов кадров - менять нечего': ' has no frame elements - nothing to change',
 ' ссылается на несуществующий банк матриц ': ' references a nonexistent matrix bank ',
 'У шейпа ': 'Shape ',
 ' нет вершин': ' has no vertices',
 ': номер цвета ': ': color index ',
 'у невыбранного клипа ': 'unselected clip ',
 ' изменились итоговые матрицы': ' had its final matrices changed',
 'изменилась вершина ': 'changed vertex ',
 ', не относящаяся к выбранным шейпам': ", which doesn't belong to the selected shapes",
 'Не понял диапазон «': "Couldn't parse range «",
 '» в списке id': '» in the id list',
 '  ⚠ Клипов, которые используются внутри других клипов: ': '  ⚠ Clips used inside other clips: ',
 ' (например ': ' (for example ',
 ' - в ': ' - in ',
 '). Внутри тех клипов они изменятся тоже.': '). They will change inside those clips too.',
 'Среди детей этих клипов нет шейпов (там только вложенные клипы)': "None of these clips' children are shapes (only nested clips)",
 ', шейпов у них напрямую: ': ', shapes directly in them: ',
 'Точка': 'Point',
 'Файла не было, я создал шаблон со всеми экспортами. Отредактируй и запусти пункт снова.': "The file didn't exist, so I created a "
                                                                                            'template with all exports. Edit it and run '
                                                                                            'this option again.',
 'В списке нет ни одной строки, где что-то меняется': 'No line in the list changes anything',
 ': индекс сетки масштабирования ': ': scale grid index ',
 ' вне списка': ' is out of range',
 ': тип команды ': ': command type ',
 ' в SC1 хаб не умеет переводить в SC2': " can't be converted from SC1 to SC2 by the hub",
 'текстура ': 'texture ',
 '  ⚠ Теги, которые хаб не разбирает (пропущены в JSON): ': "  ⚠ Tags the hub doesn't parse (skipped in JSON): ",
 'изменился невыбранный шейп ': 'changed unselected shape ',
 'вершины шейпа ': 'vertices of shape ',
 ' посчитаны не так, как ожидалось': ' were computed differently than expected',
 ' (реальная ': ' (actual ',
 'совпал': 'matched',
 'НЕ совпал': 'DID NOT match',
 'Текстура (тег ': 'Texture (tag ',
 ', формат пикселей ': ', pixel format ',
 'внешняя (пиксели в _tex.sc)': 'external (pixels in _tex.sc)',
 '  Экспортных клипов: ': '  Export clips: ',
 '. Сейчас: ': '. Currently: ',
 'Файла не было, я создал шаблон со всеми экспортами и их текущей частотой. Отредактируй и запусти пункт снова.': "The file didn't exist, "
                                                                                                                  'so I created a template '
                                                                                                                  'with all exports and '
                                                                                                                  'their current rate. '
                                                                                                                  'Edit it and run this '
                                                                                                                  'option again.',
 'В списке нет ни одного клипа, где частота меняется': 'No clip in the list has its rate changed',
 'Язык для поиска по тексту (Enter - ': 'Language for text search (Enter - ',
 'Номер ': 'Number ',
 ' вне списка (1..': ' is out of range (1..',
 'Проверка слияния: клип ': 'Merge check: clip ',
 'Проверка слияния: ссылки клипа ': 'Merge check: references of clip ',
 ' не сходятся': " don't match",
 'Проверка слияния: банк матриц клипа ': 'Merge check: matrix bank of clip ',
 ' не совпал': " didn't match",
 'Проверка слияния: шейп ': 'Merge check: shape ',
 'Проверка слияния: текстовое поле ': 'Merge check: text field ',
 '      ... и ещё ': '      ... plus ',
 ': clip_id/child не числа': ': clip_id/child are not numbers',
 ': ошибка ': ': error ',
 ' нет': ' missing',
 'сошёлся': 'matched',
 'НЕ сошёлся': 'DID NOT match',
 '  хаб может переписать файл: да (круговая сборка побайтно)': '  hub can rewrite the file: yes (round-trip rebuild, byte for byte)',
 'Стадия (Enter - ': 'Stage (Enter - ',
 ': рекомендуется 2048x1152; пропорции у него 16:9, соберу как есть.': ': recommended 2048x1152; its aspect ratio is 16:9, building as is.',
 ' вписан в ': ' fitted into ',
 ', края прозрачные': ', edges transparent',
 '  ⚠ У symbol.png нет прозрачности: символ будет прямоугольником. Нужен белый символ с альфа-каналом.': '  ⚠ symbol.png has no '
                                                                                                         'transparency: the symbol will be '
                                                                                                         'a rectangle. You need a white '
                                                                                                         'symbol with an alpha channel.',
 'Единиц игры на пиксель (Enter - ': 'Game units per pixel (Enter - ',
 '; 1 - один к одному)': '; 1 - one to one)',
 '  Примерно ': '  About ',
 ' символов в кадре.': ' symbols in frame.',
 'Число для случайной раскладки (другое число - другая расстановка)': 'Number for random layout (a different number gives a different '
                                                                      'arrangement)',
 'Сдвиг верхней части вправо, единиц (минус - влево; Enter - 0, по центру)': 'Shift of the top part to the right, units (minus - left; '
                                                                             'Enter - 0, centered)',
 '  Гифка: ': '  GIF: ',
 ' разных кадров, длительность ': ' distinct frames, duration ',
 ', символов ': ', symbols ',
 ', поле наклонено на ': ', field tilted by ',
 '°, цикл ': '°, cycle ',
 ' с: за цикл символы смещаются на шаг ': ' s: per cycle the symbols shift by step ',
 ' вдоль наклона, скорость ': ' along the tilt, speed ',
 ' ед/с': ' u/s',
 '  ⚠ Среди выбранных есть обычные имена (': '  ⚠ Selection includes ordinary names (',
 '): код игры может искать поле по этому имени.': '): the game code may look up the field by this name.',
 ': шрифт ': ': font ',
 ' и ещё ': ' and ',
 '  Совпадений: ': '  Matches: ',
 '    ничего не найдено; можно искать заново или ввести ID поля': '    nothing found; you can search again or enter a field ID',
 'ID поля для правки (Enter - искать заново)': 'Field ID to edit (Enter - search again)',
 '  ✔ Поле ': '  ✔ Field ',
 ' добавлено в список правок (всего полей: ': ' added to the edit list (total fields: ',
 'Команды шейпов делят общие вершины - перестройка атласов такое не поддерживает': 'Shape commands share vertices - atlas rebuilding does '
                                                                                   'not support this',
 'У экспортируемого клипа ': 'Exported clip ',
 ' нет поля номера экспорта - не рискую менять': ' has no export number field - not risking a change',
 ' в дескрипторе изменился без причины': ' in the descriptor changed unexpectedly',
 'Нет файла со списком: ': 'List file not found: ',
 'Повторяется id ': 'Duplicate id ',
 ' и ': ' and ',
 ' вне списка текстур': ' is outside the texture list',
 ': ребёнок ': ': child ',
 ' не найден ни среди шейпов, клипов, текстовых полей, ни среди модификаторов': ' not found among shapes, clips, text fields, or modifiers',
 ' вне списка детей': ' is outside the children list',
 ': нет в результате': ': missing in result',
 ': длина ': ': length ',
 ' против ': ' vs ',
 'Нет файла текстуры ': 'Texture file not found: ',
 ' (текстура #': ' (texture #',
 ') в папке ': ') in folder ',
 ' не похож на KTX': ' does not look like KTX',
 'Текстура ': 'Texture ',
 ' слишком большая': ' is too large',
 'изменился незатронутый клип ': 'untouched clip changed: ',
 'Проверка результата не прошла, файл не записан: изменился клип ': 'Result check failed, file not written: clip changed: ',
 'Проверка результата не прошла, файл не записан: дети клипа ': 'Result check failed, file not written: children of clip ',
 ' не совпали': ' do not match',
 '  Заменено: ': '  Replaced: ',
 '  Интро: ': '  Intro: ',
 ' кадров (': ' frames (',
 ' с при 60 к/с)': ' s at 60 fps)',
 'Картинки': 'Images',
 '\n  Отменено': '\n  Cancelled',
 '  Превью не получилось: ': '  Preview failed: ',
 '  шрифт ': '  font ',
 '  Нет файла ': '  File not found: ',
 '» - не id': '» is not an id',
 '  У объекта ': '  Object ',
 ' уже есть ExportName (': ' already has an ExportName (',
 ') - для смены имени есть способ 1': ') - use method 1 to change the name',
 '  Нет клипа, шейпа или текстового поля с id ': '  No clip, shape or text field with id ',
 '  Объекту ': '  Object ',
 ' имя уже задано (': ' already has a name (',
 '), заменяю на ': '), replacing with ',
 '# Правь только то, что надо поменять: старое=новое. Остальные строки можно удалить.\n': '# Edit only what needs changing: old=new. The '
                                                                                          'other lines can be deleted.\n',
 ' ссылается на вершины за пределами таблицы': ' references vertices outside the table',
 'Банк ': 'Bank ',
 ' полон, а у клипа ': ' is full, and clip ',
 ' нет поля банка - перенести его нельзя': ' has no bank field - it cannot be moved',
 'Банк матриц ': 'Matrix bank ',
 ' полон, и другого банка с достаточным местом для клипа ': ' is full, and there is no other bank with enough room for clip ',
 'Вершины шейпа ': 'Vertices of shape ',
 ' общие с шейпами ': ' are shared with shapes ',
 ' - править на месте нельзя': ' - cannot edit in place',
 'матрицы клипа ': 'matrices of clip ',
 '  ⚠ Шейпов, которыми пользуются несколько клипов: ': '  ⚠ Shapes used by several clips: ',
 '. Изменение затронет все эти клипы сразу.': '. The change will affect all of these clips at once.',
 '# имя  коэффициент  dx  dy   (пропущенное = 1 0 0). Строки, где менять нечего, можно удалить.\n': '# name  factor  dx  dy   (omitted = 1 '
                                                                                                    '0 0). Lines with nothing to change '
                                                                                                    'can be deleted.\n',
 '  Положение клипа (кадр 1) было:   ': '  Clip position (frame 1) was:     ',
 '                           стало:  ': '                           now:    ',
 'Не смог распаковать данные (ни zstd, ни LZMA): ': 'Could not decompress data (neither zstd nor LZMA): ',
 'пиксели внутри (': 'pixels inside (',
 '# имя  fps   (строки, где менять не надо, можно удалить)\n': '# name  fps   (lines that need no change can be deleted)\n',
 'Пропущено файлов: ': 'Files skipped: ',
 '. Объединить остальные': '. Merge the rest',
 '  ссылка на ': '  reference to ',
 'файл найден': 'file found',
 'ФАЙЛА НЕТ рядом': 'FILE NOT FOUND nearby',
 'Размер кадров гифки в файле: 1 - как есть, 0.5 - вдвое меньше по каждой стороне, в игре размер тот же (Enter - ': 'GIF frame size in the '
                                                                                                                    'file: 1 - as is, 0.5 '
                                                                                                                    '- half size on each '
                                                                                                                    'side, same size in '
                                                                                                                    'game (Enter - ',
 '  ⚠ В top.png нет прозрачных пикселей - он закроет и градиент, и символы целиком': '  ⚠ top.png has no transparent pixels - it will '
                                                                                     'cover both the gradient and the symbols entirely',
 'Наклон всего поля, градусов по часовой (10.18 как в образце; 0 - без наклона; 45 - на северо-восток)': 'Tilt of the whole field, degrees '
                                                                                                         'clockwise (10.18 as in the '
                                                                                                         'sample; 0 - no tilt; 45 - '
                                                                                                         'north-east)',
 'Шаг решётки, единиц (в образце 144)': 'Grid spacing, units (144 in the sample)',
 'Ширина символа, единиц (в образце около 80)': 'Symbol width, units (about 80 in the sample)',
 'Скорость, единиц в секунду (в образце около 14)': 'Speed, units per second (about 14 in the sample)',
 'Дополнительный поворот каждого символа, градусов (0 - только наклон поля)': 'Extra rotation of each symbol, degrees (0 - field tilt '
                                                                              'only)',
 '  (без изменений)': '  (unchanged)',
 ' (показаны первые 10)': ' (first 10 shown)',
 '  Нет текстового поля с id «': '  No text field with id «',
 '  Размер «': '  Size «',
 '» не понял, оставляю ': '» not understood, keeping ',
 'Максимальный размер атласа в пикселях': 'Maximum atlas size in pixels',
 'Отступ вокруг каждого фрагмента, px': 'Padding around each fragment, px',
 '  ⚠ Атласы не перестроены, файл записан с прежними атласами: ': '  ⚠ Atlases not rebuilt, file written with the original atlases: ',
 '  Кодирую текстуру ': '  Encoding texture ',
 ') в ASTC...': ') to ASTC...',
 'Выбери пункт: ': 'Pick an option: ',
 '\n  ✖ Ошибка: ': '\n  ✖ Error: ',
 '   -> вектор ссылок? n=': '   -> reference vector? n=',
 '] строка ': '] string ',
 '] таблица:': '] table:',
 ': нужен формат «id имя»': ': expected format «id name»',
 'Нет шейпа с id ': 'No shape with id ',
 '; используется в клипах: ': '; used in clips: ',
 'В списке «': 'In list «',
 '»: такого клипа нет в файле': '»: no such clip in the file',
 'Непрозрачность символов, %': 'Symbol opacity, %',
 'Разброс поворота, ±градусов': 'Rotation spread, ±degrees',
 ': RGB умножен на альфу': ': RGB premultiplied by alpha',
 ' (Enter - закончить выбор, * - показать все': ' (Enter - finish selecting, * - show all',
 ', lang - сменить язык': ', lang - change language',
 '    сжатие ASTC с потерями: PSNR ~': '    lossy ASTC compression: PSNR ~',
 ' дБ': ' dB',
 ' (? - выбрать в окне)': ' (? - pick in a window)',
 '   -> вектор? n=': '   -> vector? n=',
 ' начало: ': ' start: ',
 '    шейп ': '    shape ',
 ': нужен формат имя  fps': ': expected format name  fps',
 ': клипа «': ': clip «',
 '» нет в файле': '» not in the file',
 ': fps не число': ': fps is not a number',
 ' (пиксели внутри)': ' (pixels inside)',
 ' (без пикселей)': ' (no pixels)',
 'msgpack: тип ': 'msgpack: type '}


# ================================================================ меню

MENU = [
    ("1", "PNG → SC", "картинка в .sc, масштаб клипа", flow_png),
    ("2", "GIF → SC", "анимация в .sc: любой размер, прозрачность, масштаб", flow_gif),
    ("3", "Разобрать .sc", "сводка + подробный дамп в .txt (SC1 и SC2)", flow_inspect),
    ("4", "Переименовать экспорты", "ExportName'ы в готовом .sc (SC1 и SC2)", flow_rename),
    ("5", "Размер и положение", "MovieClip / Shape: коэффициент и сдвиг в px", flow_transform),
    ("6", "FPS мувиклипов", "сменить частоту кадров клипам", flow_fps),
    ("7", "JSON/msgpack + PNG <-> .sc", "разобрать .sc и собрать обратно (SC1 и SC2)", flow_decode),
    ("8", "Правка TextField'ов", "тексты (имена в клипах, SC1/SC2), размер шрифта, рамка", flow_textfield),
    ("9", "Перестроить атласы", "с нуля + выброс неиспользуемого (до 4096 по стороне)", flow_atlas),
    ("10", "Удалить экспорты", "ExportName + клипы и шейпы только их (SC1 и SC2)", flow_delete),
    ("11", "Папка output", "открыть в проводнике", flow_open_output),
    ("12", "Информация по ID", "объект, родители, ExportName, TextField/localization", flow_inspect_id),
    ("13", "SC-пакет", "найти _tex.sc и main_<int>.sctx/.zktx", flow_sc_package),
    ("14", "Объединить / в один SC2", "папка .sc -> один SC2; один пакет -> один файл", flow_merge),
    ("15", "Таблица текстов (CSV)", "выгрузить имена/тексты и применить обратно", flow_text_table),
    ("16", "Перенос экспортов", "вынести в новый .sc или добавить в другой", flow_export_transfer),
    ("17", "Диагностика файла", "отчёт для баг-репорта (только метаданные)", flow_diagnostics),
    ("18", "Очистить / оптимизировать", "убрать недостижимое, слить одинаковое", flow_optimize),
    ("19", "Создать фон меню", "градиент + движущиеся символы + верхняя часть", flow_background),
    ("20", "Загрузочный экран", "разобрать loading.sc на части и собрать со своими картинками", flow_loading),
    ("21", "Части .sc <-> .sc", "разобрать любой .sc на PNG-части и собрать после правок", flow_parts),
]


def show_menu():
    clear()
    banner()
    print()
    for key, title, desc, _fn in MENU:
        print(f"  {cyan('[' + key + ']')}{' ' * (4 - len(key))}{bold(tr(title).ljust(28))}{gray(tr(desc))}")
    print(f"  {cyan('[L]')}   {bold(tr('Язык / Language').ljust(28))}{gray('English' if LANG == 'en' else 'Русский')}")
    print(f"  {cyan('[0]')}   {bold(tr('Выход'))}")
    print()
    line()
    print(gray(f"  input:  {IN_DIR}"))
    print(gray(f"  output: {OUT_DIR}"))
    print(gray("  Enter на любом вопросе = значение в [скобках]"))
    line()


def attach_package_files(results):
    """После правки основного SC1-файла кладёт рядом с результатом файлы его пакета (_tex.sc и .sctx/.zktx),
    чтобы набор остался рабочим: правки не трогают текстуры, поэтому файлы копируются как есть.
    Имена .sctx/.zktx остаются прежними (на них ссылается _tex.sc), _tex.sc получает имя результата."""
    import shutil
    src = next((p for p in reversed(LAST_INPUTS) if p.lower().endswith(".sc") and not p.lower().endswith("_tex.sc")), None)
    outs = [r for r in (results or []) if isinstance(r, str) and r.lower().endswith(".sc")
            and not r.lower().endswith("_tex.sc")]
    if not src or not outs or any(r.lower().endswith("_tex.sc") for r in (results or []) if isinstance(r, str)):
        return
    try:
        with open(src, "rb") as fh:
            data = fh.read()
        if sniff_format(data) != "sc1":
            return
        cont = sc1_container(data)
        if not sc1_head_sizes(cont):
            return                                      # текстуры внутри файла - пакета нет
        tex_files = tex_sc_candidates(src)
    except (UserError, OSError, struct.error):
        return
    out = outs[0]
    if os.path.abspath(out) == os.path.abspath(src):
        return
    sbase, obase = os.path.splitext(src)[0], os.path.splitext(out)[0]
    copied = []
    for t in tex_files:
        suffix = next((k for k in ("_highres_tex.sc", "_lowres_tex.sc", "_tex.sc") if t.endswith(k)), None)
        if not suffix:
            continue
        dest = obase + suffix
        if os.path.abspath(dest) != os.path.abspath(t):
            shutil.copyfile(t, dest)
            copied.append(dest)
        try:
            with open(t, "rb") as fh:
                refs = sc1_ext_refs(sc1_container(fh.read())["raw"])
        except (UserError, struct.error, OSError):
            refs = []
        for r in refs:
            p = find_ext_file(r["name"], t, src)
            if p:
                dest = os.path.join(os.path.dirname(os.path.abspath(out)), r["name"])
                if os.path.abspath(dest) != os.path.abspath(p) and not os.path.isfile(dest):
                    shutil.copyfile(p, dest)
                    copied.append(dest)
    if copied:
        print(gray("  Файлы пакета положил рядом с результатом (без изменений): "
                   + ", ".join(os.path.basename(c) for c in copied)))


def main():
    enable_ansi()
    lang_set(lang_detect(), save=False)
    ensure_dirs()
    handlers = {key: fn for key, _t, _d, fn in MENU}
    while True:
        show_menu()
        try:
            choice = input(cyan("Выбери пункт: ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if choice in ("0", "q", "exit", "выход"):
            break
        if choice.lower() in ("l", "д", "lang", "язык"):
            lang_set("ru" if LANG == "en" else "en")
            continue
        fn = handlers.get(choice)
        if fn is None:
            continue
        print()
        try:
            del LAST_INPUTS[:]
            attach_package_files(fn())
        except UserError as e:
            print(red("\n  ✖ " + str(e)))
        except KeyboardInterrupt:
            print(yellow("\n  Отменено"))
        except Exception as e:
            print(red(f"\n  ✖ Ошибка: {type(e).__name__}: {e}"))
        pause()
    print(gray("Пока!"))


if __name__ == "__main__":
    main()
