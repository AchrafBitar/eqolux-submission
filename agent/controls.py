from __future__ import annotations

from collections import defaultdict
from datetime import date

from agent import config
from agent.models import Anomaly, Invoice, Line
from agent.referentiel import Article, Referentiel, Supplier
from agent.utils import close, fmt_eur, fmt_quantity, normalize
from agent.verification import needs_review

PRICE_GAP = "ecart_prix"
LINE_CALCULATION = "erreur_calcul_ligne"
TOTAL_MISMATCH = "erreur_total"
UNDUE_DELIVERY_FEE = "frais_livraison_non_dus"
DISCOUNT = "remise_non_conforme"
UNLISTED_ARTICLE = "article_hors_mercuriale"
UNKNOWN_SUPPLIER = "fournisseur_inconnu"
DUPLICATE = "doublon"
TO_REVIEW = "extraction_a_verifier"

_UNITS = {
    "kg": {"kg", "kilo", "kilos"},
    "g": {"g", "gr"},
    "litre": {"l", "lt", "litre", "litres"},
    "piece": {"piece", "pieces", "pce", "pc", "u", "unite"},
    "bouteille": {"bt", "btl", "bouteille", "bouteilles"},
    "botte": {"botte", "bottes", "bte"},
}
_PACKS = {"carton", "plateau", "colis", "caisse", "lot", "sachet", "barquette", "cagette"}
_CONVERSIONS = {("g", "kg"): 0.001, ("kg", "g"): 1000.0}


def find_anomalies(invoices: list[Invoice], referentiel: Referentiel) -> list[Anomaly]:
    """Level 2: compare every extracted line and document with the negotiated prices and terms."""
    duplicates = _duplicates(invoices)
    anomalies = list(duplicates.values())
    for invoice in invoices:
        if id(invoice) not in duplicates:
            anomalies += _check_invoice(invoice, referentiel)
    return anomalies


def price_factor(unit: str, article: Article) -> float | None:
    """How many negotiated price units one billed unit holds: 'carton 20 × 250 g' -> 20 pièces."""
    words = normalize(unit).split()
    if not words:
        return None
    if words[0] in _PACKS:
        count = next((int(word) for word in words[1:] if word.isdigit()), None) or article.pack_size
        return float(count) if count else None
    billed, negotiated = _unit(words[0]), _unit(article.price_unit)
    return 1.0 if billed == negotiated else _CONVERSIONS.get((billed, negotiated))


def is_fee(line: Line) -> bool:
    return normalize(line.designation).startswith(("frais", "livraison", "transport", "port "))


def _check_invoice(invoice: Invoice, referentiel: Referentiel) -> list[Anomaly]:
    supplier = referentiel.find_supplier(invoice.fournisseur, [line.code_article for line in invoice.lines])
    review = needs_review(invoice)
    found: list[Anomaly | None] = []
    if supplier is None:
        found.append(_anomaly(invoice, "", UNKNOWN_SUPPLIER, f"Fournisseur « {invoice.fournisseur} » absent du "
                              "référentiel : prix non contrôlés.", None, True))
    for line in invoice.lines:
        article, score = _match(line, supplier, referentiel)
        found.append(_check_calculation(invoice, line, article, review))
        if supplier and not is_fee(line) and (line.montant_ht or 0.0) >= 0:
            found.append(_check_price(invoice, line, article, score, supplier, review) if article
                         else _unlisted(invoice, line, supplier, referentiel))
    found.append(_check_total(invoice, review))
    if supplier:
        found += [_check_delivery_fee(invoice, supplier, review), _check_discount(invoice, supplier, review)]
    if review:
        reasons = "; ".join(invoice.issues)
        found.append(_anomaly(invoice, "", TO_REVIEW, f"Confiance {invoice.confidence:.0%} : {reasons}. "
                              "Comparer les lignes au PDF avant paiement.", None, True))
    return [anomaly for anomaly in found if anomaly]


def _match(line: Line, supplier: Supplier | None, referentiel: Referentiel) -> tuple[Article | None, float]:
    """The mercuriale article a line refers to: by its code, or by its label when no code is printed."""
    if line.code_article:
        return referentiel.article(line.code_article), 100.0
    if supplier is None:
        return None, 0.0
    article, score = referentiel.closest_article(supplier, line.designation)
    return (article, score) if score >= config.MATCH_THRESHOLD else (None, score)


def _check_calculation(invoice: Invoice, line: Line, article: Article | None, review: bool) -> Anomaly | None:
    computed = line.computed_amount
    if computed is None or close(computed, line.montant_ht):
        return None
    code = line.code_article or (article.code if article else "")
    description = (f"« {line.designation} » : {fmt_quantity(line.quantite)} × {fmt_eur(line.prix_unitaire_ht)} = "
                   f"{fmt_eur(computed)}, mais la ligne est facturée {fmt_eur(line.montant_ht)}.")
    return _anomaly(invoice, code, LINE_CALCULATION, description, round((line.montant_ht or 0.0) - computed, 2), review)


def _check_price(invoice: Invoice, line: Line, article: Article, score: float, supplier: Supplier,
                 review: bool) -> Anomaly | None:
    price, factor = line.prix_unitaire_ht, price_factor(line.unite, article)
    if price is None or (factor is None and close(price, article.price)):
        return None
    if factor is None:
        return _anomaly(invoice, article.code, PRICE_GAP,
                        f"« {line.designation} » facturé {fmt_eur(price)} / {line.unite} ; prix négocié "
                        f"{fmt_eur(article.price)} / {article.price_unit} : unités à rapprocher.", None, True)
    expected = article.price * factor
    if price - expected <= config.AMOUNT_TOLERANCE:
        return None
    impact = round((price - expected) * (line.quantite or 0.0), 2)
    description = (f"« {line.designation} » facturé {fmt_eur(price)} / {line.unite} au lieu de {fmt_eur(expected)} "
                   f"négociés, sur {fmt_quantity(line.quantite)} {line.unite} : {fmt_eur(impact)} HT en trop.")
    until = supplier.terms.firm_prices_until
    if until and invoice.date_document and date.fromisoformat(invoice.date_document) <= until:
        description += f" Prix fermes jusqu'au {until:%d/%m/%Y}."
    return _anomaly(invoice, article.code, PRICE_GAP, description, impact, review or score < 90)


def _unlisted(invoice: Invoice, line: Line, supplier: Supplier, referentiel: Referentiel) -> Anomaly:
    closest, score = referentiel.closest_article(supplier, line.designation)
    description = f"« {line.designation} » ({fmt_eur(line.montant_ht)} HT) n'est pas dans la mercuriale."
    impact = None
    if closest and score >= 70:
        description += (f" Article négocié le plus proche : {closest.code} « {closest.designation} » "
                        f"à {fmt_eur(closest.price)}. Substitution à valider.")
        negotiated = closest.price * (price_factor(line.unite, closest) or 1.0)
        gap = ((line.prix_unitaire_ht or 0.0) - negotiated) * (line.quantite or 0.0)
        impact = round(gap, 2) if gap > 0 else None
    return _anomaly(invoice, line.code_article, UNLISTED_ARTICLE, description, impact, True)


def _check_total(invoice: Invoice, review: bool) -> Anomaly | None:
    if invoice.total_ht is None or close(invoice.expected_total_ht, invoice.total_ht):
        return None
    description = (f"Total HT imprimé {fmt_eur(invoice.total_ht)}, alors que lignes et remises font "
                   f"{fmt_eur(invoice.expected_total_ht)}.")
    return _anomaly(invoice, "", TOTAL_MISMATCH, description, round(invoice.total_ht - invoice.expected_total_ht, 2),
                    review)


def _check_delivery_fee(invoice: Invoice, supplier: Supplier, review: bool) -> Anomaly | None:
    terms = supplier.terms
    fees = round(sum(line.montant_ht or 0.0 for line in invoice.lines if is_fee(line)), 2)
    if fees <= 0 or not terms.free_delivery:
        return None
    goods = round(invoice.lines_total - fees, 2)
    threshold = terms.free_delivery_threshold
    if threshold is None or goods >= threshold:
        reason = (f"la commande ({fmt_eur(goods)} HT hors frais) atteint le franco de {fmt_eur(threshold)}"
                  if threshold else "la livraison est offerte")
        return _anomaly(invoice, "", UNDUE_DELIVERY_FEE, f"{fmt_eur(fees)} de frais de livraison facturés alors que "
                        f"{reason}.", fees, review)
    if terms.delivery_fee is not None and fees - terms.delivery_fee > config.AMOUNT_TOLERANCE:
        return _anomaly(invoice, "", UNDUE_DELIVERY_FEE, f"{fmt_eur(fees)} de frais de livraison au lieu des "
                        f"{fmt_eur(terms.delivery_fee)} prévus.", round(fees - terms.delivery_fee, 2), review)
    return None


def _check_discount(invoice: Invoice, supplier: Supplier, review: bool) -> Anomaly | None:
    rate = supplier.terms.discount_rate
    if not rate:
        return None
    expected = round(invoice.lines_total * rate / 100, 2)
    granted = round(-sum(invoice.remises), 2)
    if expected - granted <= config.AMOUNT_TOLERANCE:
        return None
    return _anomaly(invoice, "", DISCOUNT, f"Remise négociée de {rate:g} % : {fmt_eur(expected)} attendus, "
                    f"{fmt_eur(granted)} accordés.", round(expected - granted, 2), review)


def _duplicates(invoices: list[Invoice]) -> dict[int, Anomaly]:
    """Same supplier, same number: the copy marked duplicata (or the second file) must not be paid again."""
    groups: dict[tuple[str, str], list[Invoice]] = defaultdict(list)
    for invoice in invoices:
        groups[(normalize(invoice.fournisseur), invoice.key)].append(invoice)
    found: dict[int, Anomaly] = {}
    for group in groups.values():
        original, *copies = sorted(group, key=lambda item: (item.duplicata, item.fichier_source))
        for copy in copies:
            same = close(copy.total_ht, original.total_ht)
            outcome = "ne pas la payer deux fois" if same else "avec un montant différent, à vérifier"
            found[id(copy)] = _anomaly(copy, "", DUPLICATE, f"Facture {copy.numero_document} déjà reçue "
                                       f"({original.fichier_source}) : {outcome}.", copy.total_ht, not same)
    return found


def _unit(word: str) -> str:
    word = normalize(word)
    return next((unit for unit, aliases in _UNITS.items() if word in aliases), word)


def _anomaly(invoice: Invoice, code: str, kind: str, description: str, impact: float | None, review: bool) -> Anomaly:
    return Anomaly(invoice.fichier_source, invoice.numero_document, code, kind, description, impact, review)
