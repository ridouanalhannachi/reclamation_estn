"""Tests purs du module de sécurité, sans données réelles."""
import unittest
from security_controls import (
    ProcessLoginLimiter, is_limited, session_expired, identity_key,
    MAX_FAILED_LOGINS, WINDOW_SECONDS, IDLE_SECONDS, ABSOLUTE_SECONDS,
)


class SecurityControlsTests(unittest.TestCase):
    def test_limiter(self):
        guard = ProcessLoginLimiter()
        for _ in range(MAX_FAILED_LOGINS):
            self.assertTrue(guard.allowed("USER@example.org", now=1000))
            guard.record("user@example.org", False, now=1000)
        self.assertFalse(guard.allowed("user@example.org", now=1001))
        self.assertTrue(guard.allowed("user@example.org", now=1000 + WINDOW_SECONDS + 1))

    def test_reset_on_success_and_independent_users(self):
        guard = ProcessLoginLimiter()
        for _ in range(4):
            guard.record("a@example.org", False, now=200)
        guard.record("a@example.org", True, now=201)
        self.assertTrue(guard.allowed("a@example.org", now=202))
        self.assertTrue(guard.allowed("b@example.org", now=202))

    def test_sql_drive_decision(self):
        events = [(100, "failed")] * 5
        self.assertTrue(is_limited(events, now=101))
        self.assertFalse(is_limited(events + [(105, "success")], now=106))
        self.assertFalse(is_limited(events, now=100 + WINDOW_SECONDS + 1))

    def test_session_timeout(self):
        self.assertFalse(session_expired(1000, 1500, now=1501))
        self.assertTrue(session_expired(1000, 1500, now=1500 + IDLE_SECONDS + 1))
        self.assertTrue(session_expired(1000, 8000, now=1000 + ABSOLUTE_SECONDS + 1))
        self.assertTrue(session_expired(None, None))

    def test_identity_pseudonymised(self):
        self.assertEqual(identity_key(" A@X.org "), identity_key("a@x.org"))
        self.assertNotIn("a@x.org", identity_key("a@x.org"))


if __name__ == "__main__":
    unittest.main()
