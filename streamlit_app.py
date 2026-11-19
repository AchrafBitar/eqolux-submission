from pathlib import Path

import streamlit as st

from agent import config
from agent.llm import LlmError
from agent.pipeline import analyse, anomalies_table, lines_table, quality_table, save
from agent.utils import fmt_eur

st.set_page_config(page_title="Verifact", page_icon=":material/receipt_long:", layout="wide")
st.title("Verifact")
st.caption("Contrôle des factures fournisseurs avant paiement.")

with st.form("analyse"):
    folder = st.text_input("Dossier de PDF", value=str(config.INPUT_DIR))
    uploads = st.file_uploader("Ou déposez des PDF", type="pdf", accept_multiple_files=True)
    api_key = st.text_input("Clé API Anthropic (facultative)", type="password",
                            help="Sans clé, l'agent utilise le Claude CLI installé sur ce poste.")
    submitted = st.form_submit_button("Analyser", type="primary", icon=":material/play_arrow:")

if submitted:
    pdfs = sorted(Path(folder).glob("*.pdf"))
    if uploads:
        upload_dir = config.CACHE_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        pdfs = [upload_dir / upload.name for upload in uploads]
        for path, upload in zip(pdfs, uploads, strict=True):
            path.write_bytes(upload.getvalue())
    if not pdfs:
        st.error("Aucun PDF à analyser.")
    else:
        try:
            with st.status("Analyse des factures…") as status:
                result = analyse(pdfs, api_key, on_progress=lambda name: status.write(f"{name} lu"))
                status.update(label=f"Analyse terminée · {result.engine}", state="complete")
            save(result)
            st.session_state.result = result
        except LlmError as error:
            st.error(str(error), title="Extraction impossible")

result = st.session_state.get("result")
if result:
    lines = lines_table(result.invoices)
    anomalies = anomalies_table(result.anomalies)
    quality = quality_table(result.invoices)
    impact = sum(anomaly.impact_eur or 0.0 for anomaly in result.anomalies)

    with st.container(horizontal=True):
        st.metric("Documents", len(result.invoices), border=True)
        st.metric("Lignes", len(lines), border=True)
        st.metric("Anomalies", len(anomalies), border=True)
        st.metric("Montant en jeu", fmt_eur(impact), border=True)
        st.metric("Revue humaine", int((anomalies["revue_humaine"] == "oui").sum()), border=True)

    st.subheader("Anomalies")
    st.dataframe(anomalies, hide_index=True, alt="Anomalies détectées",
                 column_config={"description": st.column_config.TextColumn(width="large")})
    st.download_button("anomalies.csv", anomalies.to_csv(index=False), "anomalies.csv", icon=":material/download:")

    st.subheader("Lignes facturées")
    st.dataframe(lines, hide_index=True, alt="Lignes facturées")
    st.download_button("lignes.csv", lines.to_csv(index=False), "lignes.csv", icon=":material/download:")

    st.subheader("Contrôle qualité")
    confidence = st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="percent")
    st.dataframe(quality, hide_index=True, alt="Vérifications par document", column_config={"confiance": confidence})

st.divider()
st.caption("Réalisé par Achraf Bitar")
