"""
Document model: .sc <-> editable document (data.json/msgpack + PNG textures), SC1 -> SC2 conversion.
Модель документа: .sc <-> редактируемый документ (data.json/msgpack + PNG), конвертация SC1 -> SC2.
No terminal, no questions to the user.
"""

import os
import struct
import sys

from .sc_format import (
    DOC_SECTIONS,
    JSON_LIMIT,
    MODIFIERS_CHUNK,
    ROOT_ORDER,
    SC1_TAGS_FOR_SC2,
    SC1_TEXTURE_TAGS,
    SC2_DOC_FLAGS,
    TEXTFIELDS_CHUNK,
    UserError,
    build_textures,
    clip_flat,
    fb_build_clips,
    fb_build_ds,
    fb_build_export_names,
    fb_build_fd_from_slots,
    fb_build_shapes_full,
    fb_build_struct_vec_chunk,
    fb_root,
    fb_slot,
    fb_slots,
    fb_table_vec,
    fb_vec,
    get_exports,
    load_model,
    parse_sc,
    read_textfields,
    sc1_build_clip,
    sc1_build_shape,
    sc1_build_stream,
    sc1_container,
    sc1_footer,
    sc1_pack,
    sc1_parse_clip,
    sc1_parse_shape,
    sc1_parse_stream,
    sc1_parse_stream_tags,
    sc1_text,
    sc1_textfields,
    sniff_format,
    u16,
    u32,
    zstd_pack,
)
from .textures import ktx_info, load_doc_textures, load_sc1_package, save_textures


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
