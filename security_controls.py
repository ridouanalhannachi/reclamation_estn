"""Contrôles communs ESTN : audit pseudonymisé, expiration, anti-bruteforce.

La journalisation ne contient ni e-mail, ni mot de passe, ni jeton.
Le backend SQL persiste les tentatives ; Drive utilise des événements append-only.
Flask a un limiteur par processus (non suffisant pour plusieurs workers).
"""
import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_FAILED_LOGINS = 5
WINDOW_SECONDS = 15 * 60
IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 8 * 60 * 60


def identity_key(email):
    """Empreinte stable d'une adresse normalisée ; jamais l'adresse brute."""
    return hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()


def is_limited(events, now=None):
    """events : séquence (timestamp UNIX, 'failed'|'success'|'limited')."""
    now = time.time() if now is None else now
    earliest = now - WINDOW_SECONDS
    failures = 0
    for at, action in events:
        if at < earliest or at > now:
            continue
        if action == "success":
            failures = 0
        elif action == "failed":
            failures += 1
    return failures >= MAX_FAILED_LOGINS


def session_expired(authenticated_at, last_seen, now=None):
    now = time.time() if now is None else now
    if not isinstance(authenticated_at, (int, float)) or not isinstance(last_seen, (int, float)):
        return True
    return (now < last_seen or now - last_seen > IDLE_SECONDS
            or now < authenticated_at or now - authenticated_at > ABSOLUTE_SECONDS)


_logger = logging.getLogger("estn.security")
_logger.propagate = False
_logger.setLevel(logging.INFO)
_logger_lock = threading.RLock()


def audit_event(event, actor=None, resource=None):
    """Write a non-sensitive event to a rotating local audit log (ephemeral in Cloud)."""
    with _logger_lock:
        if not _logger.handlers:
            target = Path(os.environ.get("ESTN_AUDIT_LOG", "instance/estn-access.log"))
            target.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(target, maxBytes=2 * 1024 * 1024,
                                          backupCount=5, encoding="utf-8")
            _logger.addHandler(handler)
        entry = {"at": datetime.now(timezone.utc).isoformat(), "event": str(event)[:60]}
        if actor is not None:
            entry["actor"] = identity_key(str(actor))[:20]
        if resource is not None:
            entry["resource"] = str(resource)[:80]
        _logger.info(json.dumps(entry, ensure_ascii=False))


class ProcessLoginLimiter:
    """Fallback Flask mono-processus, ne remplace pas un limiteur distribué."""

    def __init__(self):
        self._records = {}
        self._lock = threading.RLock()

    def allowed(self, email, now=None):
        now = time.time() if now is None else now
        key = identity_key(email)
        with self._lock:
            records = self._records.get(key, deque())
            while records and records[0][0] < now - WINDOW_SECONDS:
                records.popleft()
            self._records[key] = records
            return not is_limited(records, now)

    def record(self, email, success, now=None):
        now = time.time() if now is None else now
        key = identity_key(email)
        with self._lock:
            records = self._records.setdefault(key, deque())
            records.append((now, "success" if success else "failed"))
            while records and records[0][0] < now - WINDOW_SECONDS:
                records.popleft()
            # avoid retaining an unlimited number of identities or events
            if len(self._records) > 5000:
                for old_key in list(self._records)[:500]:
                    del self._records[old_key]
