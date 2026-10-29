"""Tests d'intégrité / autorisation du backend Streamlit, sans Cloud réel."""
import io
import tempfile
import unittest
from pathlib import Path

from streamlit_store import (
    Claim, User, add_claim, authenticate, bootstrap_admin, change_password,
    claim_detail, get_attachment, get_user, import_students, initialize,
    list_claims, make_engine, respond,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        url = "sqlite:///" + str(Path(self.folder.name) / "store.db")
        self.engine = make_engine(url)
        initialize(self.engine)
        self.assertTrue(bootstrap_admin(self.engine, "admin@example.org", "InitialPassword#2026"))
        self.admin = authenticate(self.engine, "admin@example.org", "InitialPassword#2026")
        self.assertTrue(self.admin["password_change_required"])
        self.assertFalse(bootstrap_admin(self.engine, "other@example.org", "DifferentStrong#2026"))
        self.assertEqual(len(list_claims(self.engine, self.admin["id"])), 0)

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def create_student(self, apogee="A001", email="student@example.org"):
        credentials = import_students(
            self.engine, self.admin["id"],
            "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n"
            + f"{apogee},Bennani,Salma,{email},GEE,DUT 1\n",
        )
        password = list(__import__("csv").reader(io.StringIO(credentials)))[1][2]
        user = authenticate(self.engine, email, password)
        self.assertIsNotNone(user)
        self.assertTrue(user["password_change_required"])
        return user, password

    def test_password_rotation(self):
        student, old = self.create_student()
        with self.assertRaises(ValueError):
            change_password(self.engine, student["id"], "incorrect", "NewSecurePassword!2026")
        change_password(self.engine, student["id"], old, "NewSecurePassword!2026")
        self.assertFalse(get_user(self.engine, student["id"])["password_change_required"])
        self.assertIsNone(authenticate(self.engine, student["email"], old))
        self.assertIsNotNone(authenticate(self.engine, student["email"], "NewSecurePassword!2026"))

    def test_claim_persists_and_access_is_scoped(self):
        first, _ = self.create_student()
        other, _ = self.create_student("A002", "other@example.org")
        claim_id = add_claim(
            self.engine, first["id"], "Technique", "Panne ordinateur",
            "Ordinateur hors service dans la salle TP."
        )
        self.assertEqual(len(list_claims(self.engine, first["id"])), 1)
        self.assertEqual(len(list_claims(self.engine, other["id"])), 0)
        self.assertEqual(len(list_claims(self.engine, self.admin["id"])), 1)
        with self.assertRaises(PermissionError):
            claim_detail(self.engine, other["id"], claim_id)
        with self.assertRaises(PermissionError):
            respond(self.engine, first["id"], claim_id, "Acceptée", "Intervention technique planifiée.")
        respond(self.engine, self.admin["id"], claim_id, "Acceptée", "Intervention technique planifiée.")
        detail = claim_detail(self.engine, first["id"], claim_id)
        self.assertEqual(detail["status"], "Acceptée")
        self.assertEqual(len(detail["history"]), 2)

    def test_attachment_is_durable_and_private(self):
        student, _ = self.create_student()
        other, _ = self.create_student("A002", "other@example.org")
        file_data = b"%PDF-1.7\nmock-example"
        claim_id = add_claim(
            self.engine, student["id"], "Pédagogique", "Rectification note",
            "La note du relevé n'est pas correcte.", "preuve.pdf", file_data
        )
        name, mime, payload = get_attachment(self.engine, student["id"], claim_id)
        self.assertEqual(payload, file_data)
        self.assertEqual(mime, "application/pdf")
        self.assertEqual(name, "preuve.pdf")
        with self.assertRaises(PermissionError):
            get_attachment(self.engine, other["id"], claim_id)
        with self.assertRaises(ValueError):
            add_claim(self.engine, student["id"], "Technique", "Fichier invalide",
                      "Je joins un fichier non valide.", "attaque.pdf", b"not a PDF")

    def test_import_duplicate_rolls_back_whole_batch(self):
        self.create_student()
        csv_text = (
            "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n"
            "A900,Amrani,Youssef,new@example.org,GEE,DUT 2\n"
            "A001,Bennani,Salma,duplicate@example.org,GEE,DUT 1\n"
        )
        with self.assertRaises(ValueError):
            import_students(self.engine, self.admin["id"], csv_text)
        self.assertIsNone(authenticate(self.engine, "new@example.org", "any"))

    def test_reject_plaintext_import(self):
        with self.assertRaises(ValueError):
            import_students(self.engine, self.admin["id"],
                "num_apogee,nom_etudiant,prenom,email,filiere,niveau,mot_de_passe\n"
                "A500,Test,User,test@example.org,GEE,DUT 1,pass123\n")


if __name__ == "__main__":
    unittest.main()
