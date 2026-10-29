"""Vérifier que le diagnostic public n'expose jamais les identifiants SQL."""
import unittest

from sqlalchemy.exc import ArgumentError, OperationalError
from cloud_db_diagnostics import DatabaseSetupError, safe_database_diagnostic


class FakeOriginalError(Exception):
    def __init__(self, sqlstate, message="SECRET_PASS_EXAMPLE_123"):
        super().__init__(message)
        self.sqlstate = sqlstate


class SafeDatabaseDiagnosticTests(unittest.TestCase):
    def test_invalid_credentials_redacted(self):
        error = OperationalError("connect", {"password": "SECRET_PASS_EXAMPLE_123"}, FakeOriginalError("28P01"))
        code, msg = safe_database_diagnostic(error)
        self.assertEqual(code, "DB-AUTH")
        self.assertNotIn("SECRET_PASS_EXAMPLE_123", msg)
        self.assertNotIn("connect", msg)

    def test_database_not_found(self):
        error = OperationalError("connect", {}, FakeOriginalError("3D000"))
        self.assertEqual(safe_database_diagnostic(error)[0], "DB-NAME")

    def test_database_privileges(self):
        error = OperationalError("create table", {}, FakeOriginalError("42501"))
        self.assertEqual(safe_database_diagnostic(error)[0], "DB-PERM")

    def test_network_failure(self):
        error = OperationalError("connect", {}, FakeOriginalError("08006"))
        self.assertEqual(safe_database_diagnostic(error)[0], "DB-NETWORK")

    def test_bad_url(self):
        error = ArgumentError("Contains secret DATABASE_URL=postgres://name:SECRET_PASS_EXAMPLE_123@host/db")
        code, msg = safe_database_diagnostic(error)
        self.assertEqual(code, "DB-URL")
        self.assertNotIn("SECRET_PASS_EXAMPLE_123", msg)

    def test_unknown_error_no_raw_details(self):
        error = RuntimeError("PASSWORD=SECRET_PASS_EXAMPLE_123")
        code, msg = safe_database_diagnostic(error)
        self.assertEqual(code, "DB-OTHER")
        self.assertNotIn("SECRET_PASS_EXAMPLE_123", msg)

    def test_phase_kept_without_original_error_text(self):
        error = DatabaseSetupError("Connexion et création des tables")
        self.assertEqual(error.phase, "Connexion et création des tables")


if __name__ == "__main__":
    unittest.main()
