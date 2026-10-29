"""Test the local secrets-file generator without performing real OAuth."""
import base64
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.create_gdrive_token import SCOPE, build_secrets, save_private_secrets


class CloudSecretsSetupTests(unittest.TestCase):
    def credentials(self):
        return json.dumps({
            "refresh_token": "test-refresh-token",
            "client_id": "test-client.apps.googleusercontent.com",
            "client_secret": "test-client-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": [SCOPE],
        })

    def test_real_values_round_trip_toml(self):
        contents = build_secrets(
            self.credentials(), "ADMIN@EXAMPLE.ORG", "test-long-random-password-of-30-chars"
        )
        parsed = tomllib.loads(contents)
        self.assertEqual(parsed["BOOTSTRAP_ADMIN_EMAIL"], "admin@example.org")
        self.assertEqual(parsed["BOOTSTRAP_ADMIN_PASSWORD"], "test-long-random-password-of-30-chars")
        token = json.loads(base64.b64decode(parsed["GDRIVE_TOKEN_B64"], validate=True))
        self.assertEqual(token["scopes"], [SCOPE])
        self.assertEqual(token["refresh_token"], "test-refresh-token")

    def test_requires_new_valid_scope(self):
        payload = json.loads(self.credentials())
        payload["scopes"] = ["https://www.googleapis.com/auth/drive.file"]
        with self.assertRaises(ValueError):
            build_secrets(json.dumps(payload), "admin@example.org", "long-temporary-password-2026")

    def test_requires_refresh_token(self):
        payload = json.loads(self.credentials())
        del payload["refresh_token"]
        with self.assertRaises(ValueError):
            build_secrets(json.dumps(payload), "admin@example.org", "long-temporary-password-2026")

    def test_requires_valid_email(self):
        with self.assertRaises(ValueError):
            build_secrets(self.credentials(), "not-an-email", "long-temporary-password-2026")

    def test_private_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "streamlit-secrets.toml"
            save_private_secrets(output, "first\n")
            with self.assertRaises(FileExistsError):
                save_private_secrets(output, "second\n")
            self.assertEqual(output.read_text(), "first\n")


if __name__ == "__main__":
    unittest.main()
