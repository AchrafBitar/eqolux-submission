import pytest

from agent import config
from agent.controls import find_anomalies, price_factor
from agent.models import Invoice, Line
from agent.reader import read_pdf
from agent.referentiel import Referentiel
from agent.utils import parse_number
from agent.verification import verify

REFERENTIEL = Referentiel()


def _line(code, quantity, unit, price, amount=None, raw_quantity=None):
    amount = round(quantity * price, 2) if amount is None else amount
    raw = {"quantite": raw_quantity or str(quantity), "prix_unitaire_ht": str(price), "montant_ht": str(amount)}
    return Line(code, code or "Frais de livraison", quantity, unit, price, amount, 5.5, raw=raw)


def _invoice(supplier, lines):
    invoice = Invoice("test.pdf", supplier, "facture", "T-1", "2026-11-04", lines, taux_tva=5.5)
    invoice.total_ht = invoice.expected_total_ht
    invoice.total_tva = round(invoice.total_ht * 0.055, 2)
    return invoice


def _anomalies(*invoices):
    return [(anomaly.type_anomalie, anomaly.impact_eur) for anomaly in find_anomalies(list(invoices), REFERENTIEL)]


@pytest.mark.parametrize(("raw", "expected"), [
    ("6,400", 6.4), ("1 844,00 €", 1844.0), ("-28,00", -28.0), ("172.80", 172.8), ("5,5 %", 5.5), ("", None),
])
def test_numbers_are_read_the_french_way(raw, expected):
    assert parse_number(raw) == expected


def test_lost_decimal_comma_is_repaired_by_arithmetic():
    invoice = _invoice("Boucherie Maison Lacombe", [_line("BLC-AGN03", 2600.0, "kg", 46.0, 119.6, "2600")])
    verify(invoice, "2600 46.0 119.6")
    assert invoice.lines[0].quantite == 2.6


def test_carton_price_is_converted_before_comparing():
    assert price_factor("carton 20 × 250 g", REFERENTIEL.article("CRB-BEU01")) == 20
    assert _anomalies(_invoice("Crèmerie Beaufortaine", [_line("CRB-BEU01", 3, "carton 20 × 250 g", 59.0)])) == []


def test_price_gap_is_quantified():
    invoice = _invoice("Marée Fraîche du Littoral", [_line("MFL-TUR01", 6.4, "kg", 47.0)])
    assert _anomalies(invoice) == [("ecart_prix", 32.0)]


def test_delivery_fee_above_franco_is_flagged():
    invoice = _invoice("Marée Fraîche du Littoral", [_line("MFL-BAR02", 10, "kg", 36.5), _line("", 1, "forfait", 15.0)])
    assert _anomalies(invoice) == [("frais_livraison_non_dus", 15.0)]


def test_missing_loyalty_discount_is_flagged():
    invoice = _invoice("Boucherie Maison Lacombe", [_line("BLC-FIL01", 2, "kg", 54.0)])
    assert _anomalies(invoice) == [("remise_non_conforme", 3.24)]


def test_resent_invoice_is_reported_once():
    original = _invoice("Crèmerie Beaufortaine", [_line("CRB-CRE02", 2, "L", 6.8)])
    copy = _invoice("Crèmerie Beaufortaine", [_line("CRB-CRE02", 2, "L", 6.8)])
    copy.fichier_source, copy.duplicata = "relance.pdf", True
    assert _anomalies(copy, original) == [("doublon", 13.6)]


def test_reader_rebuilds_rows_in_visual_order():
    pdf = config.INPUT_DIR / "CRB_2026-3317.pdf"
    if not pdf.exists() or config.poppler_dir() is None:
        pytest.skip("kit candidat ou Poppler absent")
    cream = next(row for row in read_pdf(pdf).text.splitlines() if "CRB-CRE02" in row)
    assert cream.split()[-4:] == ["12", "L", "6,80", "81,60"]
