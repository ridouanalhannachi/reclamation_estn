"""No-network Google Drive backend tests using a fake append-only file store."""
import csv
import io
import threading
import unittest

from drive_store import DriveStore, _name


class InMemoryDrive(DriveStore):
    def __init__(self):
        self.files = {}
        self._lock = threading.RLock()

    def _list(self):
        return [{"id": k, "name": entry["name"]} for k, entry in self.files.items()]

    def _read(self, file_id):
        return self.files[file_id]["value"].copy()

    def _write(self, kind, value, content_type="application/json"):
        name = _name(kind)
        self.files[name] = {"name": name, "value": value}
        return name

    def _binary(self, file_id):
        return self.files[file_id]["value"]


class GoogleDriveStoreTests(unittest.TestCase):
    def setUp(self):
        self.backend = InMemoryDrive()
        self.assertTrue(self.backend.bootstrap_admin("admin@estn.test", "RandomAdminPassword!2026"))
        self.assertFalse(self.backend.bootstrap_admin("other@estn.test", "OtherAdminPassword!2026"))
        self.admin = self.backend.authenticate("admin@estn.test", "RandomAdminPassword!2026")

    def student(self, number="A900", email="student@estn.test"):
        result = self.backend.import_students(
            self.admin["id"],
            "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n"
            + f"{number},Bennani,Salma,{email},GEE,DUT 1\n",
        )
        row = list(csv.reader(io.StringIO(result)))[1]
        u = self.backend.authenticate(email, row[2])
        self.assertIsNotNone(u)
        return u, row[2]

    def test_bootstrap_and_password_rotation(self):
        self.assertTrue(self.admin["password_change_required"])
        self.backend.change_password(self.admin["id"], "RandomAdminPassword!2026", "ChangedPassword#2026")
        self.assertIsNone(self.backend.authenticate("admin@estn.test", "RandomAdminPassword!2026"))
        self.assertFalse(self.backend.get_user(self.admin["id"])["password_change_required"])
        self.assertIsNotNone(self.backend.authenticate("admin@estn.test", "ChangedPassword#2026"))

    def test_claim_history_and_permissions(self):
        student, _ = self.student()
        other, _ = self.student("A901", "other@estn.test")
        claim = self.backend.add_claim(student["id"], "Technique", "PC en panne",
                                       "Mon ordinateur ne fonctionne pas.")
        self.assertEqual(len(self.backend.list_claims(student["id"])), 1)
        self.assertEqual(len(self.backend.list_claims(other["id"])), 0)
        with self.assertRaises(PermissionError):
            self.backend.claim_detail(other["id"], claim)
        with self.assertRaises(PermissionError):
            self.backend.respond(student["id"], claim, "Acceptée", "Intervention planifiée.")
        self.backend.respond(self.admin["id"], claim, "Acceptée", "Intervention planifiée.")
        detail = self.backend.claim_detail(student["id"], claim)
        self.assertEqual(detail["status"], "Acceptée")
        self.assertEqual(detail["admin_response"], "Intervention planifiée.")
        self.assertEqual(len(detail["history"]), 2)

    def test_private_attachment(self):
        student, _ = self.student()
        other, _ = self.student("A901", "other@estn.test")
        pdf = b"%PDF-1.7\nmock-pdf"
        claim = self.backend.add_claim(student["id"], "Pédagogique", "Erreur de note",
                                       "Ma note est incorrecte.", "preuve.pdf", pdf)
        name, mime, blob = self.backend.get_attachment(student["id"], claim)
        self.assertEqual((name, mime, blob), ("preuve.pdf", "application/pdf", pdf))
        with self.assertRaises(PermissionError):
            self.backend.get_attachment(other["id"], claim)
        with self.assertRaises(ValueError):
            self.backend.add_claim(student["id"], "Technique", "Fichier incorrect",
                                   "Description de fichier.", "bad.pdf", b"not pdf")

    def test_batch_duplicate_rejected_without_extra_files(self):
        self.student()
        before = len(self.backend.files)
        with self.assertRaises(ValueError):
            self.backend.import_students(self.admin["id"],
                "num_apogee,nom_etudiant,prenom,email,filiere,niveau\n"
                "A900,Bennani,Salma,another@estn.test,GEE,DUT 1\n")
        self.assertEqual(len(self.backend.files), before)

    def test_import_passwords_not_plaintext(self):
        self.student()
        docs = [e["value"] for e in self.backend.files.values()
                if e["name"].startswith("estn_v2_users_")]
        self.assertTrue(all("password_hash" in user and "password" not in user
                            for doc in docs for user in doc["users"]))
        self.assertIsNone(self.backend.authenticate("unknown@estn.test", "anything"))


if __name__ == "__main__":
    unittest.main()
