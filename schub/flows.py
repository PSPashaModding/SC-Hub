"""
Dialogs: every menu item asks the user for parameters and calls the logic modules.
Диалоги: каждый пункт меню задаёт вопросы и вызывает модули логики; здесь же меню и main().
"""

import math
import os
import re
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

from .sc_format import (
    HARD_RAW_LIMIT,
    PIVOTS,
    PIVOT_ORDER,
    UserError,
    build_sc_bytes,
    clean_export_name,
    ensure_numpy_optional,
    get_exports,
    load_model,
    open_sc,
    open_sc_bytes,
    parse_size,
    read_textfields,
    sc1_container,
    sc1_load_model,
    sniff_format,
    unquote,
)
from .textures import find_ext_file, ktx_info, load_tex_cont, sc1_ext_refs, sc1_head_sizes, tex_sc_candidates
from .sc_doc import decode_file, doc_subset, encode_doc, load_doc_dir, load_source_doc
from .atlas import ATLAS_LIMIT, atlas_finish, write_ext_outputs
from .sc_ops import (
    EMPTY_MARK,
    add_exports,
    doc_merge_exports,
    merge_exports_info,
    verify_merge_exports,
    apply_text_table,
    apply_transform,
    atlas_clean_and_prepare,
    atlas_summary,
    build_dump,
    build_report,
    check_new_export_names,
    clip_bbox,
    clip_info_line,
    delete_exports,
    describe_params,
    doc_optimize,
    edit_textfields,
    edit_tf_names,
    export_text_table,
    find_clips_by_pattern,
    fmt_bbox,
    loc_load,
    loc_scan,
    parse_bounds,
    parse_id_list,
    parse_px,
    parse_rename_file,
    parse_scale,
    parse_transform_file,
    plan_delete,
    plan_rename,
    read_text_table,
    rename_exports,
    sc_files_in,
    set_fps,
    shape_points,
    summarize,
    textfield_text,
    textfield_text_slot,
    tf_instances,
    write_merged_sc,
)
from .sc_create import (
    BG_FILES,
    BG_REF_H,
    GIF_MAX_PAGES,
    GIF_MAX_SHAPES,
    GIF_MAX_TIMELINE,
    GIF_USER_MAX_FPS,
    PARTS_README,
    bg_build_doc,
    bg_extract_gif,
    bg_find_assets,
    bg_fit_image,
    bg_motion,
    bg_symbols,
    convert_png,
    gif_analyze,
    gif_build_page,
    gif_build_plan,
    gif_effective_delays,
    gif_estimate_raw,
    gif_frames_per_page,
    gif_iter_frames,
    gif_pages_needed,
    gif_pick_tile,
    gif_plan_timeline,
    gif_resample,
    gif_suggest_fps,
    loading_build,
    loading_split,
    premultiply_png,
    render_doc,
    resolve_scale,
)
from .i18n import I18N_EN, current_language, tr
from .ui import (
    APP_DIR,
    IN_DIR,
    LAST_INPUTS,
    OUT_DIR,
    ask,
    ask_yes_no,
    banner,
    bold,
    clear,
    cyan,
    enable_ansi,
    ensure_deps,
    ensure_dirs,
    file_dialog,
    gray,
    green,
    input,
    lang_detect,
    lang_set,
    line,
    pause,
    print,
    print_sc_package,
    red,
    resolve_input,
    resolve_out_dir,
    resolve_output,
    saved,
    show_line,
    yellow,
)


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


def gif_progress(i, n):
    print(f"\r  анализ кадров: {i}/{n}", end="", flush=True)
    if i == n:
        print()


def flow_gif():
    ensure_deps()
    from PIL import Image
    import flatbuffers
    import zstandard

    src = resolve_input("Входной GIF", "input.gif", [("GIF", "*.gif"), ("Все файлы", "*.*")])
    print(gray("  Читаю GIF..."))
    try:
        meta = gif_analyze(Image, src, gif_progress)
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


def flow_inspect():
    ensure_deps()
    src = resolve_input("Какой .sc разобрать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    line()
    print(f"  Формат: {'SC2 (flatbuffers)' if sc.get('kind') != 'sc1' else 'SC1 (теги)'}")
    print_sc_package(src)
    for l in summarize(sc):
        show_line("  " + l)
    stem = os.path.splitext(os.path.basename(src))[0]
    dump_path = resolve_output(ask("Куда сохранить подробный дамп", f"{stem}_dump.txt"), ".txt")
    with open(dump_path, "w", encoding="utf-8") as fh:
        fh.write(build_dump(src, sc))
    saved(dump_path)
    print(gray("  Дамп можно отправить мне вместо самого .sc - по нему видна вся структура."))
    return [dump_path]


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


def ask_transform_params(default_scale="1"):
    k = parse_scale(ask("Коэффициент размера (1 - не менять, 0.5 - вдвое меньше, 2 - вдвое больше)", default_scale))
    dx = parse_px(ask("Сдвиг по X в пикселях (вправо +, влево -)", "0"), "X")
    dy = parse_px(ask("Сдвиг по Y в пикселях (вниз +, вверх -)", "0"), "Y")
    if abs(k[0] - 1) < 1e-12 and abs(k[1] - 1) < 1e-12 and dx == 0 and dy == 0:
        raise UserError("Коэффициент 1 и сдвиг 0 - менять нечего")
    return k[0], k[1], dx, dy


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


def ask_astc():
    return ask_yes_no("Сжать текстуры в ASTC (файл станет в разы меньше, но с потерями качества, кодирование долгое)", False)


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


def flow_merge_exports():
    import shutil
    import tempfile
    ensure_deps()
    src = resolve_input("В каком .sc объединять экспорты", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    work = tempfile.mkdtemp(prefix="schub_mexp_")
    try:
        sub = os.path.join(work, "src")
        os.makedirs(sub)
        print(gray("  Разбираю файл..."))
        doc, warns, kind = load_source_doc(src, sub)
        for w in warns[:2]:
            print(yellow("  ⚠ " + w))
        names = [e["name"] for e in doc["exports"]]
        if len(names) < 2:
            raise UserError("В файле меньше двух экспортов - объединять нечего")
        print("  Экспорты в файле:")
        for i, n in enumerate(names, 1):
            _id, frames, fps, is_clip = merge_exports_info(doc, n)
            print(f"    {i:>3}  {n}" + gray(f"   ({frames} кадр., {fps} fps)" if is_clip else "   (шейп/текст)"))

        def pick_name(prompt, default):
            raw = ask(prompt, default).strip()
            if raw.isdigit() and 1 <= int(raw) <= len(names):
                return names[int(raw) - 1]
            if raw not in names:
                raise UserError(f"Нет экспорта «{raw}»")
            return raw
        a = pick_name("Первый экспорт (номер или имя)", "1")
        b = pick_name("Второй экспорт (номер или имя)", "2" if len(names) > 1 else "1")
        print("  Какой будет сверху:")
        print(f"    1 - «{b}» поверх «{a}»")
        print(f"    2 - «{a}» поверх «{b}»")
        order = ask("Порядок слоёв", "1").strip()
        if order not in ("1", "2"):
            raise UserError("Нужно 1 или 2")
        bottom, top = (a, b) if order == "1" else (b, a)
        result = ask("Имя нового экспорта", bottom).strip()
        keep_top = ask_yes_no(f"Оставить имя «{top}» отдельным экспортом (на прежний верхний объект)", False)
        dx = dy = 0.0
        scale = 1.0
        if ask_yes_no("Подвинуть или масштабировать верхний слой", False):
            try:
                dx = float(ask("Сдвиг верхнего по X, px (вправо +)", "0").replace(",", "."))
                dy = float(ask("Сдвиг верхнего по Y, px (вниз +)", "0").replace(",", "."))
                scale = float(ask("Масштаб верхнего (1 - не менять)", "1").replace(",", "."))
            except ValueError:
                raise UserError("Сдвиг и масштаб должны быть числами")
        new, info = doc_merge_exports(doc, bottom, top, result, keep_top, dx, dy, scale)
        verify_merge_exports(doc, new, info, sub, sub)
        line()
        print(f"  Снизу «{bottom}», сверху «{top}» → экспорт «{info['result']}» ({info['frames']} кадр., {info['fps']} fps)")
        out_path = resolve_output(ask("Выходной файл", os.path.splitext(os.path.basename(src))[0] + "_combined.sc"), ".sc")
        if os.path.abspath(out_path) == os.path.abspath(src):
            raise UserError("Выходной файл совпадает со входным - укажи другое имя")
        astc = ask_astc()
        entry = {"name": os.path.basename(src), "doc": new, "dir": sub, "kind": kind, "warns": []}
        merged, rep, w2 = write_merged_sc([entry], out_path, work, astc)
        saved(out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(green("  ✔ Проверка пройдена: ") + gray("остальные экспорты выглядят как раньше, слои в нужном порядке"))
    for w in w2:
        print(yellow("  ⚠ " + w))
    return [out_path]


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


def flow_atlas():
    ensure_deps()
    src = resolve_input("Какой .sc перестраивать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    sc = open_sc(src)
    kind = sc.get("kind")
    tex_cont, tex_path = None, None
    if kind == "sc1":
        tex_path, tex_cont = load_tex_cont(src)
    print("  Рекомендуемые настройки: атлас до " + str(ATLAS_LIMIT) + " px, отступ 2 px, режим «авто», упаковка по")
    print("  контурам фигур, выброс лишних пикселей и объектов без ExportName, размеры впритык.")
    limit, pad, merge, rotate, mask, gc, pot, poly = ATLAS_LIMIT, 2, "auto", False, True, True, False, True
    if not ask_yes_no("Использовать рекомендуемые настройки", True):
        try:
            limit = int(float(ask("Максимальный размер атласа в пикселях", str(ATLAS_LIMIT)).replace(",", ".")))
            pad = int(float(ask("Отступ вокруг каждого фрагмента, px", "2").replace(",", ".")))
        except ValueError:
            raise UserError("Размер и отступ должны быть числами")
        if not 64 <= limit <= 16384 or not 0 <= pad <= 64:
            raise UserError("Размер атласа должен быть от 64 до 16384, отступ от 0 до 64")
        print("  Режим: 1 - с нуля, каждый фрагмент отдельно; 2 - сливать перекрывающиеся фрагменты;")
        print("         3 - автоматически: посчитать оба и взять тот, где атласы меньше")
        merge = {"1": False, "2": True}.get(ask("Режим", "3"), "auto")
        rotate = ask_yes_no("Разрешить поворот фрагментов на 90° ради плотности", False)
        mask = ask_yes_no("Выбросить пиксели вне фигур шейпов (контур + отступ), в том числе остатки удалённого", True)
        poly = mask and ask_yes_no("Упаковывать по контурам фигур, а не по прямоугольникам (плотнее; нужна предыдущая "
                                   "опция)", True)
        gc = ask_yes_no("Выбросить объекты, до которых не дойти ни от одного ExportName (клипы без экспорта, на "
                        "которые никто не ссылается, их шейпы и куски атласа)", True)
        print("  Размеры атласов: 1 - впритык, одна сторона " + str(limit) + " если содержимого хватает; "
              "2 - степени двойки")
        pot = ask("Размеры", "1") == "2"
    print(gray("  Распаковываю текстуры и ищу лучшую раскладку (на больших файлах это до минуты)..."))
    t0 = time.time()
    st = atlas_clean_and_prepare(sc, tex_cont, gc, limit=limit, pad=pad, pot=pot, merge=merge, rotate=rotate,
                                 polypack=poly)
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
            st = atlas_clean_and_prepare(sc_new, tex_cont, True, limit=ATLAS_LIMIT, pad=2, pot=False, merge="auto", polypack=True)
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
    made, parts, warns = loading_split(src, out_dir)
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
    res = loading_build(folder, out, o)
    print(gray("  Проверено только по рендеру и разбору готового файла; в игре поведение не проверялось."))
    return res


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
        res = loading_build(folder, out, o)
        print(gray("  Результат всегда SC2 (даже если исходник был SC1). В игре не проверялось."))
        return res
    src = resolve_input("Какой .sc разобрать", "input.sc", [("SC", "*.sc"), ("Все файлы", "*.*")])
    out_dir = resolve_out_dir(ask("Папка для частей", "sc_parts"))
    ms = ask_float("Минимальный размер части (меньшие пропускаются: точки, блики)", 12)
    print(gray("  Разбираю..."))
    made, parts, warns = loading_split(src, out_dir, None, ms, False, PARTS_README)
    print(f"  Частей: {len(parts)}")
    for p_ in parts[:40]:
        print(f"    {p_['name']:<28}{p_['px'][0]} x {p_['px'][1]}  ({p_['export']})")
    if len(parts) > 40:
        print(gray(f"    ... и ещё {len(parts) - 40}"))
    for w in warns:
        print(yellow("  ⚠ " + w))
    print(green("  ✔ Части и шаблон: ") + os.path.abspath(out_dir))
    return made


# ================================================================ меню

I18N_EN.update({
    "Рекомендуемые настройки: атлас до ": "Recommended settings: atlas up to ",
    " px, отступ 2 px, режим «авто», упаковка по": " px, padding 2 px, mode \"auto\", packing by",
    "контурам фигур, выброс лишних пикселей и объектов без ExportName, размеры впритык.":
        "shape outlines, dropping unused pixels and objects without an ExportName, tight sizes.",
    "Использовать рекомендуемые настройки": "Use recommended settings",
    "Упаковывать по контурам фигур, а не по прямоугольникам (плотнее; нужна предыдущая ": "Pack by shape outlines instead of rectangles (denser; needs the previous ",
    "опция)": "option)",
    "по контурам плотнее не вышло, оставлена раскладка по прямоугольникам": "outline packing was not denser, rectangle layout kept",
    "Упаковка: ": "Packing: ",
    "по контурам, ": "by outlines, ",
    "    по контурам (": "    by outlines (",
    "): бинов ": "): bins ",
    ", в последнем ": ", last holds ",
})


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
    ("22", "Склеить два экспорта", "два пина в одном ExportName: кто сверху, сдвиг, масштаб", flow_merge_exports),
]


def show_menu():
    clear()
    banner()
    print()
    for key, title, desc, _fn in MENU:
        print(f"  {cyan('[' + key + ']')}{' ' * (4 - len(key))}{bold(tr(title).ljust(28))}{gray(tr(desc))}")
    print(f"  {cyan('[L]')}   {bold(tr('Язык / Language').ljust(28))}{gray('English' if current_language() == 'en' else 'Русский')}")
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
            lang_set("ru" if current_language() == "en" else "en")
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
