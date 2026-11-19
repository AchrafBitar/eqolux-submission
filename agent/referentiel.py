from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz

from agent import config
from agent.utils import normalize, parse_number, similarity


@dataclass(frozen=True)
class Article:
    supplier_code: str
    code: str
    designation: str
    price_unit: str
    packaging: str
    price: float

    @property
    def pack_size(self) -> int | None:
        match = re.search(r"\d+", self.packaging)
        return int(match.group()) if match else None


@dataclass(frozen=True)
class Terms:
    """Negotiated conditions, read from the free text of fournisseurs.csv."""

    free_delivery: bool
    free_delivery_threshold: float | None
    delivery_fee: float | None
    discount_rate: float | None
    firm_prices_until: date | None

    @classmethod
    def parse(cls, text: str) -> Terms:
        threshold = re.search(r"à partir de ([\d\s]+,\d{2}) €", text)
        fee = re.search(r"([\d\s]+,\d{2}) € HT de frais", text)
        discount = re.search(r"remise[^%]*?(\d+(?:,\d+)?) ?%", text, re.IGNORECASE)
        firm = re.search(r"fermes jusqu.au (\d{2}/\d{2}/\d{4})", text)
        return cls(
            free_delivery="livraison offerte" in text.lower(),
            free_delivery_threshold=parse_number(threshold.group(1)) if threshold else None,
            delivery_fee=parse_number(fee.group(1)) if fee else None,
            discount_rate=parse_number(discount.group(1)) if discount else None,
            firm_prices_until=datetime.strptime(firm.group(1), "%d/%m/%Y").date() if firm else None,
        )


@dataclass(frozen=True)
class Supplier:
    code: str
    name: str
    terms: Terms


class Referentiel:
    def __init__(self, directory: Path = config.REFERENTIEL_DIR) -> None:
        self.suppliers = [
            Supplier(row["code_fournisseur"], row["fournisseur"], Terms.parse(row["conditions_negociees"]))
            for row in _read_csv(directory / "fournisseurs.csv")
        ]
        self.articles = [
            Article(row["code_fournisseur"], row["code_article"], row["designation"], row["unite_prix"],
                    row["conditionnement"], float(row["prix_negocie_ht"]))
            for row in _read_csv(directory / "mercuriale.csv")
        ]

    @property
    def supplier_names(self) -> list[str]:
        return [supplier.name for supplier in self.suppliers]

    def find_supplier(self, name: str, article_codes: list[str] | None = None) -> Supplier | None:
        """By name, or failing that by the prefix of its article codes (MFL-TUR01 -> MFL)."""
        scored = [(fuzz.token_set_ratio(normalize(name), normalize(s.name)), s) for s in self.suppliers]
        score, best = max(scored, key=lambda item: item[0])
        if score >= 85:
            return best
        prefixes = {code.split("-")[0].upper() for code in article_codes or [] if "-" in code}
        return next((supplier for supplier in self.suppliers if supplier.code in prefixes), None)

    def article(self, code: str) -> Article | None:
        return next((article for article in self.articles if article.code == code.strip().upper()), None)

    def closest_article(self, supplier: Supplier, designation: str) -> tuple[Article | None, float]:
        candidates = [article for article in self.articles if article.supplier_code == supplier.code]
        scored = [(similarity(designation, article.designation), article) for article in candidates]
        score, article = max(scored, key=lambda item: item[0], default=(0.0, None))
        return article, score


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
