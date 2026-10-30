import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine

from streamlit_store import initialize
from administrative_services import (
    initialize_administrative_schema, services, assign_claim,
    administrative_agents, transition_status, request_complement,
    respond_complement,
)
from administrative_workflow import ALLOWED_TRANSITIONS
from administrative_deadlines import deadline_state, treatment_duration


class AdministrativeV16Tests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "test.sqlite3"
        self.engine = create_engine(f"sqlite:///{self.db}")
        self.addCleanup(self.engine.dispose)
        from streamlit_store import bootstrap_admin
        initialize(self.engine)
        bootstrap_admin(self.engine, "admin.v16@test.invalid", "MotDePasseV16-Fictif")
        with self.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("INSERT INTO users(first_name,last_name,email,program,level,password_hash,password_change_required,role,is_active) VALUES ('Admin','V16','admin2.v16@test.invalid','','','$scrypt$',0,'admin',1)"))
            conn.execute(text("INSERT INTO users(first_name,last_name,email,program,level,password_hash,password_change_required,role,is_active,student_number) VALUES ('Étudiant','Un','etudiant1.v16@test.invalid','','','$scrypt$',0,'student',1,'TEST1')"))
            conn.execute(text("INSERT INTO users(first_name,last_name,email,program,level,password_hash,password_change_required,role,is_active,student_number) VALUES ('Étudiant','Deux','etudiant2.v16@test.invalid','','','$scrypt$',0,'student',1,'TEST2')"))
            conn.execute(text("INSERT INTO users(first_name,last_name,email,program,level,password_hash,password_change_required,role,is_active) VALUES ('Agent','Responsable','agent.v16@test.invalid','','','$scrypt$',0,'agent',1)"))
        with self.engine.connect() as conn:
            self.admin = conn.execute(__import__("sqlalchemy").text("SELECT id FROM users WHERE email='admin.v16@test.invalid'")).scalar()
            self.student1 = conn.execute(__import__("sqlalchemy").text("SELECT id FROM users WHERE email='etudiant1.v16@test.invalid'")).scalar()
            self.student2 = conn.execute(__import__("sqlalchemy").text("SELECT id FROM users WHERE email='etudiant2.v16@test.invalid'")).scalar()
            self.agent = conn.execute(__import__("sqlalchemy").text("SELECT id FROM users WHERE email='agent.v16@test.invalid'")).scalar()
        from streamlit_store import add_claim
        self.claim = add_claim(self.engine, self.student1, "Administrative", "Objet fictif de réclamation", "Description fictive suffisamment longue pour le test.")

    def test_assignment_and_reassignment_history(self):
        service = services(self.engine)[0]["id"]
        assign_claim(self.engine, self.admin, self.claim, service, self.agent, "urgente", "2099-01-01 00:00:00 UTC", "Première affectation fictive")
        assign_claim(self.engine, self.admin, self.claim, service, self.admin, "haute", "2099-01-02 00:00:00 UTC", "Réaffectation fictive")
        with self.engine.connect() as conn:
            count = conn.exec_driver_sql("SELECT COUNT(*) FROM administrative_assignments WHERE claim_id=?", (self.claim,)).scalar()
        self.assertEqual(count, 2)

    def test_allowed_and_forbidden_transitions(self):
        service = services(self.engine)[0]["id"]
        assign_claim(self.engine, self.admin, self.claim, service, self.agent, "normale", None, "Affectation test")
        transition_status(self.engine, self.agent, self.claim, "En cours", "Prise en charge")
        with self.assertRaises(ValueError):
            transition_status(self.engine, self.agent, self.claim, "Clôturée", "trop court")
        # A transition to a final status from a state that does not allow it is forbidden.
        with self.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("UPDATE claims SET status='Soumise' WHERE id=:id"), {"id": self.claim})
        with self.assertRaises(ValueError):
            transition_status(self.engine, self.agent, self.claim, "Clôturée", "Décision fictive suffisamment motivée")

    def test_complement_request_and_student_response(self):
        service = services(self.engine)[0]["id"]
        assign_claim(self.engine, self.admin, self.claim, service, self.agent, "normale", None, "Affectation test")
        request_complement(self.engine, self.admin, self.claim, "Veuillez fournir un document fictif.")
        with self.assertRaises(PermissionError):
            from administrative_services import respond_complement
            respond_complement(self.engine, self.student2, self.claim, "Réponse interdite")
        respond_complement(self.engine, self.student1, self.claim, "Voici la réponse fictive de l'étudiant.")

    def test_access_isolation(self):
        from streamlit_store import claim_detail
        with self.assertRaises(PermissionError):
            claim_detail(self.engine, self.student2, self.claim)

    def test_deadlines_and_resolution(self):
        claim = {"status": "En cours", "due_at": "2000-01-01 00:00:00 UTC", "created_at": "2000-01-01 00:00:00 UTC"}
        self.assertEqual(deadline_state(claim), "En retard")
        claim["status"] = "Clôturée"
        claim["resolved_at"] = "2000-01-02 00:00:00 UTC"
        self.assertEqual(deadline_state(claim), "Terminé")
        self.assertEqual(treatment_duration(claim), 1)

    def test_repeated_migration_preserves_data(self):
        initialize_administrative_schema(self.engine)
        initialize_administrative_schema(self.engine)
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM users").scalar(), 5)
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM claims").scalar(), 1)


if __name__ == "__main__":
    unittest.main()
