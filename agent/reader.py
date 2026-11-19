from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import numpy as np
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageOps

from agent import config
from agent.models import Document, Page

_POPPLER = config.poppler_dir()
pytesseract.pytesseract.tesseract_cmd = config.tesseract_cmd()
if config.TESSDATA_DIR.exists():
    os.environ["TESSDATA_PREFIX"] = str(config.TESSDATA_DIR)


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 100.0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def middle(self) -> float:
        return (self.y0 + self.y1) / 2


def read_pdf(path: Path) -> Document:
    """Native text layer when the page has one, OCR when it is a scan."""
    resolutions = _scan_resolutions(path)
    pages = []
    for number, native_words in _text_layer(path).items():
        if len(native_words) >= config.MIN_TEXT_WORDS:
            pages.append(Page(number, layout_text(native_words)))
        else:
            words, dpi = _ocr(path, number, resolutions.get(number))
            pages.append(Page(number, layout_text(words), scanned=True, dpi=dpi))
    return Document(path, pages)


def render_page(path: Path, number: int, dpi: int) -> Image.Image:
    return convert_from_path(path, dpi=dpi, first_page=number, last_page=number, poppler_path=_POPPLER)[0]


def layout_text(words: list[Word]) -> str:
    """Rebuild the visual lines from word positions: the PDF's internal text order cannot be trusted."""
    if not words:
        return ""
    typical = median(word.height for word in words)
    char_width = median((word.x1 - word.x0) / len(word.text) for word in words) or 1.0
    rows: list[list[Word]] = []
    for word in sorted((w for w in words if w.height <= 3.5 * typical), key=lambda w: (w.middle, w.x0)):
        if rows and abs(word.middle - _row_middle(rows[-1])) <= 0.55 * typical:
            rows[-1].append(word)
        else:
            rows.append([word])
    return "\n".join(_render_row(sorted(row, key=lambda w: w.x0), char_width) for row in rows)


def _row_middle(row: list[Word]) -> float:
    return sum(word.middle for word in row) / len(row)


def _render_row(row: list[Word], char_width: float) -> str:
    line = ""
    for word in row:
        column = int(word.x0 / char_width)
        line += " " * max(column - len(line), 1 if line else 0) + word.text
    return line


def _text_layer(path: Path) -> dict[int, list[Word]]:
    pages: dict[int, list[Word]] = {}
    for line in _poppler("pdftotext", "-tsv", "-enc", "UTF-8", str(path), "-").splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) < 12:
            continue
        words = pages.setdefault(int(cells[1]), [])
        if cells[0] == "5" and cells[11].strip():
            left, top, width, height = map(float, cells[6:10])
            words.append(Word(cells[11], left, top, left + width, top + height))
    return pages


def _scan_resolutions(path: Path) -> dict[int, float]:
    resolutions: dict[int, float] = {}
    for line in _poppler("pdfimages", "-list", str(path)).splitlines()[2:]:
        cells = line.split()
        if len(cells) >= 14 and cells[2] == "image":
            page = int(cells[0])
            resolutions[page] = max(resolutions.get(page, 0.0), float(cells[12]))
    return resolutions


def _poppler(tool: str, *args: str) -> str:
    name = tool + (".exe" if sys.platform == "win32" else "")
    executable = str(_POPPLER / name) if _POPPLER else name
    result = subprocess.run([executable, *args], capture_output=True, check=True, timeout=120)
    return result.stdout.decode("utf-8", errors="replace")


def _ocr(path: Path, number: int, native_ppi: float | None) -> tuple[list[Word], int]:
    """Clean the scan at its native resolution, then keep the DPI that Tesseract reads with the most confidence."""
    base_dpi = round(native_ppi or config.DEFAULT_SCAN_PPI)
    image = ImageOps.grayscale(render_page(path, number, base_dpi)).filter(ImageFilter.MedianFilter(3))
    image = image.rotate(_skew_angle(image), resample=Image.Resampling.BICUBIC, fillcolor=255)
    dpis = sorted({_clamp(base_dpi * factor) for factor in config.OCR_DPI_FACTORS})
    with ThreadPoolExecutor() as pool:
        trials = list(pool.map(lambda dpi: _tesseract(image, base_dpi, dpi), dpis))
    words, dpi, _ = max(trials, key=lambda trial: trial[2])
    return words, dpi


def _tesseract(image: Image.Image, base_dpi: int, dpi: int) -> tuple[list[Word], int, float]:
    scale = dpi / base_dpi
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    data = pytesseract.image_to_data(
        ImageOps.autocontrast(resized, cutoff=1), lang=config.OCR_LANGUAGE, config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )
    points = 72 / dpi
    words = [
        Word(text.strip(), left * points, top * points, (left + width) * points, (top + height) * points, float(conf))
        for text, conf, left, top, width, height in zip(
            data["text"], data["conf"], data["left"], data["top"], data["width"], data["height"], strict=True
        )
        if text.strip() and float(conf) >= 0
    ]
    return words, dpi, _mean_confidence(words)


def _mean_confidence(words: list[Word]) -> float:
    weighted = [(word.conf, len(word.text)) for word in words if sum(char.isalnum() for char in word.text) >= 2]
    total = sum(weight for _, weight in weighted)
    return sum(conf * weight for conf, weight in weighted) / total if total else 0.0


def _skew_angle(image: Image.Image) -> float:
    """Text lines give the sharpest row histogram when they are horizontal."""
    def sharpness(angle: float) -> float:
        dark = np.asarray(image.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)) < 128
        return float(np.sum(np.diff(dark.sum(axis=1).astype(float)) ** 2))

    coarse = max(np.arange(-3.0, 3.01, 0.25), key=sharpness)
    return float(max(np.arange(coarse - 0.25, coarse + 0.26, 0.05), key=sharpness))


def _clamp(dpi: float) -> int:
    return int(round(min(max(dpi, config.OCR_DPI_MIN), config.OCR_DPI_MAX), -1))
