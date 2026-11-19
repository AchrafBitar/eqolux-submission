from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from agent import config
from agent.controls import find_anomalies
from agent.extraction import extract_invoices
from agent.llm import LlmClient, make_client
from agent.models import Anomaly, Invoice
from agent.reader import read_pdf
from agent.referentiel import Referentiel
from agent.utils import fmt_amount, fmt_number
from agent.verification import needs_review, verify

LINE_COLUMNS = ["fichier_source", "fournisseur", "type_document", "numero_document", "date_document", "code_article",
                "designation", "quantite", "unite", "prix_unitaire_ht", "montant_ht", "taux_tva"]
ANOMALY_COLUMNS = ["fichier_source", "numero_document", "code_article", "type_anomalie", "description", "impact_eur",
                   "revue_humaine"]
QUALITY_COLUMNS = ["fichier_source", "numero_document", "confiance", "revue_humaine", "verifications"]


@dataclass
class Result:
    invoices: list[Invoice]
    anomalies: list[Anomaly]
    engine: str


def analyse(pdfs: list[Path], api_key: str = "", on_progress: Callable[[str], None] | None = None) -> Result:
    """Read, extract and self-check every PDF, then compare the whole batch with the référentiel."""
    client = make_client(api_key)
    referentiel = Referentiel()
    per_pdf: dict[Path, list[Invoice]] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_process, pdf, client, referentiel): pdf for pdf in pdfs}
        for future in as_completed(futures):
            per_pdf[futures[future]] = future.result()
            if on_progress:
                on_progress(futures[future].name)
    invoices = [invoice for pdf in pdfs for invoice in per_pdf[pdf]]
    return Result(invoices, find_anomalies(invoices, referentiel), client.name)


def _process(pdf: Path, client: LlmClient, referentiel: Referentiel) -> list[Invoice]:
    document = read_pdf(pdf)
    invoices = extract_invoices(document, client, referentiel.supplier_names)
    for invoice in invoices:
        supplier = referentiel.find_supplier(invoice.fournisseur, [line.code_article for line in invoice.lines])
        invoice.fournisseur = supplier.name if supplier else invoice.fournisseur
        verify(invoice, document.text)
    return invoices


def lines_table(invoices: list[Invoice]) -> pd.DataFrame:
    rows = [
        [invoice.fichier_source, invoice.fournisseur, invoice.type_document, invoice.numero_document,
         invoice.date_document, line.code_article, line.designation, fmt_number(line.quantite), line.unite,
         fmt_amount(line.prix_unitaire_ht), fmt_amount(line.montant_ht), fmt_number(line.taux_tva)]
        for invoice in invoices for line in invoice.lines
    ]
    return pd.DataFrame(rows, columns=LINE_COLUMNS)


def anomalies_table(anomalies: list[Anomaly]) -> pd.DataFrame:
    rows = [
        [anomaly.fichier_source, anomaly.numero_document, anomaly.code_article, anomaly.type_anomalie,
         anomaly.description, fmt_amount(anomaly.impact_eur), _yes_no(anomaly.revue_humaine)]
        for anomaly in anomalies
    ]
    return pd.DataFrame(rows, columns=ANOMALY_COLUMNS)


def quality_table(invoices: list[Invoice]) -> pd.DataFrame:
    rows = [
        [invoice.fichier_source, invoice.numero_document, invoice.confidence, _yes_no(needs_review(invoice)),
         " | ".join(invoice.checks)]
        for invoice in invoices
    ]
    return pd.DataFrame(rows, columns=QUALITY_COLUMNS)


def _yes_no(flag: bool) -> str:
    return "oui" if flag else "non"


def save(result: Result, folder: Path = config.OUTPUT_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines_table(result.invoices).to_csv(folder / "lignes.csv", index=False, encoding="utf-8")
    anomalies_table(result.anomalies).to_csv(folder / "anomalies.csv", index=False, encoding="utf-8")
    quality_table(result.invoices).to_csv(folder / "controle_qualite.csv", index=False, encoding="utf-8")
    return folder
