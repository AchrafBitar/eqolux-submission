from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

INPUT_DIR = Path(os.getenv("VERIFACT_INPUT_DIR", str(ROOT.parent / "kit_candidat" / "kit_candidat" / "documents")))
REFERENTIEL_DIR = ROOT / "referentiel"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / ".cache"
TESSDATA_DIR = ROOT / "tools" / "tessdata"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLI_MODEL = "opus"
API_MODEL = "claude-opus-5-5"
LLM_TIMEOUT_SECONDS = 420
VISION_DPI = 150

MIN_TEXT_WORDS = 8
OCR_LANGUAGE = "fra"
OCR_DPI_FACTORS = (1.0, 2.0, 2.5, 3.0)
OCR_DPI_MIN, OCR_DPI_MAX = 200, 400
DEFAULT_SCAN_PPI = 300

AMOUNT_TOLERANCE = 0.02
MATCH_THRESHOLD = 85
REVIEW_THRESHOLD = 0.8


def poppler_dir() -> Path | None:
    found = shutil.which("pdftoppm")
    if found:
        return Path(found).parent
    packages = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    matches = sorted(packages.glob("oschwartz10612.Poppler*/poppler-*/Library/bin"), reverse=True)
    return matches[0] if matches else None


def tesseract_cmd() -> str:
    default = Path(os.getenv("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"
    return shutil.which("tesseract") or (str(default) if default.exists() else "tesseract")
