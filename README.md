# Verifact

Agent de contrôle des factures fournisseurs (cas pratique Eqolux). Il lit un dossier de PDF sans intervention
manuelle et écrit `lignes.csv`, `anomalies.csv` et `controle_qualite.csv` dans `output/`.

*Réalisé par Achraf Bitar.*

## Livrables

- L'agent : ce dépôt.
- [`output/lignes.csv`](output/lignes.csv) et [`output/anomalies.csv`](output/anomalies.csv), produits sur les six documents du kit,
  avec [`output/controle_qualite.csv`](output/controle_qualite.csv) pour la fiabilité.
- La note : [`Note_Verifact_Achraf_Bitar.pdf`](Note_Verifact_Achraf_Bitar.pdf).

## Installation (une fois, Windows)

```powershell
winget install oschwartz10612.Poppler UB-Mannheim.TesseractOCR
New-Item -ItemType Directory -Force tools\tessdata
Invoke-WebRequest https://github.com/tesseract-ocr/tessdata_best/raw/main/fra.traineddata -OutFile tools\tessdata\fra.traineddata
py -3 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
claude auth login
```

## Lancer sur un nouveau dossier

```powershell
.\.venv\Scripts\python run.py C:\chemin\vers\pdfs
.\.venv\Scripts\streamlit run streamlit_app.py
```

Sans clé, l'agent utilise le Claude CLI local. Avec `ANTHROPIC_API_KEY` dans `.env` (ou le champ de la page),
il passe par l'API Anthropic.

## Fonctionnement

| Étape | Module | Rôle |
| --- | --- | --- |
| Lecture | `agent/reader.py` | Texte natif via Poppler, lignes reconstruites depuis la position des mots ; OCR Tesseract à DPI adaptatif pour les scans |
| Extraction | `agent/extraction.py`, `agent/llm.py` | Claude recopie chaque facture en JSON, nombres en texte ; le code les convertit |
| Fiabilité | `agent/verification.py` | Quantité × prix = montant, total HT, TVA, nombres présents dans le PDF → score de confiance, revue humaine sous 80 % |
| Contrôle | `agent/controls.py`, `agent/referentiel.py` | Prix négociés (colisage converti), franco, remise, prix fermes, hors mercuriale, doublons |
| Sorties | `agent/pipeline.py` | Orchestration et écriture des CSV |

## Hypothèses

- Un duplicata est extrait mais signalé `doublon` ; ses écarts ne sont pas comptés une seconde fois.
- Un relevé produit les lignes de ses factures détaillées ; sa page de synthèse est ignorée.
- Les remises de pied de facture ne sont pas des lignes ; elles sont contrôlées au niveau du document.

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```
