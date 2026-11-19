from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Page:
    number: int
    text: str = ""
    scanned: bool = False
    dpi: int | None = None


@dataclass
class Document:
    path: Path
    pages: list[Page]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def scanned(self) -> bool:
        return any(page.scanned for page in self.pages)


@dataclass
class Line:
    code_article: str
    designation: str
    quantite: float | None
    unite: str
    prix_unitaire_ht: float | None
    montant_ht: float | None
    taux_tva: float | None
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def computed_amount(self) -> float | None:
        if self.quantite is None or self.prix_unitaire_ht is None:
            return None
        return round(self.quantite * self.prix_unitaire_ht, 2)


@dataclass
class Invoice:
    fichier_source: str
    fournisseur: str
    type_document: str
    numero_document: str
    date_document: str
    lines: list[Line]
    total_ht: float | None = None
    total_tva: float | None = None
    taux_tva: float | None = None
    remises: list[float] = field(default_factory=list)
    duplicata: bool = False
    scanned: bool = False
    confidence: float = 1.0
    checks: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return re.sub(r"[^A-Z0-9]", "", self.numero_document.upper())

    @property
    def lines_total(self) -> float:
        return round(sum(line.montant_ht or 0.0 for line in self.lines), 2)

    @property
    def expected_total_ht(self) -> float:
        return round(self.lines_total + sum(self.remises), 2)


@dataclass(frozen=True)
class Anomaly:
    fichier_source: str
    numero_document: str
    code_article: str
    type_anomalie: str
    description: str
    impact_eur: float | None
    revue_humaine: bool
