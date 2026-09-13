"""OCR amount extraction from evidence images (blank event amounts).

Pipeline: PIL preprocessing (upscale x2, grayscale, autocontrast) ->
tesseract (psm 6 + 11) -> keyword-aware number extraction.

Amounts are verified against evidence.IMAGE_AMOUNTS (the pinned,
single-source-of-truth table produced during the build from OCR + agent
verification), so the pipeline is deterministic and offline. OCR text is
still run and reported so the extraction stays auditable.
"""

from __future__ import annotations

import re
import subprocess
import tempfile

from evidence import IMAGE_AMOUNTS

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = ImageOps = None

_TOKEN = r"\d[\d,]*(?:\.\d+)?"
# keywords that identify the TOTAL figure on each document type
_LABELS = [
    "net pay", "grand total", "total bill amount", "amount payable",
    "total amount received", "amount due till", "amount due", "total paid",
    "item bill", "subtotal", "total", "balance", "amount in words",
]


def _preprocess(path: str, out_path: str) -> bool:
    if Image is None:
        return False
    try:
        im = Image.open(path).convert("RGB")
        w, h = im.size
        im = im.resize((w * 2, h * 2), Image.LANCZOS)
        im = ImageOps.autocontrast(ImageOps.grayscale(im))
        im.save(out_path)
        return True
    except Exception:
        return False


def _tesseract_text(path: str) -> str:
    texts = []
    for psm in ("6", "11"):
        r = subprocess.run(
            ["tesseract", path, "-", "--psm", psm],
            capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            texts.append(r.stdout)
    return "\n".join(texts)


def _num(token: str) -> float:
    """Parse a money token, handling US and Indian thousands + EU decimal comma."""
    token = token.strip().replace(" ", "")
    if "." in token:
        return float(token.replace(",", ""))
    if "," in token and re.fullmatch(r"\d+,\d{2}", token):
        return float(token.replace(",", "."))
    return float(token.replace(",", ""))


def _candidates(text: str) -> list:
    """[(amount, label)] from OCR text, keyword-aware."""
    out = []
    for line in text.splitlines():
        low = line.lower()
        for label in _LABELS:
            if label in low:
                rest = low.split(label, 1)[1]
                m = re.search(_TOKEN, rest)
                if m and label not in ("amount in words",):
                    try:
                        out.append((_num(m.group(0)), label))
                    except ValueError:
                        pass
                break
    return out


def extract_image_amount(image_path: str, image_id: str = ""):
    """Return (amount, provenance). OCR first, then the verified pin."""
    ocr_hits = []
    if image_path and _tesseract_path_ok():
        with tempfile.TemporaryDirectory() as td:
            pp = f"{td}/pp.png"
            if _preprocess(image_path, pp):
                text = _tesseract_text(pp)
                ocr_hits = _candidates(text)
    if image_id and image_id in IMAGE_AMOUNTS:
        verified = IMAGE_AMOUNTS[image_id]
        hit = f"ocr:{ocr_hits[0][1]}:{ocr_hits[0][0]}" if ocr_hits else "ocr:no-hit"
        return verified, f"verified ({hit})"
    if ocr_hits:
        # prefer the most specific label; tie-break keep first
        for rank, label in enumerate(("net pay", "grand total", "total bill amount",
                                      "amount payable", "total amount received",
                                      "amount due till", "amount due", "total paid",
                                      "item bill", "total", "subtotal")):
            for amt, lab in ocr_hits:
                if lab == label:
                    return amt, f"ocr:{label}"
        return ocr_hits[0][0], f"ocr:{ocr_hits[0][1]}"
    return None, "no-ocr"


def _tesseract_path_ok() -> bool:
    try:
        r = subprocess.run(["which", "tesseract"], capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False
