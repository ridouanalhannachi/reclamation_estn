import os
import tempfile
import unittest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from streamlit_store import User, add_claim, claim_detail, initialize, make_engine
from department_management import create_department, create_program
from account_management import create_account, set_account_permissions
from access_control import require_claim_access


class RBACV17Tests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(path)
        self.engine = make_engine("sqlite:///" + path.replace("\\", "/"))
        initialize(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO users(student_number,last_name,first_name,email,program,level,password_hash,password_change_required,role,is_active,permission_version) VALUES(NULL,'Admin','Super','super@example.test','','',:hash,0,'super_admin',1,0)"), {"hash": generate_password_hash("SuperAdminPass!2026")})
            self.super_admin = conn.execute(text("SELECT id FROM users WHERE email='super@example.test'")).scalar()
            for email, role, first, last in (
                ("head.a@example.test", "department_head", "Chef", "A"),
                ("head.b@example.test", "department_head", "Chef", "B"),
                ("student.a@example.test", "student", "Etudiant", "A"),
                ("student.b@example.test", "student", "Etudiant", "B"),
            ):
                conn.execute(text("INSERT INTO users(student_number,last_name,first_name,email,program,level,password_hash,password_change_required,role,is_active,permission_version) VALUES(NULL,:last,:first,:email,'','',:hash,0,:role,1,0)"), {"hash": generate_password_hash("TestPassword!2026"), "email": email, "role": role, "first": first, "last": last})
        create_department(self.engine, self.super_admin, "Génie électrique", "GE")
        create_department(self.engine, self.super_admin, "Génie informatique", "GI")
        with self.engine.connect() as conn:
            self.dept_a = conn.execute(text("SELECT id FROM departments WHERE code='GE'")).scalar()
            self.dept_b = conn.execute(text("SELECT id FROM departments WHERE code='GI'")).scalar()
            self.head_a = conn.execute(text("SELECT id FROM users WHERE email='head.a@example.test'")).scalar()
            self.head_b = conn.execute(text("SELECT id FROM users WHERE email='head.b@example.test'")).scalar()
            self.student_a = conn.execute(text("SELECT id FROM users WHERE email='student.a@example.test'")).scalar()
            self.student_b = conn.execute(text("SELECT id FROM users WHERE email='student.b@example.test'")).scalar()
        set_account_permissions(self.engine, self.super_admin, self.head_a, department_ids=(self.dept_a,), program_ids=())
        set_account_permissions(self.engine, self.super_admin, self.head_b, department_ids=(self.dept_b,), program_ids=())

    def tearDown(self):
        self.engine.dispose()

    def test_department_scope_and_student_isolation(self):
        claim_id = add_claim(self.engine, self.student_a, "Pédagogique - Contestation de note", "Note du module", "Demande de vérification de ma note.")
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE claims SET responsible_department_id=:dept, is_grade_complaint=1 WHERE id=:claim"), {"dept": self.dept_a, "claim": claim_id})
            claim = conn.execute(text("SELECT * FROM claims WHERE id=:claim"), {"claim": claim_id}).mappings().one()
            require_claim_access(conn, self.head_a, dict(claim))
        with self.assertRaises(PermissionError):
            with self.engine.connect() as conn:
                require_claim_access(conn, self.head_b, dict(claim))
        with self.assertRaises(PermissionError):
            claim_detail(self.engine, self.student_b, claim_id)

    def test_legacy_admin_can_create_managed_account(self):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO users(student_number,last_name,first_name,email,program,level,password_hash,password_change_required,role,is_active,permission_version) VALUES(NULL,'Legacy','Admin','legacy-admin@example.test','','',:hash,0,'admin',1,0)"), {"hash": generate_password_hash("LegacyAdminPass!2026")})
            legacy_admin = conn.execute(text("SELECT id FROM users WHERE email='legacy-admin@example.test'")).scalar()
        created, _ = create_account(self.engine, legacy_admin, "Compte", "Autorisé", "managed@example.test", "administrative_agent")
        self.assertIsInstance(created, int)

    def test_account_creation_and_permission_version(self):
        created, temporary = create_account(self.engine, self.super_admin, "Agent", "Test", "agent@example.test", "administrative_agent", (self.dept_a,), (), (), True)
        self.assertTrue(temporary)
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT permission_version FROM users WHERE id=:id"), {"id": created}).scalar()
            required = conn.execute(text("SELECT password_change_required FROM users WHERE id=:id"), {"id": created}).scalar()
        self.assertEqual(row, 0)
        self.assertEqual(required, 1)
        set_account_permissions(self.engine, self.super_admin, created, active=False)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT permission_version FROM users WHERE id=:id"), {"id": created}).scalar(), 1)

    def test_repeated_migration_is_non_destructive(self):
        claim_id = add_claim(self.engine, self.student_a, "Technique", "Panne imprimante", "L'imprimante du laboratoire ne fonctionne pas.")
        initialize(self.engine)
        initialize(self.engine)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM claims WHERE id=:id"), {"id": claim_id}).scalar(), 1)


if __name__ == "__main__":
    unittest.main()
