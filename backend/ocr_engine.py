"""Optical module: image storage, OpenCV preprocessing, Tesseract OCR and reference-image comparison.

If Tesseract is not installed this module says so and returns no text. It never substitutes
canned OCR output.
"""
import os
import platform
import shutil
import uuid

import cv2
import numpy as np

from . import config

try:
    import pytesseract
    HAVE_PYTESSERACT = True
except ImportError:
    pytesseract = None
    HAVE_PYTESSERACT = False

REF_IMG_DIR = os.path.join(config.DATA_DIR, "reference_images")
os.makedirs(REF_IMG_DIR, exist_ok=True)

WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."

INSTALL_HELP = {
    "windows": "Install Tesseract for Windows (UB Mannheim build: https://github.com/UB-Mannheim/tesseract/wiki). "
               "Then add C:\\Program Files\\Tesseract-OCR to PATH or set the TESSERACT_CMD environment variable to tesseract.exe, and restart IC VISION.",
    "macos": "brew install tesseract, then restart IC VISION.",
    "linux": "sudo apt install tesseract-ocr (Debian/Ubuntu) or your distribution's package, then restart IC VISION.",
    "python": "pip install pytesseract opencv-python",
}


def _configure_cmd():
    if not HAVE_PYTESSERACT:
        return
    cmd = os.environ.get("TESSERACT_CMD")
    if cmd and os.path.exists(cmd):
        pytesseract.pytesseract.tesseract_cmd = cmd
        return
    if not shutil.which("tesseract") and platform.system() == "Windows":
        for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if os.path.exists(cand):
                pytesseract.pytesseract.tesseract_cmd = cand
                return


_configure_cmd()


def tesseract_status():
    os_key = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
    if not HAVE_PYTESSERACT:
        return {"available": False, "reason": "The pytesseract Python package is not installed.",
                "install": INSTALL_HELP["python"] + " — and the Tesseract engine: " + INSTALL_HELP[os_key]}
    try:
        ver = pytesseract.get_tesseract_version()
        return {"available": True, "version": str(ver), "engine": f"Tesseract {ver}"}
    except Exception:
        return {"available": False, "reason": "The Tesseract OCR engine was not found on this computer.",
                "install": INSTALL_HELP[os_key]}


# ------------------------------------------------------------------ image storage
def save_upload(file_storage):
    data = file_storage.read()
    return save_image_bytes(data)


def save_image_bytes(data):
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("The file is not a readable image (use JPG or PNG).")
    image_id = "img-" + uuid.uuid4().hex[:12]
    h, w = img.shape[:2]
    if max(h, w) > 3000:  # keep uploads manageable
        s = 3000.0 / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(image_path(image_id), img)
    return image_id


def save_image_file(path):
    with open(path, "rb") as f:
        return save_image_bytes(f.read())


def image_path(image_id, stage=None):
    safe = "".join(ch for ch in image_id if ch.isalnum() or ch in "-_")
    name = f"{safe}.png" if not stage else f"{safe}_{stage}.png"
    return os.path.join(config.UPLOAD_DIR, name)


# ------------------------------------------------------------------ preprocessing
def _ensure_dark_text(binary):
    """Tesseract prefers dark text on a light background; chip markings are usually light on dark."""
    return cv2.bitwise_not(binary) if binary.mean() < 127 else binary


def find_package_body(img):
    """Locate the dark moulded package body so leads and background do not feed OCR noise.

    Returns (crop, box) or (img, None) when no plausible body is found.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (9, 9), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img, None
    c = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(c)
    area_frac = (w * h) / float(img.shape[0] * img.shape[1])
    if area_frac < 0.12 or area_frac > 0.97 or w < 80 or h < 40:
        return img, None
    ix, iy = int(w * 0.03), int(h * 0.06)
    x0, y0, x1, y1 = x + ix, y + iy, x + w - ix, y + h - iy
    return img[y0:y1, x0:x1], [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]


def preprocess(img):
    img, box = find_package_body(img)
    h, w = img.shape[:2]
    target = 1000.0
    scale = target / w
    if scale > 1.05 or scale < 0.95:
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    den = cv2.bilateralFilter(clahe, 7, 40, 40)
    _, otsu = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    otsu = _ensure_dark_text(otsu)
    adaptive = cv2.adaptiveThreshold(den, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 8)
    adaptive = _ensure_dark_text(adaptive)
    adaptive = cv2.medianBlur(adaptive, 3)
    inv_contrast = cv2.bitwise_not(clahe) if clahe.mean() < 127 else clahe
    pad = lambda m: cv2.copyMakeBorder(m, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
    return {"body": img, "body_box": box, "gray": gray, "contrast": clahe, "otsu": pad(otsu),
            "adaptive": pad(adaptive), "contrast_inv": pad(inv_contrast)}


# ------------------------------------------------------------------ OCR
MIN_WORD_CONF = 25  # words below this engine confidence are listed but kept out of the extracted text


def _ocr_once(img, psm):
    cfg = f"--oem 1 --psm {psm}"
    data = pytesseract.image_to_data(img, config=cfg, output_type=pytesseract.Output.DICT)
    words, lines = [], {}
    for k in range(len(data["text"])):
        txt = (data["text"][k] or "").strip()
        try:
            conf = float(data["conf"][k])
        except (TypeError, ValueError):
            conf = -1.0
        if not txt or conf < 0 or not any(ch.isalnum() for ch in txt):
            continue
        kept = conf >= MIN_WORD_CONF
        words.append({"text": txt, "conf": round(conf, 1), "kept": kept,
                      "box": [int(data["left"][k]), int(data["top"][k]), int(data["width"][k]), int(data["height"][k])]})
        if kept:
            key = (data["block_num"][k], data["par_num"][k], data["line_num"][k])
            lines.setdefault(key, []).append(txt.upper())
    line_texts = [" ".join(v) for _, v in sorted(lines.items())]
    confs = [w["conf"] for w in words]
    return {"words": words, "lines": line_texts, "text": "\n".join(line_texts),
            "mean_conf": round(float(np.mean(confs)), 1) if confs else None,
            "dropped": sum(1 for w in words if not w["kept"])}


def run_ocr(image_id, scorer=None):
    """Run preprocessing + Tesseract on a stored image.

    scorer(text) -> float in [0, 1] (database match quality) helps pick the best variant.
    Returns stage image ids, the chosen OCR result and all attempts for transparency.
    """
    status = tesseract_status()
    img = cv2.imread(image_path(image_id), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Image not found")
    stages = preprocess(img)
    for name in ("body", "gray", "contrast", "otsu", "adaptive"):
        cv2.imwrite(image_path(image_id, name), stages[name])
    result = {"image_id": image_id, "engine": status.get("engine"), "available": status["available"],
              "stages": ["original", "body", "contrast", "otsu", "adaptive"], "body_box": stages["body_box"],
              "attempts": [], "text": "",
              "lines": [], "words": [], "mean_conf": None, "variant": None, "psm": None,
              "confidence_source": "Tesseract per-word confidence (engine-reported, 0–100)"}
    if not status["available"]:
        result["error"] = status["reason"]
        result["install"] = status["install"]
        return result
    best, best_score = None, -1.0
    for variant in ("otsu", "adaptive", "contrast_inv"):
        for psm in (6, 11):
            try:
                r = _ocr_once(stages[variant], psm)
            except Exception as exc:
                result["attempts"].append({"variant": variant, "psm": psm, "error": str(exc)})
                continue
            alnum = sum(ch.isalnum() for ch in r["text"])
            match = scorer(r["text"]) if scorer else 0.0
            score = (r["mean_conf"] or 0) / 100.0 * min(1.0, alnum / 6.0) + 0.6 * match
            result["attempts"].append({"variant": variant, "psm": psm, "mean_conf": r["mean_conf"], "chars": alnum,
                                       "db_match": round(match, 3), "score": round(score, 3), "text": r["text"][:80]})
            if score > best_score:
                best, best_score = dict(r, variant=variant, psm=psm), score
            if match >= 0.99 and (r["mean_conf"] or 0) >= 85:
                break
        if best and best_score >= 1.4:
            break
    if best:
        result.update({k: best[k] for k in ("text", "lines", "words", "mean_conf", "variant", "psm", "dropped")})
        result["min_word_conf"] = MIN_WORD_CONF
    return result


# ------------------------------------------------------------------ reference images
def reference_image_for(reference_id=None, profile_id=None, sim_profile_id=None):
    """Return (path, provenance) of a stored reference image, or (None, None)."""
    if sim_profile_id:
        p = os.path.join(config.SAMPLE_DIR, f"{sim_profile_id}_ref.png")
        if os.path.exists(p):
            return p, "simulated"
    for key in (reference_id, profile_id):
        if key:
            p = os.path.join(REF_IMG_DIR, f"{key}.png")
            if os.path.exists(p):
                return p, "known-good sample"
    return None, None


def store_reference_image(image_id, key):
    src = image_path(image_id)
    dst = os.path.join(REF_IMG_DIR, f"{key}.png")
    shutil.copyfile(src, dst)
    return dst


def _ssim(a, b):
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    sa = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a ** 2
    sb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b ** 2
    sab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (sa + sb + c2))
    return float(m.mean())


def compare_images(img_path, ref_path):
    """Indicative structural similarity after feature-based alignment. Not proof of authenticity."""
    a = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(ref_path, cv2.IMREAD_GRAYSCALE)
    if a is None or b is None:
        return None
    W = 640
    a = cv2.resize(a, (W, int(a.shape[0] * W / a.shape[1])))
    b = cv2.resize(b, (W, int(b.shape[0] * W / b.shape[1])))
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    a, b = clahe.apply(a), clahe.apply(b)
    aligned, matches = False, 0
    try:
        orb = cv2.ORB_create(1500)
        ka, da = orb.detectAndCompute(a, None)
        kb, db = orb.detectAndCompute(b, None)
        if da is not None and db is not None:
            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            ms = sorted(bf.match(da, db), key=lambda m: m.distance)[:200]
            matches = len(ms)
            if matches >= 12:
                src = np.float32([ka[m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
                dst = np.float32([kb[m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)
                H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
                if H is not None and mask is not None and mask.sum() >= 10:
                    a = cv2.warpPerspective(a, H, (b.shape[1], b.shape[0]))
                    aligned = True
                    matches = int(mask.sum())
    except cv2.error:
        pass
    if not aligned:
        h = min(a.shape[0], b.shape[0])
        a, b = a[:h], b[:h]
    score = _ssim(a, b)
    return {"ssim": round(score, 3), "aligned": aligned, "inlier_matches": matches,
            "method": "ORB feature alignment + SSIM (indicative only)"}
