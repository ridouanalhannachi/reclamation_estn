"""Scoped department dashboard backed by SQL authorization."""
import streamlit as st
from sqlalchemy import text
from access_control import require_permission, require_claim_access, has_permission, scoped_departments


def list_scoped_claims(engine, user_id, grade_only=False, status=None):
    with engine.connect() as conn:
        depts = scoped_departments(conn, user_id)
        if not depts:
            return []
        rows = conn.execute(text("SELECT * FROM claims ORDER BY id DESC")).mappings().all()
        result = []
        for claim in rows:
            claim = dict(claim)
            if not require_claim_access_safe(conn, user_id, claim):
                continue
            if grade_only and not claim.get("is_grade_complaint"):
                continue
            if status and claim.get("status") != status:
                continue
            result.append(claim)
    return result


def require_claim_access_safe(conn, user_id, claim):
    try:
        require_claim_access(conn, user_id, claim)
        return True
    except PermissionError:
        return False


def attachment_allowed(engine, user_id, claim_id):
    with engine.connect() as conn:
        claim = conn.execute(text("SELECT * FROM claims WHERE id=:id"), {"id": claim_id}).mappings().first()
        if not claim:
            raise LookupError("Réclamation introuvable.")
        require_claim_access(conn, user_id, dict(claim))
    return True


def render_department_dashboard(engine, user_id):
    if not has_permission_sql(engine, user_id, "claims.view_department"):
        st.info("Votre profil ne dispose pas d'un périmètre départemental configuré.")
        return
    grade_only = st.toggle("Réclamations de notes uniquement")
    claims = list_scoped_claims(engine, user_id, grade_only=grade_only)
    st.markdown('<div class="brand">DÉPARTEMENT</div>', unsafe_allow_html=True)
    st.title("Tableau de bord pédagogique")
    c1, c2, c3 = st.columns(3)
    c1.metric("Réclamations accessibles", len(claims))
    c2.metric("Contestations de notes", sum(bool(c.get("is_grade_complaint")) for c in claims))
    c3.metric("En retard", sum(c.get("due_at") is not None and c.get("status") not in ("Résolue", "Acceptée", "Rejetée", "Clôturée") for c in claims))
    for claim in claims[:100]:
        with st.container(border=True):
            st.markdown(f"**#{claim['id']} · {claim['subject']}**")
            st.caption(f"Statut : {claim['status']} · priorité : {claim.get('priority') or 'normale'}")


def has_permission_sql(engine, user_id, permission):
    with engine.connect() as conn:
        return has_permission(conn, user_id, permission)
