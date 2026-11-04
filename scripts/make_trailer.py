"""StackCheck 36s epic-trailer build v2. Rebuildable: python scripts/make_trailer.py
Output: temp/stackcheck_trailer.mp4 (1280x720, 30fps, 36s, drum-driven score).
v2: real animated scenes (skyline dolly, tower build-up, money race, evidence
wall, explosion verdicts, montage), moving camera on EVERY frame, percussion
score. Original content only; no copyrighted assets.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

NAVY = (11, 27, 51)
TEAL = (45, 212, 168)
AMBER = (255, 176, 32)
WHITE = (245, 247, 250)
BLACK = (0, 0, 0)
RED = (255, 70, 60)
W, H = 1280, 720
CW, CHH, BAR = 1280, 536, 92
FPS, N_FRAMES = 30, 1080
HITS = (4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 35.0)
FFMPEG = Path(
    r"C:\Users\rusha\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
)
OUT = Path("temp/stackcheck_trailer.mp4")


def F(b: bool, s: int) -> ImageFont.FreeTypeFont:
    p = Path(r"C:\Windows\Fonts") / ("arialbd.ttf" if b else "arial.ttf")
    return ImageFont.truetype(str(p), s)


# ---------------- film FX ----------------
def grain(fr: np.ndarray, rng: np.random.Generator, amt: float) -> np.ndarray:
    if amt <= 0:
        return fr.copy()
    n = rng.normal(0.0, float(amt), size=(fr.shape[0], fr.shape[1], 1)).astype(np.float32)
    return np.clip(fr.astype(np.float32) + n, 0, 255).astype(np.uint8)


def shake(fr: np.ndarray, rng: np.random.Generator, mag: float) -> np.ndarray:
    m = int(mag)
    if m <= 0:
        return fr.copy()
    dx, dy = int(rng.integers(-m, m + 1)), int(rng.integers(-m, m + 1))
    h, w = fr.shape[:2]
    o = np.zeros_like(fr)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    xd0, xd1 = max(0, -dx), min(w, w - dx)
    ys0, ys1 = max(0, dy), min(h, h + dy)
    yd0, yd1 = max(0, -dy), min(h, h - dy)
    o[yd0:yd1, xd0:xd1] = fr[ys0:ys1, xs0:xs1]
    return o


def vignette_mask() -> np.ndarray:
    a = CW / CHH
    xs = np.linspace(-a, a, CW, dtype=np.float32)
    ys = np.linspace(-1.0, 1.0, CHH, dtype=np.float32)
    xx, yy = np.meshgrid(xs, ys)
    d = np.sqrt(xx * xx + yy * yy)
    d = (d / d.max()).astype(np.float32)
    return (1.0 - 0.35 * (d * d))[..., None].astype(np.float32)


_VIG = vignette_mask()


def grade(fr: np.ndarray, strength: float) -> np.ndarray:
    s = float(np.clip(strength, 0.0, 1.0))
    if s <= 0:
        return fr.copy()
    f = fr.astype(np.float32) / 255.0
    lum = f.mean(axis=2, keepdims=True)
    shadow = np.clip(1.0 - lum * 2.0, 0.0, 1.0) * s
    high = np.clip(lum * 2.0 - 1.0, 0.0, 1.0) * s
    teal = np.array([45.0, 212.0, 168.0], np.float32) / 255.0
    amber = np.array([255.0, 176.0, 32.0], np.float32) / 255.0
    f = f + shadow * (teal - 0.5) * 0.6 + high * (amber - 0.5) * 0.6
    m = f.mean(axis=2, keepdims=True)
    f = m + (f - m) * (1.0 + 0.35 * s)
    f = np.clip((f - 0.5) * (1.0 + 0.22 * s) + 0.5, 0.0, 1.0)
    return (f * 255.0 + 0.5).astype(np.uint8)


def shockwave(fr: np.ndarray, cx: int, cy: int, prog: float, color: tuple) -> np.ndarray:
    out = fr.copy()
    p = min(1.0, float(prog))
    if p <= 0:
        return out
    Hh, Ww = out.shape[:2]
    yy, xx = np.ogrid[:Hh, :Ww]
    d = np.sqrt((xx - float(cx)) ** 2 + (yy - float(cy)) ** 2)
    R = max(Hh, Ww) * 0.55
    acc = np.zeros((Hh, Ww), dtype=np.float32)
    for k, al in ((1.0, 0.9), (0.72, 0.5)):
        r = max(1.0, p * R * k)
        w = max(2.0, R * 0.022)
        acc += (np.exp(-((d - r) ** 2) / (2 * w * w)) * al).astype(np.float32)
    fade = min(1.0, p * 3.0) * (1.0 - p * 0.4)
    a = np.clip(acc * fade, 0, 1)[..., None].astype(np.float32)
    c = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)
    return np.clip(out.astype(np.float32) * (1 - a) + c * a, 0, 255).astype(np.uint8)


def flash_burst(fr: np.ndarray, prog: float, color: tuple = (255, 220, 160)) -> np.ndarray:
    p = min(1.0, max(0.0, float(prog)))
    if p <= 0:
        return fr.copy()
    Hh, Ww = fr.shape[:2]
    yy, xx = np.ogrid[:Hh, :Ww]
    d = np.sqrt((xx - Ww / 2) ** 2 + (yy - Hh / 2) ** 2) / (max(Hh, Ww) * 0.6)
    glow = np.clip(1 - d, 0, 1) ** 2 * (1 - p)
    a = glow[..., None].astype(np.float32)
    c = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)
    return np.clip(fr.astype(np.float32) * (1 - a) + c * a, 0, 255).astype(np.uint8)


def embers(fr: np.ndarray, rng: np.random.Generator, t: float, n: int) -> np.ndarray:
    out = fr.copy()
    n = int(n)
    if n <= 0:
        return out
    Hh, Ww = out.shape[:2]
    xs = (rng.random(n) * Ww).astype(int) % Ww
    y0 = rng.random(n) * Hh
    sp = 30.0 + rng.random(n) * 90.0
    ys = ((y0 - sp * float(t)) % Hh).astype(int)
    fl = 0.65 + 0.35 * np.sin(float(t) * 9.0 + np.arange(n) * 2.4)
    cols = np.where((np.arange(n) % 3 == 0)[:, None], np.array([255, 176, 32]), np.array([255, 240, 210]))
    cols = np.where((np.arange(n) % 5 == 0)[:, None], np.array([45, 212, 168]), cols)
    for x, y, c, f in zip(xs, ys, cols, fl, strict=False):
        b = float(np.clip(f, 0.3, 1.0))
        tgt = np.asarray(c, dtype=np.float32)
        patch = out[y : y + 2, x : x + 2].astype(np.float32)
        out[y : y + 2, x : x + 2] = np.clip(patch * (1 - 0.85 * b) + tgt * (0.85 * b), 0, 255).astype(
            np.uint8
        )
    return out


def glitch(fr: np.ndarray, rng: np.random.Generator, n: int) -> np.ndarray:
    Hh, _Ww, _ = fr.shape
    o = fr.copy()
    n = max(1, int(n))
    ys = rng.integers(0, Hh, size=n)
    hs = rng.integers(4, max(5, Hh // 12), size=n)
    dx = rng.integers(-6, 7, size=n)
    for y, h, s in zip(ys, hs, dx, strict=False):
        y2 = min(Hh, int(y) + int(h))
        s = int(s)
        if y2 <= y or s == 0:
            continue
        o[y:y2, :, 0] = np.roll(fr[y:y2, :, 0], s, axis=1)
        o[y:y2, :, 2] = np.roll(fr[y:y2, :, 2], -s, axis=1)
    return o


def cam(fr: np.ndarray, px: float, py: float, sc: float) -> np.ndarray:
    """Pan/zoom camera: crop a (px,py)-centered, sc-scaled window."""
    Hh, Ww, _ = fr.shape
    im = Image.fromarray(fr).resize((int(Ww * sc), int(Hh * sc)), Image.BILINEAR)
    cx, cy = int(im.width / 2 + px * sc), int(im.height / 2 + py * sc)
    x0, y0 = cx - Ww // 2, cy - Hh // 2
    return np.asarray(im.crop((x0, y0, x0 + Ww, y0 + Hh)), dtype=np.uint8)


# ---------------- text ----------------
def title_layer(
    text: str,
    size: int,
    color: tuple,
    cy_full: int,
    sub: str = "",
    sub_size: int = 0,
    sub_cy_full: int = 0,
    bar: bool = False,
) -> np.ndarray:
    """Transparent RGBA layer with centered title (+ optional subline and
    underline bar placed BELOW the text bbox, never through it)."""
    im = Image.new("RGBA", (CW, CHH), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cy = cy_full - BAR
    fnt = F(True, size)
    d.text(
        (CW / 2, cy),
        text,
        font=fnt,
        fill=(*color, 255),
        anchor="mm",
        stroke_width=2,
        stroke_fill=(0, 0, 0, 255),
    )
    bb = d.textbbox((CW / 2, cy), text, font=fnt, anchor="mm")
    if bar:
        d.rectangle([bb[0], bb[3] + 10, bb[2], bb[3] + 16], fill=(*AMBER, 255))
    if sub:
        d.text((CW / 2, sub_cy_full - BAR), sub, font=F(False, sub_size), fill=(*TEAL, 255), anchor="mm")
    return np.asarray(im)


def slam_on(
    base: np.ndarray, text: str, size: int, prog: float, color: tuple, bar: bool = True
) -> np.ndarray:
    p = min(max(float(prog), 0.0), 1.0)
    t = min(p / 0.35, 1.0)
    sc = 1 + 1.6 * (1 - t) * (1 - t)
    layer = title_layer(text, max(8, int(size * sc)), color, 330, bar=False)
    im = Image.fromarray(base.copy()).convert("RGBA")
    im.alpha_composite(Image.fromarray(layer, "RGBA"))
    o = np.asarray(im.convert("RGB"), dtype=np.uint8)
    if p > 0.35:
        m = 12 * (1 - (p - 0.35) / 0.65)
        o = _shift(o, int(m * float(np.sin(p * 90.0))), int(m * float(np.cos(p * 73.0))))
    if bar and p > 0.5:
        fnt = F(True, size)
        tmp = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        bb = tmp.textbbox((0, 0), text, font=fnt)
        bw = min(460, (bb[2] - bb[0]) + 40)
        im2 = Image.fromarray(o)
        d2 = ImageDraw.Draw(im2, "RGBA")
        by = 330 - BAR + (bb[3] - bb[1]) // 2 + 14
        d2.rectangle([640 - bw // 2, by, 640 + bw // 2, by + 6], fill=(*AMBER, 255))
        o = np.asarray(im2, dtype=np.uint8)
    return o


def _shift(fr: np.ndarray, dx: int, dy: int) -> np.ndarray:
    Hh, Ww, _ = fr.shape
    o = np.zeros_like(fr)
    x0, x1 = max(0, dx), min(Ww, Ww + dx)
    y0, y1 = max(0, dy), min(Hh, Hh + dy)
    sx0, sy0 = max(0, -dx), max(0, -dy)
    o[y0:y1, x0:x1] = fr[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
    return o


def paste(base: np.ndarray, layer: np.ndarray) -> np.ndarray:
    im = Image.fromarray(base.copy()).convert("RGBA")
    im.alpha_composite(Image.fromarray(layer, "RGBA"))
    return np.asarray(im.convert("RGB"), dtype=np.uint8)


# ---------------- procedural art ----------------
def night_sky() -> np.ndarray:
    yy = np.linspace(0, 1, CHH, dtype=np.float32)[:, None, None]
    top = np.array([4, 10, 26], np.float32)
    bot = np.array([14, 32, 60], np.float32)
    return (top[None, None, :] * (1 - yy) + bot[None, None, :] * yy).astype(np.uint8)


def skyline_mask(seed: int = 11) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (building mask, window-cell hash, cell size)."""
    rng = np.random.default_rng(seed)
    mask = np.zeros((CHH, CW), bool)
    x = 0
    while x < CW:
        bw = int(rng.integers(70, 170))
        bh = int(rng.integers(120, 340))
        mask[CHH - bh :, x : min(CW, x + bw)] = True
        x += bw + int(rng.integers(4, 26))
    cs = 16
    chh, cww = CHH // cs, CW // cs
    cell = np.fromfunction(lambda j, i: (i * 73 + j * 149 + seed * 31) % 9, (chh, cww))
    return mask, cell.astype(np.int32), cs


_SKY_MASK, _SKY_CELL, _SKY_CS = skyline_mask()

MOON = (235, 240, 250)


def draw_skyline(base: np.ndarray, frame: int, pan: float = 0.0) -> np.ndarray:
    out = base.copy()
    Hh, Ww = out.shape[:2]
    shift = int(pan) % Ww
    mask = np.roll(_SKY_MASK, shift, axis=1)
    out[mask] = (16, 30, 55)
    lit = ((_SKY_CELL + frame // 6) % 9) < 2
    lit_px = np.kron(lit, np.ones((_SKY_CS, _SKY_CS), bool))[:Hh, :Ww]
    lit_px = np.roll(lit_px, shift, axis=1)
    win = mask & lit_px
    out[win] = AMBER
    mx, my = (Ww - int(pan * 0.3)) % Ww, 90
    yy, xx = np.ogrid[:Hh, :Ww]
    glow = np.clip(1 - np.sqrt((xx - mx) ** 2 + (yy - my) ** 2) / 130, 0, 1) ** 2
    a = glow[..., None].astype(np.float32) * 0.9
    out = np.clip(out.astype(np.float32) * (1 - a) + np.array(MOON, np.float32) * a, 0, 255).astype(np.uint8)
    return out


def draw_tower(base: np.ndarray, n_floors: float, stall: bool = False) -> np.ndarray:
    out = base.copy()
    tw, fh = 220, 30
    x0, x1 = 640 - tw // 2, 640 + tw // 2
    y_base = CHH - 30
    full = int(n_floors)
    for fl in range(full):
        y1 = y_base - fl * fh
        y0 = y1 - fh + 3
        col = (24, 44, 78) if not (stall and fl >= 8) else (46, 30, 26)
        out[y0:y1, x0:x1] = col
        out[y0 : y0 + 2, x0:x1] = (60, 90, 140)
        out[y0 + 8 : y1 - 6, x0 + 12 : x1 - 12 : 26] = (
            (45, 212, 168) if not (stall and fl >= 8) else (255, 176, 32)
        )
    frac = n_floors - full
    if frac > 0 and full < 14:
        y1 = y_base - full * fh
        out[int(y1 - (fh - 3) * frac) : y1, x0:x1] = (45, 212, 168)
    if stall:
        out[y_base - 8 * fh - 40 : y_base - 8 * fh - 34, x0:x1] = RED
    else:
        by = y_base - full * fh - 14
        if 0 < by < CHH:
            out[by - 3 : by + 3, 630:650] = RED
    return out


def draw_wall(base: np.ndarray, t: float) -> np.ndarray:
    """Three evidence panels drifting at different speeds (parallax)."""
    out = base.copy()
    panels = [
        (0.35, 200, 130, 300, 200, "REPORT", TEAL),
        (0.6, 560, 100, 340, 240, "PHOTO", AMBER),
        (0.9, 950, 150, 280, 190, "MONEY", WHITE),
    ]
    for sp, x, y, w, h, tag, col in panels:
        xi = int((x + t * 40 * sp) % (CW + 400) - 200)
        out[y : y + h, xi : xi + w] = (20, 38, 66)
        out[y : y + 3, xi : xi + w] = col
        out[y + 10 : y + h - 30, xi + 10 : xi + w - 10] = (30, 55, 92)
        for ln in range(4):
            ly = y + 30 + ln * 34
            out[ly : ly + 8, xi + 24 : xi + w - 24] = (90, 120, 165)
        im = Image.fromarray(out)
        d = ImageDraw.Draw(im)
        d.text((xi + 14, y + h - 26), tag, font=F(True, 22), fill=col)
        out = np.asarray(im, dtype=np.uint8)
    return out


def draw_race(base: np.ndarray, prog: float, counter: float) -> np.ndarray:
    out = base.copy()
    im = Image.fromarray(out)
    d = ImageDraw.Draw(im)
    teal_len = int(900 * min(1.0, prog * 1.1))
    amber_len = int(600 * min(1.0, prog * 1.1))
    d.rectangle([140, 200, 140 + teal_len, 244], fill=TEAL)
    d.rectangle([140, 280, 140 + amber_len, 324], fill=AMBER)
    d.text((150, 150), f"{counter:.1f}%", font=F(True, 72), fill=WHITE, stroke_width=2, stroke_fill=(0, 0, 0))
    d.text((150, 350), "REPORT 12  vs  PHOTO 8", font=F(True, 40), fill=WHITE)
    return np.asarray(im, dtype=np.uint8)


# ---------------- shots ----------------
# (id, start, len, kind) kinds: sky, build, stall, race, wall, barrage, montage, logo
SHOTS = [
    ("S01", 0, 120, "sky"),
    ("S02", 120, 120, "build"),
    ("S03", 240, 120, "stall"),
    ("S04", 360, 120, "race"),
    ("S05", 480, 120, "wall"),
    ("S06", 600, 120, "barrage"),
    ("S07", 720, 120, "stall2"),
    ("S08", 840, 150, "montage"),
    ("S09", 990, 90, "logo"),
]
HIT_FRAMES = tuple(round(h * FPS) for h in HITS)
_LAYERS: dict[str, np.ndarray] = {}


def layer(sid: str) -> np.ndarray:
    if sid not in _LAYERS:
        if sid == "S01":
            _LAYERS[sid] = title_layer(
                "STACKCHECK", 110, WHITE, 300, "8 RULES. EVERY FINDING HAS EVIDENCE.", 30, 400
            )
        elif sid == "S05":
            _LAYERS[sid] = title_layer("THE CHECK", 72, WHITE, 300, "8 RULES", 30, 415)
        elif sid == "S09":
            _LAYERS[sid] = title_layer(
                "STACKCHECK", 110, WHITE, 300, "8 RULES. EVERY FINDING HAS EVIDENCE.", 30, 400
            )
        else:
            _LAYERS[sid] = title_layer("", 8, WHITE, -1000)
    return _LAYERS[sid]


def render_frame(i: int) -> np.ndarray:
    _sid, start, length, kind = next(s for s in SHOTS if s[1] <= i < s[1] + s[2])
    f = i - start
    p = f / max(1, length - 1)
    t = i / FPS
    rng = np.random.default_rng(1000 + i)
    base = (
        night_sky()
        if kind in ("sky", "build", "stall", "stall2", "logo")
        else np.full((CHH, CW, 3), NAVY, dtype=np.uint8)
    )
    base = np.clip(
        base.astype(np.float32) * (1.0 + 0.08 * np.linspace(-1, 1, CHH, dtype=np.float32)[:, None, None]),
        0,
        255,
    ).astype(np.uint8)
    slam_txt: str | None = None
    shake_mag = 0.0
    emb_n, gs = 24, 6

    if kind == "sky":
        base = draw_skyline(base, i, pan=f * 0.6)
        base = paste(base, layer("S01"))
        base = cam(base, -60 + f * 1.0, 0, 1.0 + f * 0.0012)
    elif kind == "build":
        base = draw_skyline(base, i, pan=72.0)
        base = draw_tower(base, 1 + p * 11.0)
        base = paste(base, title_layer("REPORT 12 FLOORS", 64, WHITE, 140, bar=True))
        base = cam(base, f * 0.4, -f * 0.25, 1.05 + p * 0.08)
        emb_n = 30
    elif kind == "stall":
        base = draw_skyline(base, i, pan=72.0 + f * 0.2)
        base = draw_tower(base, 8.0, stall=True)
        base = paste(base, title_layer("PHOTO 8 FLOORS", 64, WHITE, 140, bar=True))
        from functools import partial  # noqa: F401  (kept local, no effect)

        base = reticle(base, (430, 60, 850, 470), p)
        base = cam(base, 30 - f * 0.5, 0, 1.1 - p * 0.03)
        emb_n = 30
    elif kind == "race":
        base = draw_race(base, p, p * 62.0)
        base = cam(base, -40 + f * 0.5, 0, 1.02 + p * 0.05)
        emb_n, gs = 34, 7
    elif kind == "wall":
        base = draw_wall(base, t)
        base = paste(base, layer("S05"))
        base = cam(base, f * 0.7, 0, 1.03 + p * 0.06)
        if f < 12:
            slam_txt = "doesn't match"
        emb_n, gs = 34, 7
    elif kind == "barrage":
        q, r = divmod(f, 30)
        words = ["gap found", "can't verify", "doesn't match", "gap found"]
        base = draw_wall(base, t * 0.4 + q)
        slam_txt = words[q]
        base = cam(base, (q % 2 * 2 - 1) * f * 0.3, 0, 1.06)
        if r < 8:
            shake_mag = 14 - 2 * r
        emb_n, gs = 50, 8
    elif kind == "stall2":
        base = draw_skyline(base, i, pan=96.0 + f * 0.3)
        base = draw_tower(base, 8.0, stall=True)
        slam_txt = "can't verify"
        base = cam(base, 0, f * 0.2, 1.08 + p * 0.05)
        emb_n, gs = 50, 8
    elif kind == "montage":
        q = f // 10
        sub = q % 4
        if sub == 0:
            base = draw_skyline(night_sky(), i, pan=f * 1.2)
        elif sub == 1:
            base = draw_tower(night_sky(), 12.0)
        elif sub == 2:
            base = draw_wall(np.full((CHH, CW, 3), NAVY, dtype=np.uint8), t)
        else:
            base = draw_race(np.full((CHH, CW, 3), NAVY, dtype=np.uint8), 1.0, 62.0)
        if f >= 60:
            base = paste(base, title_layer("EVERY FINDING CARRIES ITS EVIDENCE", 56, WHITE, 300))
        else:
            base = paste(base, title_layer("EVIDENCE", 72, TEAL, 300))
        base = cam(base, (q % 2 * 2 - 1) * 20, 0, 1.12)
        emb_n, gs = 60, 8
    elif kind == "logo":
        base = draw_skyline(base, i, pan=132.0)
        base = paste(base, layer("S09"))
        sweep_x = int(CW * (1.3 - 1.6 * p))
        im = Image.fromarray(base)
        d = ImageDraw.Draw(im, "RGBA")
        d.polygon(
            [(sweep_x, 0), (sweep_x + 110, 0), (sweep_x - 30, CHH), (sweep_x - 140, CHH)],
            fill=(255, 255, 255, 26),
        )
        base = np.asarray(im, dtype=np.uint8)
        base = cam(base, 0, 0, 1.05 - p * 0.03)
        emb_n, gs = (30, 6) if f < length - 20 else (0, 4)

    if slam_txt is not None:
        if kind in ("barrage", "stall2"):
            prog = min(1.0, (f % 30 + 1) / 10.0) if kind == "barrage" else min(1.0, (f + 1) / 10.0)
        else:
            prog = min(1.0, (f + 1) / 10.0)
        col = AMBER if "verify" not in slam_txt or kind != "stall2" else WHITE
        if kind == "stall2" and f > 60:
            col = WHITE
        base = slam_on(base, slam_txt, 72 if kind != "wall" else 64, prog, col)
        if kind == "wall" and f < 6:
            shake_mag = 12 - 2 * f

    for hf in HIT_FRAMES:
        if hf <= i <= hf + 10 and start <= hf < start + length:
            q = (i - hf) / 10.0
            base = flash_burst(base, 1 - q)
            base = shockwave(base, 640, 268, q, TEAL if kind != "barrage" else AMBER)
            shake_mag = max(shake_mag, 10 * (1 - q))
    if shake_mag > 0:
        base = shake(base, rng, shake_mag)
    if emb_n:
        base = embers(base, rng, t, emb_n)
    base = np.clip(base.astype(np.float32) * _VIG, 0, 255).astype(np.uint8)
    base = grade(base, 0.7)
    base = grain(base, rng, gs)
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    frame[BAR : BAR + CHH] = base
    return frame


def reticle(fr: np.ndarray, box: tuple, prog: float) -> np.ndarray:
    out = fr.copy()
    _Hh, _Ww = out.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in box)
    th, L = 3, 60
    teal = np.array([45, 212, 168], dtype=np.uint8)
    for xa, xb, ya, yb in (
        (x0, x0 + L, y0, y0 + th),
        (x0, x0 + th, y0, y0 + L),
        (x1 - L, x1, y0, y0 + th),
        (x1 - th, x1, y0, y0 + L),
        (x0, x0 + L, y1 - th, y1),
        (x0, x0 + th, y1 - L, y1),
        (x1 - L, x1, y1 - th, y1),
        (x1 - th, x1, y1 - L, y1),
    ):
        out[ya:yb, xa:xb] = teal
    sy = int(y0 + min(1.0, max(0.0, float(prog))) * (y1 - y0))
    out[max(y0, sy - 1) : min(y1 + 1, sy + 2), x0 : x1 + 1] = np.array([245, 247, 250], dtype=np.uint8)
    return out
