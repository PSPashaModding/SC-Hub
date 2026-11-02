"""
Creating new files from images: PNG/GIF -> .sc, animated menu backgrounds, loading screens, parts.
Создание новых файлов из картинок: PNG/GIF -> .sc, фоны меню, загрузочные экраны, части.
No terminal, no questions to the user.
"""

import math
import os
import re

from .sc_format import (
    FRAMERATE,
    HUB_VERSION,
    PAGE_MAX_DIM,
    PIVOTS,
    SC2_DOC_FLAGS,
    UserError,
    build_layout,
    build_sc_bytes,
    notify,
    parse_size,
    saved_msg,
)
from .sc_doc import doc_subset, encode_doc, load_source_doc
from .atlas import atlas_build_pages, atlas_plan, atlas_remap


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
        notify(f"  ⚠ PNG больше {PAGE_MAX_DIM}px, текстура ужата до {tex.size[0]}x{tex.size[1]} "
               f"(размер в игре не меняется)")
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


def gif_analyze(Image, path, progress=None):
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
            if progress is not None and ((i + 1) % 25 == 0 or i + 1 == n):
                progress(i + 1, n)
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


def loading_split(src, out_dir, report=None, min_size=None, roles=True, readme=None):
    """Разбирает loading-файл src в папку out_dir. Возвращает список созданных файлов."""
    import json
    import shutil
    import tempfile
    from PIL import Image
    report = report or notify
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
            report(f"  ⚠ Превью не получилось: {e}")
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


def doc_finalize(doc, tmp, out_path, astc=False, report=None):
    """Подрезает документ до достижимого, пересобирает атлас (до 4096), кодирует SC2 и пишет файл."""
    report = report or notify
    sub = doc_subset(doc, [e["name"] for e in doc["exports"]])
    before, after = doc_repack_atlas(sub, tmp)
    report(f"  Атлас: {before / 1e6:.1f} -> {after / 1e6:.1f} млн пикселей")
    main, _t, warns = encode_doc(sub, tmp, astc)
    with open(out_path, "wb") as fh:
        fh.write(main)
    return sub, warns


def loading_build(folder, out_path, o, report=None):
    """Собирает загрузочный экран из папки частей. o: bg_image, bg_mode, bg_scale, logo_all, logo_scale, intro
    ('keep' | 'replace' | 'transition'), intro_logo, intro_scale, intro_seconds, astc, preview. Возвращает пути."""
    import json
    import shutil
    import tempfile
    from PIL import Image
    report = report or notify
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
            report("  Заменено: " + ", ".join(changed))
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
            report(f"  Интро: {a} -> {b} кадров ({a / 60:.1f} -> {b / 60:.1f} с при 60 к/с)")
        sub, w2 = doc_finalize(doc, tmp, out_path, o.get("astc", False), report)
        results.append(out_path)
        report(saved_msg(out_path))
        for w_ in list(warns) + list(w2):
            report("  ⚠ " + w_)
        if o.get("preview", True):
            pdir = tempfile.mkdtemp(prefix="schub_loadp_")
            try:
                d2, _w, _k = load_source_doc(out_path, pdir)           # смотрим на готовый файл, а не на черновик
                img = loading_preview(d2, pdir, premult, shots_t)
                if img is not None:
                    pp = os.path.splitext(out_path)[0] + "_preview.png"
                    img.save(pp)
                    report(saved_msg(pp))
                    results.append(pp)
            except Exception as e:
                report(f"  ⚠ Превью не получилось: {e}")
            finally:
                shutil.rmtree(pdir, ignore_errors=True)
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PARTS_README = """Части .sc (разобрано хабом SC Hub)

  parts.json   - список частей и где они стоят (не трогай, по нему идёт сборка)
  template.sc  - весь исходный файл как шаблон
  *.png        - части картинками "как на экране", без поворотов атласа; прозрачность обычная (не предумноженная);
                 всё, что не входило в форму шейпа (соседи по атласу), убрано

Как менять: перерисуй нужные PNG (можно другого размера - вписывается в ту же рамку) и собери обратно в SC Hub,
пункт 21, режим 2. Заменяются только изменённые файлы. Части с анимацией по кадрам, кривые многоугольники и
шейпы из нескольких кусков в части не попадают и остаются как в шаблоне.
"""
