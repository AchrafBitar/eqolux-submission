from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent import config
from agent.llm import LlmClient
from agent.models import Document, Invoice, Line
from agent.reader import render_page
from agent.utils import parse_number

_PROMPTS = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (_PROMPTS / "extraction.md").read_text(encoding="utf-8")
SCHEMA = json.loads((_PROMPTS / "schema.json").read_text(encoding="utf-8"))
_RAW_NUMBERS = ("quantite", "prix_unitaire_ht", "montant_ht")


def extract_invoices(document: Document, client: LlmClient, suppliers: list[str]) -> list[Invoice]:
    """The model only transcribes the document; numbers stay text until parse_number reads them."""
    images = [_page_image(document, page.number) for page in document.pages if page.scanned]
    payload = _ask_cached(client, _prompt(document, suppliers), images)
    return [_to_invoice(document, item) for item in payload["documents"]]


def _prompt(document: Document, suppliers: list[str]) -> str:
    parts = [f"Fichier : {document.name}", f"Fournisseurs connus : {', '.join(suppliers)}"]
    for page in document.pages:
        source = f"OCR à {page.dpi} DPI, image jointe" if page.scanned else "texte natif"
        parts.append(f"\n=== Page {page.number} ({source}) ===\n{page.text}")
    return "\n".join(parts)


def _page_image(document: Document, number: int) -> Path:
    digest = hashlib.sha256(document.path.read_bytes()).hexdigest()[:16]
    path = config.CACHE_DIR / "pages" / f"{digest}_p{number}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        render_page(document.path, number, config.VISION_DPI).save(path)
    return path


def _ask_cached(client: LlmClient, prompt: str, images: list[Path]) -> dict[str, Any]:
    """Same document, same model: reuse the answer instead of paying for it twice."""
    digest = hashlib.sha256("\n".join([client.name, SYSTEM_PROMPT, json.dumps(SCHEMA), prompt]).encode())
    for image in images:
        digest.update(image.read_bytes())
    path = config.CACHE_DIR / "llm" / f"{digest.hexdigest()[:32]}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    payload = client.ask(SYSTEM_PROMPT, prompt, SCHEMA, images)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def _to_invoice(document: Document, item: dict[str, Any]) -> Invoice:
    lines = [
        Line(
            code_article=line["code_article"].strip(),
            designation=" ".join(line["designation"].split()),
            quantite=parse_number(line["quantite"]),
            unite=line["unite"].strip(),
            prix_unitaire_ht=parse_number(line["prix_unitaire_ht"]),
            montant_ht=parse_number(line["montant_ht"]),
            taux_tva=parse_number(line["taux_tva"]),
            raw={key: line[key] for key in _RAW_NUMBERS},
        )
        for line in item["lignes"]
    ]
    rates = {line.taux_tva for line in lines if line.taux_tva is not None}
    return Invoice(
        fichier_source=document.name,
        fournisseur=item["fournisseur"].strip(),
        type_document=item["type_document"],
        numero_document=item["numero_document"].strip(),
        date_document=_iso_date(item["date_document"]),
        lines=lines,
        total_ht=parse_number(item["total_ht"]),
        total_tva=parse_number(item["total_tva"]),
        taux_tva=rates.pop() if len(rates) == 1 else None,
        remises=[-abs(amount) for remise in item["remises"] if (amount := parse_number(remise["montant"]))],
        duplicata=item["duplicata"],
        scanned=document.scanned,
    )


def _iso_date(value: str) -> str:
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            continue
    return ""
