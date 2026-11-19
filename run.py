"""Usage: python run.py [dossier_de_pdf] [--sortie dossier]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent import config
from agent.llm import LlmError
from agent.pipeline import analyse, save
from agent.verification import needs_review


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifact : contrôle des factures fournisseurs.")
    parser.add_argument("dossier", nargs="?", type=Path, default=config.INPUT_DIR)
    parser.add_argument("--sortie", type=Path, default=config.OUTPUT_DIR)
    args = parser.parse_args()

    pdfs = sorted(args.dossier.glob("*.pdf"))
    if not pdfs:
        print(f"Aucun PDF dans {args.dossier}", file=sys.stderr)
        return 1
    try:
        result = analyse(pdfs, on_progress=lambda name: print(f"  lu : {name}"))
    except LlmError as error:
        print(f"Extraction impossible : {error}", file=sys.stderr)
        return 1

    folder = save(result, args.sortie)
    to_review = sum(needs_review(invoice) for invoice in result.invoices)
    print(f"\n{result.engine} : {len(result.invoices)} documents, {sum(len(i.lines) for i in result.invoices)} lignes, "
          f"{len(result.anomalies)} anomalies, {to_review} document(s) à revoir.")
    print(f"CSV écrits dans {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
