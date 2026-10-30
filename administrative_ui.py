"""Streamlit components for the v1.6 administrative workflow."""
import streamlit as st
from sqlalchemy import text
from administrative_services import (
    services, administrative_agents, administrative_dashboard, assign_claim,
    transition_status, request_complement,
)
from administrative_workflow import STATUSES_V16


def _is_sql_engine(engine):
    return hasattr(engine, "connect") and hasattr(engine, "dialect")


def render_administrative_dashboard(engine, admin_id, all_claims, respond):
    st.markdown('<div class="brand">TRAITEMENT ADMINISTRATIF</div>', unsafe_allow_html=True)
    st.title("Pilotage opérationnel")
    if not _is_sql_engine(engine):
        st.warning("Le traitement v1.6 est disponible pour SQLAlchemy (SQLite/PostgreSQL). Google Drive reste limité à son historique append-only et n'est pas déclaré compatible avec les transactions administratives structurées.")
        return
    dashboard = administrative_dashboard(engine)
    cols = st.columns(4)
    for col, (label, value) in zip(cols, (("Total", dashboard["total"]), ("Urgentes", dashboard["urgent"]), ("En retard", dashboard["overdue"]), ("Échéance proche", dashboard["near_due"]))):
        col.metric(label, value)

    st.subheader("Réclamations")
    service_rows = services(engine)
    agent_rows = administrative_agents(engine)
    service_names = {s["id"]: s["name"] for s in service_rows}
    agent_names = {a["id"]: f'{a["first_name"]} {a["last_name"]}' for a in agent_rows}
    c1, c2, c3 = st.columns(3)
    service_filter = c1.selectbox("Service", ("Tous",) + tuple(service_names.values()))
    status_filter = c2.selectbox("Statut", ("Tous",) + STATUSES_V16)
    priority_filter = c3.selectbox("Priorité", ("Toutes", "basse", "normale", "haute", "urgente"))
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, subject, status, service_id, assigned_agent_id, priority, due_at, created_at FROM claims ORDER BY id DESC LIMIT 100")).mappings().all()
    filtered = []
    for row in rows:
        item = dict(row)
        if service_filter != "Tous" and service_names.get(item["service_id"]) != service_filter:
            continue
        if status_filter != "Tous" and item["status"] != status_filter:
            continue
        if priority_filter != "Toutes" and item["priority"] != priority_filter:
            continue
        filtered.append(item)
    for item in filtered:
        with st.container(border=True):
            st.markdown(f"**#{item['id']} · {item['subject']}**")
            st.caption(f"{item['status']} · Service : {service_names.get(item['service_id'], 'Non affectée')} · Responsable : {agent_names.get(item['assigned_agent_id'], 'Non affecté')}")
            key = f"claim_{item['id']}"
            if st.button("Afficher", key=f"show_{key}"):
                st.session_state["selected_claim"] = item["id"]
                st.rerun()


def render_administrative_actions(engine, admin_id, claim_id, claim_status, due_at=None):
    """Compact SQL-only assignment and transition controls."""
    if not _is_sql_engine(engine):
        st.caption("Actions administratives avancées non disponibles avec Google Drive.")
        return
    st.subheader("Affectation administrative")
    service_rows = services(engine)
    agent_rows = administrative_agents(engine)
    if service_rows and agent_rows:
        service = st.selectbox("Service responsable", [s["id"] for s in service_rows],
                               format_func=lambda i: next(s["name"] for s in service_rows if s["id"] == i), key=f"service_{claim_id}")
        agent = st.selectbox("Agent responsable", [a["id"] for a in agent_rows],
                             format_func=lambda i: f'{next(a for a in agent_rows if a["id"] == i)["first_name"]} {next(a for a in agent_rows if a["id"] == i)["last_name"]}', key=f"agent_{claim_id}")
        priority = st.selectbox("Priorité", ("basse", "normale", "haute", "urgente"), index=1, key=f"priority_{claim_id}")
        due_text = st.text_input("Date limite (AAAA-MM-JJ)", value=(due_at or "")[:10], key=f"due_{claim_id}")
        reason = st.text_area("Motif", key=f"reason_{claim_id}")
        if st.button("Affecter / réaffecter", key=f"assign_{claim_id}"):
            try:
                due_iso = due_text.strip() + " 00:00:00 UTC" if due_text.strip() else None
                assign_claim(engine, admin_id, claim_id, service, agent, priority, due_iso, reason)
            except (ValueError, LookupError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.success("Affectation enregistrée dans l'historique.")
                st.rerun()

    st.subheader("Demander un complément")
    complement_request = st.text_area("Informations demandées", key=f"complement_request_{claim_id}")
    if st.button("Mettre en attente de complément", key=f"request_complement_{claim_id}"):
        try:
            request_complement(engine, admin_id, claim_id, complement_request)
        except (ValueError, LookupError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.success("Complément demandé et historique enregistré.")
            st.rerun()

    st.subheader("Changer le statut")
    allowed = [claim_status] if claim_status in STATUSES_V16 else ["Soumise"] + list(STATUSES_V16)
    new_status = st.selectbox("Nouveau statut", allowed[1:] if allowed[0] == "Soumise" else allowed, key=f"status_{claim_id}")
    justification = st.text_area("Justification", key=f"status_reason_{claim_id}")
    if st.button("Appliquer la transition", key=f"transition_{claim_id}"):
        try:
            transition_status(engine, admin_id, claim_id, new_status, justification)
        except (ValueError, LookupError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.success("Transition enregistrée.")
            st.rerun()
