from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from agent import config

_STOPWORDS = {"de", "du", "des", "la", "le", "les", "d", "l", "et", "a", "au", "aux", "en"}


def parse_number(raw: str | None) -> float | None:
    """Read an amount printed the French way: '1 844,00' -> 1844.0, '6,400' -> 6.4, '-28,00' -> -28.0."""
    if not raw or not any(char.isdigit() for char in raw):
        return None
    text = raw.strip()
    digits = re.sub(r"[^\d,.]", "", text)
    cut = max(digits.rfind(","), digits.rfind("."))
    if cut >= 0:
        digits = digits[:cut].replace(",", "").replace(".", "") + "." + digits[cut + 1:]
    value = float(digits)
    return -value if text.startswith(("-", "−", "(")) or text.endswith("-") else value


def number_candidates(raw: str) -> list[float]:
    """Other ways to read a number whose decimal separator was lost or misread ('2600' may be 2.6)."""
    value = parse_number(raw)
    if value is None:
        return []
    sign = -1.0 if value < 0 else 1.0
    whole = int(re.sub(r"\D", "", raw))
    return list(dict.fromkeys([value, sign * whole / 1000, sign * whole / 100, sign * whole]))


def close(left: float | None, right: float | None, tolerance: float = config.AMOUNT_TOLERANCE) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= max(tolerance, 0.001 * abs(right))


def normalize(text: str) -> str:
    text = text.replace("œ", "oe").replace("Œ", "OE")
    text = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def similarity(left: str, right: str) -> float:
    """0-100 match between two product labels, tolerant to plurals and extra words; differing numbers cost 20."""
    left_words, right_words = _keywords(left), _keywords(right)
    if not left_words or not right_words:
        return 0.0
    a, b = " ".join(left_words), " ".join(right_words)
    score = (fuzz.token_set_ratio(a, b) + fuzz.token_sort_ratio(a, b)) / 2
    left_numbers = {word for word in left_words if word.isdigit()}
    right_numbers = {word for word in right_words if word.isdigit()}
    if left_numbers and right_numbers and left_numbers != right_numbers:
        score -= 20
    return max(score, 0.0)


def _keywords(text: str) -> list[str]:
    words = [word for word in normalize(text).split() if word not in _STOPWORDS]
    return [word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words]


def fmt_amount(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def fmt_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def fmt_quantity(value: float | None) -> str:
    return fmt_number(value).replace(".", ",") or "—"


def fmt_eur(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} €".replace(",", " ").replace(".", ",")
