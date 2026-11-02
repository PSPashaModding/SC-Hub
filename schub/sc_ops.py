"""
Operations on existing files: inspect, rename, resize/move, FPS, TextFields, localization, merge,
delete exports, remove objects, optimize, diagnostics.
Операции над готовыми файлами: просмотр, переименование, размер/положение, FPS, тексты, локализация,
слияние, удаление, оптимизация, диагностика. No terminal, no questions to the user.
"""

import os
import re
import struct

from .sc_format import (
    CHUNK_NAMES,
    HUB_VERSION,
    SC1_TEXTURE_TAGS,
    SC1_TEXT_TAGS,
    SC2_DOC_FLAGS,
    SEARCH_DIRS,
    TF_REC,
    UserError,
    _align4,
    _append_string,
    _tf_string,
    choose_text_slot,
    clip_flat,
    f32,
    fb_build_clips,
    fb_build_export_names,
    fb_build_fd_like,
    fb_root,
    fb_slot,
    fb_slots,
    fb_str,
    fb_table_vec,
    fb_vec,
    fd_export_names,
    finalize_sc,
    fmt_size,
    get_exports,
    is_localization_key,
    load_model,
    open_sc,
    open_sc_bytes,
    parse_sc,
    read_textfields,
    sc1_build_clip,
    sc1_build_shape,
    sc1_build_stream,
    sc1_container,
    sc1_exports,
    sc1_footer,
    sc1_load_model,
    sc1_mat_float,
    sc1_mat_int,
    sc1_parse_stream,
    sc1_parse_stream_tags,
    sc1_require_writable,
    sc1_text,
    sc1_write,
    sniff_format,
    u16,
    u32,
    warn_line,
)
from .textures import (
    _zstd_all,
    find_ext_file,
    ktx_info,
    pick_tex_file,
    sc1_ext_refs,
    sc2_tex_infos,
    tex_sc_candidates,
)
from .sc_doc import doc_subset, encode_doc
from .atlas import atlas_prepare


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
        lines.append(warn_line("  Блоков не 7, как в файлах gifconv/png2sc - структура другая, "
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
        lines.append(warn_line(f"DataStorage: не разобрать по схеме gifconv ({e})"))
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
        lines.append(warn_line(f"ExportNames: не разобрать по схеме gifconv ({e})"))
    try:
        sh = chunks[3]
        t = fb_root(sh)
        p = fb_slot(sh, t, 0)
        n = fb_vec(sh, p)[1] if p is not None else 0
        lines.append(f"Shapes: {n}")
    except Exception as e:
        lines.append(warn_line(f"Shapes: не разобрать по схеме gifconv ({e})"))
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
        lines.append(warn_line(f"MovieClips: не разобрать по схеме gifconv ({e})"))
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
        lines.append(warn_line(f"Textures: не разобрать по схеме gifconv ({e})"))
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
        lines.append(warn_line(f"TextFields: не разобрать ({e})"))
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


def bank_matrices(m, bank):
    if bank >= len(m["banks"]):
        raise UserError(f"Клип ссылается на несуществующий банк матриц {bank}")
    bk = m["banks"][bank]
    return list(struct.iter_unpack("<6f", m["ds"][bk["start"]:bk["start"] + 24 * bk["count"]]))


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


def describe_params(kx, ky, dx, dy):
    scale = f"{kx:g}" if abs(kx - ky) < 1e-12 else f"{kx:g} по X, {ky:g} по Y"
    return f"размер ×{scale}, сдвиг {dx:+g} по X и {dy:+g} по Y"


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
        lines.append(warn_line(f"Теги, которых я не знаю (хаб сохраняет их как есть, но не разбирает): {odd}"))
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


def _csv_rows(path):
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        yield from csv.reader(fh)


def loc_scan(dirs=None):
    """Папки input (и input/localizations) -> {'ЯЗЫК': [(файл, номер колонки), ...]}; патчи идут последними."""
    dirs = dirs or [d for base in SEARCH_DIRS[:1] for d in (base, os.path.join(base, "localizations"))]
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


def merge_exports_info(doc, name):
    """(id, число кадров, fps, это клип?) объекта под ExportName'ом name."""
    ex = next((e for e in doc["exports"] if e["name"] == name), None)
    if ex is None:
        raise UserError(f"Нет экспорта «{name}»")
    clip = next((c for c in doc["movieclips"] if c["id"] == ex["id"]), None)
    if clip is None:
        return ex["id"], 1, None, False
    return ex["id"], max(1, len(clip["frames"])), clip["frame_rate"], True


def doc_merge_exports(doc, bottom, top, result_name=None, keep_top_name=False, dx=0.0, dy=0.0, scale=1.0):
    """Склеивает два экспорта в один: новый клип кладёт объект bottom под объект top (кто идёт позже в списке
    элементов кадра, тот рисуется выше). Оба старых объекта остаются как вложенные. Имя результата result_name
    (по умолчанию имя bottom), второе имя удаляется, если не keep_top_name. Верхнему можно задать сдвиг dx, dy
    (в единицах клипа) и масштаб. Возвращает (новый документ, сведения). Исходный документ не меняется."""
    import copy
    if bottom == top:
        raise UserError("Выбери два разных экспорта")
    id_b, n_b, fps_b, _cb = merge_exports_info(doc, bottom)
    id_t, n_t, fps_t, _ct = merge_exports_info(doc, top)
    if scale <= 0:
        raise UserError("Масштаб должен быть больше нуля")
    result_name = result_name or bottom
    if not result_name or any(ord(c) < 32 for c in result_name):
        raise UserError("Недопустимое имя результата")
    taken = {e["name"] for e in doc["exports"]} - {bottom, top}
    if result_name in taken:
        raise UserError(f"Имя «{result_name}» уже занято другим экспортом")
    if keep_top_name and result_name == top:
        raise UserError("Имя результата совпадает с именем верхнего экспорта, которое нужно оставить")
    new = copy.deepcopy(doc)
    used = {x["id"] for sec in ("shapes", "movieclips", "textfields", "movieclip_modifiers") for x in new[sec]}
    used |= {e["id"] for e in new["exports"]}
    cid = next((i for i in range(1, 65536) if i not in used), None)
    if cid is None:
        raise UserError("Не хватает id: в одном .sc их не может быть больше 65536")
    if not new["matrix_banks"]:
        new["matrix_banks"].append({"matrices": [], "color_transforms": []})
    mi = 0xFFFF
    if abs(dx) > 1e-9 or abs(dy) > 1e-9 or abs(scale - 1) > 1e-9:
        mats = new["matrix_banks"][0]["matrices"]
        mats.append({"a": float(scale), "b": 0.0, "c": 0.0, "d": float(scale),
                     "tx": round(float(dx) * 20) / 20, "ty": round(float(dy) * 20) / 20})
        mi = len(mats) - 1
    n = max(n_b, n_t)
    fps = (fps_b if n_b >= n_t else fps_t) or fps_b or fps_t or 30
    new["movieclips"].append({
        "id": cid, "frame_rate": fps, "bank_index": 0, "unknown_flag": False,
        "childrens": [{"id": id_b, "blend_mode": 0, "name": ""}, {"id": id_t, "blend_mode": 0, "name": ""}],
        "frames": [{"label": "", "elements_count": 2} for _ in range(n)],
        "frame_elements": [e for _ in range(n) for e in (
            {"instance_index": 0, "matrix_index": 0xFFFF, "colorTransform_index": 0xFFFF},
            {"instance_index": 1, "matrix_index": mi, "colorTransform_index": 0xFFFF})]})
    exports, placed = [], False
    for e in new["exports"]:
        if e["name"] == bottom:
            exports.append({"id": cid, "name": result_name})
            placed = True
        elif e["name"] == top and not keep_top_name:
            continue
        else:
            exports.append(e)
    if not placed:
        exports.append({"id": cid, "name": result_name})
    new["exports"] = exports
    return new, {"id": cid, "frames": n, "fps": fps, "result": result_name, "bottom": bottom, "top": top,
                 "offset": (dx, dy, scale)}


def verify_merge_exports(old, new, info, folder_old, folder_new):
    """Остальные экспорты выглядят как раньше, у нового клипа дети в нужном порядке."""
    th_o, th_n = tex_hashes(old, folder_old), tex_hashes(new, folder_new)
    skip = {info["bottom"], info["top"]}
    for e in old["exports"]:
        if e["name"] in skip:
            continue
        if doc_signature(old, e["name"], th_o) != doc_signature(new, e["name"], th_n):
            raise UserError(f"Проверка не прошла: изменился внешний вид экспорта «{e['name']}». Файл не записан.")
    clip = next((c for c in new["movieclips"] if c["id"] == info["id"]), None)
    ids_b = next(e["id"] for e in old["exports"] if e["name"] == info["bottom"])
    ids_t = next(e["id"] for e in old["exports"] if e["name"] == info["top"])
    if clip is None or [c["id"] for c in clip["childrens"]] != [ids_b, ids_t]:
        raise UserError("Проверка не прошла: у нового клипа не те дети или порядок слоёв нарушен. Файл не записан.")
    if sum(f["elements_count"] for f in clip["frames"]) != len(clip["frame_elements"]):
        raise UserError("Проверка не прошла: число элементов кадров не сходится. Файл не записан.")


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
            f"Размеры новых атласов: {desc}"] + ([f"Режим: {plan['auto_note']}"] if plan.get("auto_note") else []) + \
        ([f"Упаковка: {plan['poly_note']}"] if plan.get("poly_note") else [])


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
