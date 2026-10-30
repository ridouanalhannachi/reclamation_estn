"""Tests isolés Google Drive, sans réseau et sans secrets."""
import csv
import io
import threading
import unittest
from drive_store import DriveStore, _name


class FakeDrive(DriveStore):
    def __init__(self):
        self.files = {}
        self._lock = threading.RLock()

    def _list(self):
        return [{"id": k, "name": record["name"]} for k, record in self.files.items()]

    def _read(self, file_id):
        return self.files[file_id]["value"].copy()

    def _write(self, kind, value, content_type="application/json"):
        name = _name(kind)
        self.files[name] = {"name": name, "value": value}
        return name

    def _binary(self, file_id):
        return self.files[file_id]["value"]


class DriveV14Tests(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.drive.bootstrap_admin("admin@example.org", "StrongAdminPassword#2026")
        self.admin = self.drive.authenticate("admin@example.org", "StrongAdminPassword#2026")
        content = "num_apogee,nom_etudiant,prenom,email,filiere,niveau\nA001,Bennani,Salma,student@example.org,GEE,S1\n"
        exported = self.drive.import_students(self.admin["id"], content)
        password = list(csv.reader(io.StringIO(exported)))[1][2]
        self.student = self.drive.authenticate("student@example.org", password)
        self.password = password

    def test_deactivation_keeps_claims_and_denies_access(self):
        claim = self.drive.add_claim(self.student["id"], "Technique", "PC en panne", "Le poste ne démarre plus.")
        self.drive.update_student(self.admin["id"], self.student["id"], {"is_active": False})
        self.assertIsNone(self.drive.get_user(self.student["id"]))
        self.assertIsNone(self.drive.authenticate("student@example.org", self.password))
        with self.assertRaises(PermissionError):
            self.drive.claim_detail(self.student["id"], claim)
        self.assertEqual(self.drive.claim_detail(self.admin["id"], claim)["id"], claim)
        self.assertEqual(len(self.drive.list_students(self.admin["id"], active="Inactifs")), 1)
        self.assertEqual(len(self.drive._files("claim_")), 1)

    def test_security_and_duplicate(self):
        with self.assertRaises(PermissionError):
            self.drive.list_students(self.student["id"])
        with self.assertRaises(PermissionError):
            self.drive.update_student(self.student["id"], self.student["id"], {"program": "GI"})
        with self.assertRaises(ValueError):
            self.drive.update_student(self.admin["id"], self.student["id"], {"email": "admin@example.org"})
        self.drive.update_student(self.admin["id"], self.student["id"], {"program": "GI"})
        self.assertEqual(len(self.drive.list_students(self.admin["id"], program="GI")), 1)


if __name__ == "__main__":
    unittest.main()
