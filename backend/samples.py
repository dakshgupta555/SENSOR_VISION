"""Generates the synthetic package images used by simulation mode.

These are rendered drawings, not photographs. They exist so the OCR → reference lookup path can
be demonstrated without a camera; the UI and reports label them as synthetic.
"""
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config, database

PIN_COUNT = {"DIP-14": 14, "DIP-8": 8, "DIP-28": 28}


def _font(size, bold=True, mono=False):
    name = "DejaVuSansMono.ttf" if mono else ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    return ImageFont.truetype(os.path.join(config.FONT_DIR, name), size)


def render_chip(marking, package="DIP-14", variant="genuine", quality="good", photo=True, seed=1):
    rng = random.Random(seed)
    pins = PIN_COUNT.get(package, 14)
    per_side = pins // 2
    body_w = max(560, per_side * 100 + 80)
    W, H = body_w + 200, 560
    img = Image.new("RGB", (W, H), (214, 219, 226))
    d = ImageDraw.Draw(img)
    # bench gradient
    for y in range(H):
        c = int(222 - 18 * y / H)
        d.line([(0, y), (W, y)], fill=(c, c + 3, c + 8))
    bx0, by0, bx1, by1 = 100, 140, 100 + body_w, 420
    pitch = (body_w - 80) / max(per_side - 1, 1)
    for k in range(per_side):
        x = bx0 + 40 + k * pitch
        for (y0, y1) in ((by0 - 70, by0 + 4), (by1 - 4, by1 + 70)):
            d.rectangle([x - 16, y0, x + 16, y1], fill=(176, 180, 186), outline=(130, 134, 140))
            d.rectangle([x - 8, y0 + 6, x + 8, y1 - 6], fill=(205, 208, 212))
    body_col = (34, 36, 40) if variant != "remarked" else (50, 52, 57)
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=10, fill=body_col, outline=(20, 21, 24), width=3)
    # notch + pin-1 dimple
    d.pieslice([bx0 - 34, (by0 + by1) / 2 - 34, bx0 + 34, (by0 + by1) / 2 + 34], -90, 90, fill=(24, 25, 28))
    d.ellipse([bx0 + 30, by1 - 58, bx0 + 52, by1 - 36], fill=(26, 27, 30))
    if variant == "remarked":
        for _ in range(140):  # resurfacing scratches
            y = rng.uniform(by0 + 8, by1 - 8)
            x0 = rng.uniform(bx0 + 10, bx1 - 160)
            d.line([(x0, y), (x0 + rng.uniform(60, 220), y + rng.uniform(-1.5, 1.5))], fill=(62, 64, 70), width=1)
    ink = {"good": (205, 208, 213), "worn": (170, 173, 179), "poor": (92, 95, 100)}.get(quality, (205, 208, 213))
    lines = list(marking.get("lines", []))
    logo = marking.get("logo", "")
    tx = bx0 + 90
    ty = by0 + 34
    if logo:
        lf = _font(40 if len(logo) <= 4 else 30)
        stroke = 2 if variant == "remarked" else 0
        d.text((tx, ty - 10), logo, font=lf, fill=ink, stroke_width=stroke, stroke_fill=ink)
        tx2, ty_lines = tx, ty + 48
    else:
        tx2, ty_lines = tx, ty + 20
    sizes = [54, 40, 36]
    y = ty_lines
    for n, line in enumerate(lines):
        f = _font(sizes[min(n, 2)], bold=(n == 0), mono=(n > 0))
        x = tx2 if n == 0 else tx2 + (22 if variant == "remarked" else 0)
        d.text((x, y), line, font=f, fill=ink)
        y += sizes[min(n, 2)] + 18
    if photo:
        img = img.rotate(rng.uniform(-1.6, 1.6), resample=Image.BICUBIC, fillcolor=(210, 214, 220))
        blur = {"good": 0.7, "worn": 1.1, "poor": 2.2}.get(quality, 0.8)
        img = img.filter(ImageFilter.GaussianBlur(blur))
        amp = {"good": 6, "worn": 10, "poor": 16}.get(quality, 6)
        arr = np.asarray(img).astype(np.float32)
        yy, xx = np.mgrid[0:H, 0:W]
        light = 1.0 + 0.10 * np.exp(-(((xx - W * 0.35) / W) ** 2 + ((yy - H * 0.4) / H) ** 2) * 6) - 0.06
        noise = np.random.default_rng(seed).normal(0, amp, (H, W))
        arr = arr * light[..., None] + noise[..., None]
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return img


def ensure_samples(force=False):
    os.makedirs(config.SAMPLE_DIR, exist_ok=True)
    profiles = {p["id"]: p for p in database.list_profiles()}
    made = []
    for sp in database.list_sim_profiles():
        pkg = profiles.get(sp["component_profile_id"], {}).get("package", "DIP-14")
        photo = os.path.join(config.SAMPLE_DIR, f"{sp['id']}.png")
        ref = os.path.join(config.SAMPLE_DIR, f"{sp['id']}_ref.png")
        mk = sp["marking"]
        seed = sum(ord(c) for c in sp["id"])
        if force or not os.path.exists(photo):
            render_chip(mk, pkg, mk.get("variant", "genuine"), mk.get("quality", "good"), photo=True, seed=seed).save(photo)
            made.append(photo)
        if sp.get("reference_id") and (force or not os.path.exists(ref)):
            genuine = dict(mk)
            render_chip(genuine, pkg, "genuine", "good", photo=False, seed=seed + 7).save(ref)
            made.append(ref)
    return made
