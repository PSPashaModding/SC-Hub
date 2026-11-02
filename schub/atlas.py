"""
Atlas rebuild: rectangle and outline (mask) packing, remapping UV, writing new pages.
Перестройка атласов: упаковка по прямоугольникам и по контурам, пересчёт UV, запись страниц.
No terminal, no questions to the user.
"""

import math
import os
import re
import struct

from .sc_format import (
    UserError,
    build_textures,
    fb_root,
    fb_slot,
    fb_slots,
    fb_table_vec,
    fb_vec,
    finalize_sc,
    load_model,
    notify,
    open_sc_bytes,
    saved_msg,
    sc1_build_shape,
    sc1_build_stream,
    sc1_container,
    sc1_load_model,
    sc1_require_writable,
    sc1_write,
)
from .textures import (
    read_ext_texture,
    sc1_build_ext_tex_raw,
    sc1_ext_refs,
    sc1_tex_infos,
    sc2_tex_infos,
    texture_image,
    write_ext_texture,
)


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


def atlas_plan(sizes, cmds, pad, limit, pot, merge=False, rotate=False, say=None, prefer_limit=True, polypack=False):
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
    plan = {"items": items, "comp_of": comp_of, "place": best[1]["place"], "sizes": best[1]["sizes"],
            "trials": len(combos), "best": f"{best[2]}, {best[3]}", "rotate": rotate, "merge": merge}
    if polypack:
        pp = poly_pack_plan(items, sizes, cmds, pad, limit, pot, rotate, prefer_limit, say)
        rect_m = (sum(w * h for w, h in plan["sizes"]), len(plan["sizes"]))
        if pp is not None and (sum(w * h for w, h in pp["sizes"]), len(pp["sizes"])) < rect_m:
            plan.update(place=pp["place"], sizes=pp["sizes"], best=pp["best"], poly=True)
            plan["trials"] += 3
        else:
            plan["poly_note"] = "по контурам плотнее не вышло, оставлена раскладка по прямоугольникам"
    return plan


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


# ---------------------------------------------------------------- упаковка по контурам (маскам), а не по прямоугольникам
#
# Каждый фрагмент - это маска (контур шейпа + отступ). Фрагменты кладутся "сверху вниз" на горизонт (skyline):
# для каждого столбца запоминается, до какой строки он уже занят; фрагмент ставится туда, где его верхний
# край профиля ложится на горизонт ниже всего, а скошенные края соседей входят друг в друга. Углы
# прямоугольников вокруг диагональных шейпов больше не пропадают зря. Нужны пиксели масок без пересечений:
# при сборке фрагменты вставляются через маску (atlas_build_pages с mask=True), поэтому рамки могут перекрываться.

_SKY_BIG = 1 << 30


def _poly_variants(Image, items, sizes, cmds, pad, rotate):
    np = _np()
    out = []
    for it in items:
        polys = [cmd_poly(cmds[ci], sizes[it["tex"]], (it["src"][0], it["src"][1])) for ci in it["cmds"]]
        m = np.asarray(draw_mask(Image, (it["w"], it["h"]), polys, max(1, pad))) > 0
        if not m.any():
            m = np.ones((it["h"], it["w"]), dtype=bool)
        vs = []
        for r in ((0, 1) if rotate else (0,)):
            a = np.ascontiguousarray(m.T if r else m)
            rows, cols = np.flatnonzero(a.any(axis=1)), np.flatnonzero(a.any(axis=0))
            y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
            t = a[y0:y1, x0:x1]
            ne = t.any(axis=0)
            top = np.where(ne, t.argmax(axis=0), _SKY_BIG).astype(np.int64)
            bot = np.where(ne, t.shape[0] - t[::-1].argmax(axis=0), -_SKY_BIG).astype(np.int64)
            vs.append({"r": r, "x0": x0, "y0": y0, "w": x1 - x0, "h": y1 - y0, "top": top, "bot": bot,
                       "ne": ne.astype(np.float64), "nne": int(ne.sum()),
                       "stop": float(top[ne].sum())})
        out.append({"v": vs, "area": int(m.sum())})
    return out


def _sky_best(sky, v, H):
    """Лучшее место для варианта v на горизонте sky: (счёт, x, y) или None."""
    np = _np()
    from numpy.lib.stride_tricks import sliding_window_view
    W, w = sky.shape[0], v["w"]
    if w > W or v["h"] > H:
        return None
    n = W - w + 1
    win = sliding_window_view(sky, w)
    y = np.empty(n, dtype=np.int64)
    for a in range(0, n, 512):
        y[a:a + 512] = (win[a:a + 512] - v["top"]).max(axis=1)
    np.maximum(y, 0, out=y)
    end = y + v["h"]
    ok = end <= H
    if not ok.any():
        return None
    waste = v["nne"] * y + v["stop"] - np.correlate(sky.astype(np.float64), v["ne"], "valid")
    score = np.where(ok, end * (1 << 36) + np.minimum(waste, (1 << 35) - 1).astype(np.int64), np.iinfo(np.int64).max)
    x = int(score.argmin())
    return int(score[x]), x, int(y[x])


def _sky_put(sky, v, x, y):
    np = _np()
    np.maximum(sky[x:x + v["w"]], y + v["bot"], out=sky[x:x + v["w"]])


def _sky_pack_ids(pv, ids, W, H):
    """Кладёт ids в один бин W x H. Возвращает ({i: (x, y, r)}, высота) или None."""
    np = _np()
    sky = np.zeros(W, dtype=np.int64)
    place = {}
    for i in ids:
        best = None
        for v in pv[i]["v"]:
            res = _sky_best(sky, v, H)
            if res is not None and (best is None or res[0] < best[0][0]):
                best = (res, v)
        if best is None:
            return None
        (_s, x, y), v = best
        _sky_put(sky, v, x, y)
        place[i] = (x - v["x0"], y - v["y0"], v["r"])
    return place, int(sky.max())


def _sky_bins(pv, order, limit):
    np = _np()
    skies, place = [], {}
    for i in order:
        done = False
        for bi, sky in enumerate(skies):
            if limit - int(sky.min()) < min(v["h"] for v in pv[i]["v"]):
                continue
            best = None
            for v in pv[i]["v"]:
                res = _sky_best(sky, v, limit)
                if res is not None and (best is None or res[0] < best[0][0]):
                    best = (res, v)
            if best is not None:
                (_s, x, y), v = best
                _sky_put(sky, v, x, y)
                place[i] = (bi, x - v["x0"], y - v["y0"], v["r"])
                done = True
                break
        if not done:
            sky = np.zeros(limit, dtype=np.int64)
            best = None
            for v in pv[i]["v"]:
                res = _sky_best(sky, v, limit)
                if res is not None and (best is None or res[0] < best[0][0]):
                    best = (res, v)
            if best is None:
                return None
            (_s, x, y), v = best
            _sky_put(sky, v, x, y)
            skies.append(sky)
            place[i] = (len(skies) - 1, x - v["x0"], y - v["y0"], v["r"])
    last = len(skies) - 1
    last_ids = [i for i in order if place[i][0] == last]
    return {"place": place, "nbins": len(skies), "last_ids": last_ids,
            "last_area": sum(pv[i]["area"] for i in last_ids)}


def _sky_shrink_last(pv, ids, limit, pot, prefer_limit):
    """Наименьший (по площади) размер последнего атласа. Возвращает (w, h, {i: (x, y, r)}) или None."""
    total = sum(pv[i]["area"] for i in ids)
    if pot:
        ws = [2 ** k for k in range(4, 15) if 2 ** k <= limit]
    elif prefer_limit and total >= limit * limit / 6:
        ws = [limit]
    else:
        lo = max(16, int(math.sqrt(total)))
        ws = sorted({min(limit, _round_up(int(lo * f), 4)) for f in (1.0, 1.15, 1.35, 1.6, 2.0, 2.6, 3.4)} | {limit})
    best = None
    for W in ws:
        res = _sky_pack_ids(pv, ids, W, limit)
        if res is None:
            continue
        pl, hh = res
        H = min(limit, 2 ** max(4, (max(hh, 1) - 1).bit_length())) if pot else min(limit, _round_up(hh, 4))
        if best is None or (W * H, abs(W - H)) < (best[0] * best[1], abs(best[0] - best[1])):
            best = (W, H, pl)
    return best


def poly_pack_plan(items, sizes, cmds, pad, limit, pot, rotate, prefer_limit, say=None):
    """Раскладка по контурам. Возвращает {'place', 'sizes', 'best'} или None (нет numpy/PIL или не вышло)."""
    if _np() is None:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    pv = _poly_variants(Image, items, sizes, cmds, pad, rotate)
    runs = []
    for name in ("area", "maxside", "height"):
        order = sorted(range(len(items)), key=lambda i: ATLAS_ORDERS[name](items[i], i))
        st = _sky_bins(pv, order, limit)
        if st is None:
            continue
        st["order"] = name
        runs.append(st)
        if say:
            say(f"    по контурам ({name}): бинов {st['nbins']}, в последнем {st['last_area'] / 1e6:.2f} МП")
    runs.sort(key=lambda s: (s["nbins"], s["last_area"]))
    best = None
    for st in runs[:2]:
        last = st["nbins"] - 1
        fit = _sky_shrink_last(pv, st["last_ids"], limit, pot, prefer_limit)
        if fit is None:
            continue
        w, h, pl = fit
        place = dict(st["place"])
        for i, (x, y, r) in pl.items():
            place[i] = (last, x, y, r)
        sizes_out = [(limit, limit)] * last + [(w, h)]
        metric = (sum(a * b for a, b in sizes_out), len(sizes_out))
        if best is None or metric < best[0]:
            best = (metric, {"place": place, "sizes": sizes_out, "best": f"по контурам, {st['order']}"})
    return best[1] if best else None


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
                  prefer_limit=True, polypack=False):
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
        plan_a = atlas_plan(old_sizes, cmds, pad, limit, pot, False, rotate, say, prefer_limit, polypack)
        plan_b = atlas_plan(old_sizes, cmds, pad, limit, pot, True, rotate, say, prefer_limit, polypack)
        area = lambda p: (sum(w * h for w, h in p["sizes"]), len(p["sizes"]))
        plan = plan_a if area(plan_a) <= area(plan_b) else plan_b
        plan["auto_note"] = (f"автовыбор: с нуля {area(plan_a)[0] / 1e6:.2f} МП, со слиянием перекрытий "
                             f"{area(plan_b)[0] / 1e6:.2f} МП -> взят режим «"
                             f"{'с нуля' if plan is plan_a else 'со слиянием'}»")
    else:
        plan = atlas_plan(old_sizes, cmds, pad, limit, pot, merge, rotate, say, prefer_limit, polypack)
    return {"sc": sc, "tex_cont": tex_cont, "kind": kind, "infos": infos, "cmds": cmds, "used": used,
            "old_imgs": old_imgs, "old_sizes": old_sizes, "plan": plan, "pad": pad, "limit": limit,
            "old_px": sum(w * h for w, h in old_sizes.values()),
            "new_px": sum(w * h for w, h in plan["sizes"]),
            "need_px": sum(it["w"] * it["h"] for it in plan["items"])}


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


def write_ext_outputs(tex_cont, out_path):
    """Записывает внешние текстуры (.sctx/.zktx), которые подготовила перестройка атласов, рядом с результатом."""
    items = (tex_cont or {}).get("ext_out") or []
    out = []
    for it in items:
        path = os.path.join(os.path.dirname(os.path.abspath(out_path)), it["name"])
        notify(f"  Кодирую {it['name']} ({it['w']}x{it['h']}, ASTC; на больших текстурах это минуты)...")
        same = write_ext_texture(path, it["rgba"], it["w"], it["h"], template=it["template"])
        notify(saved_msg(path))
        out.append(path)
        if same:
            notify("    файл не менялся (картинка та же), оставлен как был")
        elif it["w"] * it["h"] <= 4_000_000:
            try:
                import numpy as np
                back = np.frombuffer(read_ext_texture(path)["rgba"], np.uint8).astype(float)
                src_px = np.frombuffer(it["rgba"], np.uint8).astype(float)
                m = ((back - src_px) ** 2).mean()
                notify(f"    сжатие ASTC с потерями: PSNR ~{99 if m == 0 else 10 * np.log10(255 ** 2 / m):.1f} дБ")
            except Exception:
                pass
    if items:
        notify("  ⚠ Внешние текстуры закодированы встроенным энкодером ASTC: он проще astcenc, поэтому "
               "картинка чуть хуже (блоки, которые не менялись, взяты из оригинала без потерь). "
               "Как это выглядит в игре, хаб проверить не может.")
    return out
