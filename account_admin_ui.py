"""Streamlit account and RBAC administration."""
import streamlit as st
from sqlalchemy import text
from access_control import PERMISSIONS, ROLES
from department_management import list_departments, list_programs, create_department, create_program
from account_management import create_account, set_account_permissions
from permission_management import (
    SCOPES, initialize_permission_schema, role_permission_matrix,
    save_role_permission_matrix, restore_role_permissions,
)

SCOPE_LABELS = {
    "global": "Global — établissement",
    "department": "Département",
    "filiere": "Filière",
    "service": "Service administratif",
    "assigned": "Dossiers affectés uniquement",
    "own": "Dossiers personnels uniquement",
}


ROLE_LABELS = {
    "super_admin": "Super administrateur",
    "admin": "Administrateur",
    "department_head": "Chef de département",
    "program_manager": "Responsable de filière",
    "administrative_agent": "Agent administratif",
    "student": "Étudiant",
}

PERMISSION_LABELS = {
    "claims.view_own": "Réclamations — consulter mes propres dossiers",
    "claims.view_department": "Réclamations — consulter les dossiers du département",
    "claims.view_program": "Réclamations — consulter les dossiers de la filière",
    "claims.view_assigned": "Réclamations — consulter les dossiers qui me sont affectés",
    "claims.view_grade_complaints": "Réclamations — consulter les contestations de notes",
    "claims.respond": "Réclamations — répondre aux étudiants",
    "claims.request_information": "Réclamations — demander un complément d'information",
    "claims.assign": "Réclamations — affecter un dossier",
    "claims.change_status": "Réclamations — modifier le statut",
    "students.view": "Étudiants — consulter les dossiers",
    "students.manage": "Étudiants — modifier les informations",
    "users.create": "Comptes — créer des utilisateurs",
    "users.manage": "Comptes — gérer les utilisateurs",
    "permissions.manage": "Habilitations — gérer les permissions",
    "audit.view": "Journal — consulter les actions",
}


def render_accounts(engine, actor_id):
    st.markdown('<div class="brand">COMPTES ET PERMISSIONS</div>', unsafe_allow_html=True)
    st.title("Gestion des comptes")
    departments = list_departments(engine)
    programs = list_programs(engine)

    tab_create, tab_manage, tab_matrix, tab_refs, tab_history = st.tabs([
        "Créer", "Habilitations", "Rôles et permissions", "Référentiels", "Historique"
    ])
    with tab_matrix:
        st.subheader("Matrice des rôles et permissions")
        st.caption("Les paramètres sont appliqués par le backend. L'accès sans privilège explicite est refusé.")
        initialize_permission_schema(engine)
        matrix = role_permission_matrix(engine)
        changes = {}
        for permission in PERMISSIONS:
            row = st.container(border=True)
            with row:
                st.markdown(f"**{PERMISSION_LABELS.get(permission, permission)}**")
                role_values = {}
                columns = st.columns(3)
                for index, role_name in enumerate([r for r in ROLES if r != "student"]):
                    current = matrix.get(role_name, {}).get(permission, {"granted": False, "scope": "global"})
                    with columns[index % 3]:
                        granted = st.checkbox(
                            ROLE_LABELS[role_name],
                            value=bool(current["granted"]),
                            key=f"matrix_{role_name}_{permission}",
                        )
                        scope = st.selectbox(
                            "Périmètre",
                            SCOPES,
                            index=SCOPES.index(current["scope"] if current["scope"] in SCOPES else "global"),
                            format_func=lambda value: SCOPE_LABELS[value],
                            key=f"scope_{role_name}_{permission}",
                            disabled=not granted,
                        )
                        role_values[role_name] = {"granted": granted, "scope": scope}
                changes[permission] = role_values

        preview = sum(bool(value["granted"]) for roles in changes.values() for value in roles.values())
        st.caption(f"Aperçu : {preview} attributions de rôle seront enregistrées.")
        confirm = st.checkbox("Je confirme l'enregistrement de cette matrice")
        if st.button("Enregistrer la matrice", type="primary", disabled=not confirm):
            try:
                save_role_permission_matrix(engine, actor_id, changes)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.success("Matrice enregistrée et sessions d'utilisateurs invalidées.")
                st.rerun()
        if st.button("Restaurer les permissions par défaut", type="secondary"):
            try:
                restore_role_permissions(engine, actor_id, confirmed=confirm)
            except PermissionError as exc:
                st.error(str(exc))
            else:
                st.success("Permissions par défaut restaurées. Les affectations ont été conservées.")
                st.rerun()

    with tab_refs:
        st.subheader("Départements")
        col1, col2 = st.columns([4, 2])
        dept_name = col1.text_input("Nom du département")
        dept_code = col2.text_input("Code")
        if st.button("Ajouter le département"):
            try: create_department(engine, actor_id, dept_name, dept_code)
            except (ValueError, PermissionError) as exc: st.error(str(exc))
            else: st.success("Département enregistré."); st.rerun()
        st.subheader("Filières")
        if departments:
            dept = st.selectbox("Département", [d["id"] for d in departments], format_func=lambda i: next(d["name"] for d in departments if d["id"] == i))
            name = st.text_input("Nom de la filière")
            code = st.text_input("Code de la filière")
            level = st.text_input("Niveau")
            if st.button("Ajouter la filière"):
                try: create_program(engine, actor_id, dept, name, code, level)
                except (ValueError, PermissionError) as exc: st.error(str(exc))
                else: st.success("Filière enregistrée."); st.rerun()

    with tab_create:
        st.subheader("Nouveau compte responsable ou administratif")
        with st.form("create_account_v17"):
            first = st.text_input("Prénom")
            last = st.text_input("Nom")
            email = st.text_input("Adresse e-mail")
            role_codes = [r for r in ROLES if r != "student"]
            role = st.selectbox("Rôle", role_codes, format_func=lambda value: ROLE_LABELS[value])
            dept_ids = st.multiselect("Départements", [d["id"] for d in departments], format_func=lambda i: next(d["name"] for d in departments if d["id"] == i)) if departments else []
            program_ids = st.multiselect("Filières", [p["id"] for p in programs], format_func=lambda i: next(p["name"] for p in programs if p["id"] == i)) if programs else []
            extra = st.multiselect(
                "Permissions individuelles supplémentaires",
                PERMISSIONS,
                format_func=lambda value: PERMISSION_LABELS.get(value, value),
            )
            submit = st.form_submit_button("Créer le compte", type="primary")
        if submit:
            try:
                user_id, temporary = create_account(engine, actor_id, first, last, email, role, dept_ids, program_ids, extra, return_temp_password=True)
            except (ValueError, PermissionError, LookupError) as exc:
                st.error(str(exc))
            else:
                st.success(f"Compte créé (ID {user_id}). Communiquez ce mot de passe temporaire hors de l'application.")
                st.code(temporary, language="text")

    with tab_manage:
        st.subheader("Modifier les habilitations")
        with engine.connect() as conn:
            users = conn.execute(text("SELECT id, first_name, last_name, email, role, is_active FROM users WHERE role IN ('super_admin','admin','department_head','program_manager','administrative_agent') ORDER BY last_name")).mappings().all()
        if users:
            selected = st.selectbox(
                "Compte",
                [u["id"] for u in users],
                format_func=lambda i: (
                    f'{next(u for u in users if u["id"] == i)["first_name"]} '
                    f'{next(u for u in users if u["id"] == i)["last_name"]} — '
                    f'{ROLE_LABELS.get(next(u for u in users if u["id"] == i)["role"], "Rôle inconnu")}'
                ),
            )
            user = next(u for u in users if u["id"] == selected)
            manageable_roles = [r for r in ROLES[:-1]]
            role = st.selectbox(
                "Rôle du compte",
                manageable_roles,
                index=max(0, manageable_roles.index(user["role"])) if user["role"] in manageable_roles else 0,
                format_func=lambda value: ROLE_LABELS[value],
            )
            active = st.toggle("Compte actif", value=bool(user["is_active"]))
            dept_ids = st.multiselect("Départements autorisés", [d["id"] for d in departments], format_func=lambda i: next(d["name"] for d in departments if d["id"] == i))
            program_ids = st.multiselect("Filières autorisées", [p["id"] for p in programs], format_func=lambda i: next(p["name"] for p in programs if p["id"] == i))
            grants = st.multiselect(
                "Permissions accordées",
                PERMISSIONS,
                format_func=lambda value: PERMISSION_LABELS.get(value, value),
            )
            revokes = st.multiselect(
                "Permissions retirées",
                PERMISSIONS,
                format_func=lambda value: PERMISSION_LABELS.get(value, value),
            )
            if st.button("Enregistrer les habilitations", type="primary"):
                try: set_account_permissions(engine, actor_id, selected, role, dept_ids, program_ids, grants, revokes, active)
                except (ValueError, PermissionError, LookupError) as exc: st.error(str(exc))
                else: st.success("Habilitations enregistrées et version de session incrémentée."); st.rerun()

    with tab_history:
        st.subheader("Historique des modifications")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, actor_id, target_user_id, action, permission, note, created_at FROM account_permission_history ORDER BY id DESC LIMIT 200")).mappings().all()
        for row in rows:
            with st.container(border=True):
                st.caption(f"#{row['id']} · {row['created_at']} · acteur {row['actor_id']} → compte {row['target_user_id']}")
                if row["permission"]:
                    st.caption(f"Permission : {PERMISSION_LABELS.get(row['permission'], row['permission'])}")
                st.write(row["note"])
