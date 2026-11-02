"""
Pixels: ASTC decoder/encoder, KTX, .sctx/.zktx, atlases as images, linked texture packages.
Пиксели: ASTC (декодер и кодировщик), KTX, .sctx/.zktx, связанные текстурные пакеты.
No terminal, no questions to the user.
"""

import os
import struct
import subprocess
import sys

from .sc_format import (
    KTX_MAGIC,
    SC1_TEXTURE_TAGS,
    SEARCH_DIRS,
    UserError,
    ensure_numpy_optional,
    fb_root,
    fb_slot,
    fb_slots,
    fb_table_vec,
    fb_vec,
    notify,
    sc1_container,
    sc1_parse_stream,
    sc1_parse_stream_tags,
    u16,
    u32,
)


ASTC_BLOCKS = {}
for _i, _bd in enumerate([(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8),
                          (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12)]):
    ASTC_BLOCKS[0x93B0 + _i] = _bd          # COMPRESSED_RGBA_ASTC_*
    ASTC_BLOCKS[0x93D0 + _i] = _bd          # COMPRESSED_SRGB8_ALPHA8_ASTC_*


def ktx_info(payload):
    """(glInternalFormat, ширина, высота, смещение данных первого уровня, размер) или None."""
    if len(payload) < 68 or payload[:12] != KTX_MAGIC:
        return None
    (_end, _gt, _gts, _gf, gl_internal, _gb, pw, ph, _pd, _arr, _faces, _mips, kv) = \
        struct.unpack_from("<13I", payload, 12)
    at = 64 + kv
    size = struct.unpack_from("<I", payload, at)[0]
    return gl_internal, pw, ph, at + 4, size


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
        notify("  Ставлю texture2ddecoder для распаковки ASTC-текстур...")
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
        for d in SEARCH_DIRS:
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
    dirs = [os.path.dirname(os.path.abspath(p)) for p in (tex_path, main_path) if p] + list(SEARCH_DIRS)
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
                notify(f"  Кодирую текстуру {i} ({w}x{h}) в ASTC...")
                out.append({"kind": "ktx", "w": w, "h": h, "payload": png_to_ktx_astc(rgba.tobytes(), w, h),
                            "fmt": 8, "file": path, "flags": flags})
            else:
                out.append({"kind": "raw", "w": w, "h": h, "payload": rgba.tobytes(), "fmt": 0, "file": path,
                            "flags": flags})
    return out
