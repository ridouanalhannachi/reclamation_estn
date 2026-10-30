"""Application Streamlit Cloud pour Réclamations ESTN.

Point d'entrée : streamlit_app.py. Définir DATABASE_URL et les secrets de
bootstrap dans la console Streamlit Cloud, jamais dans le dépôt GitHub.
"""
import os
import time
from security_controls import session_expired, audit_event
from collections import Counter

import streamlit as st
from student_admin_ui import render_import, render_students
from administrative_ui import render_administrative_dashboard, render_administrative_actions
from administrative_services import request_complement, respond_complement
from account_admin_ui import ROLE_LABELS, render_accounts
from department_dashboard import render_department_dashboard
from access_control import has_permission

from cloud_db_diagnostics import DatabaseSetupError, safe_database_diagnostic
from streamlit_store import (
    CATEGORIES, STATUSES, add_claim, authenticate, bootstrap_admin,
    change_password, claim_detail, get_attachment, get_user, import_students,
    initialize, list_claims, make_engine, respond,
    preview_students, list_students, update_student,
)

st.set_page_config(
    page_title="Réclamations ESTN",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
:root { --estn-blue: #2457d6; --estn-navy: #142d52; --estn-bg: #f7f9fc; }
.stApp { background: radial-gradient(ellipse at 80% 0%, #e9f2ff, transparent 26%), #f7f9fc; }
[data-testid="stHeader"] { background: rgba(247,249,252,.8); }
[data-testid="stSidebar"] { background: #edf3fc; border-right: 1px solid #dae6f6; }
h1, h2, h3 { color: #142d52; letter-spacing: -.025em; }
div[data-testid="stMetric"] {
  background: white; border: 1px solid #e4ebf7; border-radius: 16px;
  padding: 20px 22px; box-shadow: 0 8px 28px rgba(20,45,82,.05);
}
div[data-testid="stMetricValue"] { color: #142d52; font-weight: 750; }
div[data-testid="stForm"], div[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 16px;
}
.stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] {
  background: #2457d6; border-radius: 10px; border: 0;
}
.brand { font-size: 13px; font-weight: 800; letter-spacing: .12em; color: #2457d6; margin: 0 0 7px; }
.subdued { font-size: 15px; color: #64758d; margin: 2px 0 22px; }
.card-label { font-size: 12px; color: #64758d; text-transform: uppercase; letter-spacing: .08em; }
</style>
""", unsafe_allow_html=True)


def configuration(name):
    value = os.getenv(name)
    if value:
        return value
    try:
        return str(st.secrets.get(name, ""))
    except Exception:  # Aucun secrets.toml local : la configuration vient de l'environnement.
        return ""


@st.cache_resource(show_spinner=False)
def database_engine(database_url):
    try:
        engine = make_engine(database_url)
    except Exception as exc:
        raise DatabaseSetupError("URL de connexion") from exc
    try:
        initialize(engine)
    except Exception as exc:
        engine.dispose()
        raise DatabaseSetupError("Connexion et création des tables") from exc
    return engine


def display_setup_error(exc, phase):
    code, message = safe_database_diagnostic(exc)
    st.error(f"Impossible de préparer la base PostgreSQL ({code}).")
    st.warning(message)
    st.caption(
        f"Étape concernée : {phase}. "
        "Vérifiez Settings → Secrets dans Streamlit Cloud. "
        "Ne publiez jamais DATABASE_URL ou vos mots de passe."
    )


drive_token = configuration("GDRIVE_TOKEN_B64")
if drive_token:
    # Prefer Google Drive when its secret is provided: PostgreSQL is optional.
    from drive_store import (
        CATEGORIES, STATUSES, add_claim, authenticate, bootstrap_admin,
        change_password, claim_detail, get_attachment, get_user, import_students,
        list_claims, respond, preview_students, list_students, update_student,
    )
    from drive_store import make_engine as make_drive_store

    @st.cache_resource(show_spinner=False)
    def drive_engine(token):
        return make_drive_store(token)

    try:
        engine = drive_engine(drive_token)
    except Exception:
        st.error(
            "Connexion Google Drive impossible (GDRIVE-AUTH). "
            "Un nouveau jeton OAuth avec le scope drive.appdata est nécessaire. "
            "Vérifiez aussi l'API Google Drive et l'accès au compte."
        )
        st.caption(
            "L'ancien GDRIVE_TOKEN_B64 ne doit pas être réutilisé. "
            "Ne partagez jamais la valeur du jeton."
        )
        st.stop()
else:
    database_url = configuration("DATABASE_URL")
    if not database_url:
        st.error(
            "Aucun stockage configuré. Ajoutez un nouveau GDRIVE_TOKEN_B64 "
            "dans les secrets Streamlit Cloud, ou une URL PostgreSQL DATABASE_URL."
        )
        st.stop()

    if not database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        if os.getenv("ESTN_ALLOW_LOCAL_SQLITE") != "1" or not database_url.startswith("sqlite:///"):
            st.error("Base incompatible avec Streamlit Cloud : PostgreSQL requis pour ce mode.")
            st.stop()

    try:
        engine = database_engine(database_url)
    except DatabaseSetupError as exc:
        display_setup_error(exc.__cause__, exc.phase)
        st.stop()
    except Exception as exc:
        display_setup_error(exc, "Initialisation de la connexion")
        st.stop()

initial_email = configuration("BOOTSTRAP_ADMIN_EMAIL")
initial_password = configuration("BOOTSTRAP_ADMIN_PASSWORD")
if bool(initial_email) != bool(initial_password):
    st.error(
        "Configuration administrateur incomplète : fournissez les deux secrets "
        "BOOTSTRAP_ADMIN_EMAIL et BOOTSTRAP_ADMIN_PASSWORD, ou aucun si le compte existe déjà."
    )
    st.stop()
if initial_email and initial_password:
    try:
        bootstrap_admin(engine, initial_email, initial_password)
    except Exception as exc:
        display_setup_error(exc, "Initialisation du compte administrateur")
        st.stop()


def can_actor_permission(permission):
    if isinstance(engine, type(None)) or hasattr(engine, "read_appdata"):
        return False
    with engine.connect() as conn:
        return has_permission(conn, user_id, permission)


def reset_session():
    st.session_state.pop("uid", None)
    st.session_state.pop("auth_at", None)
    st.session_state.pop("last_seen", None)
    st.session_state.pop("permission_version", None)
    st.session_state.pop("csv_credentials", None)
    st.session_state.pop("selected_claim", None)


def display_login():
    st.markdown('<div class="brand">PORTAIL UNIVERSITAIRE · EST NADOR</div>', unsafe_allow_html=True)
    st.title("Réclamations Université")
    st.markdown('<p class="subdued">Connectez-vous pour déposer, suivre et traiter vos demandes en toute confidentialité.</p>', unsafe_allow_html=True)
    left, center, right = st.columns([1, 1.3, 1])
    with center:
        with st.container(border=True):
            st.subheader("Connexion à votre espace")
            with st.form("login", clear_on_submit=False):
                email = st.text_input("Adresse e-mail", max_chars=200)
                password = st.text_input("Mot de passe", type="password")
                submit = st.form_submit_button("Se connecter", type="primary", use_container_width=True)
            if submit:
                user = authenticate(engine, email, password)
                if user is None:
                    st.error("Identifiants incorrects.")
                else:
                    reset_session()
                    st.session_state["uid"] = user["id"]
                    st.session_state["permission_version"] = user.get("permission_version", 0)
                    st.session_state["auth_at"] = st.session_state["last_seen"] = time.time()
                    st.rerun()
            st.caption("Vous n'avez pas de compte ? Contactez l'administration de l'établissement.")
    st.stop()


user_id = st.session_state.get("uid")
if user_id:
    if session_expired(st.session_state.get("auth_at"), st.session_state.get("last_seen")):
        audit_event("session_expired", user_id)
        reset_session()
        st.warning("Session expirée. Veuillez vous reconnecter.")
        st.stop()
    st.session_state["last_seen"] = time.time()
else:
    display_login()

try:
    user = get_user(engine, user_id)
except Exception:
    reset_session()
    st.error("Connexion interrompue. Actualisez la page pour vous reconnecter.")
    st.stop()

if not user:
    reset_session()
    st.rerun()
if st.session_state.get("permission_version") != user.get("permission_version", 0):
    audit_event("session_permissions_revoked", user_id)
    reset_session()
    st.warning("Vos permissions ont changé. Veuillez vous reconnecter.")
    st.stop()


def display_password_change():
    st.markdown('<div class="brand">SÉCURITÉ DU COMPTE</div>', unsafe_allow_html=True)
    st.title("Définir un nouveau mot de passe")
    st.info("Vous devez remplacer votre mot de passe temporaire avant d'accéder à vos réclamations.")
    with st.form("password_change"):
        current = st.text_input("Mot de passe actuel", type="password")
        new = st.text_input("Nouveau mot de passe (12 caractères minimum)", type="password")
        confirm = st.text_input("Confirmer le nouveau mot de passe", type="password")
        save = st.form_submit_button("Enregistrer", type="primary")
    if save:
        if new != confirm:
            st.error("Les deux mots de passe ne correspondent pas.")
        else:
            try:
                change_password(engine, user["id"], current, new)
            except ValueError as exc:
                st.error(str(exc))
            else:
                audit_event("password_changed", user_id)
                reset_session()
                st.success("Mot de passe modifié. Reconnectez-vous.")
                st.rerun()
    if st.button("Se déconnecter"):
        reset_session()
        st.rerun()
    st.stop()


if user["password_change_required"]:
    display_password_change()

with st.sidebar:
    st.markdown("### 🎓 Réclamations ESTN")
    st.caption(f"{user['first_name']} {user['last_name']}")
    st.caption(ROLE_LABELS.get(user["role"], "Rôle utilisateur") if user["role"] != "student" else "Espace étudiant")
    st.divider()
    privileged = user["role"] in ("admin", "super_admin", "department_head", "program_manager", "administrative_agent")
    if user["role"] in ("admin", "super_admin"):
        items = ["Tableau de bord", "Traitement administratif", "Ajouter des étudiants", "Gestion des étudiants", "Comptes et permissions", "Mon compte"]
        page = st.radio("Navigation", items)
    elif privileged:
        page = st.radio("Navigation", ["Département / filière", "Traitement administratif", "Mon compte"])
    else:
        page = st.radio("Navigation", ["Mes réclamations", "Nouvelle réclamation", "Mon compte"])
    st.divider()
    if st.button("Se déconnecter", use_container_width=True):
        reset_session()
        st.rerun()


def render_metrics(claims):
    counts = Counter(c["status"] for c in claims)
    cols = st.columns(4)
    labels = [
        ("Total", len(claims)),
        ("Soumises", counts["Soumise"]),
        ("En cours", counts["En cours"]),
        ("Traitées", sum(counts[k] for k in ("Acceptée", "Rejetée", "Clôturée"))),
    ]
    for col, (label, value) in zip(cols, labels):
        with col:
            st.metric(label, value)


def render_list(claims, admin=False):
    if not claims:
        st.info("Aucune réclamation correspondant aux critères.")
        return
    st.caption(f"{len(claims)} demande(s), affichage des 100 plus récentes.")
    for item in claims[:100]:
        with st.container(border=True):
            first, second, third = st.columns([5, 2, 1.2], vertical_alignment="center")
            with first:
                st.markdown(f"**#{item['id']} · {item['subject']}**")
                st.caption(
                    (item["student_name"] + " · " if admin else "")
                    + item["category"] + " · " + item["created_at"]
                )
            with second:
                st.write(item["status"])
            with third:
                if st.button("Traiter" if admin else "Voir", key=f"view_{item['id']}"):
                    st.session_state["selected_claim"] = item["id"]
                    st.rerun()


def render_detail():
    claim_id = st.session_state.get("selected_claim")
    if not claim_id:
        return False
    try:
        c = claim_detail(engine, user_id, claim_id)
    except (LookupError, PermissionError):
        st.session_state.pop("selected_claim", None)
        st.error("Cette réclamation est introuvable ou inaccessible.")
        return False
    if st.button("← Retour à la liste"):
        st.session_state.pop("selected_claim", None)
        st.rerun()
    st.markdown('<div class="brand">DÉTAIL DE LA RÉCLAMATION</div>', unsafe_allow_html=True)
    st.title(c["subject"])
    st.caption(f"#{c['id']} · {c['category']} · {c['created_at']}")
    st.info(f"Statut actuel : {c['status']}")
    if user["role"] != "student":
        st.write(f"**Étudiant :** {c['student_name']} · {c['program']}")
    with st.container(border=True):
        st.subheader("Description")
        st.text(c["description"])
        if c["has_attachment"]:
            try:
                attached = get_attachment(engine, user_id, claim_id)
                if attached:
                    name, mime, payload = attached
                    st.download_button("Télécharger la pièce jointe", payload,
                                       file_name=name, mime=mime, key=f"download_{claim_id}")
            except (PermissionError, LookupError):
                st.error("Pièce jointe indisponible.")
    with st.container(border=True):
        st.subheader("Réponse de l'administration")
        st.write(c["admin_response"] or "Aucune réponse pour le moment.")
    if user["role"] == "student" and c["status"] == "En attente de complément":
        with st.form(f"complement_{claim_id}"):
            st.subheader("Complément demandé")
            response_text = st.text_area("Votre réponse", max_chars=5000)
            complement_file = st.file_uploader("Document facultatif", type=["pdf", "png", "jpg", "jpeg"])
            send = st.form_submit_button("Envoyer le complément", type="primary")
        if send:
            try:
                mime = complement_file.type if complement_file else None
                respond_complement(engine, user_id, claim_id, response_text,
                                   complement_file.name if complement_file else None, mime,
                                   complement_file.getvalue() if complement_file else None)
            except (ValueError, PermissionError, LookupError) as exc:
                st.error(str(exc))
            else:
                st.success("Complément envoyé. La réclamation est retournée au traitement.")
                st.rerun()
    if can_actor_permission("claims.change_status"):
        render_administrative_actions(engine, user_id, claim_id, c["status"])
        with st.form(f"respond_{claim_id}"):
            st.subheader("Réponse générale")
            status = st.selectbox("Nouveau statut", STATUSES, index=STATUSES.index(c["status"]))
            text = st.text_area("Réponse / justification", value=c["admin_response"], max_chars=5000, height=130)
            submit = st.form_submit_button("Enregistrer la décision", type="primary")
        if submit:
            try:
                respond(engine, user_id, claim_id, status, text)
            except (ValueError, PermissionError, LookupError) as exc:
                st.error(str(exc))
            else:
                st.success("Décision enregistrée.")
                st.rerun()
    st.subheader("Historique")
    for event in c["history"]:
        with st.container(border=True):
            st.markdown(f"**{event['action']}**")
            st.caption(f"Par {event['by']} · {event['at']}")
            if event["note"]:
                st.write(event["note"])
    return True


if page == "Mon compte":
    st.markdown('<div class="brand">MON COMPTE</div>', unsafe_allow_html=True)
    st.title("Sécurité du compte")
    st.write(f"Connecté : **{user['email']}**")
    with st.form("manual_change"):
        current_password = st.text_input("Mot de passe actuel", type="password")
        new_password = st.text_input("Nouveau mot de passe", type="password")
        verify_password = st.text_input("Confirmer", type="password")
        submit = st.form_submit_button("Changer mon mot de passe", type="primary")
    if submit:
        if new_password != verify_password:
            st.error("La confirmation est différente.")
        else:
            try:
                change_password(engine, user_id, current_password, new_password)
            except ValueError as exc:
                st.error(str(exc))
            else:
                audit_event("password_changed", user_id)
                reset_session()
                st.success("Mot de passe changé. Reconnectez-vous.")
                st.rerun()
    st.stop()

if user["role"] == "student":
    if page == "Nouvelle réclamation":
        st.markdown('<div class="brand">ESPACE ÉTUDIANT</div>', unsafe_allow_html=True)
        st.title("Nouvelle réclamation")
        st.caption("Les informations fournies seront accessibles aux agents administrateurs autorisés.")
        with st.form("new_claim"):
            category = st.selectbox("Catégorie", CATEGORIES)
            subject = st.text_input("Objet", max_chars=150)
            description = st.text_area("Description détaillée", max_chars=5000, height=170)
            file = st.file_uploader("Pièce jointe facultative (PDF, PNG, JPEG · 5 Mo max)",
                                    type=["pdf", "png", "jpg", "jpeg"])
            submit = st.form_submit_button("Soumettre la réclamation", type="primary")
        if submit:
            try:
                claim_id = add_claim(engine, user_id, category, subject, description,
                                     file.name if file else None,
                                     file.getvalue() if file else None)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Votre réclamation #{claim_id} a été enregistrée. Consultez-la dans Mes réclamations.")
    else:
        if not render_detail():
            st.markdown('<div class="brand">ESPACE ÉTUDIANT</div>', unsafe_allow_html=True)
            st.title("Mes réclamations")
            st.markdown('<p class="subdued">Retrouvez le suivi de vos demandes et les réponses de l’administration.</p>', unsafe_allow_html=True)
            claims = list_claims(engine, user_id)
            render_metrics(claims)
            st.subheader("Historique des demandes")
            render_list(claims)
else:
    if page == "Comptes et permissions":
        render_accounts(engine, user_id)
    elif page == "Département / filière":
        render_department_dashboard(engine, user_id)
    elif page == "Ajouter des étudiants":
        render_import(engine, user_id, import_students, preview_students)
    elif page == "Gestion des étudiants":
        render_students(engine, user_id, list_students, update_student)
    elif page == "Traitement administratif":
        render_administrative_dashboard(engine, user_id, [], respond)
    else:
        if not render_detail():
            st.markdown('<div class="brand">ADMINISTRATION GÉNÉRALE</div>', unsafe_allow_html=True)
            st.title("Tableau de bord administrateur")
            st.markdown('<p class="subdued">Pilotage et suivi des réclamations des étudiants.</p>', unsafe_allow_html=True)
            all_claims = list_claims(engine, user_id)
            render_metrics(all_claims)
            st.subheader("Liste des réclamations")
            first, second = st.columns(2)
            with first:
                selected_category = st.selectbox("Filtrer par catégorie", ("Tous les types",) + CATEGORIES)
            with second:
                selected_status = st.selectbox("Filtrer par statut", ("Tous les statuts",) + STATUSES)
            filtered = [
                c for c in all_claims
                if (selected_category == "Tous les types" or c["category"] == selected_category)
                and (selected_status == "Tous les statuts" or c["status"] == selected_status)
            ]
            render_list(filtered, admin=True)
