"""StackCheck logo sting: rebuildable 32 s animated intro (1280x720, 30 fps, MP4).

Re-run:  python scripts/make_logo_sting.py
Output: temp/logo_sting.mp4 + three preview PNGs in temp/.
No Blender needed: frames are drawn with PIL/numpy, encoded with ffmpeg.
"""

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

P = {
    "width": 1280,
    "height": 720,
    "fps": 30,
    "duration_s": 32,
    "bg": (11, 27, 51),
    "teal": (45, 212, 168),
    "white": (245, 247, 250),
    "muted": (150, 170, 195),
    "title": "STACKCHECK",
    "tagline": "Checks the claims. Shows the evidence.",
    "out": Path("temp/logo_sting.mp4"),
    "ffmpeg": Path(
        r"C:\Users\rusha\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
    ),
}

WIN_FONTS = Path(r"C:\Windows\Fonts")


def load_font(bold: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arialbd.ttf", "arial.ttf") if bold else ("arial.ttf",):
        p = WIN_FONTS / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                pass
    return ImageFont.load_default()


def base_frame() -> Image.Image:
    w, h = P["width"], P["height"]
    bg = np.zeros((h, w, 3), dtype=np.float32)
    top = np.array([16, 38, 70], dtype=np.float32)
    bot = np.array([8, 20, 40], dtype=np.float32)
    ramp = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    bg = top[None, None, :] * (1 - ramp) + bot[None, None, :] * ramp
    img = Image.fromarray(bg.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, w, 80):
        d.line([(x, 0), (x, h)], fill=(120, 160, 210, 22), width=1)
    for y in range(0, h, 80):
        d.line([(0, y), (w, y)], fill=(120, 160, 210, 22), width=1)
    return img


def draw_badge(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, prog: float) -> None:
    teal = P["teal"]
    if prog <= 0:
        return
    extent = int(360 * min(prog * 1.4, 1.0))
    d.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=-90 + extent, fill=(*teal, 255), width=7)
    cp = min(max((prog - 0.25) / 0.75, 0.0), 1.0)
    if cp <= 0:
        return
    p0 = (cx - r * 0.45, cy + r * 0.02)
    p1 = (cx - r * 0.10, cy + r * 0.38)
    p2 = (cx + r * 0.52, cy - r * 0.34)
    if cp < 0.45:
        f = cp / 0.45
        end = (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
        d.line([p0, end], fill=(*teal, 255), width=10, joint="curve")
    else:
        f = (cp - 0.45) / 0.55
        end = (p1[0] + (p2[0] - p1[0]) * f, p1[1] + (p2[1] - p1[1]) * f)
        d.line([p0, p1, end], fill=(*teal, 255), width=10, joint="curve")


def draw_title(img: Image.Image, t: float, f_title: ImageFont.ImageFont) -> None:
    n = len(P["title"])
    start, span = 6.0, 6.0
    shown = int(n * min(max((t - start) / span, 0.0), 1.0) + 0.999)
    if shown <= 0:
        return
    d = ImageDraw.Draw(img, "RGBA")
    w = P["width"]
    widths = [d.textlength(ch, font=f_title) for ch in P["title"]]
    tracking = 8
    total = sum(widths) + tracking * (n - 1)
    x = (w - total) / 2
    y = 400
    local = min(max((t - start) / span, 0.0), 1.0) * n
    for i, ch in enumerate(P["title"][:shown]):
        frac = min(max(local - i, 0.0), 1.0)
        dy = int(26 * (1 - frac))
        alpha = int(255 * frac)
        d.text((x, y + dy), ch, font=f_title, fill=P["white"] + (alpha,))
        x += widths[i] + tracking


def draw_tagline(d: ImageDraw.ImageDraw, t: float, f_tag: ImageFont.ImageFont) -> None:
    start, span = 12.0, 8.0
    frac = min(max((t - start) / span, 0.0), 1.0)
    if frac <= 0:
        return
    chars = int(len(P["tagline"]) * frac)
    w = P["width"]
    tw = d.textlength(P["tagline"], font=f_tag)
    d.text(((w - tw) / 2, 510), P["tagline"][:chars], font=f_tag, fill=P["muted"] + (255,))


def draw_rule_dots(d: ImageDraw.ImageDraw, t: float) -> None:
    if t < 17.0:
        return
    fade = min((t - 17.0) / 1.5, 1.0)
    n = 8
    gap, r0 = 54, 9
    w = P["width"]
    x0 = (w - gap * (n - 1)) / 2
    y = 580
    for i in range(n):
        pulse = 0.5 + 0.5 * math.sin(t * 3.0 + i * 0.8)
        r = int(r0 + 3 * pulse)
        a = int(255 * fade)
        d.ellipse(
            [x0 + i * gap - r, y - r, x0 + i * gap + r, y + r],
            fill=P["teal"] + (a,),
            outline=P["white"] + (a,),
        )


def render_frame(i: int, statics: dict) -> Image.Image:
    t = i / P["fps"]
    img = statics["base"].copy()
    d = ImageDraw.Draw(img, "RGBA")
    w, h = P["width"], P["height"]
    if 2.0 <= t <= 10.0:  # scan sweep across the badge row
        f = (t - 2.0) / 8.0
        x = int(w * f)
        d.rectangle([x - 60, 0, x, h], fill=(45, 212, 168, 26))
    if 20.0 <= t <= 24.0:  # shine sweep over the title
        f = (t - 20.0) / 4.0
        x = int(w * (1.2 - 1.4 * (1 - f)) - 0.1 * w)
        d.polygon([(x, 0), (x + 90, 0), (x - 40, h), (x - 130, h)], fill=(255, 255, 255, 20))
    draw_badge(d, w // 2, 210, 84, min(max((t - 2.0) / 4.0, 0.0), 1.0))
    draw_title(img, t, statics["f_title"])
    draw_tagline(ImageDraw.Draw(img, "RGBA"), t, statics["f_tag"])
    d = ImageDraw.Draw(img, "RGBA")
    draw_rule_dots(d, t)
    fade_in = min(t / 1.0, 1.0)
    fade_out = min(max((P["duration_s"] - t) / 1.0, 0.0), 1.0)
    a = min(fade_in, fade_out)
    if a < 1.0:
        black = Image.new("RGB", (w, h), (0, 0, 0))
        img = Image.blend(black, img, a)
    return img


def main() -> None:
    out = P["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    n = P["duration_s"] * P["fps"]
    statics = {
        "base": base_frame(),
        "f_title": load_font(True, 92),
        "f_tag": load_font(False, 34),
    }
    # Static vignette baked into the base.
    w, h = P["width"], P["height"]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xx - w / 2) / (w / 2)
    dy = (yy - h / 2) / (h / 2)
    vig = 1.0 - 0.28 * np.clip(dx * dx + dy * dy - 0.35, 0, 1)
    statics["base"] = Image.fromarray(
        (np.asarray(statics["base"], dtype=np.float32) * vig[..., None]).astype(np.uint8)
    )
    cmd = [
        str(P["ffmpeg"]),
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-framerate",
        str(P["fps"]),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "20",
        "-preset",
        "medium",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    previews = {100: "temp/preview_03s.png", 450: "temp/preview_15s.png", 800: "temp/preview_27s.png"}
    for i in range(n):
        frame = render_frame(i, statics)
        if i in previews:
            frame.save(previews[i])
        proc.stdin.write(frame.convert("RGB").tobytes())
        if i % 120 == 0:
            print(f"frame {i}/{n}", flush=True)
    proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        raise SystemExit(f"ffmpeg failed with code {rc}")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
