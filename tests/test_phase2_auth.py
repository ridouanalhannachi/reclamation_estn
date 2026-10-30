"""Tests SQL de limitation persistante, séparation des droits et journalisation."""
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import select
from streamlit_store import (
    AuthAudit, add_claim, authenticate, bootstrap_admin, claim_detail,
    get_attachment, import_students, initialize, list_claims, make_engine, respond,
)


class AuthPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = make_engine("sqlite:///" + str(Path(self.folder.name) / "auth.sqlite"))
        initialize(self.engine)
        bootstrap_admin(self.engine, "admin@estn.test", "PasswordForTesting#2026")

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def test_rate_limit_is_stored_and_no_password_logged(self):
        for _ in range(5):
            self.assertIsNone(authenticate(self.engine, "admin@estn.test", "incorrect"))
        self.assertIsNone(authenticate(self.engine, "admin@estn.test", "PasswordForTesting#2026"))
        with self.engine.begin() as conn:
            rows = conn.execute(select(AuthAudit.action, AuthAudit.identity)).all()
        self.assertEqual(rows[-1][0], "limited")
        self.assertTrue(all("@" not in key for _, key in rows))

    def test_claims_cannot_be_read_by_other_students(self):
        import csv, io
        admin = authenticate(self.engine, "admin@estn.test", "PasswordForTesting#2026")
        students = []
        for apogee, email in (("A1", "one@estn.test"), ("A2", "two@estn.test")):
            credentials = import_students(self.engine, admin["id"],
                "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n" +
                f"{apogee},Test,Etudiant,{email},GEE,S1\n")
            pwd = list(csv.reader(io.StringIO(credentials)))[1][2]
            students.append(authenticate(self.engine, email, pwd))
        claimed = add_claim(self.engine, students[0]["id"], "Technique", "PC défectueux",
                            "La machine ne démarre pas.")
        self.assertEqual(len(list_claims(self.engine, students[1]["id"])), 0)
        with self.assertRaises(PermissionError):
            claim_detail(self.engine, students[1]["id"], claimed)
        with self.assertRaises(PermissionError):
            get_attachment(self.engine, students[1]["id"], claimed)
        with self.assertRaises(PermissionError):
            respond(self.engine, students[1]["id"], claimed, "En cours", "")


if __name__ == "__main__":
    unittest.main()
