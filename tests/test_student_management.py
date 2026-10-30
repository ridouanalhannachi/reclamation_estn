import io
import csv
import tempfile
import unittest
from pathlib import Path

from student_management import parse_csv_preview, normalize_profile
from streamlit_store import (
    make_engine, initialize, bootstrap_admin, authenticate, import_students,
    add_claim, claim_detail, get_user, list_claims, list_students, update_student,
    preview_students,
)


class StudentsV14Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = make_engine("sqlite:///" + str(Path(self.temp.name) / "v14.sqlite"))
        initialize(self.engine)
        bootstrap_admin(self.engine, "admin@example.org", "InitialPassword#2026")
        self.admin = authenticate(self.engine, "admin@example.org", "InitialPassword#2026")

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def student(self, number="A001", email="test@example.org"):
        content = "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n" + (
            f"{number},Bennani,Salma,{email},GEE,DUT 1\n")
        pwd = list(csv.reader(io.StringIO(import_students(self.engine, self.admin["id"], content))))[1][2]
        return authenticate(self.engine, email, pwd), pwd

    def test_preview_duplicate_and_import(self):
        self.student()
        content = "num_apogee,nom_etudiant,prenom,email,filiere,niveau\nA001,Test,One,other@example.org,GEE,DUT 1\n"
        preview = preview_students(self.engine, self.admin["id"], content)
        self.assertTrue(preview["issues"])
        self.assertEqual(len(list_students(self.engine, self.admin["id"])), 1)
        repeated = "num_apogee,nom_etudiant,prenom,email,filiere,niveau\nA1,A,B,a@example.org,GEE,S1\nA1,C,D,b@example.org,GEE,S1\n"
        self.assertTrue(parse_csv_preview(repeated)[1])

    def test_inactive_keeps_claim_history(self):
        student, password = self.student()
        claim = add_claim(self.engine, student["id"], "Technique", "PC en panne", "Le poste ne démarre plus.")
        update_student(self.engine, self.admin["id"], student["id"], {"is_active": False})
        self.assertIsNone(get_user(self.engine, student["id"]))
        self.assertIsNone(authenticate(self.engine, student["email"], password))
        with self.assertRaises(PermissionError):
            claim_detail(self.engine, student["id"], claim)
        self.assertEqual(claim_detail(self.engine, self.admin["id"], claim)["id"], claim)
        self.assertEqual(len(list_claims(self.engine, self.admin["id"])), 1)
        self.assertEqual(len(list_students(self.engine, self.admin["id"], active="Inactifs")), 1)
        update_student(self.engine, self.admin["id"], student["id"], {"is_active": True})
        self.assertIsNotNone(get_user(self.engine, student["id"]))

    def test_admin_only_and_duplicate_edits(self):
        first, _ = self.student()
        second, _ = self.student("A002", "second@example.org")
        with self.assertRaises(PermissionError):
            update_student(self.engine, first["id"], second["id"], {"program": "GI"})
        with self.assertRaises(ValueError):
            update_student(self.engine, self.admin["id"], second["id"], {"email": first["email"]})
        update_student(self.engine, self.admin["id"], second["id"], {"program": "GI", "level": "DUT 2"})
        self.assertEqual(len(list_students(self.engine, self.admin["id"], program="GI")), 1)
        with self.assertRaises(PermissionError):
            list_students(self.engine, first["id"])

    def test_additive_migration_preserves_existing_users(self):
        from sqlalchemy import text, inspect
        from werkzeug.security import generate_password_hash
        url = "sqlite:///" + str(Path(self.temp.name) / "legacy.sqlite")
        engine = make_engine(url)
        with engine.begin() as conn:
            conn.execute(text("""CREATE TABLE users (
                id INTEGER PRIMARY KEY, student_number VARCHAR(200) UNIQUE,
                last_name VARCHAR(200) NOT NULL, first_name VARCHAR(200) NOT NULL,
                email VARCHAR(200) NOT NULL UNIQUE, program VARCHAR(200) NOT NULL,
                level VARCHAR(200) NOT NULL, password_hash TEXT NOT NULL,
                password_change_required INTEGER NOT NULL DEFAULT 0,
                role VARCHAR(20) NOT NULL
            )"""))
            conn.execute(text("INSERT INTO users (id, student_number, last_name, first_name,"
                              "email,program,level,password_hash,role) VALUES"
                              " (1,'OLD1','Ancien','Compte','old@example.org','GEE','S1',:ph,'student')"),
                         {"ph": generate_password_hash("LegacyPass#2026")})
        initialize(engine)
        initialize(engine)  # Idempotence
        self.assertIn("is_active", [c["name"] for c in inspect(engine).get_columns("users")])
        with engine.connect() as conn:
            active = conn.execute(text("SELECT is_active FROM users WHERE id=1")).scalar_one()
        self.assertEqual(active, 1)
        engine.dispose()

    def test_invalid_profile(self):
        with self.assertRaises(ValueError):
            normalize_profile({"role": "admin"})
        with self.assertRaises(ValueError):
            normalize_profile({"is_active": "false"})


if __name__ == "__main__":
    unittest.main()
