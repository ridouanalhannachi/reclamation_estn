"""Test contrôles Flask sans internet ni vraie base de production."""
import tempfile
import unittest
from pathlib import Path
from app import create_app


class FlaskSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "long-enough-test-key-do-not-use-in-production-2026",
            "DATABASE": str(root / "local.sqlite3"),
            "UPLOAD_FOLDER": str(root / "uploads"),
        })
        self.client = self.app.test_client()
        self.assertEqual(self.app.test_cli_runner().invoke(args=["init-db"]).exit_code, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_login_limited_after_five_failures(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            csrf = session["_csrf"]
        for _ in range(5):
            response = self.client.post("/login", data={
                "_csrf": csrf, "email": "unknown@example.org", "password": "incorrect",
            })
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/login", data={
            "_csrf": csrf, "email": "unknown@example.org", "password": "incorrect",
        })
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.client.get("/admin").status_code, 302)


if __name__ == "__main__":
    unittest.main()
