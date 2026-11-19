from __future__ import annotations

import re
from dataclasses import dataclass

from agent import config
from agent.models import Invoice, Line
from agent.utils import close, fmt_eur, number_candidates

_SCAN_PENALTY = 0.10
_REPAIR_PENALTY = 0.05


@dataclass(frozen=True)
class Check:
    ok: bool
    message: str
    penalty: float = 0.0


def verify(invoice: Invoice, source_text: str) -> None:
    """Level 3: re-check the extraction, give it a confidence score and keep the trail of what was checked."""
    repairs = _repair_misread_numbers(invoice)
    checks = [
        _lines_add_up(invoice),
        _total_matches(invoice),
        _vat_matches(invoice),
        _numbers_found_in_source(invoice, source_text),
        _header_complete(invoice),
    ]
    penalty = sum(check.penalty for check in checks if not check.ok)
    penalty += _REPAIR_PENALTY * len(repairs) + (_SCAN_PENALTY if invoice.scanned else 0.0)
    invoice.confidence = round(max(0.0, 1.0 - penalty), 2)
    invoice.checks = [f"{'✔' if check.ok else '✘'} {check.message}" for check in checks]
    invoice.checks += [f"ⓘ {repair}" for repair in repairs]
    invoice.issues = [check.message for check in checks if not check.ok and check.penalty] + repairs
    if invoice.scanned:
        invoice.checks.append("ⓘ document scanné : lu par OCR et sur l'image")
        invoice.issues.append("document scanné")


def needs_review(invoice: Invoice) -> bool:
    return invoice.confidence < config.REVIEW_THRESHOLD


def _adds_up(line: Line) -> bool:
    return line.computed_amount is None or close(line.computed_amount, line.montant_ht)


def _repair_misread_numbers(invoice: Invoice) -> list[str]:
    """'6,400' read as 6400: try the other readings of the quantity and the price, keep the one that adds up."""
    repairs = []
    for line in invoice.lines:
        if line.quantite is None or line.prix_unitaire_ht is None or _adds_up(line):
            continue
        for field, other in (("quantite", line.prix_unitaire_ht), ("prix_unitaire_ht", line.quantite)):
            raw = line.raw.get(field, "")
            fixed = next((value for value in number_candidates(raw)
                          if close(round(value * other, 2), line.montant_ht)), None)
            if fixed is not None:
                setattr(line, field, fixed)
                repairs.append(f"« {line.designation} » : {field} « {raw} » relu {fixed:g}")
                break
    return repairs


def _lines_add_up(invoice: Invoice) -> Check:
    """No penalty: a wrong line whose numbers are all in the PDF is the supplier's mistake, reported as an anomaly."""
    good = sum(_adds_up(line) for line in invoice.lines)
    return Check(good == len(invoice.lines), f"{good}/{len(invoice.lines)} lignes : quantité × prix = montant")


def _total_matches(invoice: Invoice) -> Check:
    if invoice.total_ht is None:
        return Check(False, "total HT introuvable", 0.15)
    ok = close(invoice.expected_total_ht, invoice.total_ht)
    message = f"somme des lignes {fmt_eur(invoice.expected_total_ht)}, total HT {fmt_eur(invoice.total_ht)}"
    return Check(ok, message, 0.35)


def _vat_matches(invoice: Invoice) -> Check:
    if invoice.total_ht is None or invoice.total_tva is None or invoice.taux_tva is None:
        return Check(True, "TVA : pas de taux unique à vérifier")
    expected = round(invoice.total_ht * invoice.taux_tva / 100, 2)
    ok = close(expected, invoice.total_tva)
    return Check(ok, f"TVA {fmt_eur(invoice.total_tva)}, attendue {fmt_eur(expected)}", 0.10)


def _numbers_found_in_source(invoice: Invoice, source_text: str) -> Check:
    """A number the model wrote but that is nowhere in the PDF text is a misreading or an invention."""
    printed = {re.sub(r"\D", "", token) for token in source_text.split()}
    printed |= {re.sub(r"\D", "", group) for group in re.findall(r"\d{1,3}(?: \d{3})+(?:,\d+)?", source_text)}
    missing = [raw for line in invoice.lines for raw in line.raw.values()
               if raw and re.sub(r"\D", "", raw) not in printed]
    if not missing:
        return Check(True, "tous les nombres sont dans le texte source")
    penalty = 0.05 if invoice.scanned else min(0.4, 0.2 * len(missing))
    return Check(False, f"nombres absents du texte source : {', '.join(missing[:4])}", penalty)


def _header_complete(invoice: Invoice) -> Check:
    fields = {"numéro": invoice.numero_document, "date": invoice.date_document, "fournisseur": invoice.fournisseur}
    missing = [name for name, value in fields.items() if not value]
    if not missing:
        return Check(True, "en-tête complet")
    return Check(False, f"en-tête incomplet : {', '.join(missing)}", 0.2 * len(missing))
