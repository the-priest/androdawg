#!/usr/bin/env python3
"""
THE DAWG // iconsmith  --  pro launcher icons in pure Python (no PIL, no Kivy)

Give it an app name, get back a set of crisp, modern, Play-Store-looking assets:

    icon.png            512x512 squircle launcher icon (Android-12 adaptive shape)
    icon_round.png      512x512 round variant (some launchers ask for it)
    icon_fg.png         adaptive foreground layer (transparent, safe-zone aware)
    icon_bg.png         adaptive background layer (the gradient, full-bleed)
    presplash.png       720x720 centred emblem on the brand colour (no white flash)

Everything is deterministic from the name -- the same app always gets the same identity,
two different apps never collide -- and it's written with nothing but struct + zlib, so it
runs anywhere Python does and never drags a build-time dependency into the APK.

Standalone:
    python3 iconsmith.py "Alarm Clock"            # -> ./icon.png, presplash.png, ...
    python3 iconsmith.py "Alarm Clock" out/ 512   # into out/, size 512

Importable (drop-in for apkforge's write_assets):
    import iconsmith
    iconsmith.write_assets(project_dir, "Alarm Clock")
"""
import os
import math
import zlib
import struct
import hashlib


# --------------------------------------------------------------------------- PNG writer
def _png_bytes(w, h, buf):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)                       # filter type 0 (none) per scanline
        raw.extend(buf[y * stride:(y + 1) * stride])
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# --------------------------------------------------------------------------- colour
def _hsl(h, s, l):
    import colorsys
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return (int(r * 255), int(g * 255), int(b * 255))


def palette(name):
    """A cohesive brand palette derived from the app name."""
    seed = int(hashlib.sha256((name or "app").encode()).hexdigest(), 16)
    hue = seed % 360
    top = _hsl(hue, 0.74, 0.62)                 # gradient top    (bright)
    bot = _hsl((hue + 34) % 360, 0.82, 0.44)    # gradient bottom (deep)
    fg = (255, 255, 255)                        # emblem: white reads on any brand hue
    splash_bg = _hsl(hue, 0.32, 0.11)           # near-black brand tint for presplash
    return {"hue": hue, "top": top, "bot": bot, "fg": fg, "splash_bg": splash_bg,
            "seed": seed}


# --------------------------------------------------------------------------- geometry
def _squircle_alpha(nx, ny, n=4.0, edge=0.045):
    """Superellipse (Android-12 squircle) coverage at normalised centre-coords in [-1,1].
    Returns 0..1 with a soft ~edge-wide antialiased rim."""
    d = (abs(nx) ** n + abs(ny) ** n) ** (1.0 / n)
    if d <= 1.0 - edge:
        return 1.0
    if d >= 1.0:
        return 0.0
    return (1.0 - d) / edge


def _circle_alpha(nx, ny, edge=0.02):
    d = math.hypot(nx, ny)
    if d <= 1.0 - edge:
        return 1.0
    if d >= 1.0:
        return 0.0
    return (1.0 - d) / edge


# --------------------------------------------------------------- stroke font (A-Z 0-9)
# Each glyph is a list of polylines on a 0..1 grid (x right, y DOWN). Drawn as thick,
# round-capped strokes -- a clean geometric sans that stays legible at 512px and at 48px.
_F = {
    "A": [[(.1, 1), (.5, 0), (.9, 1)], [(.25, .62), (.75, .62)]],
    "B": [[(.2, 0), (.2, 1)], [(.2, 0), (.62, 0), (.75, .16), (.62, .5), (.2, .5)],
          [(.2, .5), (.66, .5), (.8, .74), (.64, 1), (.2, 1)]],
    "C": [[(.85, .18), (.6, 0), (.3, 0), (.12, .28), (.12, .72), (.3, 1), (.6, 1), (.85, .82)]],
    "D": [[(.2, 0), (.2, 1)], [(.2, 0), (.55, 0), (.82, .3), (.82, .7), (.55, 1), (.2, 1)]],
    "E": [[(.8, 0), (.2, 0), (.2, 1), (.8, 1)], [(.2, .5), (.68, .5)]],
    "F": [[(.8, 0), (.2, 0), (.2, 1)], [(.2, .5), (.66, .5)]],
    "G": [[(.85, .18), (.6, 0), (.3, 0), (.12, .28), (.12, .72), (.3, 1), (.62, 1),
           (.85, .82), (.85, .55), (.55, .55)]],
    "H": [[(.2, 0), (.2, 1)], [(.8, 0), (.8, 1)], [(.2, .5), (.8, .5)]],
    "I": [[(.5, 0), (.5, 1)], [(.3, 0), (.7, 0)], [(.3, 1), (.7, 1)]],
    "J": [[(.75, 0), (.75, .72), (.55, 1), (.3, 1), (.15, .8)]],
    "K": [[(.2, 0), (.2, 1)], [(.78, 0), (.2, .52)], [(.36, .4), (.8, 1)]],
    "L": [[(.24, 0), (.24, 1), (.8, 1)]],
    "M": [[(.15, 1), (.15, 0), (.5, .55), (.85, 0), (.85, 1)]],
    "N": [[(.2, 1), (.2, 0), (.8, 1), (.8, 0)]],
    "O": [[(.5, 0), (.2, .22), (.12, .5), (.2, .78), (.5, 1), (.8, .78), (.88, .5),
           (.8, .22), (.5, 0)]],
    "P": [[(.2, 1), (.2, 0), (.62, 0), (.78, .2), (.62, .52), (.2, .52)]],
    "Q": [[(.5, 0), (.2, .22), (.12, .5), (.2, .78), (.5, 1), (.8, .78), (.88, .5),
           (.8, .22), (.5, 0)], [(.6, .72), (.9, 1)]],
    "R": [[(.2, 1), (.2, 0), (.62, 0), (.78, .2), (.62, .52), (.2, .52)], [(.5, .52), (.82, 1)]],
    "S": [[(.82, .16), (.55, 0), (.28, .06), (.2, .28), (.4, .48), (.66, .56), (.8, .74),
           (.68, .96), (.36, 1), (.16, .84)]],
    "T": [[(.1, 0), (.9, 0)], [(.5, 0), (.5, 1)]],
    "U": [[(.2, 0), (.2, .72), (.4, 1), (.6, 1), (.8, .72), (.8, 0)]],
    "V": [[(.12, 0), (.5, 1), (.88, 0)]],
    "W": [[(.1, 0), (.28, 1), (.5, .4), (.72, 1), (.9, 0)]],
    "X": [[(.16, 0), (.84, 1)], [(.84, 0), (.16, 1)]],
    "Y": [[(.14, 0), (.5, .5), (.86, 0)], [(.5, .5), (.5, 1)]],
    "Z": [[(.16, 0), (.84, 0), (.16, 1), (.84, 1)]],
    "0": [[(.5, 0), (.22, .22), (.16, .5), (.22, .78), (.5, 1), (.78, .78), (.84, .5),
           (.78, .22), (.5, 0)], [(.32, .74), (.68, .26)]],
    "1": [[(.32, .22), (.52, 0), (.52, 1)], [(.32, 1), (.72, 1)]],
    "2": [[(.18, .22), (.42, 0), (.68, .08), (.74, .34), (.2, 1), (.82, 1)]],
    "3": [[(.2, .12), (.5, 0), (.76, .18), (.5, .48), (.78, .74), (.52, 1), (.2, .88)]],
    "4": [[(.66, 1), (.66, 0), (.16, .66), (.86, .66)]],
    "5": [[(.78, 0), (.28, 0), (.24, .46), (.5, .4), (.78, .6), (.7, .94), (.36, 1), (.16, .86)]],
    "6": [[(.74, .1), (.46, 0), (.22, .3), (.18, .72), (.4, 1), (.64, 1), (.82, .74),
           (.66, .5), (.36, .5), (.2, .66)]],
    "7": [[(.16, 0), (.84, 0), (.44, 1)]],
    "8": [[(.5, .48), (.26, .3), (.34, .06), (.66, .06), (.74, .3), (.5, .48), (.28, .68),
           (.36, .96), (.64, .96), (.72, .68), (.5, .48)]],
    "9": [[(.8, .28), (.6, .5), (.36, .5), (.18, .26), (.34, 0), (.6, 0), (.8, .28),
           (.78, .7), (.54, 1), (.26, .9)]],
    "&": [[(.82, 1), (.3, .32), (.36, .08), (.6, .08), (.62, .34), (.2, .64), (.24, .92),
           (.5, 1), (.74, .74)]],
    "?": [[(.2, .24), (.42, 0), (.68, .08), (.72, .36), (.5, .54), (.5, .72)], [(.5, .92), (.5, 1)]],
    "#": [[(.34, .05), (.24, .95)], [(.66, .05), (.56, .95)], [(.14, .36), (.82, .36)],
          [(.1, .64), (.78, .64)]],
    "+": [[(.5, .18), (.5, .82)], [(.2, .5), (.8, .5)]],
}


def _initials(name):
    """1-2 characters to render: initials of the first two words, else first two letters."""
    words = [w for w in ''.join(c if (c.isalnum() or c == ' ') else ' '
                                 for c in (name or '')).split() if w]
    if not words:
        return "A"
    if len(words) >= 2:
        s = (words[0][0] + words[1][0])
    else:
        s = words[0][:2]
    s = s.upper()
    return ''.join(c for c in s if c in _F) or (words[0][0].upper() if words[0][0].upper() in _F else "A")


# --------------------------------------------------------------------------- rasteriser
def _blend(buf, W, H, x, y, col, cov, mask=None):
    """Correct straight-alpha 'source over' compositing.

    The naive version (lerp RGB toward the source, then raise alpha) is wrong on a
    TRANSPARENT canvas: the destination RGB there is 0,0,0, so every antialiased edge
    pixel gets pulled toward black and the result is a grey halo around white artwork.
    Doing real source-over means an edge pixel over emptiness keeps the source colour and
    only its alpha varies -- which is what a compositor expects.

    `mask` (optional, one byte per pixel) clips drawing to the icon silhouette so the
    emblem and its shadow can never spill outside the squircle into the transparent
    corners."""
    if x < 0 or y < 0 or x >= W or y >= H or cov <= 0:
        return
    p = y * W + x
    sa = cov if cov < 1.0 else 1.0
    if mask is not None:
        m = mask[p]
        if not m:
            return
        if m < 255:
            sa *= m / 255.0
            if sa <= 0:
                return
    i = p * 4
    da = buf[i + 3] / 255.0
    oa = sa + da * (1.0 - sa)
    if oa <= 0:
        return
    inv = da * (1.0 - sa)
    buf[i] = int((col[0] * sa + buf[i] * inv) / oa + 0.5)
    buf[i + 1] = int((col[1] * sa + buf[i + 1] * inv) / oa + 0.5)
    buf[i + 2] = int((col[2] * sa + buf[i + 2] * inv) / oa + 0.5)
    buf[i + 3] = int(oa * 255 + 0.5)


def _stroke_seg(buf, W, H, x0, y0, x1, y1, r, col, mask=None, alpha=1.0):
    """Anti-aliased round-capped capsule from (x0,y0) to (x1,y1) of radius r.
    Coverage is computed analytically, so this needs no supersampling to look smooth."""
    minx = max(0, int(min(x0, x1) - r - 1)); maxx = min(W, int(max(x0, x1) + r + 2))
    miny = max(0, int(min(y0, y1) - r - 1)); maxy = min(H, int(max(y0, y1) + r + 2))
    dx = x1 - x0; dy = y1 - y0
    ll = dx * dx + dy * dy or 1.0
    aa = 1.2
    r_in = r - aa
    hyp = math.hypot
    for y in range(miny, maxy):
        for x in range(minx, maxx):
            t = ((x - x0) * dx + (y - y0) * dy) / ll
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            d = hyp(x - (x0 + t * dx), y - (y0 + t * dy))
            if d <= r_in:
                cov = 1.0
            elif d >= r:
                continue
            else:
                cov = (r - d) / aa
            _blend(buf, W, H, x, y, col, cov * alpha, mask)


def _draw_polylines(buf, W, H, polys, box, r, col, mask=None, alpha=1.0):
    """Draw normalized (0..1) polylines mapped into box=(x,y,w,h)."""
    bx, by, bw, bh = box
    for poly in polys:
        pts = [(bx + p[0] * bw, by + p[1] * bh) for p in poly]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            _stroke_seg(buf, W, H, x0, y0, x1, y1, r, col, mask, alpha)


# --------------------------------------------------------------------------- composition
def _emblem_layout(text, cx, cy, R, dy_bias=0.02):
    """Geometry for the monogram: (list of per-glyph boxes, stroke radius)."""
    two = len(text) == 2
    gw = 0.72 if two else 0.42
    gh = 0.44
    bx = cx - gw * R
    by = cy - gh * R + R * dy_bias
    bw = gw * 2 * R
    bh = gh * 2 * R
    stroke = max(1.4, R * (0.072 if two else 0.085))
    if two:
        half = bw / 2.0
        boxes = [(text[i], (bx + i * half + half * 0.08, by, half * 0.84, bh))
                 for i in range(2)]
    else:
        boxes = [(text[0], (bx, by, bw, bh))]
    return boxes, stroke


def _draw_emblem(buf, W, H, boxes, stroke, fg, shadow_dy, mask=None, shadow=True):
    """Soft drop shadow, then the crisp emblem on top. Exactly two passes -- an earlier
    version also ran a stray black pass at zero offset, which left every two-letter icon
    with a heavy black outline around its initials.

    The shadow is translucent and offset, at the SAME stroke radius as the glyph. Drawing
    it opaque and slightly fatter (the old `stroke * 1.06`) ringed every letter in hard
    black -- that reads as a crude outline, not depth.

    shadow=False for the adaptive FOREGROUND layer: the launcher applies its own elevation
    shadow, and baking a black one into a transparent layer just leaves a dark halo."""
    if shadow:
        for ch, (bx, by, bw, bh) in boxes:
            _draw_polylines(buf, W, H, _F.get(ch, _F["A"]),
                            (bx, by + shadow_dy, bw, bh), stroke, (0, 0, 0), mask,
                            alpha=0.26)
    for ch, (bx, by, bw, bh) in boxes:
        _draw_polylines(buf, W, H, _F.get(ch, _F["A"]),
                        (bx, by, bw, bh), stroke, fg, mask)


def _base_layer(name, size, ss, shape, transparent_outside):
    """Render the gradient + monogram at size*ss. Returns (buf, W, H).

    Shape coverage and every stroke are anti-aliased ANALYTICALLY, so this looks smooth
    without brute-force supersampling -- which is why ss defaults to 1 now. The hot loop
    also precomputes the shape and gradient terms per row/column instead of recomputing
    pow()/hypot() a million times per icon; the old version took ~3s for one 512px icon."""
    W = H = size * ss
    pal = palette(name)
    top, bot, fg = pal["top"], pal["bot"], pal["fg"]
    buf = bytearray(W * H * 4)
    mask = bytearray(W * H)
    cx = cy = (W - 1) / 2.0
    R = W / 2.0

    # shape: |nx|^n + |ny|^n <= 1  (n=4 squircle / Android-12 shape, n=2 circle)
    n = 2.0 if shape == "round" else 4.0
    edge = 0.02 if shape == "round" else 0.045
    inner_pow = (1.0 - edge) ** n
    axs = [abs((x - cx) / R) ** n for x in range(W)]
    ays = [abs((y - cy) / R) ** n for y in range(H)]
    inv_n = 1.0 / n

    # gradient runs along the main diagonal, so it only depends on (x + y): one LUT.
    grad = []
    for s in range(W + H - 1):
        t = 0.5 + ((s - (cx + cy)) / R) * 0.42
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        grad.append((int(top[0] * (1 - t) + bot[0] * t),
                     int(top[1] * (1 - t) + bot[1] * t),
                     int(top[2] * (1 - t) + bot[2] * t)))

    # the sheen is only non-zero inside a small disc; bound it instead of testing globally
    sh_cx = cx - 0.4 * R
    sh_cy = cy - 0.5 * R
    sh_r = 0.5 * R
    sh_x0 = max(0, int(sh_cx - sh_r)); sh_x1 = min(W, int(sh_cx + sh_r) + 1)
    sh_y0 = max(0, int(sh_cy - sh_r)); sh_y1 = min(H, int(sh_cy + sh_r) + 1)
    hyp = math.hypot

    for y in range(H):
        ay = ays[y]
        row = y * W
        in_sheen_row = sh_y0 <= y < sh_y1
        for x in range(W):
            d = axs[x] + ay
            if d >= 1.0:
                if transparent_outside:
                    continue                      # leave it fully transparent
                a = 1.0
            elif d <= inner_pow:
                a = 1.0
            else:
                a = (1.0 - d ** inv_n) / edge
                if a <= 0.0:
                    if transparent_outside:
                        continue
                    a = 0.0
                elif a > 1.0:
                    a = 1.0
            r, g, b = grad[x + y]
            if in_sheen_row and sh_x0 <= x < sh_x1:
                s = 0.5 * R - hyp(x - sh_cx, y - sh_cy)
                if s > 0:
                    lift = int(31.875 * s / R)    # 255 * (s/R) * 0.5 * 0.25
                    r += lift; g += lift; b += lift
                    if r > 255: r = 255
                    if g > 255: g = 255
                    if b > 255: b = 255
            p = row + x
            i = p * 4
            buf[i] = r; buf[i + 1] = g; buf[i + 2] = b
            av = int(255 * a) if transparent_outside else 255
            buf[i + 3] = av
            mask[p] = av

    text = _initials(name)
    boxes, stroke = _emblem_layout(text, cx, cy, R)
    _draw_emblem(buf, W, H, boxes, stroke, fg, R * 0.02,
                 mask if transparent_outside else None)
    return buf, W, H


def _downsample(buf, W, H, size, ss):
    if ss == 1:
        return buf                      # analytic AA already: nothing to average
    out = bytearray(size * size * 4)
    n = ss * ss
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    si = ((y * ss + sy) * W + (x * ss + sx)) * 4
                    r += buf[si]; g += buf[si + 1]; b += buf[si + 2]; a += buf[si + 3]
            o = (y * size + x) * 4
            out[o] = r // n; out[o + 1] = g // n; out[o + 2] = b // n; out[o + 3] = a // n
    return out


def icon_png(name, size=512, shape="squircle", ss=1):
    buf, W, H = _base_layer(name, size, ss, shape, transparent_outside=True)
    return _png_bytes(size, size, _downsample(buf, W, H, size, ss))


def icon_round_png(name, size=512, ss=1):
    return icon_png(name, size, shape="round", ss=ss)


def presplash_png(name, size=720, ss=1):
    """Emblem centred on the brand background -- full-bleed, no rounded mask."""
    pal = palette(name)
    W = H = size * ss
    bg = pal["splash_bg"]
    # solid fill via one bytes multiply instead of a per-pixel Python loop
    buf = bytearray(bytes((bg[0], bg[1], bg[2], 255)) * (W * H))
    cx = cy = (W - 1) / 2.0
    R = W / 2.0
    text = _initials(name)
    # the splash emblem sits smaller than the icon's: scale the shared layout down
    boxes, stroke = _emblem_layout(text, cx, cy, R * 0.47, dy_bias=0.0)
    _draw_emblem(buf, W, H, boxes, stroke, pal["fg"], R * 0.012)
    return _png_bytes(size, size, _downsample(buf, W, H, size, ss))


def presplash_hex(name):
    bg = palette(name)["splash_bg"]
    return "#%02x%02x%02x" % bg


def adaptive_bg_png(name, size=512, ss=1):
    """Full-bleed gradient, no emblem -- the adaptive-icon background layer."""
    pal = palette(name)
    W = H = size * ss
    top, bot = pal["top"], pal["bot"]
    buf = bytearray(W * H * 4)
    cx = cy = (W - 1) / 2.0
    R = W / 2.0
    # the diagonal gradient depends only on (x + y): build one row LUT, then fill rows
    grad = []
    for s in range(W + H - 1):
        t = 0.5 + ((s - (cx + cy)) / R) * 0.42
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        grad.append(bytes((int(top[0] * (1 - t) + bot[0] * t),
                           int(top[1] * (1 - t) + bot[1] * t),
                           int(top[2] * (1 - t) + bot[2] * t), 255)))
    for y in range(H):
        row = b"".join(grad[y:y + W])
        off = y * W * 4
        buf[off:off + W * 4] = row
    return _png_bytes(size, size, _downsample(buf, W, H, size, ss))


def adaptive_fg_png(name, size=512, ss=1):
    """Transparent layer carrying only the emblem, sized into the adaptive safe zone."""
    W = H = size * ss
    buf = bytearray(W * H * 4)
    cx = cy = (W - 1) / 2.0
    R = W / 2.0 * 0.66            # keep the emblem inside the 66% adaptive safe zone
    pal = palette(name)
    boxes, stroke = _emblem_layout(_initials(name), cx, cy, R, dy_bias=0.0)
    _draw_emblem(buf, W, H, boxes, stroke, pal["fg"], R * 0.02, shadow=False)
    return _png_bytes(size, size, _downsample(buf, W, H, size, ss))


# --------------------------------------------------------------- apkforge-compatible API
def write_assets(project_dir, name, size=512, full_set=False):
    """Generate icon.png + presplash.png (drop-in for apkforge.write_assets).
    Returns (icon_ok, splash_ok). With full_set=True also writes the round + adaptive
    layers, which the downloadable project can wire into buildozer.spec if wanted."""
    icon_ok = splash_ok = False
    try:
        with open(os.path.join(project_dir, "icon.png"), "wb") as f:
            f.write(icon_png(name, size))
        icon_ok = True
    except Exception:
        pass
    try:
        with open(os.path.join(project_dir, "presplash.png"), "wb") as f:
            f.write(presplash_png(name, max(size, 720)))
        splash_ok = True
    except Exception:
        pass
    if full_set:
        for fn, data in (("icon_round.png", lambda: icon_round_png(name, size)),
                         ("icon_fg.png",    lambda: adaptive_fg_png(name, size)),
                         ("icon_bg.png",    lambda: adaptive_bg_png(name, size))):
            try:
                with open(os.path.join(project_dir, fn), "wb") as f:
                    f.write(data())
            except Exception:
                pass
    return icon_ok, splash_ok


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "The Dawg"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "."
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 512
    os.makedirs(outdir, exist_ok=True)
    ok_i, ok_s = write_assets(outdir, name, size=size, full_set=True)
    print("iconsmith: '%s' (%dx%d)" % (name, size, size))
    print("  brand colour:", presplash_hex(name), " initials:", _initials(name))
    print("  icon.png       ", "ok" if ok_i else "FAILED")
    print("  presplash.png  ", "ok" if ok_s else "FAILED")
    print("  + icon_round.png, icon_fg.png, icon_bg.png -> " + os.path.abspath(outdir))
