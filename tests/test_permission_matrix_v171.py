import os
import tempfile
import unittest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from streamlit_store import initialize, make_engine
from permission_management import (
    PERMISSIONS, role_permission_matrix, save_role_permission_matrix,
    restore_role_permissions,
)
from access_control import has_permission, permission_scopes


class PermissionMatrixV171Tests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(path)
        self.engine = make_engine("sqlite:///" + path.replace("\\", "/"))
        initialize(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO users(student_number,last_name,first_name,email,program,level,password_hash,password_change_required,role,is_active,permission_version) VALUES(NULL,'Admin','Super','matrix-super@example.test','','',:hash,0,'super_admin',1,0)"), {"hash": generate_password_hash("MatrixAdminPass!2026")})
            self.actor = conn.execute(text("SELECT id FROM users WHERE email='matrix-super@example.test'")).scalar()

    def tearDown(self):
        self.engine.dispose()

    def test_save_matrix_is_backend_enforced_and_scoped(self):
        matrix = role_permission_matrix(self.engine)
        changes = {}
        for permission in PERMISSIONS:
            changes[permission] = {}
            for role_name, values in matrix.items():
                current = values.get(permission, {"granted": False, "scope": "global"})
                changes[permission][role_name] = dict(current)
        changes["claims.view_department"]["program_manager"] = {"granted": True, "scope": "department"}
        save_role_permission_matrix(self.engine, self.actor, changes)
        with self.engine.connect() as conn:
            user = conn.execute(text("SELECT id FROM users WHERE role='program_manager' LIMIT 1")).scalar()
            if user:
                self.assertTrue(has_permission(conn, user, "claims.view_department"))
                self.assertEqual(permission_scopes(conn, user).get("claims.view_department"), "department")
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT granted,scope FROM role_permissions WHERE role='program_manager' AND permission='claims.view_department'")).one()
        self.assertEqual(row.scope, "department")
        self.assertEqual(row.granted, 1)

    def test_restore_defaults_requires_confirmation(self):
        with self.assertRaises(PermissionError):
            restore_role_permissions(self.engine, self.actor, confirmed=False)
        restore_role_permissions(self.engine, self.actor, confirmed=True)
        matrix = role_permission_matrix(self.engine)
        self.assertIn("claims.view_department", matrix["department_head"])

    def test_repeated_migration_preserves_matrix(self):
        initialize(self.engine)
        before = role_permission_matrix(self.engine)
        initialize(self.engine)
        self.assertEqual(before, role_permission_matrix(self.engine))


if __name__ == "__main__":
    unittest.main()
