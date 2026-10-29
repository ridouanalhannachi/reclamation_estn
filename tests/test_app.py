"""Tests fonctionnels du socle ESTN (base isolée et jetons CSRF)."""
import io
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash
from app import create_app, db


class ReclamationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test-key-with-adequate-entropy-for-unit-tests",
            "DATABASE": str(root / "test.sqlite3"),
            "UPLOAD_FOLDER": str(root / "uploads"),
        })
        self.client = self.app.test_client()
        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            with db():
                for role, email in (("student", "student@test.ma"), ("admin", "admin@test.ma"), ("student", "other@test.ma")):
                    db().execute(
                        "INSERT INTO users (student_number, first_name, last_name, email, "
                        "program, level, password_hash, role) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (email, "Test", "User", email, "GEE", "DUT 1",
                         generate_password_hash("StrongPassword123!"), role),
                    )

    def tearDown(self):
        self.temp.cleanup()

    def csrf(self):
        with self.client.session_transaction() as sess:
            return sess.get("_csrf", "")

    def login(self, email):
        self.client.get("/login")
        response = self.client.post("/login", data={
            "_csrf": self.csrf(), "email": email, "password": "StrongPassword123!"
        })
        self.assertEqual(response.status_code, 302)

    def claim(self):
        return self.client.post("/student/new", data={
            "_csrf": self.csrf(), "category": "Technique",
            "subject": "Problème ordinateur", "description": "La machine de la salle TP est en panne.",
        })

    def test_student_submission_and_history(self):
        self.login("student@test.ma")
        response = self.claim()
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(response.headers["Location"])
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Probl", detail.data)
        self.assertIn(b"historique", detail.data.lower())

    def test_no_csrf_rejected(self):
        self.client.get("/login")
        response = self.client.post("/login", data={"email": "admin@test.ma", "password": "StrongPassword123!"})
        self.assertEqual(response.status_code, 400)

    def test_student_cannot_open_admin(self):
        self.login("student@test.ma")
        self.assertEqual(self.client.get("/admin").status_code, 403)
        self.assertEqual(self.client.get("/admin/students/import").status_code, 403)

    def test_student_cannot_see_other_claim(self):
        self.login("student@test.ma")
        response = self.claim()
        claim_url = response.headers["Location"]
        self.client.post("/logout", data={"_csrf": self.csrf()})
        self.login("other@test.ma")
        self.assertEqual(self.client.get(claim_url).status_code, 403)

    def test_admin_can_respond_and_history_is_visible(self):
        self.login("student@test.ma")
        response = self.claim()
        claim_url = response.headers["Location"]
        claim_id = int(claim_url.rsplit("/", 1)[1])
        self.client.post("/logout", data={"_csrf": self.csrf()})
        self.login("admin@test.ma")
        reply = self.client.post(f"/admin/claims/{claim_id}/respond", data={
            "_csrf": self.csrf(), "status": "Acceptée", "response": "Le service technique interviendra lundi."
        })
        self.assertEqual(reply.status_code, 302)
        page = self.client.get(claim_url)
        self.assertIn("Acceptée".encode(), page.data)
        self.assertIn(b"service technique", page.data)

    def test_csv_import_and_duplicate_rollback(self):
        self.login("admin@test.ma")
        csv_data = (
            "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n"
            "A100,AMRANI,Youssef,import@test.ma,GEE,DUT 1\n"
        )
        resp = self.client.post("/admin/students/import", data={
            "_csrf": self.csrf(), "csv_file": (io.BytesIO(csv_data.encode()), "students.csv")
        }, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"mot_de_passe_temporaire", resp.data)
        self.assertIn("no-store", resp.headers["Cache-Control"])
        resp = self.client.post("/admin/students/import", data={
            "_csrf": self.csrf(), "csv_file": (io.BytesIO(csv_data.encode()), "students.csv")
        }, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            count = db().execute("SELECT COUNT(*) FROM users WHERE email='import@test.ma'").fetchone()[0]
            self.assertEqual(count, 1)

    def test_imported_account_must_change_password(self):
        self.login("admin@test.ma")
        content = (
            "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n"
            "A200,DUPONT,Lea,lea@test.ma,GEE,DUT 1\n"
        )
        resp = self.client.post("/admin/students/import", data={
            "_csrf": self.csrf(), "csv_file": (io.BytesIO(content.encode()), "students.csv")
        }, content_type="multipart/form-data")
        temporary_password = resp.data.decode().splitlines()[1].split(",")[2]
        self.client.post("/logout", data={"_csrf": self.csrf()})
        self.client.get("/login")
        login_resp = self.client.post("/login", data={
            "_csrf": self.csrf(), "email": "lea@test.ma", "password": temporary_password
        })
        self.assertEqual(login_resp.status_code, 302)
        self.assertIn("/account/change-password", login_resp.headers["Location"])
        self.assertEqual(self.client.get("/student").status_code, 302)
        changed = self.client.post("/account/change-password", data={
            "_csrf": self.csrf(), "old_password": temporary_password,
            "new_password": "MyDifferentStrongPass!123",
            "confirm_password": "MyDifferentStrongPass!123",
        })
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(self.client.get("/student").status_code, 200)

    def test_plaintext_password_column_rejected(self):
        self.login("admin@test.ma")
        content = ("num_apogee,nom_etudiant,prenom,email,filiere,niveau,mot_de_passe\n"
                   "A123,Dupont,Lea,new@test.ma,GEE,DUT 2,123456\n")
        resp = self.client.post("/admin/students/import", data={
            "_csrf": self.csrf(), "csv_file": (io.BytesIO(content.encode()), "students.csv")
        }, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db().execute("SELECT id FROM users WHERE email='new@test.ma'").fetchone())


if __name__ == "__main__":
    unittest.main()
