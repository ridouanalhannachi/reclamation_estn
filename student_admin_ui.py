"""Pages administrateur Streamlit; aucune mutation sans action explicite."""
import streamlit as st


def render_import(engine, admin_id, importer, previewer):
    st.markdown('<div class="brand">ADMINISTRATION GÉNÉRALE</div>', unsafe_allow_html=True)
    st.title("Importer des étudiants")
    st.caption("Aucune ligne n'est créée lors de la prévisualisation.")
    st.code("num_apogee,nom_etudiant,prenom,email,filiere,niveau", language="text")
    upload = st.file_uploader("CSV UTF-8 (maximum 1000 lignes)", type=["csv"])
    if upload:
        try:
            content = upload.getvalue().decode("utf-8-sig")
            preview = previewer(engine, admin_id, content)
        except (ValueError, UnicodeError, PermissionError) as exc:
            st.error(f"Prévisualisation impossible : {exc}")
        else:
            st.dataframe(preview["rows"], hide_index=True, use_container_width=True)
            if preview["issues"]:
                for issue in preview["issues"][:30]:
                    st.error(issue)
                if len(preview["issues"]) > 30:
                    st.warning(f"{len(preview['issues']) - 30} autres erreurs à corriger.")
            else:
                st.success(f"{len(preview['rows'])} étudiants validés. Aucun doublon détecté.")
            if st.button("Confirmer l'importation", type="primary", disabled=bool(preview["issues"])):
                try:
                    export = importer(engine, admin_id, content)
                except (ValueError, PermissionError) as exc:
                    st.error(f"Import annulé : {exc}")
                else:
                    st.session_state["csv_credentials"] = export
                    st.success("Import effectué. Téléchargez les identifiants provisoires.")
    if st.session_state.get("csv_credentials"):
        st.download_button("Télécharger les identifiants provisoires (confidentiel)",
                           st.session_state["csv_credentials"],
                           file_name="identifiants-etudiants.csv", mime="text/csv")
        st.caption("Ne transmettez jamais ces secrets par GitHub, capture ou conversation.")
        if st.button("Effacer le CSV confidentiel de cette session"):
            st.session_state.pop("csv_credentials", None)
            st.rerun()


def render_students(engine, admin_id, lister, updater):
    st.markdown('<div class="brand">ADMINISTRATION GÉNÉRALE</div>', unsafe_allow_html=True)
    st.title("Gestion des étudiants")
    search = st.text_input("Rechercher : nom, prénom, e-mail ou numéro APOGÉE")
    col1, col2, col3 = st.columns(3)
    with col1:
        program = st.text_input("Filière (facultatif)")
    with col2:
        level = st.text_input("Niveau (facultatif)")
    with col3:
        active = st.selectbox("État du compte", ["Tous", "Actifs", "Inactifs"])
    try:
        students = lister(engine, admin_id, search, program, level, active)
    except PermissionError:
        st.error("Accès réservé à l'administration.")
        return
    st.caption(f"{len(students)} compte(s) affiché(s) (maximum 500).")
    st.dataframe(students, hide_index=True, use_container_width=True)
    if not students:
        return
    options = {str(s["id"]): s for s in students}
    chosen = st.selectbox("Sélectionner une fiche à modifier", options, format_func=lambda k:
                           f"{options[k]['student_number']} · {options[k]['first_name']} {options[k]['last_name']}")
    student = options[chosen]
    with st.form(f"student_edit_{chosen}"):
        st.subheader("Modifier la fiche")
        number = st.text_input("Numéro APOGÉE", value=student["student_number"])
        last = st.text_input("Nom", value=student["last_name"])
        first = st.text_input("Prénom", value=student["first_name"])
        email = st.text_input("E-mail", value=student["email"])
        program_edit = st.text_input("Filière", value=student["program"])
        level_edit = st.text_input("Niveau", value=student["level"])
        enabled = st.checkbox("Compte actif", value=bool(student["is_active"]))
        if st.form_submit_button("Enregistrer la fiche", type="primary"):
            try:
                updater(engine, admin_id, student["id"], {
                    "student_number": number, "last_name": last, "first_name": first,
                    "email": email, "program": program_edit, "level": level_edit,
                    "is_active": enabled,
                })
            except (ValueError, PermissionError, LookupError) as exc:
                st.error(f"Modification annulée : {exc}")
            else:
                st.success("Fiche enregistrée. Aucune réclamation n'a été supprimée.")
                st.rerun()
