"""
Format layer: reading and writing .sc files (SC1 tags / SC2 flatbuffers), containers, builders.
Слой формата: чтение и запись .sc (SC1 теги / SC2 flatbuffers), контейнеры, сборщики.
No terminal, no questions to the user: only files and data. Messages go through notify().
"""

import base64
import os
import re
import struct
import subprocess
import sys


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


# Логика ничего не печатает сама: сообщения о ходе работы идут через notify(), а интерфейс подписывается
# на них (set_notifier). Строки с "⚠" - предупреждения, с "✔" - успех, остальные - ход работы.
_NOTIFIER = None
SEARCH_DIRS = []          # где искать связанные файлы (_tex.sc, .sctx, таблицы локализации); задаёт интерфейс


def set_notifier(fn):
    global _NOTIFIER
    _NOTIFIER = fn


def notify(msg):
    if _NOTIFIER is not None:
        _NOTIFIER(msg)


def saved_msg(path):
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    return f"  ✔ Сохранено: {os.path.abspath(path)}  ({fmt_size(size)})"


def warn_line(text):
    return "⚠ " + text.lstrip()


def unquote(text):
    return text.strip().strip('"').strip("'")


def fmt_size(n):
    return f"{n / 1024:.1f} КБ" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} МБ"


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


def clip_flat(m, clip):
    if m.get("kind") == "sc1":
        return clip["flat"]
    """Плоский список uint16: (ребёнок, матрица, цвет) * число элементов."""
    if not clip["n_el"]:
        return []
    return list(struct.unpack_from(f"<{3 * clip['n_el']}H", m["ds"], m["e_start"] + 2 * clip["offset"]))


# ================================================================ разбор в JSON/msgpack + PNG
#
# Формат результата - тот же, что в твоём data.msgpack (slop_guide.md): is_sc2, version, exports,
# matrix_banks, textures, shapes, movieclips, textfields, movieclip_modifiers. Для SC2 результат
# сверен с твоим data.msgpack (см. тесты ниже в истории чата), для SC1 - с примером SonicFaces.
# Лишние ключи, которых нет в твоём формате: у текстур width/height, у SC1 корневой блок "sc1".

JSON_LIMIT = 20 * 1024 * 1024
KTX_MAGIC = b"\xabKTX 11\xbb\r\n\x1a\n"


SC1_TEXTURE_TAGS = (1, 16, 19, 24, 27, 28, 29, 34)


ROOT_ORDER = ["is_sc2", "version", "use_external_textures", "use_external_texture", "use_multi_resolution",
              "use_low_resolution", "use_precision_matrix", "use_half_precision_matrices", "use_short_frames",
              "exports", "matrix_banks", "textures", "shapes", "movieclips", "textfields",
              "movieclip_modifiers", "sc1"]


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


SC1_TAGS_FOR_SC2 = {7, 15, 33, 30, 37, 38, 39, 40, 42}


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


def sniff_format(data):
    if data[:2] != b"SC":
        raise UserError("Это не .sc (нет заголовка SC)")
    return "sc2" if data[2:6] == b"\x05\x00\x00\x00" else "sc1"


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


# ================================================================ локализация (CSV из папки input)
#
# Формат таблиц: "TID","<ЯЗЫК>" и строка "string"; texts.csv - это EN. texts_patch.csv - таблица сразу на все языки,
# её строки перекрывают основные. Хаб читает только выбранный язык, чтобы не тратить память на 24 таблицы.

HUB_VERSION = "1.7.0"


# ================================================================ тексты: имена экземпляров в клипах
#
# Игра берёт текст надписи не из записи TextField (там text пустой), а из ИМЕНИ экземпляра: в мувиклипе у
# ребёнка, который ссылается на TextField, лежит имя - либо ключ локализации (TID_...), либо готовая строка,
# либо служебное имя (txt, spacer), по которому код находит поле. Поэтому текст правится здесь: у
# конкретного экземпляра в конкретном клипе. Запись TextField (размер, рамка) общая для всех экземпляров.

SC1_TEXT_TAGS = (7, 15, 20, 21, 25, 33, 43, 44)


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


DOC_SECTIONS = ("exports", "matrix_banks", "textures", "shapes", "movieclips", "textfields", "movieclip_modifiers")


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
